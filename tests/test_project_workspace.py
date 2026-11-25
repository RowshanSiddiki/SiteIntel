import io
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

from PIL import Image

from siteintel import create_app
from siteintel.auth.models import User
from siteintel.extensions import db
from siteintel.ml.models import DocumentPrediction
from siteintel.projects import Document, DocumentChunk, Project, ProjectMember
from siteintel.reports.models import DailyConstructionReport, ReportReview
from siteintel.documents.retrieval import search_project_documents
from siteintel.documents.extraction import Extraction, ExtractionError, PageText
from siteintel.documents.embedding import EMBEDDING_MODEL, embed
from siteintel.documents.processing import process_document


REPORT = """planned_pour_tonnes: 40
site_rolling_mean_30: 35
rain_mm: 12 mm
avg_temp_c: 27 C"""


class ProjectWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "test-key-that-is-long-enough-for-siteintel",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SQLALCHEMY_ENGINE_OPTIONS": {},
            "WTF_CSRF_ENABLED": False, "REPORT_UPLOAD_ROOT": self.directory.name,
            "DOCUMENT_UPLOAD_ROOT": self.directory.name,
            "HANDWRITING_OCR_ENDPOINT": "", "HANDWRITING_OCR_KEY": "",
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            users = [User(full_name=name, email=f"{name}@example.test", password_hash="unused")
                     for name in ("owner", "editor", "viewer", "stranger")]
            db.session.add_all(users)
            db.session.flush()
            self.ids = {user.full_name: user.id for user in users}
            project = Project(owner_id=self.ids["owner"], name="Workspace")
            db.session.add(project)
            db.session.flush()
            self.project_id = project.id
            db.session.add_all([
                ProjectMember(project_id=project.id, user_id=self.ids["editor"], role="editor"),
                ProjectMember(project_id=project.id, user_id=self.ids["viewer"], role="viewer"),
            ])
            db.session.commit()
        self.login("owner")

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        self.directory.cleanup()

    def login(self, name):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.ids[name])
            session["_fresh"] = True

    def upload(self, text=REPORT, reporting_date=None, filename="report.png"):
        stream = io.BytesIO()
        Image.new("RGB", (40, 40), "white").save(stream, "PNG")
        stream.seek(0)
        data = {"project_id": str(self.project_id), "image": (stream, filename)}
        if reporting_date:
            data["reporting_date"] = reporting_date
        with patch("siteintel.reports.routes.extract_ocr", return_value=(text, [])):
            response = self.client.post("/api/reports/extract", data=data)
        self.assertEqual(response.status_code, 201)
        return response.json["document_id"]

    def latest_cement(self):
        data = self.client.get(f"/api/projects/{self.project_id}/predictions").json
        return next(item for item in data["predictions"] if item["model_key"] == "cement_demand")

    def test_correct_confirm_and_revert_creates_truthful_history(self):
        document_id = self.upload()
        path = f"/api/reports/documents/{document_id}"
        initial = self.client.get(path).json
        self.assertFalse(initial["reviewed"])
        file_response = self.client.get(path + "/file")
        self.assertEqual(file_response.status_code, 200)
        file_response.close()
        self.assertEqual(self.client.get(f"/api/projects/{self.project_id}/predictions").json["predictions"], [])
        first = self.client.post(path + "/confirm", json={"fields": initial["fields"], "review_id": None})
        self.assertEqual(first.status_code, 200)
        before = self.latest_cement()
        fields = dict(initial["fields"], rain_mm="25 mm")
        saved = self.client.post(path + "/confirm", json={"fields": fields, "review_id": first.json["review_id"]})
        self.assertEqual(saved.status_code, 200)
        corrected = self.latest_cement()
        self.assertEqual(corrected["inputs"]["rain_mm"]["value"], 25)
        self.assertEqual(corrected["inputs"]["rain_mm"]["source"], "manual")
        self.assertEqual(corrected["inputs"]["avg_temp_c"]["source"], "ocr")
        self.assertNotEqual(before["result"], corrected["result"])
        self.assertEqual(self.client.get(path).json["fields"], fields)
        self.assertEqual(self.client.get(path).json["extracted_text"], REPORT)
        self.client.post(path + "/confirm", json={"fields": fields, "review_id": saved.json["review_id"]})
        with self.app.app_context():
            self.assertEqual(db.session.query(ReportReview).count(), 2)
            count = db.session.query(DocumentPrediction).count()
        self.client.post(path + "/confirm", json={"fields": initial["fields"], "review_id": saved.json["review_id"]})
        reverted = self.latest_cement()
        self.assertEqual(reverted["inputs"]["rain_mm"]["value"], 12)
        self.assertEqual(reverted["inputs"]["rain_mm"]["source"], "ocr")
        self.assertEqual(reverted["id"], before["id"])
        with self.app.app_context():
            self.assertEqual(db.session.query(ReportReview).count(), 3)
            self.assertEqual(db.session.query(DocumentPrediction).count(), count)

    def test_review_preserves_manually_completed_missing_fields(self):
        document_id = self.upload(REPORT.replace("avg_temp_c: 27 C", ""))
        path = f"/api/reports/documents/{document_id}"
        original = self.client.get(path).json["fields"]
        saved = self.client.post(path + "/confirm", json={"fields": original, "review_id": None})
        record = self.latest_cement()
        self.client.post(f"/api/predictions/{record['id']}/complete", json={"avg_temp_c": 27})
        fields = self.client.get(path).json["fields"]
        fields["rain_mm"] = "20 mm"
        self.assertEqual(self.client.post(path + "/confirm", json={"fields": fields, "review_id": saved.json["review_id"]}).status_code, 200)
        latest = self.latest_cement()
        self.assertEqual(latest["status"], "complete")
        self.assertEqual(latest["inputs"]["avg_temp_c"]["source"], "manual")
        self.assertEqual(latest["inputs"]["rain_mm"]["value"], 20)

    def test_invalid_or_conflicting_corrections_never_create_prediction(self):
        document_id = self.upload(REPORT + "\nrain mm: 12 mm")
        path = f"/api/reports/documents/{document_id}"
        fields = self.client.get(path).json["fields"]
        invalid = dict(fields, rain_mm="20 mm\navg_temp_c: 2")
        self.assertEqual(self.client.post(path + "/confirm", json={"fields": invalid, "review_id": None}).status_code, 400)
        self.assertEqual(self.client.post(path + "/confirm", json={"fields": {"new": "2"}, "review_id": None}).status_code, 400)
        fields["rain_mm"] = "20 mm"
        self.assertEqual(self.client.post(path + "/confirm", json={"fields": fields, "review_id": None}).status_code, 200)
        latest = self.latest_cement()
        self.assertEqual(latest["status"], "missing_information")
        self.assertIn("rain_mm", [item["name"] for item in latest["missing"]])

    def test_handwritten_site_notes_are_reviewed_reported_and_indexed(self):
        reporting_date = "2026-03-14"
        document_id = self.upload(
            REPORT + "\nCrew poured footing concrete by hand.",
            reporting_date=reporting_date,
            filename="site-notes.png",
        )
        report_path = f"/api/reports/documents/{document_id}"
        intake = self.client.get(
            f"/api/reports/projects/{self.project_id}/builder/intake?date={reporting_date}"
        )
        self.assertEqual(intake.status_code, 200)
        document = intake.json["documents"][0]
        self.assertIn("Crew poured footing concrete by hand.", document["fields"]["Site notes"])
        self.assertEqual(
            self.client.post(
                f"/api/reports/projects/{self.project_id}/builder/generate",
                json={"reporting_date": reporting_date},
            ).status_code,
            409,
        )
        review = self.client.get(report_path).json
        confirmed = self.client.post(
            report_path + "/confirm",
            json={"fields": review["fields"], "review_id": review["review_id"]},
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.json)

        generated = self.client.post(
            f"/api/reports/projects/{self.project_id}/builder/generate",
            json={"reporting_date": reporting_date},
        )
        self.assertEqual(generated.status_code, 201, generated.json)
        self.assertEqual(generated.json["index_status"], "completed")
        overview_data = self.client.get(f"/api/projects/{self.project_id}")
        self.assertEqual(overview_data.status_code, 200, overview_data.json)
        self.assertTrue(
            any(
                item["report"] == "Daily Construction Report - 2026-03-14.pdf"
                for item in overview_data.json["history"]
                if item["type"] == "predictions_generated"
            )
        )
        daily = self.client.get(
            f"/api/reports/projects/{self.project_id}/builder/reports/{generated.json['report']['id']}"
        )
        self.assertEqual(daily.status_code, 200)
        detail_page = self.client.get(generated.json["url"])
        self.assertEqual(detail_page.status_code, 200)
        self.assertIn(b"Crew poured footing concrete by hand.", detail_page.data)
        self.assertEqual(
            daily.json["fields"]["sections"][0]["items"][0]["source_name"],
            "site-notes.png",
        )
        with self.app.app_context():
            saved = db.session.scalar(db.select(DailyConstructionReport))
            generated_document_id = saved.document_id
            self.assertIn("Crew poured footing concrete by hand.", saved.report_text)
            chunks = db.session.scalars(
                db.select(DocumentChunk).where(DocumentChunk.document_id == saved.document_id)
            ).all()
            predictions = db.session.scalars(
                db.select(DocumentPrediction).where(DocumentPrediction.document_id == generated_document_id)
            ).all()
            self.assertEqual(len(predictions), 3)
            self.assertTrue(any("Crew poured footing concrete by hand." in chunk.text for chunk in chunks))
            search = search_project_documents(self.project_id, "footing concrete")
            self.assertTrue(search.chunks)
            report_chunks = [
                chunk for chunk in search.chunks
                if "Daily Construction Report" in chunk.document_name
            ]
            self.assertTrue(report_chunks)
            self.assertEqual(report_chunks[0].page_number, 1)
            prediction_count = db.session.query(DocumentPrediction).filter_by(
                document_id=generated_document_id
            ).count()

        downloaded = self.client.get(generated.json["download_url"])
        self.assertEqual(downloaded.status_code, 200)
        self.assertIn(b"Crew poured footing concrete by hand.", downloaded.data)
        downloaded.close()
        original = self.client.get(intake.json["documents"][0]["view_url"])
        self.assertEqual(original.status_code, 200)
        original.close()
        repeated = self.client.post(
            f"/api/reports/projects/{self.project_id}/builder/generate",
            json={"reporting_date": reporting_date},
        )
        self.assertEqual(repeated.status_code, 201, repeated.json)
        self.assertEqual(repeated.json["report"]["id"], generated.json["report"]["id"])
        with self.app.app_context():
            self.assertEqual(db.session.query(DailyConstructionReport).count(), 1)
            self.assertEqual(
                db.session.query(DocumentPrediction).filter_by(
                    document_id=generated_document_id
                ).count(),
                prediction_count,
            )

    def test_prediction_error_does_not_turn_successful_report_into_server_error(self):
        reporting_date = "2026-03-15"
        document_id = self.upload(REPORT, reporting_date=reporting_date)
        path = f"/api/reports/documents/{document_id}"
        fields = self.client.get(path).json["fields"]
        confirmed = self.client.post(
            path + "/confirm",
            json={"fields": fields, "review_id": None},
        )
        self.assertEqual(confirmed.status_code, 200)

        with patch(
            "siteintel.reports.routes.generate_for_review",
            side_effect=RuntimeError("test-only prediction failure"),
        ):
            response = self.client.post(
                f"/api/reports/projects/{self.project_id}/builder/generate",
                json={"reporting_date": reporting_date},
            )
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(response.json["index_status"], "completed")
        self.assertEqual(response.json["prediction_status"], "failed")
        with self.app.app_context():
            saved = db.session.scalar(
                db.select(DailyConstructionReport).where(
                    DailyConstructionReport.reporting_date == date.fromisoformat(reporting_date)
                )
            )
            self.assertIsNotNone(saved)

    def test_daily_report_search_citations_reprocessing_and_isolation(self):
        reporting_date = "2026-11-19"
        document_id = self.upload(
            "Work completed: Foundation excavation\n"
            "Cement received: 40 bags\n"
            "Workers present: 12\n"
            "Delay: Rain slowed outdoor work\n"
            "Materials used: steel rods",
            reporting_date=reporting_date,
        )
        path = f"/api/reports/documents/{document_id}"
        review = self.client.get(path).json
        self.assertEqual(self.client.post(
            path + "/confirm", json={"fields": review["fields"], "review_id": None}
        ).status_code, 200)
        generate_url = f"/api/reports/projects/{self.project_id}/builder/generate"
        with patch(
            "siteintel.documents.processing.extract_pdf_pages",
            side_effect=ExtractionError("Test indexing failure."),
        ):
            generated = self.client.post(generate_url, json={"reporting_date": reporting_date})
        self.assertEqual(generated.status_code, 201)
        self.assertEqual(generated.json["index_status"], "failed")
        with self.app.app_context():
            saved = db.session.scalar(db.select(DailyConstructionReport))
            generated_id = saved.document_id

        base = f"/api/projects/{self.project_id}/documents"
        retry = self.client.post(f"{base}/{generated_id}/process?force=1")
        self.assertEqual(retry.json["status"], "completed")
        with self.app.app_context():
            count = db.session.query(DocumentChunk).filter_by(document_id=generated_id).count()
            questions = [
                "What work was completed on 19 November 2026?",
                "How much cement was received?",
                "How many workers were present?",
                "Were there any construction delays?",
                "What materials were used that day?",
            ]
            for question in questions:
                result = search_project_documents(self.project_id, question, document_ids=[generated_id])
                self.assertTrue(result.sufficient, question)
                self.assertEqual(result.chunks[0].reporting_date, reporting_date)
                self.assertEqual(result.chunks[0].page_number, 1)

            for question in (
                "What happened in the daily construction report?",
                "What happened on 2026-11-19?",
                "Summarize the report for 19 November 2026",
            ):
                result = search_project_documents(self.project_id, question)
                self.assertTrue(result.sufficient, question)
                self.assertEqual(result.chunks[0].document_id, generated_id)
                self.assertEqual(result.chunks[0].reporting_date, reporting_date)
            self.assertFalse(search_project_documents(
                self.project_id, "What happened on 2026-11-18?"
            ).sufficient)
            self.assertFalse(search_project_documents(
                self.project_id, "What is the cement price on 2026-11-19?"
            ).sufficient)

            other = Project(owner_id=self.ids["stranger"], name="Other")
            db.session.add(other)
            db.session.commit()
            result = search_project_documents(other.id, "cement received", document_ids=[generated_id])
            self.assertEqual(result.chunks, [])

        with patch("siteintel.documents.qa.settings.llm_settings", return_value=object()), \
             patch("siteintel.documents.qa.llm.generate", return_value="40 bags of cement were received. [1]") as llm:
            answer = self.client.post(f"{base}/ask", json={"question": "How much cement was received?"})
        self.assertEqual(answer.json["status"], "answered")
        citation = answer.json["citations"][0]
        self.assertEqual(citation["document_id"], generated_id)
        self.assertEqual(citation["reporting_date"], reporting_date)
        self.assertTrue(citation["view_url"].endswith("/file#page=1"))
        self.assertIn("40 bags", llm.call_args.args[2])
        self.assertIn("Reporting date: 2026-11-19", llm.call_args.args[2])
        with patch("siteintel.documents.qa.settings.llm_settings", return_value=None):
            answer = self.client.post(f"{base}/ask", json={"question": "What happened on 2026-11-19?"})
            self.assertEqual(answer.json["status"], "answered")
            self.assertEqual(answer.json["citations"][0]["document_id"], generated_id)
            self.assertEqual(answer.json["citations"][0]["reporting_date"], reporting_date)
            missing = self.client.post(f"{base}/ask", json={"question": "What happened on 2026-11-18?"})
            self.assertEqual(missing.json["status"], "no_answer")

        with patch("siteintel.documents.qa.settings.llm_settings", return_value=object()), \
             patch("siteintel.documents.qa.llm.generate", return_value=(
                 "I could not find sufficient information in the uploaded project documents to answer this question."
             )):
            answer = self.client.post(f"{base}/ask", json={"question": "What is the cement price?"})
        self.assertEqual(answer.json["status"], "no_answer")

        self.assertEqual(self.client.post(f"{base}/{generated_id}/process?force=1").json["status"], "completed")
        self.assertEqual(self.client.post(generate_url, json={"reporting_date": reporting_date}).json["index_status"], "completed")
        with self.app.app_context():
            self.assertEqual(db.session.query(DailyConstructionReport).count(), 1)
            self.assertEqual(db.session.query(DocumentChunk).filter_by(document_id=generated_id).count(), count)

        second_id = self.upload("Cement received: 25 bags", reporting_date="2026-11-20", filename="next.png")
        second_path = f"/api/reports/documents/{second_id}"
        second = self.client.get(second_path).json
        self.client.post(second_path + "/confirm", json={"fields": second["fields"], "review_id": None})
        self.client.post(generate_url, json={"reporting_date": "2026-11-20"})
        with self.app.app_context():
            second_report = db.session.scalar(
                db.select(DailyConstructionReport).where(
                    DailyConstructionReport.reporting_date == date(2026, 11, 20)
                )
            )
            results = search_project_documents(self.project_id, "cement received")
            self.assertEqual(
                {chunk.document_id for chunk in results.chunks},
                {generated_id, second_report.document_id},
            )
            dated = search_project_documents(self.project_id, "cement received on 19 November 2026")
            self.assertEqual(dated.chunks[0].document_id, generated_id)
            reference = Document(
                project_id=self.project_id, uploaded_by_id=self.ids["owner"],
                file_name="agreement.pdf", storage_key="reference.pdf",
                content_type="application/pdf", byte_size=1,
                category="agreement", processing_status="completed",
            )
            db.session.add(reference)
            db.session.flush()
            text = "Construction delay and construction delays are described in this agreement."
            vector, length = embed(text)
            db.session.add_all([
                DocumentChunk(
                    document_id=reference.id, project_id=self.project_id,
                    chunk_index=index, page_number=index + 1, text=text,
                    token_count=length, embedding=vector, embedding_model=EMBEDDING_MODEL,
                )
                for index in range(6)
            ])
            db.session.commit()
            mixed = search_project_documents(self.project_id, "Were there any construction delays?")
            self.assertIn(reference.id, {chunk.document_id for chunk in mixed.chunks})
            all_results = search_project_documents(self.project_id, "Were there any construction delays?", top_k=100)
            self.assertIn(
                generated_id, {chunk.document_id for chunk in mixed.chunks},
                [(chunk.document_id, chunk.score, chunk.coverage) for chunk in all_results.chunks],
            )

        updated_fields = dict(review["fields"], **{"Cement received": "41 bags"})
        latest = self.client.get(path).json
        self.assertEqual(self.client.post(
            path + "/confirm",
            json={"fields": updated_fields, "review_id": latest["review_id"]},
        ).status_code, 200)
        regenerated = self.client.post(generate_url, json={"reporting_date": reporting_date})
        self.assertEqual(regenerated.json["index_status"], "completed")
        with self.app.app_context():
            chunks = db.session.scalars(
                db.select(DocumentChunk).where(DocumentChunk.document_id == generated_id)
            ).all()
            self.assertTrue(any("41 bags" in chunk.text for chunk in chunks))
            self.assertFalse(any("40 bags" in chunk.text for chunk in chunks))

    def test_daily_report_costs_are_reviewable_and_source_linked(self):
        document_id = self.upload(
            "Material cost: BDT 230.00\nWork completed: Foundation excavation",
            reporting_date="2026-11-19",
        )
        path = f"/api/reports/documents/{document_id}"
        review = self.client.get(path).json
        self.assertEqual(self.client.post(
            path + "/confirm", json={"fields": review["fields"], "review_id": None}
        ).status_code, 200)
        generated = self.client.post(
            f"/api/reports/projects/{self.project_id}/builder/generate",
            json={"reporting_date": "2026-11-19"},
        )
        self.assertEqual(generated.status_code, 201)
        from siteintel.projects.models import FinancialObservation
        with self.app.app_context():
            observation = db.session.scalar(db.select(FinancialObservation).where(
                FinancialObservation.project_id == self.project_id,
                FinancialObservation.kind == "direct_expense",
            ))
            self.assertIsNotNone(observation)
            self.assertEqual(observation.status, "pending")
            observation_id, report_document_id = observation.id, observation.document_id
        self.assertEqual(self.client.post(
            f"/projects/{self.project_id}/finance/observations/{observation_id}/review",
            data={"decision": "confirmed"},
        ).status_code, 302)
        expense = self.client.get(f"/projects/{self.project_id}/finance/analytics.json").json["charts"]["expense"]
        self.assertEqual(expense["series"][0]["values"], [230.0])
        page = self.client.get(f"/dashboard/projects/{self.project_id}/expenses/").get_data(as_text=True)
        self.assertEqual(self.client.get(f"/api/projects/{self.project_id}").status_code, 200)

    def test_overview_is_compact_and_hides_empty_location(self):
        page = self.client.get(f"/dashboard/projects/{self.project_id}/").get_data(as_text=True)
        for removed in ("No location set", "Project information", "No project summary"):
            self.assertNotIn(removed, page)
        for label in ("Reports", "Report Builder", "ML Predictions", "Expenses &amp; Bills",
                      "Document Assistant", "AI Agent", "Email &amp; Communication"):
            self.assertIn(f"<span>{label}</span>", page)
        self.assertIn('id="overview-predictions"', page)
        self.assertEqual(page.count("<h1>"), 1)
        with self.app.app_context():
            db.session.get(Project, self.project_id).location = "Dhaka"
            db.session.commit()
        self.assertIn("Dhaka", self.client.get(f"/dashboard/projects/{self.project_id}/").get_data(as_text=True))
        self.login("stranger")
        self.assertEqual(self.client.get(f"/dashboard/projects/{self.project_id}/").status_code, 404)

    def test_generated_report_index_keeps_repeated_page_content(self):
        document_id = self.upload("Cement received: 40 bags", reporting_date="2026-11-19")
        path = f"/api/reports/documents/{document_id}"
        fields = self.client.get(path).json["fields"]
        self.client.post(path + "/confirm", json={"fields": fields, "review_id": None})
        self.client.post(
            f"/api/reports/projects/{self.project_id}/builder/generate",
            json={"reporting_date": "2026-11-19"},
        )
        with self.app.app_context():
            saved = db.session.scalar(db.select(DailyConstructionReport))
            pages = Extraction([
                PageText(number, f"Cement received: 40 bags\nUnique page {number}")
                for number in range(1, 4)
            ], 3)
            with patch("siteintel.documents.processing.extract_pdf_pages", return_value=pages):
                self.assertEqual(process_document(saved.document_id, force=True), "completed")
            chunks = db.session.scalars(
                db.select(DocumentChunk).where(DocumentChunk.document_id == saved.document_id)
            ).all()
            self.assertEqual(len(chunks), 3)
            self.assertTrue(all("Cement received: 40 bags" in chunk.text for chunk in chunks))

    def test_project_members_are_scoped_for_reports_and_review(self):
        document_id = self.upload()
        path = f"/api/reports/documents/{document_id}"
        self.login("viewer")
        self.assertEqual(self.client.get(f"/api/projects/{self.project_id}").status_code, 200)
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["editable"])
        file_response = self.client.get(path + "/file")
        self.assertEqual(file_response.status_code, 200)
        file_response.close()
        self.assertEqual(self.client.post(path + "/confirm", json={"fields": response.json["fields"], "review_id": None}).status_code, 404)
        self.login("stranger")
        self.assertEqual(self.client.get(f"/api/projects/{self.project_id}").status_code, 404)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get(path + "/file").status_code, 404)
        self.login("editor")
        fields = self.client.get(path).json["fields"]
        self.assertEqual(self.client.post(path + "/confirm", json={"fields": fields, "review_id": None}).status_code, 200)

    def test_dashboard_and_project_details_reflect_accessible_data(self):
        document_id = self.upload()
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Total Projects", response.data)
        self.assertIn(b"Open Project", response.data)
        details = self.client.get(f"/api/projects/{self.project_id}").json
        self.assertEqual(details["documents"][0]["id"], document_id)
        self.assertEqual(details["records"], [])
        self.assertEqual(details["expenses"], [])
        workspace = f"/dashboard/projects/{self.project_id}/"
        self.assertEqual(self.client.get(workspace).status_code, 200)
        self.assertIn(b'id="report-upload-form"', self.client.get(workspace + "reports/").data)
        with self.app.app_context():
            before = db.session.query(DocumentPrediction).count()
        self.client.get(workspace)
        self.client.get(f"/api/projects/{self.project_id}/predictions")
        with self.app.app_context():
            self.assertEqual(db.session.query(DocumentPrediction).count(), before)
        self.login("stranger")
        self.assertEqual(self.client.get(workspace).status_code, 404)
        self.assertNotIn(b"report.png", self.client.get("/dashboard/").data)

    def test_confirm_requires_csrf(self):
        document_id = self.upload()
        fields = self.client.get(f"/api/reports/documents/{document_id}").json["fields"]
        self.app.config["WTF_CSRF_ENABLED"] = True
        response = self.client.post(f"/api/reports/documents/{document_id}/confirm", json={"fields": fields, "review_id": None})
        self.assertEqual(response.status_code, 400)

    def test_stale_review_does_not_overwrite_newer_confirmation(self):
        document_id = self.upload()
        path = f"/api/reports/documents/{document_id}"
        fields = self.client.get(path).json["fields"]
        first = dict(fields, rain_mm="20 mm")
        saved = self.client.post(path + "/confirm", json={"fields": first, "review_id": None})
        self.assertEqual(saved.status_code, 200)
        stale = self.client.post(path + "/confirm", json={"fields": fields, "review_id": None})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.get(path).json["fields"], first)


if __name__ == "__main__":
    unittest.main()
