import gc
import unittest
from pathlib import Path
from numbers import Real
from tempfile import TemporaryDirectory
from unittest.mock import patch

import joblib
import pandas as pd

from backend.modules.ml.cement_demand import predict_cement_demand
from backend.modules.ml.civil_risk import predict_civil_risk
from backend.modules.ml.construction_cost import predict_construction_cost
from backend.modules.ml.model_loader import (
    InvalidPredictionInput,
    MODEL_DIRECTORY,
    ModelLoadError,
    ModelNotFoundError,
    load_model,
)
from backend.modules.ml.supply_chain import predict_supply_chain_stockout
from backend.modules.ml.workforce import predict_workforce_attendance


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_PREDICTORS = {
    "workforce_attendance": predict_workforce_attendance,
    "cement_demand": predict_cement_demand,
    "supply_chain": predict_supply_chain_stockout,
    "construction_cost": predict_construction_cost,
    "civil_risk": predict_civil_risk,
}


def _dataset_features(model_key: str, feature_names: tuple[str, ...]) -> dict[str, object]:
    datasets = {
        "workforce_attendance": "workforce_attendance_sample.csv",
        "cement_demand": "cement_demand_features.csv",
        "supply_chain": "supply_chain_dataset1.csv",
        "construction_cost": "construction_estimates.csv",
        "civil_risk": "Civil Engineering Global Project Dataset.csv",
    }
    frame = pd.read_csv(PROJECT_ROOT / "Datasets" / datasets[model_key])

    if model_key == "cement_demand":
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["date_year"] = frame["date"].dt.year
        frame["date_month"] = frame["date"].dt.month
        frame["date_day"] = frame["date"].dt.day
        frame["date_dayofweek"] = frame["date"].dt.day_of_week
    elif model_key == "supply_chain":
        dates = pd.to_datetime(frame.pop("Date"), errors="coerce")
        frame["date_year"] = dates.dt.year
        frame["date_month"] = dates.dt.month
        frame["date_day"] = dates.dt.day
        frame["date_dayofweek"] = dates.dt.day_of_week
    elif model_key in {"workforce_attendance", "construction_cost"}:
        frame = frame.drop(
            columns=[
                "risk_level",
                "risk_reason",
                "late_severity_band",
                "absence_category",
                "overtime_band",
                "manager_action_category",
                "manager_follow_up_required_flag",
                "coaching_recommended_flag",
                "employee_id",
                "employee_name",
                "record_id",
                "Total_Estimate",
            ],
            errors="ignore",
        )
    elif model_key == "civil_risk":
        frame = frame.drop(columns=["is_good", "Project ID"], errors="ignore")

    missing_columns = set(feature_names) - set(frame.columns)
    if missing_columns:
        raise AssertionError(f"Dataset does not provide model features: {missing_columns}")

    frame = frame.loc[:, feature_names]
    if model_key == "workforce_attendance":
        row = {}
        for feature in feature_names:
            values = frame[feature].dropna()
            if values.empty:
                raise AssertionError(f"Dataset has no values for '{feature}'.")
            value = values.iloc[0]
            row[feature] = value.item() if hasattr(value, "item") else value
        return row

    complete_rows = frame.dropna(axis=0, how="any")
    if complete_rows.empty:
        raise AssertionError(f"Dataset has no complete model input for '{model_key}'.")
    return {
        feature: (
            value.item() if hasattr(value, "item") else value
        )
        for feature, value in complete_rows.iloc[0].items()
    }


class ModelIntegrationTests(unittest.TestCase):
    def tearDown(self):
        load_model.cache_clear()
        gc.collect()

    def test_every_existing_model_loads_once_and_predicts(self):
        for key, predictor in MODEL_PREDICTORS.items():
            with self.subTest(model=key):
                artifact = load_model(key)
                self.assertIs(artifact, load_model(key))

                features = _dataset_features(key, artifact.input_features)
                result = predictor(features)
                reversed_result = predictor(dict(reversed(list(features.items()))))

                self.assertEqual(result["model"], key)
                self.assertEqual(result["target"], artifact.target)
                if isinstance(result["prediction"], Real):
                    self.assertAlmostEqual(
                        result["prediction"], reversed_result["prediction"], places=10
                    )
                else:
                    self.assertEqual(
                        result["prediction"], reversed_result["prediction"]
                    )
                self.assertIn("prediction", result)
                self.assertIn("probabilities", result)
                if result["probabilities"] is not None:
                    self.assertTrue(result["probabilities"])
                    self.assertAlmostEqual(
                        sum(item["probability"] for item in result["probabilities"]),
                        1.0,
                        places=6,
                    )
                load_model.cache_clear()
                gc.collect()

    def test_prediction_rejects_missing_and_extra_features(self):
        artifact = load_model("construction_cost")
        features = _dataset_features("construction_cost", artifact.input_features)

        with self.assertRaises(InvalidPredictionInput):
            predict_construction_cost(
                {name: value for name, value in features.items() if name != "Policy_Reason"}
            )
        with self.assertRaises(InvalidPredictionInput):
            predict_construction_cost({**features, "unexpected": 1})
        with self.assertRaises(InvalidPredictionInput):
            predict_construction_cost({**features, "Policy_Reason": None})

    def test_prediction_rejects_invalid_feature_values(self):
        artifact = load_model("construction_cost")
        features = _dataset_features("construction_cost", artifact.input_features)
        features["Material_Cost"] = "not-a-number"
        with self.assertRaises(InvalidPredictionInput):
            predict_construction_cost(features)

    def test_prediction_rejects_numeric_values_outside_float_range(self):
        artifact = load_model("construction_cost")
        features = _dataset_features("construction_cost", artifact.input_features)
        features["Material_Cost"] = 10**400
        with self.assertRaises(InvalidPredictionInput):
            predict_construction_cost(features)

    def test_missing_model_file_has_a_specific_error(self):
        with TemporaryDirectory() as temporary_directory:
            with patch(
                "backend.modules.ml.model_loader.MODEL_DIRECTORY",
                Path(temporary_directory),
            ):
                with self.assertRaises(ModelNotFoundError):
                    load_model("construction_cost")

    def test_invalid_model_file_has_a_specific_error(self):
        with TemporaryDirectory() as temporary_directory:
            model_directory = Path(temporary_directory)
            (model_directory / "construction_estimate_model.pkl").write_text(
                "not a pickle", encoding="utf-8"
            )
            joblib.dump(
                {
                    "target": "Total_Estimate",
                    "features": [
                        "Material_Cost",
                        "Labor_Cost",
                        "Profit_Rate",
                        "Discount_or_Markup",
                        "Policy_Reason",
                    ],
                    "numeric_features": [],
                    "categorical_features": [],
                },
                model_directory / "construction_estimate_model_metadata.pkl",
            )
            with patch(
                "backend.modules.ml.model_loader.MODEL_DIRECTORY",
                model_directory,
            ):
                with self.assertLogs(
                    "backend.modules.ml.model_loader", level="ERROR"
                ):
                    with self.assertRaises(ModelLoadError):
                        load_model("construction_cost")


if __name__ == "__main__":
    unittest.main()
