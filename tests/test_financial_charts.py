import unittest
from datetime import date
from types import SimpleNamespace

from siteintel.expenses import analytics
from siteintel.extensions import db
from siteintel.projects.models import Document
from siteintel.reports.models import DailyConstructionReport
from tests.test_documents import DocumentTestCase


def report(*sections):
    return SimpleNamespace(fields={"sections": [
        {"name": name, "items": [{"label": l, "value": v, "source_document_id": s} for l, v, s in items]}
        for name, items in sections]})


NOTES = ("Daily Site Report\nWork done today:\n- Column reinforcement completed.\n- Stair work ongoing.\n"
         "Workers present:\nMasons:\n1. Rahim - Present\nEnd of Day\nCompleted:\n- Column reinforcement completed\n"
         "- Shuttering completed.\nRainfall after 3 PM:\nApproximately 6 mm")


class ParserTests(unittest.TestCase):
    def test_materials_reject_noise_and_pair_quantities(self):
        r = report(("Materials and quantities", [
            ("Cement", "358 kg", 1), ("Concrete prepared", "2.5 m3", 1), ("Material", "Sand", 2),
            ("Quantity", "4 bags", 2), ("Quantity", "70@ kg", 3), ("Steel", "5@ Bags", 1),
        ]), ("Other extracted information", [("Brick", "500 pcs", 1)]))
        self.assertEqual(analytics._materials(r), {
            ("Cement", "kg"): 358.0, ("Concrete Prepared", "m3"): 2.5, ("Sand", "bag"): 4.0})

    def test_labour_counts_known_trades_only(self):
        r = report(("Other extracted information", [
            ("Mason", "6", 1), ("Helper", "8", 1), ("Total Present", "8", 1), ("Foreman", "2", 1),
            ("Electrician", "many", 1)]))
        self.assertEqual(analytics._labour(r), {"Mason": 6, "Helper": 8})

    def test_equipment_status_is_classified_not_guessed(self):
        r = report(("Equipment", [("Cutting Machine", "Needs maintenance", 1), ("Water Pump", "Operational", 1),
                                  ("Concrete Mixer", "unknown", 1), ("Cement", "Operational", 1)]))
        self.assertEqual(analytics._equipment(r), {"Cutting Machine": "attention", "Water Pump": "operational"})

    def test_weather_progress_and_deduplicated_activities(self):
        r = report(("Weather and site conditions", [("Temperature", "29°C", 1)]),
                   ("Other extracted information", [("Site notes", NOTES, 1), ("Overall progress", "Satisfactory.", 1),
                                                    ("Progress", "45%", 1)]))
        self.assertEqual(analytics._weather(r), (29.0, 6.0))
        self.assertEqual(analytics._progress(r), (3, 45.0))

    def test_missing_data_stays_missing(self):
        r = report(("Other extracted information", [("Note", "Nothing useful", 1)]))
        self.assertEqual(analytics._weather(r), (None, None))
        self.assertEqual(analytics._progress(r), (0, None))
        self.assertEqual(analytics._materials(r), {})


class ChartApiTests(DocumentTestCase):
    def add(self, day, sections, pid=None):
        pid = pid or self.pid
        with self.app.app_context():
            name = f"{day}-{pid}.pdf"
            document = Document(
                project_id=pid, uploaded_by_id=self.ids["owner"], file_name=name,
                storage_key=f"{pid}/{name}", content_type="application/pdf", byte_size=10,
                sha256=(name.encode().hex() * 4)[:64], category="other", processing_status="completed")
            db.session.add(document)
            db.session.flush()
            db.session.add(DailyConstructionReport(
                project_id=pid, reporting_date=day, report_text="x", created_by_id=self.ids["owner"],
                document_id=document.id,
                fields={"sections": [{"name": n, "items": [
                    {"label": l, "value": v, "source_document_id": 1} for l, v in items]} for n, items in sections]}))
            db.session.commit()

    def api(self, query="", pid=None):
        return self.client.get(f"/projects/{pid or self.pid}/finance/analytics.json{query}")

    def test_empty_project_returns_empty_charts(self):
        data = self.api().json
        self.assertEqual(data["kpis"]["reports"], 0)
        self.assertTrue(all(chart["series"] == [] for chart in data["charts"].values()))
        self.assertIn("Not enough report data yet", self.client.get(
            f"/static/js/expenses.js").get_data(as_text=True))

    def test_new_report_updates_charts_automatically(self):
        self.add(date(2026, 11, 18), [("Workforce", [("Mason", "5")]), ("Materials and quantities", [("Cement", "100 kg")])])
        first = self.api().json
        self.assertEqual(first["charts"]["labour"]["series"], [{"name": "Mason", "values": [5]}])
        self.add(date(2026, 11, 19), [("Workforce", [("Mason", "6"), ("Helper", "8")]),
                                      ("Weather", [("Temperature", "30°C")]),
                                      ("Equipment", [("Water Pump", "Operational")])])
        data = self.api().json
        labour = data["charts"]["labour"]
        self.assertEqual(labour["dates"], ["2026-11-18", "2026-11-19"])
        self.assertEqual({s["name"]: s["values"] for s in labour["series"]},
                         {"Helper": [None, 8], "Mason": [5, 6]})
        self.assertEqual(data["charts"]["material"]["series"][0]["values"], [100.0])
        self.assertEqual(data["charts"]["weather"]["series"][0]["values"], [30.0])
        self.assertEqual(data["charts"]["equipment"]["series"][0]["values"], [1])
        self.assertEqual(data["kpis"]["reports"], 2)
        self.assertEqual(data["kpis"]["workforce"], 14)
        self.assertEqual(self.api("?start=2026-11-19").json["kpis"]["reports"], 1)
        self.assertEqual(self.api("?start=bad").status_code, 400)
        self.assertEqual(self.api("?start=2026-12-01&end=2026-11-01").status_code, 400)

    def test_charts_are_isolated_by_project_and_permission(self):
        self.add(date(2026, 11, 19), [("Workforce", [("Mason", "6")])], pid=self.other_pid)
        self.assertEqual(self.api().json["kpis"]["reports"], 0)
        self.login("stranger")
        self.assertEqual(self.api().status_code, 404)
        self.assertEqual(self.api(pid=self.other_pid).json["kpis"]["reports"], 1)
        self.client.post("/auth/logout")
        self.assertIn(self.api(pid=self.other_pid).status_code, (302, 401))


if __name__ == "__main__":
    unittest.main()
