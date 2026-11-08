import io
import re
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw, ImageFont
import pandas as pd
import joblib

from siteintel import create_app
from siteintel.reports.extraction import calculate_readiness, extract_report_fields
from siteintel.reports.model_registry import MODEL_DIRECTORY, MODEL_SPECS, ModelRegistry
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

    def _extract(
        self,
        token: str,
        text: str | None = None,
        ocr_calls: list[Mock] | None = None,
    ):
        stream, filename = self._image_upload()
        with patch(
            "siteintel.reports.routes.extract_ocr",
            return_value=(self.report_text if text is None else text, []),
        ) as ocr_mock:
            if ocr_calls is not None:
                ocr_calls.append(ocr_mock)
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

    def test_dashboard_contains_all_five_independent_prediction_cards(self):
        self._login()
        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        for label in (
            "Predict Workforce Attendance Risk",
            "Predict Supply Chain Stockout Risk",
            "Predict Construction Cost",
            "Predict Civil Project Risk",
            "Predict Cement Demand",
        ):
            self.assertIn(label, page)
        for model_key in (
            "workforce",
            "supply",
            "construction-estimate",
            "civil-risk",
            "cement-demand",
        ):
            self.assertIn(f'id="{model_key}-required-fields"', page)

    def test_upload_runs_ocr_only_once_for_all_five_models(self):
        token = self._login()
        ocr_calls = []
        response = self._extract(token, ocr_calls=ocr_calls)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(ocr_calls), 1)
        ocr_calls[0].assert_called_once()
        self.assertEqual(
            set(response.json["readiness"]),
            {
                "workforce",
                "supply_chain",
                "construction_estimate",
                "civil_risk",
                "cement_demand",
            },
        )
        registry = self.app.extensions["siteintel_model_registry"]
        for model_key in response.json["readiness"]:
            self.assertEqual(
                response.json["readiness"][model_key]["required_features"],
                list(registry.get(model_key).input_features),
            )
            stem, target = MODEL_SPECS[model_key]
            self.assertEqual(
                response.json["readiness"][model_key]["model_file"],
                f"{stem}.pkl",
            )
            self.assertEqual(
                response.json["readiness"][model_key]["metadata_file"],
                f"{stem}_metadata.pkl",
            )
            self.assertEqual(response.json["readiness"][model_key]["target"], target)
            self.assertEqual(
                response.json["readiness"][model_key]["model_loaded"],
                registry.get(model_key).model is not None,
            )
            self.assertEqual(
                response.json["readiness"][model_key]["model_available"],
                registry.get(model_key).usable,
            )

    def test_one_model_load_failure_does_not_block_other_models(self):
        original_load = joblib.load

        def fail_construction_model(path, *args, **kwargs):
            if Path(path).name == "construction_estimate_model.pkl":
                raise OSError("test artifact load failure")
            return original_load(path, *args, **kwargs)

        with patch(
            "siteintel.reports.model_registry.joblib.load",
            side_effect=fail_construction_model,
        ):
            registry = ModelRegistry(directory=MODEL_DIRECTORY)

        failed_artifact = registry.get("construction_estimate")
        self.assertFalse(failed_artifact.usable)
        self.assertIn("test artifact load failure", failed_artifact.issue)
        self.assertEqual(
            failed_artifact.input_features,
            ("Material_Cost", "Labor_Cost", "Profit_Rate", "Discount_or_Markup", "Policy_Reason"),
        )
        self.assertTrue(registry.get("civil_risk").usable)
        self.assertTrue(registry.get("cement_demand").usable)

    def test_new_model_readiness_is_independent_and_lists_missing_inputs(self):
        registry = self.app.extensions["siteintel_model_registry"]
        report_text = "\n".join(
            (
                "Material Cost: 10000",
                "Labor Cost: 3000",
                "Profit Rate: 10",
                "Discount or Markup: 100",
                "Policy Reason: Market adjustment",
                "Certificates: 4",
                "Years of Experience: 8",
                "Age: 38",
                "Time Arrival Strafe: 2.5",
                "Project Cost: 120000",
                "Project Proximity: 80",
                "Violation Risk Index: 0.2",
                "Company PCAB Score: 4",
                "Weekly Overtime Hours: 5",
                "Salary Bracket: 3",
                "Date: 2026-03-14",
                "Planned Pour Tonnes: 40",
                "Site ID: SITE_001",
                "Cement Type: CEM_I",
            )
        )
        additional_models = {
            model_key: (
                registry.get(model_key).input_features,
                registry.get(model_key).numeric_features,
            )
            for model_key in (
                "construction_estimate",
                "civil_risk",
                "cement_demand",
            )
        }
        fields = extract_report_fields(
            report_text,
            workforce_features=registry.get("workforce").input_features,
            workforce_numeric=registry.get("workforce").numeric_features,
            supply_features=registry.get("supply_chain").input_features,
            supply_numeric=registry.get("supply_chain").numeric_features,
            additional_models=additional_models,
        )
        readiness = calculate_readiness(
            fields,
            workforce_available=False,
            workforce_issue="known model limitation",
            supply_available=False,
            supply_issue="known model limitation",
            additional_model_status={
                model_key: (
                    registry.get(model_key).usable,
                    registry.get(model_key).issue,
                )
                for model_key in additional_models
            },
        )

        self.assertTrue(readiness["construction_estimate"]["prediction_enabled"])
        self.assertIn("not an independent cost estimate", readiness["construction_estimate"]["model_warning"])
        self.assertTrue(readiness["civil_risk"]["prediction_enabled"])
        self.assertIn("age and experience", readiness["civil_risk"]["model_warning"])
        self.assertFalse(readiness["cement_demand"]["data_ready"])
        self.assertIn("site_demand_lag_1", readiness["cement_demand"]["missing_fields"])
        self.assertTrue(readiness["construction_estimate"]["prediction_enabled"])
        self.assertTrue(readiness["civil_risk"]["prediction_enabled"])

    def test_new_model_inputs_match_metadata_order_and_predict_actual_dataset_rows(self):
        registry = self.app.extensions["siteintel_model_registry"]
        sample_sources = {
            "construction_estimate": (
                "construction_estimate_model",
                "construction_estimates.csv",
            ),
            "civil_risk": (
                "civil_risk_model",
                "Civil Engineering Global Project Dataset.csv",
            ),
            "cement_demand": (
                "cement_demand_model",
                "cement_demand_features.csv",
            ),
        }
        for model_key, (stem, dataset_name) in sample_sources.items():
            with self.subTest(model=model_key):
                artifact = registry.get(model_key)
                metadata = pd.read_pickle(
                    Path(__file__).resolve().parents[1]
                    / "pkl_files"
                    / f"{stem}_metadata.pkl"
                )
                declared_features = metadata.get(
                    "features", metadata.get("feature_columns")
                )
                self.assertEqual(list(artifact.features), declared_features)
                self.assertNotIn(artifact.target, artifact.input_features)
                dataset = pd.read_csv(
                    Path(__file__).resolve().parents[1]
                    / "Datasets"
                    / dataset_name,
                    nrows=1,
                )
                row = dataset.iloc[0]
                values = {}
                for feature in artifact.features:
                    value = row.get(feature)
                    if model_key == "cement_demand" and feature.startswith("date_"):
                        report_date = date.fromisoformat(str(row["date"]))
                        value = {
                            "date_year": report_date.year,
                            "date_month": report_date.month,
                            "date_day": report_date.day,
                            "date_dayofweek": report_date.weekday(),
                        }[feature]
                    if hasattr(value, "item"):
                        value = value.item()
                    values[feature] = value
                self.assertEqual(tuple(values), artifact.features)
                prediction = artifact.predict(values)
                self.assertTrue(pd.notna(prediction))
                if model_key == "construction_estimate":
                    self.assertTrue(pd.notna(float(prediction)))
                elif model_key == "civil_risk":
                    self.assertIn(int(prediction), (0, 1))
                    probabilities = artifact.predict_probabilities(values)
                    self.assertEqual(set(probabilities), {"0", "1"})
                    self.assertAlmostEqual(sum(probabilities.values()), 1.0)
                else:
                    self.assertTrue(pd.notna(float(prediction)))

    def test_new_prediction_endpoints_reject_missing_extracted_features(self):
        token = self._login()
        response = self._extract(token)
        self.assertEqual(response.status_code, 201)
        report_id = response.json["report_id"]
        endpoints = {
            "construction-estimate": "construction_estimate",
            "civil-risk": "civil_risk",
            "cement-demand": "cement_demand",
        }
        for endpoint, model_key in endpoints.items():
            with self.subTest(model=model_key):
                self.assertFalse(
                    response.json["readiness"][model_key]["prediction_enabled"]
                )
                result = self.client.post(
                    f"/api/reports/{report_id}/predict/{endpoint}",
                    headers={"X-CSRFToken": token},
                )
                self.assertEqual(result.status_code, 422)
                self.assertEqual(
                    result.json["error"]["code"],
                    "insufficient_report_data",
                )

    def test_new_prediction_endpoints_use_actual_artifacts_and_dataset_rows(self):
        token = self._login()
        project_root = Path(__file__).resolve().parents[1]
        registry = self.app.extensions["siteintel_model_registry"]
        rows = {
            "construction_estimate": pd.read_csv(
                project_root / "Datasets" / "construction_estimates.csv",
                nrows=1,
            ).iloc[0],
            "civil_risk": pd.read_csv(
                project_root / "Datasets" / "Civil Engineering Global Project Dataset.csv",
                nrows=1,
            ).iloc[0],
            "cement_demand": pd.read_csv(
                project_root / "Datasets" / "cement_demand_features.csv",
                nrows=1,
            ).iloc[0],
        }
        report_parts = [f"Date: {rows['cement_demand']['date']}"]
        for model_key, row in rows.items():
            artifact = registry.get(model_key)
            for feature in artifact.features:
                if feature in row.index:
                    report_parts.append(f"{feature}: {row[feature]}")
        report_text = "\n".join(report_parts)
        ocr_calls = []
        extracted = self._extract(token, text=report_text, ocr_calls=ocr_calls)

        self.assertEqual(extracted.status_code, 201, extracted.get_data(as_text=True))
        self.assertEqual(len(ocr_calls), 1)
        ocr_calls[0].assert_called_once()
        report_id = extracted.json["report_id"]
        route_map = {
            "construction-estimate": "construction_estimate",
            "civil-risk": "civil_risk",
            "cement-demand": "cement_demand",
        }
        for endpoint, model_key in route_map.items():
            with self.subTest(model=model_key):
                self.assertTrue(
                    extracted.json["readiness"][model_key]["prediction_enabled"],
                    extracted.json["readiness"][model_key],
                )
                response = self.client.post(
                    f"/api/reports/{report_id}/predict/{endpoint}",
                    headers={"X-CSRFToken": token},
                )
                self.assertEqual(
                    response.status_code,
                    200,
                    response.get_data(as_text=True),
                )
                prediction = response.json["prediction"]
                self.assertTrue(pd.notna(prediction))
                if model_key == "civil_risk":
                    self.assertIn(prediction, (0, 1))
                    self.assertEqual(
                        set(response.json["class_probabilities"]),
                        {"0", "1"},
                    )
                else:
                    self.assertIsInstance(prediction, (int, float))

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
        self.assertNotIn(
            "replacement_employee_id",
            data["prepared_inputs"]["workforce"],
        )
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

    def test_cement_date_features_use_monday_zero_and_sunday_six(self):
        registry = self.app.extensions["siteintel_model_registry"]
        schemas = {
            model_key: (
                registry.get(model_key).input_features,
                registry.get(model_key).numeric_features,
            )
            for model_key in (
                "construction_estimate",
                "civil_risk",
                "cement_demand",
            )
        }
        for value, expected in (("2026-11-09", 0), ("2026-11-08", 6)):
            with self.subTest(date=value):
                fields = extract_report_fields(
                    f"Date: {value}",
                    workforce_features=registry.get("workforce").input_features,
                    workforce_numeric=registry.get("workforce").numeric_features,
                    supply_features=registry.get("supply_chain").input_features,
                    supply_numeric=registry.get("supply_chain").numeric_features,
                    additional_models=schemas,
                )
                cement = fields["cement_demand"]
                self.assertEqual(cement["date_dayofweek"], expected)
                self.assertEqual(cement["date_year"], 2026)
                self.assertEqual(cement["date_month"], 11)

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
