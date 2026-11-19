import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from PIL import Image, ImageDraw, ImageFont

from siteintel import create_app
from siteintel.auth.models import User
from siteintel.documents import chunking, embedding, llm, qa
from siteintel.documents import processing
from siteintel.documents.processing import process_document
from siteintel.documents.retrieval import search_project_documents
from siteintel.extensions import db
from siteintel.projects import Document, DocumentChunk, Project, ProjectMember

AGREEMENT_PAGES = [
    "Construction Agreement between the Owner and the Contractor.\nThis agreement covers the villa at Plot 12.",
    "Payment Schedule\nThe owner shall pay 20 percent as advance, 30 percent on completion of the "
    "plinth and 50 percent on handover of the building.",
]
SPEC_PAGES = [
    "Technical Specification\nConcrete shall be grade M25 using Ordinary Portland Cement 53 grade.\n"
    "Reinforcement steel shall be Fe500D.",
]
OTHER_PROJECT_PAGES = ["Secret site notes\nThe cement grade for the secret bridge is M99 special."]


def _escape(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages):
    """Build a small text PDF by hand so tests have known content per page."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        lines = ["BT /F1 12 Tf 14 TL 50 740 Td"]
        for line in text.split("\n"):
            lines.append(f"({_escape(line)}) Tj T*")
        lines.append("ET")
        stream = "\n".join(lines).encode("latin-1")
        content_id = len(objects) + 2
        page_id = len(objects) + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 3 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(f"{page_id} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


def make_scanned_pdf(text):
    image = Image.new("RGB", (1700, 600), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 70)
    except OSError:
        font = ImageFont.load_default()
    draw.text((40, 200), text, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PDF")
    return buffer.getvalue()


class DocumentTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"LLM_API_KEY": "", "LLM_MODEL": ""})
        self.env.start()
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "test-key-that-is-long-enough-for-siteintel",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SQLALCHEMY_ENGINE_OPTIONS": {},
            "WTF_CSRF_ENABLED": False, "REPORT_UPLOAD_ROOT": self.directory.name + "/reports",
            "DOCUMENT_UPLOAD_ROOT": self.directory.name + "/docs",
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            users = [User(full_name=f"{n} Person", email=f"{n}@example.test", password_hash="unused")
                     for n in ("owner", "viewer", "stranger")]
            db.session.add_all(users)
            db.session.flush()
            self.ids = {u.email.split("@")[0]: u.id for u in users}
            mine = Project(owner_id=self.ids["owner"], name="Mine", status="active")
            theirs = Project(owner_id=self.ids["stranger"], name="Theirs", status="active")
            db.session.add_all([mine, theirs])
            db.session.flush()
            self.pid, self.other_pid = mine.id, theirs.id
            db.session.add(ProjectMember(project_id=mine.id, user_id=self.ids["viewer"], role="viewer"))
            db.session.commit()
        self.login("owner")

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        self.env.stop()
        self.directory.cleanup()

    def login(self, name):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.ids[name])
            session["_fresh"] = True

    def url(self, suffix="", project=None):
        return f"/api/projects/{project or self.pid}/documents{suffix}"

    def upload(self, pages=None, name="agreement.pdf", category="agreement", data=None,
               content_type="application/pdf", project=None, client=None):
        payload = data if data is not None else make_pdf(pages or AGREEMENT_PAGES)
        return (client or self.client).post(
            self.url(project=project),
            data={"category": category, "file": (io.BytesIO(payload), name, content_type)},
            content_type="multipart/form-data",
        )


class UploadTests(DocumentTestCase):
    def test_upload_processes_and_lists_document(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.get_json())
        body = response.get_json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["page_count"], 2)
        self.assertEqual(body["category"], "agreement")
        listing = self.client.get(self.url()).get_json()
        self.assertEqual([d["file_name"] for d in listing["documents"]], ["agreement.pdf"])
        self.assertTrue(listing["editable"])

    def test_rejects_bad_extension_mime_and_content(self):
        self.assertEqual(self.upload(name="a.txt").status_code, 400)
        self.assertEqual(self.upload(content_type="text/plain").status_code, 400)
        self.assertEqual(self.upload(data=b"not a pdf at all").status_code, 400)
        self.assertEqual(self.client.get(self.url()).get_json()["documents"], [])

    def test_rejects_missing_or_invalid_category_and_file(self):
        self.assertEqual(self.upload(category="nonsense").status_code, 400)
        response = self.client.post(self.url(), data={"category": "other"}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)

    def test_size_limit_enforced(self):
        self.app.config["DOCUMENT_MAX_BYTES"] = 2000
        response = self.upload(data=make_pdf(["x " * 4000]))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.url()).get_json()["documents"], [])

    def test_duplicate_upload_rejected_and_file_cleaned(self):
        self.assertEqual(self.upload().status_code, 201)
        self.assertEqual(self.upload(name="copy.pdf").status_code, 409)
        files = [p for p in Path(self.directory.name, "docs").rglob("*.pdf")]
        self.assertEqual(len(files), 1)

    def test_filename_is_never_used_for_storage(self):
        response = self.upload(name="../../evil.pdf")
        self.assertEqual(response.status_code, 201)
        with self.app.app_context():
            key = db.session.scalar(db.select(Document.storage_key))
        self.assertRegex(key, r"^\d+/[a-f0-9]{32}\.pdf$")

    def test_corrupt_pdf_marks_document_failed_not_completed(self):
        broken = b"%PDF-1.4\n" + b"garbage" * 50
        response = self.upload(data=broken, name="broken.pdf")
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(body["status"], "failed")
        self.assertTrue(body["error"])
        self.assertNotIn(self.directory.name, body["error"])

    def test_scanned_pdf_uses_ocr(self):
        response = self.upload(data=make_scanned_pdf("Cement grade is OPC 53"), name="scan.pdf")
        body = response.get_json()
        self.assertEqual(body["status"], "completed", body)
        result = self.client.post(self.url("/search"), json={"question": "cement grade OPC"}).get_json()
        self.assertEqual(result["status"], "found")


class ProcessingTests(DocumentTestCase):
    def test_pages_are_preserved_on_chunks(self):
        self.upload()
        with self.app.app_context():
            rows = db.session.scalars(db.select(DocumentChunk).order_by(DocumentChunk.chunk_index)).all()
            self.assertEqual({r.page_number for r in rows}, {1, 2})
            self.assertTrue(all(r.project_id == self.pid and r.embedding for r in rows))
            self.assertTrue(any("advance" in r.text and r.page_number == 2 for r in rows))
            self.assertTrue(all(r.embedding_model == embedding.EMBEDDING_MODEL for r in rows))

    def test_completed_document_is_not_reprocessed_without_force(self):
        self.upload()
        with self.app.app_context():
            doc_id = db.session.scalar(db.select(Document.id))
            with mock.patch("siteintel.documents.processing.extract_pdf_pages") as extract:
                process_document(doc_id)
                extract.assert_not_called()
            with mock.patch("siteintel.documents.processing.extract_pdf_pages", side_effect=processing.extract_pdf_pages) as extract:
                process_document(doc_id, force=True)
                extract.assert_called_once()
            self.assertEqual(db.session.scalar(db.select(db.func.count(DocumentChunk.id))), 2)

    def test_chunking_overlaps_and_never_crosses_pages(self):
        paragraph = "\n".join(f"Line number {i} describes the structural work in detail." for i in range(60))
        chunks = chunking.chunk_page(paragraph, max_chars=400, overlap=80)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(c) <= 480 for c in chunks))
        import re
        first = set(re.findall(r"Line number \d+", chunks[0]))
        second = set(re.findall(r"Line number \d+", chunks[1]))
        self.assertTrue(first & second, "adjacent chunks should overlap")

    def test_repeated_headers_removed(self):
        pages = [f"ACME BUILDERS CONFIDENTIAL\nUnique content {n}\nPage footer line" for n in range(4)]
        cleaned = chunking.strip_repeated_lines(pages)
        self.assertTrue(all("ACME" not in p for p in cleaned))
        self.assertTrue(all(f"Unique content {n}" in p for n, p in enumerate(cleaned)))

    def test_embedding_is_deterministic(self):
        first = embedding.embed("Payment schedule for the owner")
        second = embedding.embed("Payment schedule for the owner")
        self.assertEqual(first, second)


class RetrievalTests(DocumentTestCase):
    def setUp(self):
        super().setUp()
        self.upload(AGREEMENT_PAGES, "agreement.pdf", "agreement")
        self.upload(SPEC_PAGES, "spec.pdf", "specification")
        other = self.app.test_client()
        with other.session_transaction() as session:
            session["_user_id"] = str(self.ids["stranger"])
            session["_fresh"] = True
        self.upload(OTHER_PROJECT_PAGES, "secret.pdf", "specification", project=self.other_pid, client=other)

    def test_finds_correct_document_and_page(self):
        with self.app.app_context():
            result = search_project_documents(self.pid, "What is the payment schedule?")
            self.assertTrue(result.sufficient)
            top = result.chunks[0]
            self.assertEqual((top.document_name, top.page_number), ("agreement.pdf", 2))
            result = search_project_documents(self.pid, "Which cement grade is specified?")
            self.assertEqual(result.chunks[0].document_name, "spec.pdf")

    def test_project_isolation(self):
        with self.app.app_context():
            mine = search_project_documents(self.pid, "secret bridge cement grade M99")
            self.assertTrue(all(c.document_name != "secret.pdf" for c in mine.chunks))
            theirs = search_project_documents(self.other_pid, "secret bridge cement grade M99")
            self.assertEqual(theirs.chunks[0].document_name, "secret.pdf")
            self.assertEqual(qa.answer_project_question(self.pid, "cement grade for the secret bridge M99").status, "no_answer")

    def test_unsupported_question_returns_exact_refusal(self):
        response = self.client.post(self.url("/ask"), json={"question": "What is the colour of the elevator cables?"})
        body = response.get_json()
        self.assertEqual(body["status"], "no_answer")
        self.assertEqual(body["answer"], qa.NO_ANSWER)
        self.assertEqual(body["citations"], [])

    def test_extractive_answer_has_correct_citations(self):
        body = self.client.post(self.url("/ask"), json={"question": "What is the payment schedule?"}).get_json()
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["mode"], "extractive")
        first = body["citations"][0]
        self.assertEqual((first["document_name"], first["page_number"]), ("agreement.pdf", 2))
        self.assertIn("advance", first["excerpt"])
        self.assertTrue(first["view_url"].endswith("/file#page=2"))

    def test_no_documents_status(self):
        with self.app.app_context():
            empty = Project(owner_id=self.ids["owner"], name="Empty", status="active")
            db.session.add(empty)
            db.session.commit()
            pid = empty.id
        body = self.client.post(self.url("/ask", pid), json={"question": "anything at all?"}).get_json()
        self.assertEqual(body["status"], "no_documents")

    def test_question_validation(self):
        for payload in ({}, {"question": ""}, {"question": "hi"}, {"question": "x" * 1001},
                        {"question": "valid question", "document_ids": ["a"]}):
            self.assertEqual(self.client.post(self.url("/ask"), json=payload).status_code, 400, payload)
        self.assertEqual(self.client.post(self.url("/ask"), data="question=abc").status_code, 400)

    def test_get_document_sources_drops_foreign_and_deleted_documents(self):
        with self.app.app_context():
            own = search_project_documents(self.pid, "payment schedule").chunks[0]
            foreign = search_project_documents(self.other_pid, "secret bridge cement").chunks[0]
            cites = [qa._citation(1, own), qa._citation(2, foreign)]
            resolved = qa.get_document_sources(self.pid, cites)
            self.assertEqual([r["document_name"] for r in resolved], ["agreement.pdf"])


class LLMTests(RetrievalTests):
    def ask(self, raw):
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "k", "LLM_MODEL": "m"}), \
                mock.patch.object(llm, "generate", return_value=raw) as generate:
            body = self.client.post(self.url("/ask"), json={"question": "What is the payment schedule?"}).get_json()
        return body, generate

    def test_grounded_llm_answer_keeps_valid_citations_only(self):
        body, generate = self.ask("20 percent advance [1] and a made up fact [9].")
        self.assertEqual(body["mode"], "llm")
        self.assertEqual(body["status"], "answered")
        self.assertNotIn("[9]", body["answer"])
        self.assertTrue(all(c["document_name"] in {"agreement.pdf", "spec.pdf"} for c in body["citations"]))
        prompt = generate.call_args.args[2]
        self.assertIn("agreement.pdf", prompt)
        self.assertNotIn("secret.pdf", prompt)

    def test_uncited_llm_answer_is_refused(self):
        body, _ = self.ask("The schedule is 10 percent monthly.")
        self.assertEqual(body["status"], "no_answer")
        self.assertEqual(body["answer"], qa.NO_ANSWER)

    def test_llm_refusal_passes_through(self):
        body, _ = self.ask(qa.NO_ANSWER)
        self.assertEqual(body["status"], "no_answer")

    def test_llm_failure_falls_back_to_extractive(self):
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "k", "LLM_MODEL": "m"}), \
                mock.patch.object(llm, "generate", side_effect=llm.LLMError("down")):
            body = self.client.post(self.url("/ask"), json={"question": "What is the payment schedule?"}).get_json()
        self.assertEqual(body["mode"], "extractive")
        self.assertTrue(body["notice"])
        self.assertTrue(body["citations"])


class LLMClientTests(unittest.TestCase):
    def test_request_sends_user_agent_auth_and_parses_reply(self):
        from siteintel.documents.settings import LLMSettings
        captured = {}

        class Reply(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout):
            captured["headers"] = {k.lower(): v for k, v in request.header_items()}
            captured["url"] = request.full_url
            return Reply(b'{"choices": [{"message": {"content": " Done [1] "}}]}')

        config = LLMSettings("secret-key", "some-model", "https://llm.example/v1", 5)
        with mock.patch.object(llm.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(llm.generate(config, "sys", "user"), "Done [1]")
        self.assertEqual(captured["url"], "https://llm.example/v1/chat/completions")
        self.assertEqual(captured["headers"]["user-agent"], "SiteIntel/1.0")
        self.assertEqual(captured["headers"]["authorization"], "Bearer secret-key")

    def test_http_error_raises_llm_error_without_leaking_key(self):
        import urllib.error
        from siteintel.documents.settings import LLMSettings
        error = urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b"error code: 1010"))
        config = LLMSettings("secret-key", "m", "https://llm.example/v1", 5)
        with mock.patch.object(llm.urllib.request, "urlopen", side_effect=error), \
                self.assertLogs("siteintel.documents.llm", "ERROR") as logs, \
                self.assertRaises(llm.LLMError) as raised:
            llm.generate(config, "s", "u")
        self.assertNotIn("secret-key", "".join(logs.output) + str(raised.exception))


class AccessTests(DocumentTestCase):
    def test_anonymous_gets_401(self):
        anon = self.app.test_client()
        self.assertEqual(anon.get(self.url()).status_code, 401)
        self.assertEqual(anon.post(self.url("/ask"), json={"question": "abc def"}).status_code, 401)

    def test_stranger_gets_404_everywhere(self):
        self.upload()
        with self.app.app_context():
            doc_id = db.session.scalar(db.select(Document.id))
        self.login("stranger")
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(self.client.get(self.url(f"/{doc_id}/file")).status_code, 404)
        self.assertEqual(self.client.delete(self.url(f"/{doc_id}")).status_code, 404)
        self.assertEqual(self.upload().status_code, 404)
        self.assertEqual(self.client.post(self.url("/ask"), json={"question": "payment schedule"}).status_code, 404)

    def test_document_of_another_project_not_reachable_via_own_project(self):
        self.upload()
        with self.app.app_context():
            doc_id = db.session.scalar(db.select(Document.id))
        self.login("stranger")
        self.assertEqual(self.client.get(self.url(f"/{doc_id}/file", self.other_pid)).status_code, 404)
        self.assertEqual(self.client.delete(self.url(f"/{doc_id}", self.other_pid)).status_code, 404)

    def test_viewer_can_read_and_ask_but_not_change(self):
        self.upload()
        with self.app.app_context():
            doc_id = db.session.scalar(db.select(Document.id))
        self.login("viewer")
        listing = self.client.get(self.url()).get_json()
        self.assertFalse(listing["editable"])
        self.assertEqual(self.client.get(self.url(f"/{doc_id}/file")).status_code, 200)
        self.assertEqual(self.client.post(self.url("/ask"), json={"question": "payment schedule"}).status_code, 200)
        self.assertEqual(self.upload().status_code, 404)
        self.assertEqual(self.client.delete(self.url(f"/{doc_id}")).status_code, 404)
        self.assertEqual(self.client.post(self.url(f"/{doc_id}/process")).status_code, 404)

    def test_view_and_download_headers(self):
        self.upload()
        with self.app.app_context():
            doc_id = db.session.scalar(db.select(Document.id))
        view = self.client.get(self.url(f"/{doc_id}/file"))
        self.assertEqual(view.mimetype, "application/pdf")
        self.assertTrue(view.data.startswith(b"%PDF"))
        self.assertEqual(view.headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("attachment", view.headers.get("Content-Disposition", ""))
        download = self.client.get(self.url(f"/{doc_id}/file?download=1"))
        self.assertIn("attachment", download.headers["Content-Disposition"])

    def test_delete_removes_record_chunks_and_file(self):
        self.upload()
        with self.app.app_context():
            doc_id = db.session.scalar(db.select(Document.id))
        self.assertTrue(list(Path(self.directory.name, "docs").rglob("*.pdf")))
        self.assertEqual(self.client.delete(self.url(f"/{doc_id}")).status_code, 200)
        self.assertEqual(list(Path(self.directory.name, "docs").rglob("*.pdf")), [])
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count(DocumentChunk.id))), 0)
            self.assertEqual(db.session.scalar(db.select(db.func.count(Document.id))), 0)

    def test_reprocess_failed_document(self):
        self.upload(data=b"%PDF-1.4\n" + b"junk" * 40, name="bad.pdf")
        with self.app.app_context():
            doc = db.session.scalar(db.select(Document))
            self.assertEqual(doc.processing_status, "failed")
            doc_id = doc.id
        response = self.client.post(self.url(f"/{doc_id}/process?force=1"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "failed")


class PhaseOneIsolationTests(DocumentTestCase):
    def test_knowledge_documents_are_not_reports(self):
        self.upload()
        with self.app.app_context():
            doc_id = db.session.scalar(db.select(Document.id))
            self.assertEqual(db.session.scalars(db.select(Document).where(Document.reports_only())).all(), [])
        self.assertEqual(self.client.get(f"/api/projects/{self.pid}/documents/{doc_id}/review").status_code, 404)
        details = self.client.get(f"/api/projects/{self.pid}").get_json()
        self.assertEqual(details.get("documents", []), [])
        self.assertEqual(self.client.get(f"/dashboard/projects/{self.pid}/document-assistant/").status_code, 200)

    def test_document_assistant_page_renders_layout(self):
        html = self.client.get(f"/dashboard/projects/{self.pid}/document-assistant/").get_data(as_text=True)
        for text in ("Upload Documents", "Document Library", "RAG Retrieval", "Document Q&amp;A",
                     "Example question", "Expected answer", "js/documents.js"):
            self.assertIn(text, html)


class MigrationTests(unittest.TestCase):
    def test_migration_is_single_head_and_additive(self):
        config = AlembicConfig(str(Path(__file__).resolve().parents[1] / "migrations" / "alembic.ini"))
        config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
        script = ScriptDirectory.from_config(config)
        self.assertEqual(script.get_heads(), ["8af3c21d7e90"])
        revision = script.get_revision("7d2e8f31b4c5")
        self.assertEqual(revision.down_revision, "6c1d4e7a9b20")
        source = Path(revision.path).read_text()
        self.assertIn("document_chunks", source)
        self.assertNotIn('drop_table("documents"', source)
        latest = script.get_revision("8af3c21d7e90")
        self.assertEqual(latest.down_revision, "7d2e8f31b4c5")
        self.assertIn("daily_construction_reports", Path(latest.path).read_text())


if __name__ == "__main__":
    unittest.main()
