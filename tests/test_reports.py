import io
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from siteintel import create_app
from siteintel.reports.extraction import calculate_readiness, extract_report_fields
from siteintel.reports.ocr import extract_ocr


class ConstructionReportTests(unittest.TestCase):
    report_text = "\n".join(
        (
            "Date: 2026-03-14",
            "Attendance Status: Present",
            "Minutes Late: 0",
            "Units Sold: 35",
            "Inventory Level: 80",
            "Supplier Lead Time Days: 14",
            "Reorder Point: 100",
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

    def test_api_requires_authentication(self):
        response = self.client.get("/api/reports/" + "a" * 32)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["error"]["code"], "authentication_required")

    def test_extraction_maps_features_and_checks_models_independently(self):
        token = self._login()
        response = self._extract(token)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        data = response.json

        self.assertEqual(data["extracted_text"], self.report_text)
        self.assertEqual(data["prepared_inputs"]["workforce"]["work_date"], "3/14/2026")
        self.assertEqual(data["prepared_inputs"]["workforce"]["attendance_status"], "Present")
        self.assertEqual(data["prepared_inputs"]["workforce"]["minutes_late"], 0)
        self.assertEqual(data["prepared_inputs"]["supply_chain"]["date_year"], 2026)
        self.assertEqual(data["prepared_inputs"]["supply_chain"]["date_month"], 3)
        self.assertEqual(data["prepared_inputs"]["supply_chain"]["date_day"], 14)
        self.assertEqual(data["prepared_inputs"]["supply_chain"]["date_dayofweek"], 5)
        self.assertTrue(data["readiness"]["workforce"]["data_ready"])
        self.assertTrue(data["readiness"]["supply_chain"]["data_ready"])
        self.assertIn("attendance_record_id", self.app.extensions["siteintel_model_registry"].get("workforce").issue)
        self.assertIn("classes 0 and 1", self.app.extensions["siteintel_model_registry"].get("supply_chain").issue)
        self.assertFalse(data["readiness"]["workforce"]["prediction_enabled"])
        self.assertFalse(data["readiness"]["supply_chain"]["prediction_enabled"])
        self.assertNotIn("employee_id", data["prepared_inputs"]["workforce"])
        self.assertNotIn("employee_name", data["prepared_inputs"]["workforce"])
        self.assertNotIn("attendance_record_id", data["prepared_inputs"]["workforce"])
        self.assertNotIn("Date", data["prepared_inputs"]["supply_chain"])
        self.assertNotIn("Demand_Forecast", data["prepared_inputs"]["supply_chain"])

    def test_readiness_for_one_model_does_not_depend_on_the_other(self):
        registry = self.app.extensions["siteintel_model_registry"]
        report = extract_report_fields(
            "Date: 2026-03-14\nAttendance Status: Present\nMinutes Late: 3",
            workforce_features=registry.get("workforce").input_features,
            workforce_numeric=registry.get("workforce").numeric_features,
            supply_features=registry.get("supply_chain").input_features,
            supply_numeric=registry.get("supply_chain").numeric_features,
        )
        readiness = calculate_readiness(
            report,
            workforce_available=False,
            workforce_issue="known model limitation",
            supply_available=False,
            supply_issue="known model limitation",
        )
        self.assertTrue(readiness["workforce"]["data_ready"])
        self.assertFalse(readiness["supply_chain"]["data_ready"])
        self.assertIn("Inventory_Level", readiness["supply_chain"]["missing_fields"])

    def test_multiple_workforce_table_rows_are_not_silently_collapsed(self):
        registry = self.app.extensions["siteintel_model_registry"]

        def line(number, values):
            centers = (50, 252, 445)
            return {
                "block": 1,
                "paragraph": 1,
                "line": number,
                "tokens": [
                    {"text": value, "center": center}
                    for value, center in zip(values, centers)
                ],
            }

        lines = [
            line(1, ("Date", "Attendance Status", "Minutes Late")),
            line(2, ("2026-03-14", "Present", "3")),
            line(3, ("2026-03-14", "Absent", "0")),
        ]
        lines[0]["tokens"] = [
            {"text": "Date", "center": 50},
            {"text": "Attendance", "center": 225},
            {"text": "Status", "center": 280},
            {"text": "Minutes", "center": 420},
            {"text": "Late", "center": 470},
        ]
        report = extract_report_fields(
            "",
            workforce_features=registry.get("workforce").input_features,
            workforce_numeric=registry.get("workforce").numeric_features,
            supply_features=registry.get("supply_chain").input_features,
            supply_numeric=registry.get("supply_chain").numeric_features,
            ocr_lines=lines,
        )
        readiness = calculate_readiness(
            report,
            workforce_available=False,
            workforce_issue="known model limitation",
            supply_available=False,
            supply_issue="known model limitation",
        )

        self.assertTrue(report["ambiguities"]["workforce"])
        self.assertIsNone(report["workforce"]["work_date"])
        self.assertFalse(readiness["workforce"]["data_ready"])
        self.assertIn("Multiple workforce records", readiness["workforce"]["data_issue"])
        self.assertEqual(readiness["workforce"]["model_issue"], "known model limitation")
        self.assertFalse(readiness["supply_chain"]["data_ready"])
        self.assertIsNone(readiness["supply_chain"]["data_issue"])

    def test_invalid_calendar_date_is_preserved_without_derived_features(self):
        registry = self.app.extensions["siteintel_model_registry"]
        report = extract_report_fields(
            "Date: 99/99/2026\nAttendance Status: Present\nMinutes Late: 3",
            workforce_features=registry.get("workforce").input_features,
            workforce_numeric=registry.get("workforce").numeric_features,
            supply_features=registry.get("supply_chain").input_features,
            supply_numeric=registry.get("supply_chain").numeric_features,
        )

        self.assertEqual(report["report_date"], "99/99/2026")
        self.assertIsNone(report["workforce"]["work_date"])
        self.assertIsNone(report["supply_chain"]["date_month"])

    def test_prediction_endpoints_refuse_incompatible_artifacts(self):
        token = self._login()
        response = self._extract(token)
        report_id = response.json["report_id"]
        for endpoint in ("workforce", "supply-chain"):
            result = self.client.post(
                f"/api/reports/{report_id}/predict/{endpoint}",
                headers={"X-CSRFToken": token},
            )
            self.assertEqual(result.status_code, 409)
            self.assertEqual(result.json["error"]["code"], "model_unavailable")

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

    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract is not installed")
    def test_local_tesseract_reads_printed_report_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "printed-report.png"
            image = Image.new("RGB", (1400, 180), "white")
            draw = ImageDraw.Draw(image)
            draw.text(
                (30, 30),
                "Attendance Status: Present",
                fill="black",
                font=ImageFont.load_default(size=42),
            )
            image.save(path)
            text, _lines = extract_ocr(path)
        self.assertTrue(_lines)
        self.assertIn("Attendance", text)
        self.assertIn("Present", text)

    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract is not installed")
    def test_local_tesseract_extracts_fields_from_a_table(self):
        registry = self.app.extensions["siteintel_model_registry"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report-table.png"
            image = Image.new("RGB", (1800, 360), "white")
            draw = ImageDraw.Draw(image)
            font = ImageFont.load_default(size=30)
            headers = (
                "Date",
                "Units Sold",
                "Inventory Level",
                "Supplier Lead Time Days",
                "Reorder Point",
            )
            values = ("2026-03-14", "35", "80", "14", "100")
            positions = (20, 360, 650, 1020, 1450)
            for left, header, value in zip(positions, headers, values):
                draw.text((left, 30), header, fill="black", font=font)
                draw.text((left, 150), value, fill="black", font=font)
            image.save(path)
            text, lines = extract_ocr(path)

        report = extract_report_fields(
            text,
            workforce_features=registry.get("workforce").input_features,
            workforce_numeric=registry.get("workforce").numeric_features,
            supply_features=registry.get("supply_chain").input_features,
            supply_numeric=registry.get("supply_chain").numeric_features,
            ocr_lines=lines,
        )
        self.assertEqual(report["supply_chain"]["Units_Sold"], 35)
        self.assertEqual(report["supply_chain"]["Inventory_Level"], 80)
        self.assertEqual(report["supply_chain"]["Supplier_Lead_Time_Days"], 14)
        self.assertEqual(report["supply_chain"]["Reorder_Point"], 100)
        self.assertEqual(report["supply_chain"]["date_month"], 3)

    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract is not installed")
    def test_local_tesseract_does_not_collapse_multiple_workforce_rows(self):
        registry = self.app.extensions["siteintel_model_registry"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attendance-table.png"
            image = Image.new("RGB", (1600, 420), "white")
            draw = ImageDraw.Draw(image)
            font = ImageFont.load_default(size=30)
            for left, header in zip(
                (20, 420, 1000),
                ("Date", "Attendance Status", "Minutes Late"),
            ):
                draw.text((left, 20), header, fill="black", font=font)
            rows = (
                ("2026-03-14", "Present", "3"),
                ("2026-03-15", "Absent", "0"),
            )
            for top, row in zip((140, 250), rows):
                for left, value in zip((20, 420, 1000), row):
                    draw.text((left, top), value, fill="black", font=font)
            image.save(path)
            text, lines = extract_ocr(path)

        report = extract_report_fields(
            text,
            workforce_features=registry.get("workforce").input_features,
            workforce_numeric=registry.get("workforce").numeric_features,
            supply_features=registry.get("supply_chain").input_features,
            supply_numeric=registry.get("supply_chain").numeric_features,
            ocr_lines=lines,
        )

        self.assertIn("Present", text)
        self.assertTrue(report["ambiguities"]["workforce"])
        self.assertIsNone(report["workforce"]["work_date"])
        self.assertIsNone(report["workforce"]["attendance_status"])


if __name__ == "__main__":
    unittest.main()
