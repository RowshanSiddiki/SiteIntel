"""Regression tests: documents are retrievable by title, not only by body text."""
from siteintel.documents.qa import answer_project_question
from siteintel.documents.retrieval import search_project_documents
from siteintel.extensions import db
from siteintel.projects.models import Document
from tests.test_documents import DocumentTestCase

SUPPLY_PAGES = [
    "Cement 50 bags delivered by Rahim Traders at BDT 520 per bag.\nSand 100 CFT delivered on site.",
    "Steel rods 12mm, 2 tonnes, supplied by Karim Steel. Payment due within thirty days.",
]
OTHER_PAGES = ["Electrical wiring schedule for the ground floor lighting circuits and panels."]


class TitleRetrievalTests(DocumentTestCase):
    def setUp(self):
        super().setUp()
        self.upload(SUPPLY_PAGES, "Materials Supply 2.pdf", "other")
        self.upload(OTHER_PAGES, "Electrical Plan.pdf", "specification")

    def names(self, question, project=None):
        with self.app.app_context():
            result = search_project_documents(project or self.pid, question)
            return result.sufficient, [c.document_name for c in result.chunks], result

    def test_exact_title(self):
        for question in ("Materials Supply 2", "Tell me about the Materials Supply 2"):
            ok, names, _ = self.names(question)
            self.assertTrue(ok, question)
            self.assertEqual(set(names), {"Materials Supply 2.pdf"})

    def test_partial_title_and_filename(self):
        for question in ("materials supply", "Materials_Supply_2.pdf contents", "what is in supply 2"):
            ok, names, _ = self.names(question)
            self.assertTrue(ok, question)
            self.assertEqual(set(names), {"Materials Supply 2.pdf"})

    def test_content_question_still_works(self):
        ok, names, _ = self.names("Who supplied the steel rods?")
        self.assertTrue(ok)
        self.assertEqual(names[0], "Materials Supply 2.pdf")

    def test_title_plus_detail_narrows_to_that_document(self):
        ok, names, _ = self.names("In Materials Supply 2 who supplied the steel rods")
        self.assertTrue(ok)
        self.assertEqual(set(names), {"Materials Supply 2.pdf"})
        self.assertEqual(names[0], "Materials Supply 2.pdf")

    def test_multiple_chunks_and_pages_returned(self):
        _, _, result = self.names("Materials Supply 2")
        self.assertEqual({c.page_number for c in result.chunks}, {1, 2})

    def test_answer_cites_document_and_page(self):
        with self.app.app_context():
            answer = answer_project_question(self.pid, "Tell me about the Materials Supply 2")
        self.assertEqual(answer.status, "answered")
        self.assertTrue(answer.citations)
        self.assertTrue(all(c.document_name == "Materials Supply 2.pdf" for c in answer.citations))
        self.assertTrue(all(c.page_number in (1, 2) for c in answer.citations))

    def test_unrelated_title_gets_no_answer(self):
        with self.app.app_context():
            answer = answer_project_question(self.pid, "Tell me about the Roofing Contract")
        self.assertEqual(answer.status, "no_answer")

    def test_project_isolation(self):
        ok, names, result = self.names("Materials Supply 2", project=self.other_pid)
        self.assertFalse(ok)
        self.assertEqual(names, [])
        self.assertEqual(result.unindexed_documents, [])

    def test_ask_endpoint_permissions(self):
        url = self.url("/ask")
        response = self.client.post(url, json={"question": "Tell me about the Materials Supply 2"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "answered")
        self.login("viewer")
        self.assertEqual(self.client.post(url, json={"question": "Materials Supply 2"}).status_code, 200)
        self.login("stranger")
        self.assertEqual(self.client.post(url, json={"question": "Materials Supply 2"}).status_code, 404)

    def test_unindexed_document_is_reported_clearly(self):
        self.upload(["Handover checklist for keys and warranty papers."], "Site Handover Notes.pdf", "other")
        with self.app.app_context():
            doc = db.session.scalar(db.select(Document).where(Document.file_name == "Site Handover Notes.pdf"))
            doc.processing_status = "failed"
            db.session.commit()
            answer = answer_project_question(self.pid, "Tell me about the Site Handover Notes")
        self.assertEqual(answer.status, "not_indexed")
        self.assertIn("Site Handover Notes.pdf", answer.answer)
