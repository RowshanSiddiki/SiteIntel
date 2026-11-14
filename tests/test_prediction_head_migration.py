import importlib.util
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


class PredictionHeadMigrationTests(unittest.TestCase):
    def test_backfill_selects_latest_snapshot_per_document_and_model(self):
        path = Path(__file__).resolve().parents[1] / "migrations/versions/5b86d3042fa1_add_prediction_heads.py"
        spec = importlib.util.spec_from_file_location("prediction_head_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = sa.create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE documents (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE document_predictions (id INTEGER PRIMARY KEY, document_id INTEGER, model_key VARCHAR(32))")
            connection.exec_driver_sql("INSERT INTO documents VALUES (1), (2)")
            connection.exec_driver_sql(
                "INSERT INTO document_predictions VALUES "
                "(1, 1, 'cement_demand'), (2, 1, 'cement_demand'), "
                "(3, 1, 'delay_risk'), (4, 2, 'cement_demand')"
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            rows = connection.exec_driver_sql(
                "SELECT document_id, model_key, prediction_id FROM prediction_heads ORDER BY document_id, model_key"
            ).all()
            self.assertEqual(rows, [(1, "cement_demand", 2), (1, "delay_risk", 3), (2, "cement_demand", 4)])
        engine.dispose()
