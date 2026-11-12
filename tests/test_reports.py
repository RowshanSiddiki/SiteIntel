import io
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw, ImageFont

from siteintel import create_app
from siteintel.ml.model_registry import MODEL_FILES, MODEL_DIRECTORY, ModelRegistry
from siteintel.reports.extraction import extract_report_fields
from siteintel.reports.ocr import extract_ocr


class ConstructionReportTests(unittest.TestCase):
    report_text = "\n".join(
        (
            "Report Date: 2026-03-14",
            "Project Name: Riverside Bridge",
            "Site Location: Dhaka",
        )
    )

    def setUp(self):
        self.upload_directory = tempfile.TemporaryDirectory()
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key-that-is-long-enough-for-siteintel",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_ENGINE_OPTIONS": {},
                "SESSION_COOKIE_SECURE": False,
                "MAX_CONTENT_LENGTH": 10 * 1024 * 1024,
                "REPORT_UPLOAD_ROOT": self.upload_directory.name,
            }
        )
        self.client = self.app.test_client()
        with self.app.app_context():
            from siteintel.extensions import db

            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            from siteintel.extensions import db

            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        self.upload_directory.cleanup()

    def _csrf_token(self, path: str, client=None) -> str:
        response = (client or self.client).get(path)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        match = re.search(
            r'<meta name="csrf-token" content="([^"]+)"', html
        ) or re.search(
            r'name="csrf_token"[^>]*value="([^"]+)"',
            html,
        )
        self.assertIsNotNone(match)
        return match.group(1)

    def _login(self, email="reports@example.com"):
        token = self._csrf_token("/auth/register")
        response = self.client.post(
            "/auth/register",
            data={
                "csrf_token": token,
                "full_name": "Report Test User",
                "email": email,
                "password": "StrongPass123!",
                "password_confirmation": "StrongPass123!",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        token = self._csrf_token("/auth/login")
        response = self.client.post(
            "/auth/login",
            data={
                "csrf_token": token,
                "email": email,
                "password": "StrongPass123!",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        return self._csrf_token("/dashboard/")

    @staticmethod
    def _image_upload(name="report.png"):
        image = Image.new("RGB", (40, 40), "white")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        stream.seek(0)
        return stream, name

    def _extract(self, token: str, text: str | None = None):
        stream, filename = self._image_upload()
        with patch(
            "siteintel.reports.routes.extract_ocr",
            return_value=(self.report_text if text is None else text, []),
        ):
            return self.client.post(
                "/api/reports/extract",
                data={"image": (stream, filename)},
                headers={"X-CSRFToken": token},
                content_type="multipart/form-data",
            )

    def test_dashboard_keeps_report_upload_without_prediction_controls(self):
        self._login()
        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('id="report-upload-form"', page)
        self.assertIn('id="report-image"', page)
        self.assertIn('id="original-extracted-text"', page)
        self.assertNotIn("/predict/", page)
        self.assertNotIn("predict-", page)

    def test_api_requires_authentication(self):
        response = self.client.get("/api/reports/" + "a" * 32)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["error"]["code"], "authentication_required")

    def test_upload_extracts_and_stores_labeled_information(self):
        token = self._login()
        response = self._extract(token)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json["extracted_information"],
            {
                "report": {
                    "Report Date": "2026-03-14",
                    "Project Name": "Riverside Bridge",
                    "Site Location": "Dhaka",
                }
            },
        )
        self.assertEqual(response.json["extracted_text"], self.report_text)

        report_id = response.json["report_id"]
        stored = self.client.get(f"/api/reports/{report_id}")
        self.assertEqual(stored.status_code, 200)
        self.assertEqual(stored.json["extracted_text"], self.report_text)

        report_records = list(
            Path(self.upload_directory.name).glob(f"*/{report_id}.json")
        )
        self.assertEqual(len(report_records), 1)

    def test_uploaded_report_is_not_visible_to_another_user(self):
        token = self._login()
        response = self._extract(token)
        report_id = response.json["report_id"]

        other_client = self.app.test_client()
        token = self._csrf_token("/auth/register", other_client)
        other_client.post(
            "/auth/register",
            data={
                "csrf_token": token,
                "full_name": "Other Test User",
                "email": "other-reports@example.com",
                "password": "StrongPass123!",
                "password_confirmation": "StrongPass123!",
            },
        )
        login_page = other_client.get("/auth/login")
        login_token = re.search(
            r'name="csrf_token"[^>]*value="([^"]+)"',
            login_page.get_data(as_text=True),
        ).group(1)
        other_client.post(
            "/auth/login",
            data={
                "csrf_token": login_token,
                "email": "other-reports@example.com",
                "password": "StrongPass123!",
            },
        )
        response = other_client.get(f"/api/reports/{report_id}")
        self.assertEqual(response.status_code, 404)

    def test_invalid_image_is_rejected(self):
        token = self._login()
        response = self.client.post(
            "/api/reports/extract",
            data={"image": (io.BytesIO(b"not an image"), "report.png")},
            headers={"X-CSRFToken": token},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"]["code"], "invalid_image")

    def test_api_post_requires_csrf_token(self):
        self._login()
        stream, filename = self._image_upload()
        response = self.client.post(
            "/api/reports/extract",
            data={"image": (stream, filename)},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"]["code"], "csrf_failed")

    def test_backend_rejects_uploads_over_size_limit(self):
        token = self._login()
        self.app.config["MAX_CONTENT_LENGTH"] = 1024
        response = self.client.post(
            "/api/reports/extract",
            data={"image": (io.BytesIO(b"x" * 2048), "report.png")},
            headers={"X-CSRFToken": token},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json["error"]["code"], "image_too_large")

    def test_model_registry_targets_only_the_three_future_artifacts(self):
        registry = ModelRegistry()
        self.assertEqual(
            MODEL_FILES,
            {
                "cement_demand": "cement_demand.pkl",
                "budget_overrun": "budget_overrun.pkl",
                "construction_delay": "construction_delay.pkl",
            },
        )
        self.assertEqual(registry.directory, MODEL_DIRECTORY.resolve())
        self.assertTrue(registry.model_path("cement_demand").is_absolute())
        self.assertEqual(registry.model_path("budget_overrun").parent, MODEL_DIRECTORY)
        self.assertEqual(registry.model_path("construction_delay").parent, MODEL_DIRECTORY)

    def test_model_registry_loads_each_artifact_once(self):
        registry = ModelRegistry()
        model = object()
        loader = Mock(return_value=model)

        with patch.object(Path, "is_file", return_value=True):
            self.assertIs(registry.load("cement_demand", loader), model)
            self.assertIs(registry.load("cement_demand", loader), model)

        loader.assert_called_once_with(MODEL_DIRECTORY / "cement_demand.pkl")

    def test_no_prediction_api_routes_are_registered(self):
        self.assertIn("ml", self.app.blueprints)
        route_rules = (
            rule.rule for rule in self.app.url_map.iter_rules()
        )
        self.assertFalse(any("/predict/" in rule for rule in route_rules))

    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract is not installed")
    def test_local_tesseract_reads_and_extracts_printed_report_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "printed-report.png"
            image = Image.new("RGB", (1400, 180), "white")
            draw = ImageDraw.Draw(image)
            image_text = "Project Name: Riverside Bridge"
            draw.text(
                (30, 30),
                image_text,
                fill="black",
                font=ImageFont.load_default(size=42),
            )
            image.save(path)
            text, lines = extract_ocr(path)

        self.assertTrue(lines)
        self.assertIn("Riverside", text)
        extracted = extract_report_fields(text)
        self.assertTrue(
            any("Project Name" in label and "Riverside" in value
                for label, value in extracted.items())
        )

    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract is not installed")
    def test_authenticated_upload_runs_ocr_and_stores_extracted_fields(self):
        token = self._login()
        image = Image.new("RGB", (1400, 180), "white")
        ImageDraw.Draw(image).text(
            (30, 30),
            "Project Name: Riverside Bridge",
            fill="black",
            font=ImageFont.load_default(size=42),
        )
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        stream.seek(0)

        response = self.client.post(
            "/api/reports/extract",
            data={"image": (stream, "report.png")},
            headers={"X-CSRFToken": token},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertIn("Riverside", response.json["extracted_text"])
        self.assertTrue(
            any(
                "Project Name" in label and "Riverside" in value
                for label, value in response.json["extracted_information"][
                    "report"
                ].items()
            )
        )


if __name__ == "__main__":
    unittest.main()
