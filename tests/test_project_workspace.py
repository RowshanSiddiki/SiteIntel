import io
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from siteintel import create_app
from siteintel.auth.models import User
from siteintel.extensions import db
from siteintel.ml.models import DocumentPrediction
from siteintel.projects import Project, ProjectMember
from siteintel.reports.models import ReportReview


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

    def upload(self, text=REPORT):
        stream = io.BytesIO()
        Image.new("RGB", (40, 40), "white").save(stream, "PNG")
        stream.seek(0)
        with patch("siteintel.reports.routes.extract_ocr", return_value=(text, [])):
            response = self.client.post("/api/reports/extract", data={
                "project_id": str(self.project_id), "image": (stream, "report.png")
            })
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
        self.assertIn(b"Workspace", response.data)
        details = self.client.get(f"/api/projects/{self.project_id}").json
        self.assertEqual(details["documents"][0]["id"], document_id)
        self.assertEqual(details["records"], [])
        self.assertEqual(details["expenses"], [])
        workspace = f"/dashboard/projects/{self.project_id}/"
        self.assertEqual(self.client.get(workspace).status_code, 200)
        self.assertIn(b'id="report-upload-form"', self.client.get(workspace).data)
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
