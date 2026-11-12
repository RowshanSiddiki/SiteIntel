import re
import unittest

from siteintel import create_app
from siteintel.ml.model_registry import (
    DELAY_FEATURE_ENCODERS,
    MODEL_DIRECTORY,
    ModelArtifactError,
    ModelRegistry,
)
from siteintel.ml.predictors import (
    PredictionInputError,
    predict_cement_demand,
    predict_concrete_strength,
    predict_delay_risk,
)


CEMENT_INPUTS = {
    "planned_pour_tonnes": 40.0,
    "site_rolling_mean_30": 35.0,
    "rain_mm": 12.0,
    "avg_temp_c": 27.0,
}
CONCRETE_INPUTS = {
    "cement": 350.0,
    "slag": 0.0,
    "water": 180.0,
    "age": 28.0,
}
DELAY_INPUTS = {
    "Material_Availability": "Medium",
    "Equipment_Availability": "High",
    "Weather_Risk": "Medium",
    "Supplier_Delay": "Yes",
}


class ModelPredictionTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key-that-is-long-enough-for-siteintel",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_ENGINE_OPTIONS": {},
                "SESSION_COOKIE_SECURE": False,
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

    def _csrf_token(self, path="/auth/login"):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            response.get_data(as_text=True),
        )
        self.assertIsNotNone(match)
        return match.group(1)

    def _login(self):
        token = self._csrf_token("/auth/register")
        response = self.client.post(
            "/auth/register",
            data={
                "csrf_token": token,
                "full_name": "ML Test User",
                "email": "ml@example.com",
                "password": "StrongPass123!",
                "password_confirmation": "StrongPass123!",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        token = self._csrf_token()
        response = self.client.post(
            "/auth/login",
            data={
                "csrf_token": token,
                "email": "ml@example.com",
                "password": "StrongPass123!",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        return self._csrf_token("/dashboard/ml/")

    def _post(self, path, values, token=None):
        return self.client.post(
            path,
            json=values,
            headers={"X-CSRFToken": token or self._csrf_token()},
        )

    def test_artifacts_load_once_from_root_model_directory(self):
        registry = ModelRegistry()
        self.assertEqual(registry.directory, MODEL_DIRECTORY.resolve())
        self.assertTrue(registry.directory.is_absolute())
        for model_key, expected_type in (
            ("cement_demand", "RandomForestRegressor"),
            ("concrete_strength", "GradientBoostingRegressor"),
            ("delay_risk", "GradientBoostingClassifier"),
        ):
            with self.subTest(model=model_key):
                package = registry.get(model_key)
                self.assertEqual(type(package.model).__name__, expected_type)
                self.assertTrue(
                    all((registry.directory / artifact).is_file()
                        for artifact in (
                            registry.artifact_path(model_key, "model").name,
                            registry.artifact_path(model_key, "features").name,
                            registry.artifact_path(model_key, "metadata").name,
                        ))
                )
                self.assertIs(package, registry.get(model_key))
        self.assertEqual(
            registry.get("delay_risk").encoders,
            DELAY_FEATURE_ENCODERS,
        )

    def test_missing_model_artifact_fails_clearly(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ModelArtifactError, "artifact is missing"):
                ModelRegistry(Path(directory)).get("cement_demand")

    def test_prediction_functions_return_real_model_outputs(self):
        registry = self.app.extensions["siteintel_model_registry"]
        cement = predict_cement_demand(CEMENT_INPUTS, registry)
        concrete = predict_concrete_strength(CONCRETE_INPUTS, registry)
        delay = predict_delay_risk(DELAY_INPUTS, registry=registry)

        self.assertAlmostEqual(
            cement["predicted_consumption_tonnes"], 31.4730834108, places=7
        )
        self.assertAlmostEqual(
            concrete["compressive_strength_mpa"], 39.8513159924, places=7
        )
        self.assertAlmostEqual(delay["delay_probability"], 0.5188931681, places=7)
        self.assertEqual(delay["predicted_class"], 1)
        self.assertEqual(delay["delay_risk"], "Yes")

    def test_prediction_functions_reject_missing_and_invalid_values(self):
        registry = self.app.extensions["siteintel_model_registry"]
        for predictor, valid_values in (
            (predict_cement_demand, CEMENT_INPUTS),
            (predict_concrete_strength, CONCRETE_INPUTS),
            (predict_delay_risk, DELAY_INPUTS),
        ):
            with self.subTest(predictor=predictor.__name__):
                missing = dict(valid_values)
                missing.pop(next(iter(missing)))
                with self.assertRaises(PredictionInputError):
                    predictor(missing, registry=registry)

        invalid_cement = dict(CEMENT_INPUTS, rain_mm=float("nan"))
        with self.assertRaisesRegex(PredictionInputError, "finite"):
            predict_cement_demand(invalid_cement, registry)
        invalid_concrete = dict(CONCRETE_INPUTS, cement=-1)
        with self.assertRaisesRegex(PredictionInputError, "negative"):
            predict_concrete_strength(invalid_concrete, registry)
        invalid_type = dict(CEMENT_INPUTS, avg_temp_c="warm")
        with self.assertRaisesRegex(PredictionInputError, "number"):
            predict_cement_demand(invalid_type, registry)

    def test_delay_risk_rejects_unknown_categories_and_invalid_thresholds(self):
        registry = self.app.extensions["siteintel_model_registry"]
        invalid = dict(DELAY_INPUTS, Weather_Risk="Severe")
        with self.assertRaisesRegex(PredictionInputError, "Weather_Risk"):
            predict_delay_risk(invalid, registry=registry)
        with self.assertRaisesRegex(ValueError, "threshold"):
            predict_delay_risk(DELAY_INPUTS, threshold=1.1, registry=registry)

    def test_authenticated_dashboard_has_three_distinct_manual_forms(self):
        self._login()
        response = self.client.get("/dashboard/ml/")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        for field in CEMENT_INPUTS:
            self.assertIn(f'name="{field}"', page)
        for field in CONCRETE_INPUTS:
            self.assertIn(f'name="{field}"', page)
        for field in DELAY_INPUTS:
            self.assertIn(f'name="{field}"', page)
        self.assertEqual(page.count('class="ml-form"'), 3)
        self.assertIn("not an optimized threshold", page)

    def test_ml_page_requires_authentication(self):
        response = self.client.get("/dashboard/ml/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response.location)

    def test_prediction_endpoints_require_authentication(self):
        token = self._csrf_token()
        for path, values in (
            ("/api/ml/cement-demand", CEMENT_INPUTS),
            ("/api/ml/concrete-strength", CONCRETE_INPUTS),
            ("/api/ml/delay-risk", DELAY_INPUTS),
        ):
            with self.subTest(path=path):
                response = self._post(path, values, token)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(
                    response.json["error"]["code"],
                    "authentication_required",
                )

    def test_all_prediction_endpoints_return_expected_outputs(self):
        token = self._login()
        cases = (
            (
                "/api/ml/cement-demand",
                CEMENT_INPUTS,
                "predicted_consumption_tonnes",
            ),
            (
                "/api/ml/concrete-strength",
                CONCRETE_INPUTS,
                "compressive_strength_mpa",
            ),
            ("/api/ml/delay-risk", DELAY_INPUTS, "delay_probability"),
        )
        for path, values, result_key in cases:
            with self.subTest(path=path):
                response = self._post(path, values, token)
                self.assertEqual(response.status_code, 200, response.json)
                self.assertTrue(response.json["success"])
                self.assertIn(result_key, response.json["prediction"])
        delay_response = self._post("/api/ml/delay-risk", DELAY_INPUTS, token)
        self.assertEqual(
            set(delay_response.json["prediction"]),
            {"delay_probability", "delay_risk", "predicted_class"},
        )

    def test_endpoints_return_clear_missing_and_invalid_input_errors(self):
        token = self._login()
        cases = (
            ("/api/ml/cement-demand", CEMENT_INPUTS, "planned_pour_tonnes"),
            ("/api/ml/concrete-strength", CONCRETE_INPUTS, "cement"),
            ("/api/ml/delay-risk", DELAY_INPUTS, "Material_Availability"),
        )
        for path, values, field in cases:
            missing = dict(values)
            missing.pop(field)
            with self.subTest(path=path):
                response = self._post(path, missing, token)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json["error"]["code"], "invalid_input")
                self.assertIn(field, response.json["error"]["message"])

        invalid = dict(CEMENT_INPUTS, rain_mm=float("inf"))
        response = self._post("/api/ml/cement-demand", invalid, token)
        self.assertEqual(response.status_code, 400)
        self.assertIn("finite", response.json["error"]["message"])

        invalid = dict(CONCRETE_INPUTS, cement=float("nan"))
        response = self._post("/api/ml/concrete-strength", invalid, token)
        self.assertEqual(response.status_code, 400)
        self.assertIn("finite", response.json["error"]["message"])

        invalid_category = dict(DELAY_INPUTS, Supplier_Delay="Maybe")
        response = self._post("/api/ml/delay-risk", invalid_category, token)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Supplier_Delay", response.json["error"]["message"])

    def test_delay_endpoint_uses_configured_threshold(self):
        token = self._login()
        self.app.config["DELAY_RISK_THRESHOLD"] = 0.6
        response = self._post("/api/ml/delay-risk", DELAY_INPUTS, token)
        self.assertEqual(response.status_code, 200)
        prediction = response.json["prediction"]
        self.assertEqual(prediction["predicted_class"], 0)
        self.assertEqual(prediction["delay_risk"], "No")
        self.assertAlmostEqual(prediction["delay_probability"], 0.5188931681, places=7)


if __name__ == "__main__":
    unittest.main()
