import io
import shutil
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from siteintel import create_app
from siteintel.auth.models import User
from siteintel.extensions import db
from siteintel.ml.document_predictions import generate_for_document, ocr_inputs
from siteintel.ml.models import DocumentPrediction
from siteintel.projects import Document, Project, ProjectMember
from siteintel.reports.ocr import PDFProcessingError


REPORT = """planned_pour_tonnes: 40
site_rolling_mean_30: 35 tonnes
rain_mm: 12 mm
avg_temp_c: 27 C
cement: 350 kg/m3
slag: 0 kg/m3
water: 180 kg/m3
age: 28 days
Material Availability: Medium
Equipment Availability: High
Weather Risk: Medium
Supplier Delay: Yes"""

# Verbatim OCR lines from the real SiteIntel_ML_Prediction_Test_Report.pdf:
# separator-less table rows plus lines that must not be read as measurements.
TABLE_REPORT = """Cement 120 50 35 135 bags
Cement purchase and delivery 32,500 50 bags delivered
Ordinary Portland Cement was used; no ground-granulated blast-furnace slag was added.
Weather risk Medium Cloudy conditions with intermittent light rain forecast.
Supplier delay Yes Cement supplier informed the site that the next delivery may arrive one day late.
Material availability Low Available cement stock is below the planned two-day requirement.
Equipment availability High Concrete mixer, vibrator, and lifting equipment are inspected and available.
Concrete age 28 days (test age)
Slag 0 kg/m?
Water 180 kg/m?
Cement 350 kg/m?
Rainfall 12 mm (forecast for planned pour day)
Average temperature 27 °C (forecast daily average)
Planned pour 40 tonnes (planned concrete pour quantity as specified by project team)
30-day site demand average 35 tonnes (average daily cement demand over the previous 30 days)"""


class DocumentPredictionTests(unittest.TestCase):
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
            users = [
                User(full_name=name, email=f"{name}@example.com", password_hash="unused")
                for name in ("owner", "editor", "viewer", "stranger")
            ]
            db.session.add_all(users)
            db.session.flush()
            self.ids = {user.full_name: user.id for user in users}
            project = Project(owner_id=self.ids["owner"], name="Test project")
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

    def upload(self, text=REPORT, project=True):
        stream = io.BytesIO()
        Image.new("RGB", (40, 40), "white").save(stream, "PNG")
        stream.seek(0)
        data = {"image": (stream, "report.png")}
        if project:
            data["project_id"] = str(self.project_id)
        with patch("siteintel.reports.routes.extract_ocr", return_value=(text, [])):
            return self.client.post("/api/reports/extract", data=data)

    def predictions(self):
        return self.client.get(f"/api/projects/{self.project_id}/predictions")

    def confirm(self, response):
        path = f"/api/reports/documents/{response.json['document_id']}"
        report = self.client.get(path).json
        saved = self.client.post(path + "/confirm", json={"fields": report["fields"], "review_id": report["review_id"]})
        self.assertEqual(saved.status_code, 200, saved.json)
        return saved

    def test_project_confirmation_runs_all_real_models_with_ocr_inputs(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(response.json["project_id"], self.project_id)
        self.assertEqual(self.predictions().json["predictions"], [])
        self.confirm(response)
        predictions = self.predictions().json["predictions"]
        self.assertEqual(len(predictions), 3)
        models = {record["model_key"]: record for record in predictions}
        self.assertTrue(all(record["status"] == "complete" for record in predictions))
        self.assertAlmostEqual(models["cement_demand"]["result"]["predicted_consumption_tonnes"], 31.4730834108)
        self.assertAlmostEqual(models["concrete_strength"]["result"]["compressive_strength_mpa"], 39.8513159924)
        self.assertAlmostEqual(models["delay_risk"]["result"]["delay_probability"], 0.5188931681)
        self.assertTrue(all(
            item["source"] == "ocr"
            for record in predictions for item in record["inputs"].values()
        ))

    def test_separatorless_table_rows_map_to_all_twelve_features(self):
        from siteintel.reports.extraction import extract_report_fields

        with self.app.app_context():
            expected = {
                "cement_demand": {"planned_pour_tonnes": 40, "site_rolling_mean_30": 35, "rain_mm": 12, "avg_temp_c": 27},
                "concrete_strength": {"cement": 350, "slag": 0, "water": 180, "age": 28},
                "delay_risk": {"Material_Availability": "Low", "Equipment_Availability": "High", "Weather_Risk": "Medium", "Supplier_Delay": "Yes"},
            }
            for model_key, values in expected.items():
                inputs, missing = ocr_inputs(model_key, TABLE_REPORT)
                self.assertEqual(missing, {})
                self.assertEqual({k: v["value"] for k, v in inputs.items()}, values)
                self.assertTrue(all(v["source"] == "ocr" and v["evidence"] for v in inputs.values()))
            fields = extract_report_fields(TABLE_REPORT)
            self.assertEqual(fields["Rainfall (mm)"], "12")
            self.assertEqual(fields["Cement (kg/m\u00b3)"], "350")
            self.assertNotIn("Cement", fields)

    def test_units_are_normalized_and_unitless_numbers_are_rejected(self):
        text = "Rainfall 1.5 cm\nAverage temperature 86 \u00b0F\nPlanned pour 2,500 kg\nConcrete age 2 weeks\nCement 350"
        with self.app.app_context():
            cement, missing = ocr_inputs("cement_demand", text)
            self.assertAlmostEqual(cement["rain_mm"]["value"], 15)
            self.assertAlmostEqual(cement["avg_temp_c"]["value"], 30)
            self.assertAlmostEqual(cement["planned_pour_tonnes"]["value"], 2.5)
            self.assertEqual(set(missing), {"site_rolling_mean_30"})
            concrete, missing = ocr_inputs("concrete_strength", text)
            self.assertEqual(concrete["age"]["value"], 14)
            self.assertIn("cement", missing)

    def test_table_report_upload_confirm_runs_models_and_refresh_is_stable(self):
        response = self.upload(TABLE_REPORT)
        fields = self.client.get(f"/api/reports/documents/{response.json['document_id']}").json["fields"]
        self.assertEqual(fields["Planned pour (tonnes)"], "40")
        self.assertEqual(self.predictions().json["predictions"], [])
        self.confirm(response)
        first = self.predictions().json["predictions"]
        self.assertEqual({r["status"] for r in first}, {"complete"})
        self.assertEqual(len(first), 3)
        self.assertAlmostEqual(
            {r["model_key"]: r for r in first}["cement_demand"]["result"]["predicted_consumption_tonnes"], 31.4730834108
        )
        self.client.get(f"/api/reports/documents/{response.json['document_id']}")
        self.assertEqual([r["id"] for r in self.predictions().json["predictions"]], [r["id"] for r in first])
        with self.app.app_context():
            self.assertEqual(db.session.query(DocumentPrediction).count(), 3)

    def test_workspace_shows_one_current_prediction_per_model(self):
        # An older upload of the same report that lacks the inputs must not
        # produce a missing-information form beside the completed predictions.
        self.confirm(self.upload("Site note: nothing useful"))
        self.confirm(self.upload(TABLE_REPORT))
        data = self.predictions().json
        self.assertEqual(len(data["predictions"]), 6)
        self.assertEqual(len(data["current"]), 3)
        self.assertEqual([r["model_key"] for r in data["current"]], ["delay_risk", "concrete_strength", "cement_demand"])
        self.assertTrue(all(r["status"] == "complete" and not r["missing"] for r in data["current"]))

    def test_genuinely_incomplete_model_keeps_its_missing_form(self):
        self.confirm(self.upload(TABLE_REPORT.replace("Rainfall 12 mm (forecast for planned pour day)", "")))
        current = {r["model_key"]: r for r in self.predictions().json["current"]}
        self.assertEqual(current["cement_demand"]["status"], "missing_information")
        self.assertEqual([m["name"] for m in current["cement_demand"]["missing"]], ["rain_mm"])
        self.assertEqual(current["delay_risk"]["status"], "complete")

    def test_project_history_lists_only_user_facing_events_in_order(self):
        self.confirm(self.upload("Site note: nothing useful"))
        self.confirm(self.upload(TABLE_REPORT))
        self.predictions()
        self.predictions()
        history = self.client.get(f"/api/projects/{self.project_id}").json["history"]
        self.assertEqual([e["type"] for e in history][0], "project_created")
        self.assertEqual({e["type"] for e in history}, {"project_created", "report_uploaded", "report_confirmed", "predictions_generated"})
        generated = [e for e in history if e["type"] == "predictions_generated"]
        self.assertEqual([e["description"] for e in generated], ["3 ML predictions generated"])
        self.assertEqual([e["time"] for e in history], sorted(e["time"] for e in history))
        with self.app.app_context():
            self.assertEqual(db.session.query(DocumentPrediction).count(), 6)

    def test_missing_fields_and_invalid_values_need_manual_completion(self):
        text = REPORT.replace("rain_mm: 12 mm", "rain_mm: -12 mm").replace("slag: 0 kg/m3", "slag: 0")
        self.confirm(self.upload(text))
        predictions = {item["model_key"]: item for item in self.predictions().json["predictions"]}
        cement = predictions["cement_demand"]
        concrete = predictions["concrete_strength"]
        self.assertEqual([field["name"] for field in cement["missing"]], ["rain_mm"])
        self.assertEqual([field["name"] for field in concrete["missing"]], ["slag"])
        self.assertEqual(cement["missing"][0]["unit"], "mm")
        endpoint = f"/api/predictions/{cement['id']}/complete"
        for value in (None, -1, "12", True, float("inf")):
            self.assertEqual(self.client.post(endpoint, json={"rain_mm": value}).status_code, 400)
        self.assertEqual(self.client.post(endpoint, json={"avg_temp_c": 8}).status_code, 400)
        response = self.client.post(endpoint, json={"rain_mm": 12})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["prediction"]["status"], "complete")
        self.assertEqual(response.json["prediction"]["inputs"]["rain_mm"]["source"], "manual")
        self.assertEqual(response.json["prediction"]["inputs"]["avg_temp_c"]["source"], "ocr")
        self.assertEqual(self.client.post(endpoint, json={"rain_mm": 12}).status_code, 409)

    def test_human_readable_labels_and_units_map_to_all_models(self):
        report = """Planned pour (tonnes): 40
30-day site demand average (tonnes): 35
Precipitation (mm): 12
Average temperature (°C): 27
Cement content (kg/m³): 350
Slag content (kg/m³): 0
Water content (kg/m³): 180
Curing age (days): 28
Material availability: Medium
Equipment availability: High
Weather risk: Medium
Supplier delay: Yes"""
        self.confirm(self.upload(report))
        models = self.predictions().json["predictions"]
        self.assertEqual(len(models), 3)
        self.assertTrue(all(item["status"] == "complete" for item in models))

    def test_upload_retry_is_idempotent_and_rejects_different_file(self):
        key = "a" * 32
        def post(color):
            stream = io.BytesIO()
            Image.new("RGB", (40, 40), color).save(stream, "PNG")
            stream.seek(0)
            return self.client.post("/api/reports/extract", data={
                "project_id": str(self.project_id), "image": (stream, "report.png")
            }, headers={"Idempotency-Key": key})
        with patch("siteintel.reports.routes.extract_ocr", return_value=(REPORT, [])) as ocr:
            first = post("white")
            retry = post("white")
            conflict = post("black")
        self.assertEqual((first.status_code, retry.status_code, conflict.status_code), (201, 200, 409))
        self.assertEqual(first.json["document_id"], retry.json["document_id"])
        self.assertEqual(ocr.call_count, 1)
        with self.app.app_context():
            self.assertEqual(db.session.query(Document).count(), 1)
            self.assertEqual(db.session.query(DocumentPrediction).count(), 0)

    def test_existing_pdf_is_not_deleted_on_upload_identifier_collision(self):
        from pathlib import Path
        from siteintel.reports.storage import save_report_pdf
        from werkzeug.datastructures import FileStorage

        with self.app.app_context():
            path = Path(self.directory.name) / str(self.ids["owner"]) / ("b" * 32 + ".pdf")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"existing")
            upload = FileStorage(stream=io.BytesIO(b"%PDF-1.4"), filename="report.pdf")
            with self.assertRaises(FileExistsError):
                save_report_pdf(upload, str(self.ids["owner"]), "b" * 32)
            self.assertEqual(path.read_bytes(), b"existing")

    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract is not installed")
    def test_real_image_upload_extracts_and_confirms(self):
        image = Image.new("RGB", (1400, 300), "white")
        ImageDraw.Draw(image).text(
            (40, 60), "Rainfall: 12 mm", fill="black", font=ImageFont.load_default(size=48)
        )
        stream = io.BytesIO()
        image.save(stream, "PNG")
        stream.seek(0)
        response = self.client.post("/api/reports/extract", data={
            "project_id": str(self.project_id), "image": (stream, "report.png")
        })
        self.assertEqual(response.status_code, 201, response.json)
        self.confirm(response)
        cement = next(p for p in self.predictions().json["predictions"] if p["model_key"] == "cement_demand")
        self.assertEqual(cement["inputs"]["rain_mm"]["value"], 12)

    def test_conflicting_labels_and_units_are_not_silently_used(self):
        inputs, missing = ocr_inputs("cement_demand", REPORT + "\nrain mm: 4 mm")
        self.assertNotIn("rain_mm", inputs)
        self.assertIn("conflicting", missing["rain_mm"])
        inputs, missing = ocr_inputs("concrete_strength", REPORT.replace("350 kg/m3", "350 tonnes"))
        self.assertNotIn("cement", inputs)
        self.assertIn("unit", missing["cement"])

    def test_permissions_for_viewing_and_mutating(self):
        self.confirm(self.upload("rain_mm: 12 mm"))
        prediction_id = self.predictions().json["predictions"][0]["id"]
        self.login("viewer")
        self.assertEqual(self.predictions().status_code, 200)
        self.assertFalse(self.predictions().json["editable"])
        self.assertEqual(self.upload().status_code, 404)
        self.assertEqual(self.client.post(f"/api/predictions/{prediction_id}/complete", json={}).status_code, 404)
        self.login("stranger")
        self.assertEqual(self.predictions().status_code, 404)
        self.assertEqual(self.upload().status_code, 404)
        self.assertEqual(self.client.post(f"/api/predictions/{prediction_id}/complete", json={}).status_code, 404)
        self.login("editor")
        self.assertEqual(self.upload().status_code, 201)

    def test_no_project_preserves_legacy_upload(self):
        response = self.upload(project=False)
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("project_id", response.json)
        self.assertEqual(self.predictions().json["predictions"], [])

    def test_identical_snapshots_do_not_duplicate(self):
        self.upload()
        with self.app.app_context():
            document = db.session.scalar(db.select(Document))
            generate_for_document(document, self.ids["owner"], REPORT)
            db.session.commit()
            self.assertEqual(len(db.session.scalars(db.select(DocumentPrediction)).all()), 3)

    def test_explicit_rerun_reuses_unchanged_result(self):
        self.confirm(self.upload())
        prediction = self.predictions().json["predictions"][0]
        response = self.client.post(f"/api/predictions/{prediction['id']}/rerun")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["prediction"]["id"], prediction["id"])
        self.assertEqual(response.json["prediction"]["result"], prediction["result"])
        self.assertEqual(len(self.predictions().json["predictions"]), 3)

    def test_model_failure_is_not_exposed_and_other_models_work(self):
        from siteintel.ml.model_registry import ModelArtifactError
        registry = self.app.extensions["siteintel_model_registry"]
        original = registry.get
        def get(model_key):
            if model_key == "concrete_strength":
                raise ModelArtifactError("secret internal file")
            return original(model_key)
        with patch.object(registry, "get", side_effect=get):
            self.confirm(self.upload())
        payload = self.predictions().json
        statuses = {item["model_key"]: item["status"] for item in payload["predictions"]}
        self.assertEqual(statuses["concrete_strength"], "unavailable")
        self.assertEqual(statuses["cement_demand"], "complete")
        self.assertNotIn("secret internal file", str(payload))

    def test_pdf_reuses_existing_extraction_and_errors_explicitly(self):
        def post_pdf():
            return self.client.post("/api/reports/extract", data={
                "project_id": str(self.project_id),
                "image": (io.BytesIO(b"%PDF-1.4 test"), "report.pdf"),
            })
        with patch("siteintel.reports.routes.extract_pdf_ocr", return_value=REPORT):
            self.confirm(post_pdf())
            self.assertEqual(len(self.predictions().json["predictions"]), 3)
        with patch("siteintel.reports.routes.extract_pdf_ocr", side_effect=PDFProcessingError()):
            self.assertEqual(post_pdf().status_code, 422)

    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract is not installed")
    def test_real_pdf_upload_renders_and_runs_ocr(self):
        image = Image.new("RGB", (1400, 300), "white")
        ImageDraw.Draw(image).text(
            (40, 60), "rain_mm: 12 mm", fill="black", font=ImageFont.load_default(size=48)
        )
        stream = io.BytesIO()
        image.save(stream, "PDF", resolution=150)
        stream.seek(0)
        response = self.client.post("/api/reports/extract", data={
            "project_id": str(self.project_id), "image": (stream, "report.pdf")
        })
        self.assertEqual(response.status_code, 201, response.json)
        self.assertIn("rain_mm", response.json["extracted_text"])
        self.confirm(response)
        cement = next(p for p in self.predictions().json["predictions"] if p["model_key"] == "cement_demand")
        self.assertEqual(cement["inputs"]["rain_mm"]["value"], 12)
        self.assertEqual(cement["inputs"]["rain_mm"]["source"], "ocr")

    def test_invalid_and_oversized_pdf_are_rejected(self):
        invalid = self.client.post("/api/reports/extract", data={
            "image": (io.BytesIO(b"%PDF-1.4 invalid"), "report.pdf")
        })
        self.assertEqual(invalid.status_code, 422)
        image = Image.new("RGB", (10, 10), "white")
        stream = io.BytesIO()
        image.save(stream, "PDF", save_all=True, append_images=[image] * 20)
        stream.seek(0)
        oversized = self.client.post("/api/reports/extract", data={"image": (stream, "report.pdf")})
        self.assertEqual(oversized.status_code, 422)

    def test_create_and_list_projects(self):
        self.assertEqual(self.client.post("/api/projects", json={"name": " "}).status_code, 400)
        response = self.client.post("/api/projects", json={"name": "New site"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["workspace_url"], f"/dashboard/projects/{response.json['id']}/")
        self.assertEqual(self.client.get(response.json["workspace_url"]).status_code, 200)
        self.assertIn(response.json["id"], [p["id"] for p in self.client.get("/api/projects").json["projects"]])

    def test_project_prediction_mutations_require_csrf(self):
        self.confirm(self.upload())
        prediction_id = self.predictions().json["predictions"][0]["id"]
        self.app.config["WTF_CSRF_ENABLED"] = True
        self.assertEqual(self.client.post("/api/projects", json={"name": "Blocked"}).status_code, 400)
        self.assertEqual(self.client.post(
            f"/api/predictions/{prediction_id}/rerun", json={}
        ).status_code, 400)

    def test_out_of_range_project_identifier_is_rejected(self):
        stream = io.BytesIO()
        Image.new("RGB", (10, 10), "white").save(stream, "PNG")
        stream.seek(0)
        response = self.client.post("/api/reports/extract", data={
            "project_id": "9" * 10000, "image": (stream, "report.png")
        })
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
