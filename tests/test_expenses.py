import importlib.util
import shutil
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from siteintel.documents.extraction import Extraction, PageText
from siteintel.documents.settings import LLMSettings
from siteintel.extensions import db
from siteintel.projects.models import Expense, FinancialObservation
from tests.test_documents import DocumentTestCase, make_scanned_pdf


INVOICE = (
    "Invoice No: ABC\nDate: 2026-01-10\n"
    "Material cost: BDT 100.00\nLabour cost: BDT 50.00\n"
    "Total: BDT 150.00\nBalance due: BDT 25.00\nBudget: BDT 200.00"
)
QUOTATION = (
    "MATERIAL QUOTATION\nQuotation No. MQ-2026-118\nQuotation Date 12 November 2026\n"
    "Material Specification Quantity Unit Unit Price Total\n"
    "Cement Portland cement 50 bags BDT 520 BDT 26,000\n"
    "Sand coarse sand 100 CFT BDT 45 BDT 4,500\n"
    "Grand Total BDT 140,000 (including delivery charge)\nDelivery charge BDT 2,000\n"
    "4.3 Delivery Charge. A flat delivery charge of BDT 2,000 applies\n"
    "Advance payment 30% BDT 42,000\nBalance payment 70% BDT 98,000"
)


class FinancialAnalyticsTests(DocumentTestCase):
    def upload_invoice(self):
        response = self.upload([INVOICE], "invoice.pdf", "other")
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.json["status"], "completed")
        return response.json["id"]

    def review_all(self):
        with self.app.app_context():
            ids = db.session.scalars(db.select(FinancialObservation.id).where(
                FinancialObservation.project_id == self.pid,
                FinancialObservation.status == "pending",
            )).all()
        for item_id in ids:
            response = self.client.post(
                f"/projects/{self.pid}/finance/observations/{item_id}/review",
                data={"decision": "confirmed"},
            )
            self.assertEqual(response.status_code, 302)

    def page(self, query=""):
        return self.client.get(f"/dashboard/projects/{self.pid}/expenses/{query}")

    def api(self, query="", pid=None):
        return self.client.get(f"/projects/{pid or self.pid}/finance/analytics.json{query}")

    def test_extract_review_aggregate_and_project_api(self):
        with self.app.app_context():
            db.session.add(Expense(
                project_id=self.pid, created_by_id=self.ids["owner"],
                expense_date=date(2026, 1, 10), description="Legacy manual record",
                category="Materials", currency="BDT", amount=Decimal("999.00"),
            ))
            db.session.commit()
        self.upload_invoice()
        before = self.page().get_data(as_text=True)
        self.assertIn("awaiting confirmation", before)
        self.assertNotIn("Add Expense", before)
        self.assertNotIn("Upload Bill", before)
        self.assertNotIn("Open Document Assistant", before)
        self.assertEqual(self.client.get(f"/projects/{self.pid}/finance/new").status_code, 404)
        self.assertEqual(self.api().json["charts"]["expense"]["series"], [])
        self.review_all()
        expense = self.api().json["charts"]["expense"]
        self.assertEqual(expense["unit"], "BDT")
        self.assertEqual(expense["dates"], ["2026-01-10"])
        self.assertEqual(expense["series"][0]["values"], [150.0])
        self.assertNotIn("999", str(self.api().json))
        self.assertEqual(self.api("?start=2026-01-11").json["charts"]["expense"]["series"], [])
        api = self.client.get(f"/api/projects/{self.pid}")
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json["id"], self.pid)

    def test_quotation_schedules_and_delivery_not_expenses(self):
        response = self.upload([QUOTATION], "quote.pdf", "quotation")
        self.assertEqual(response.status_code, 201)
        with self.app.app_context():
            rows = db.session.scalars(db.select(FinancialObservation).where(
                FinancialObservation.document_id == response.json["id"])).all()
            self.assertEqual({row.kind for row in rows},
                             {"quotation_total", "delivery_charge", "advance_schedule",
                              "balance_schedule", "material_component"})
            self.assertEqual(sum(row.kind == "delivery_charge" for row in rows), 1)
            self.assertTrue(all(row.observed_date == date(2026, 11, 12) for row in rows))
            self.assertEqual(next(row.category for row in rows if row.kind == "delivery_charge"),
                             "Transportation")
            self.assertEqual(next(row.amount for row in rows if row.kind == "quotation_total"),
                             Decimal("140000"))
        self.review_all()
        self.assertEqual(self.api().json["charts"]["expense"]["series"], [])

    def test_extract_material_table_and_unknown_category(self):
        from siteintel.expenses.extraction import extract_observations
        text = ("MATERIAL QUOTATION\nQuotation Date 12 November 2026\n"
                "Material Specification Quantity Unit Unit Price Total\n"
                "Cement Portland composite cement, 50\nkg 50 Bags ৳520 ৳26,000\n"
                "Sand Sylhet coarse sand 100 CFT ৳45 ৳4,500\n"
                "Grand Total ৳30,500\nAdvance payment 30% ৳9,150")
        rows = list(extract_observations([PageText(1, text)], document_category="quotation").values())
        components = [row for row in rows if row["kind"] == "material_component"]
        self.assertEqual([(row["label"], row["amount"]) for row in components],
                         [("Cement", Decimal("26000")), ("Sand", Decimal("4500"))])
        self.assertEqual(next(row["category"] for row in rows if row["kind"] == "quotation_total"),
                         "Unclassified")

    def test_different_vendors_do_not_share_invoice_identity(self):
        from siteintel.expenses.extraction import extract_observations
        first = "Vendor: North Supply\nInvoice No: A-001\nGrand Total: USD 50.00"
        second = first.replace("North", "South")
        a = next(iter(extract_observations([PageText(1, first)]).values()))
        b = next(iter(extract_observations([PageText(1, second)]).values()))
        self.assertNotEqual(a["document_reference"], b["document_reference"])

    def test_payment_date_is_not_inferred_from_invoice_date(self):
        from siteintel.expenses.extraction import extract_observations
        text = "Invoice No: A-001\nDate: 2026-01-10\nAmount paid: USD 50.00"
        row = next(iter(extract_observations([PageText(1, text)]).values()))
        self.assertIsNone(row["observed_date"])
        dated = text + "\nPayment Date: 2026-01-15"
        row = next(iter(extract_observations([PageText(1, dated)]).values()))
        self.assertEqual(row["observed_date"], date(2026, 1, 15))

    def test_invoice_payment_and_duplicate_references(self):
        first = self.upload(["Vendor: Example Supply\nInvoice No: INV-001\nDate: 2026-01-10\n"
                             "Grand Total: BDT 100.00\nAmount paid: BDT 30.00"],
                            "first.pdf", "other")
        second = self.upload(["Vendor: Example Supply\nInvoice No: INV-001\nDate: 2026-01-10\n"
                              "Grand Total: BDT 100.00\nAmount paid: BDT 30.00\nCopy"],
                             "copy.pdf", "other")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.review_all()
        expense = self.api().json["charts"]["expense"]
        self.assertEqual(expense["series"][0]["values"], [100.0])
        self.assertEqual(expense["dates"], ["2026-01-10"])

    def test_legacy_confirmed_quotation_observations_are_excluded(self):
        response = self.upload([QUOTATION], "quote.pdf", "quotation")
        with self.app.app_context():
            db.session.add(FinancialObservation(
                project_id=self.pid, document_id=response.json["id"],
                fingerprint="a" * 64, kind="expense", category="Other", label="Old total",
                amount=Decimal("284000"), currency="BDT", page_number=1,
                source_text="Legacy", status="confirmed",
            ))
            db.session.commit()
        self.assertEqual(self.api().json["charts"]["expense"]["series"], [])
        self.assertIsNone(self.api().json["kpis"]["expenses"])

    def test_reprocessing_is_idempotent_and_changed_source_needs_review(self):
        document_id = self.upload_invoice()
        self.review_all()
        url = self.url(f"/{document_id}/process?force=1")
        self.assertEqual(self.client.post(url).json["status"], "completed")
        with self.app.app_context():
            rows = db.session.scalars(db.select(FinancialObservation).where(
                FinancialObservation.document_id == document_id)).all()
            self.assertEqual(len(rows), 5)
            self.assertTrue(all(row.status == "confirmed" for row in rows))
        changed = INVOICE.replace("BDT 150.00", "BDT 175.00")
        extraction = Extraction([PageText(1, changed)], 1)
        with patch("siteintel.documents.processing.extract_pdf_pages", return_value=extraction):
            self.assertEqual(self.client.post(url).json["status"], "completed")
        page = self.page().get_data(as_text=True)
        self.assertIn("awaiting confirmation", page)
        self.assertEqual(self.api().json["charts"]["expense"]["series"], [])
        with self.app.app_context():
            rows = db.session.scalars(db.select(FinancialObservation).where(
                FinancialObservation.document_id == document_id,
                FinancialObservation.kind == "invoice_total")).all()
            self.assertEqual({row.status for row in rows}, {"stale", "pending"})

    def test_invalid_missing_and_conflicting_values_are_not_guessed(self):
        from siteintel.expenses.extraction import extract_observations
        pages = [PageText(1, "Invoice\nTotal: 100.00\nTotal: BDT 50.00\nTotal: BDT 60.00\n"
                            "Material cost: USD 10.00\nBudget: 500.00\nBalance due: unknown")]
        observations = list(extract_observations(pages).values())
        self.assertEqual([(o["kind"], str(o["amount"]), o["currency"]) for o in observations],
                         [("material_component", "10.00", "USD")])
        self.assertEqual(extract_observations([
            PageText(1, "Estimated material cost: BDT 500.00")
        ]), {})
        symbols = list(extract_observations([PageText(1, "Cement cost: ৳ 250.00")]).values())
        self.assertEqual((symbols[0]["currency"], str(symbols[0]["amount"])), ("BDT", "250.00"))

    def test_project_and_review_permissions(self):
        self.upload_invoice()
        with self.app.app_context():
            item_id = db.session.scalar(db.select(FinancialObservation.id))
        self.login("viewer")
        self.assertNotIn("awaiting confirmation", self.page().get_data(as_text=True))
        self.assertEqual(self.api().status_code, 200)
        self.assertEqual(self.client.post(
            f"/projects/{self.pid}/finance/observations/{item_id}/review",
            data={"decision": "confirmed"}).status_code, 404)
        self.login("stranger")
        self.assertEqual(self.page().status_code, 404)
        self.assertEqual(self.api().status_code, 404)
        self.assertEqual(self.api(pid=self.other_pid).json["charts"]["expense"]["series"], [])
        self.assertEqual(self.api(pid=self.other_pid).json["kpis"]["reports"], 0)
        self.assertEqual(self.client.post(
            f"/projects/{self.other_pid}/finance/observations/{item_id}/review",
            data={"decision": "confirmed"}).status_code, 404)
        self.client.post("/auth/logout")
        self.assertIn(self.api().status_code, (302, 401))

    def test_review_decisions_can_be_reversed(self):
        self.upload_invoice()
        with self.app.app_context():
            item_id = db.session.scalar(db.select(FinancialObservation.id).where(
                FinancialObservation.kind == "invoice_total"))
        path = f"/projects/{self.pid}/finance/observations/{item_id}/review"
        self.assertEqual(self.client.post(path, data={"decision": "confirmed"}).status_code, 302)
        self.assertEqual(self.api().json["kpis"]["expenses"], 150.0)
        self.assertEqual(self.client.post(path, data={"decision": "pending"}).status_code, 302)
        self.assertIsNone(self.api().json["kpis"]["expenses"])
        self.assertEqual(self.client.post(path, data={"decision": "ignored"}).status_code, 302)
        self.assertIsNone(self.api().json["kpis"]["expenses"])
        self.assertEqual(self.client.post(path, data={"decision": "pending"}).status_code, 302)

    def test_local_llm_refuses_remote_hosts_and_redirects(self):
        from siteintel.documents import llm
        remote = LLMSettings("key", "model", "https://example.test", 5)
        with self.assertRaises(llm.LLMError):
            llm.generate(remote, "system", "sensitive", local_only=True)
        handler = llm._NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "", {}, "https://example.test"))

    def test_backfill_is_idempotent(self):
        self.upload_invoice()
        self.review_all()
        result = self.app.test_cli_runner().invoke(args=["finance-backfill"])
        self.assertEqual(result.exit_code, 0, result.output)
        with self.app.app_context():
            rows = db.session.scalars(db.select(FinancialObservation)).all()
            self.assertEqual(len(rows), 5)
            self.assertTrue(all(row.status == "confirmed" for row in rows))

    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract is not installed")
    def test_scanned_document_uses_existing_ocr_and_requires_review(self):
        response = self.upload(data=make_scanned_pdf("Material cost: BDT 125.00"),
                               name="receipt.pdf", category="other")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["status"], "completed")
        with self.app.app_context():
            observation = db.session.scalar(db.select(FinancialObservation).where(
                FinancialObservation.document_id == response.json["id"]))
            self.assertIsNotNone(observation)
            self.assertEqual(str(observation.amount), "125.00")
            self.assertEqual(observation.status, "pending")


class ExpenseMigrationTests(unittest.TestCase):
    def test_additive_migrations_preserve_existing_expense(self):
        root = Path(__file__).resolve().parents[1] / "migrations/versions"
        engine = sa.create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE projects (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE documents (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE expenses (id INTEGER PRIMARY KEY, amount NUMERIC, status VARCHAR(16))")
            connection.exec_driver_sql("INSERT INTO expenses VALUES (1, 25.50, 'approved')")
            for name in ("9be43f1862a1_add_expenses_bills.py", "1f0a38d746b2_add_financial_observations.py", "2c87e4b91d10_add_finance_reference.py"):
                spec = importlib.util.spec_from_file_location("finance_migration", root / name)
                migration = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(migration)
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
            row = connection.exec_driver_sql("SELECT amount, status, paid_amount FROM expenses").one()
            self.assertEqual(tuple(row), (25.5, "approved", 0))
        engine.dispose()
