import datetime
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy.exc import OperationalError

from siteintel import create_app
from siteintel.auth.models import User
from siteintel.extensions import db
from siteintel.projects import ConstructionRecord, Document, Project, ProjectMember


class DashboardWorkspaceTests(unittest.TestCase):
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
            users = [User(full_name=f"{name} Person", email=f"{name}@example.test", password_hash="unused")
                     for name in ("owner", "editor", "stranger")]
            db.session.add_all(users)
            db.session.flush()
            self.ids = {user.email.split("@")[0]: user.id for user in users}
            made = {}
            for name, status, offset in (
                ("Alpha Tower", "active", 3), ("Beta Bridge", "in_progress", 2),
                ("Gamma Road", "completed", 1), ("Delta Dam", "paused", 0),
            ):
                project = Project(
                    owner_id=self.ids["owner"], name=name, status=status,
                    location=f"{name} City",
                    created_at=datetime.datetime(2025, 1, 1 + offset),
                )
                db.session.add(project)
                db.session.flush()
                made[name] = project.id
            other = Project(owner_id=self.ids["stranger"], name="Secret Site", status="active")
            db.session.add(other)
            db.session.flush()
            self.ids_by_name = made
            self.other_id = other.id
            db.session.add(ProjectMember(project_id=made["Alpha Tower"], user_id=self.ids["editor"], role="editor"))
            db.session.add(ConstructionRecord(
                project_id=made["Alpha Tower"], created_by_id=self.ids["owner"],
                work_date=datetime.date(2025, 1, 2), record_type="daily", title="Day 1",
                progress_percent=42,
            ))
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

    def test_stats_come_from_accessible_projects(self):
        html = self.client.get("/dashboard/").get_data(as_text=True)
        for label, value in (("Total Projects", 4), ("Active Projects", 1), ("In Progress", 1), ("Completed", 1)):
            self.assertRegex(html, rf"<strong>{value}</strong><span>{label}</span>")
        self.assertNotIn("Secret Site", html)
        self.login("editor")
        html = self.client.get("/dashboard/").get_data(as_text=True)
        self.assertRegex(html, r"<strong>1</strong><span>Total Projects</span>")
        self.assertIn("Alpha Tower", html)
        self.assertNotIn("Beta Bridge", html)

    def test_search_filter_and_sort(self):
        html = self.client.get("/dashboard/?q=bridge").get_data(as_text=True)
        self.assertIn("Beta Bridge", html)
        self.assertNotIn("Alpha Tower", html)
        html = self.client.get("/dashboard/?status=completed").get_data(as_text=True)
        self.assertIn("Gamma Road", html)
        self.assertNotIn("Beta Bridge", html)
        self.assertIn("No matching projects", self.client.get("/dashboard/?q=zzz").get_data(as_text=True))
        newest = self.client.get("/dashboard/?sort=newest").get_data(as_text=True)
        oldest = self.client.get("/dashboard/?sort=oldest").get_data(as_text=True)
        self.assertLess(newest.index("Alpha Tower"), newest.index("Delta Dam"))
        self.assertLess(oldest.index("Delta Dam"), oldest.index("Alpha Tower"))
        # Unknown filter values fall back to defaults instead of failing.
        self.assertEqual(self.client.get("/dashboard/?status=bogus&sort=bogus").status_code, 200)

    def test_empty_state_for_user_without_projects(self):
        with self.app.app_context():
            user = User(full_name="New Person", email="new@example.test", password_hash="unused")
            db.session.add(user)
            db.session.commit()
            self.ids["new"] = user.id
        self.login("new")
        html = self.client.get("/dashboard/").get_data(as_text=True)
        self.assertIn("No projects yet", html)
        self.assertRegex(html, r"<strong>0</strong><span>Total Projects</span>")

    def test_progress_only_shown_when_data_exists(self):
        html = self.client.get("/dashboard/").get_data(as_text=True)
        self.assertEqual(html.count("% complete"), 1)
        self.assertIn("42% complete", html)

    def test_control_room_theme_and_project_create_tile_render(self):
        response = self.client.get("/dashboard/")
        html = response.get_data(as_text=True)
        self.assertIn("control-room.css", html)
        self.assertIn('class="architecture-backdrop"', html)
        self.assertIn('class="architecture-build"', html)
        self.assertIn('class="build-crane"', html)
        self.assertIn('class="project-create-tile"', html)
        self.assertIn('title="Dashboard" aria-label="Dashboard"', html)
        self.assertIn('data-open-dialog="create-project-dialog"', html)

    def test_every_section_loads_for_owner_and_is_hidden_from_strangers(self):
        project_id = self.ids_by_name["Alpha Tower"]
        base = f"/dashboard/projects/{project_id}/"
        sections = ["", "ml-predictions/", "document-assistant/", "expenses/", "ai-agent/",
                    "communication/", "reports/", "settings/"]
        for section in sections:
            response = self.client.get(base + section)
            self.assertEqual(response.status_code, 200, section)
            self.assertIn(f'data-project-id="{project_id}"', response.get_data(as_text=True))
        self.assertEqual(self.client.get(base + "unknown/").status_code, 404)
        self.login("stranger")
        for section in sections:
            self.assertEqual(self.client.get(base + section).status_code, 404, section)

    def test_workspace_shows_only_its_own_project(self):
        html = self.client.get(f"/dashboard/projects/{self.ids_by_name['Alpha Tower']}/").get_data(as_text=True)
        self.assertIn("Alpha Tower City", html)
        self.assertNotIn("Beta Bridge", html.split('class="app-nav"', 1)[1].split("</nav>", 1)[0].replace("Alpha Tower", ""))

    def test_create_project_with_location_and_settings_update(self):
        created = self.client.post("/api/projects", json={"name": "Omega Plant", "location": "Austin, TX"})
        self.assertEqual(created.status_code, 201)
        project_id = created.json["id"] if "id" in created.json else created.json["project"]["id"]
        self.assertIn(f"/dashboard/projects/{project_id}", created.json["workspace_url"])
        updated = self.client.patch(f"/api/projects/{project_id}", json={"status": "in_progress", "location": "Dallas"})
        self.assertEqual(updated.status_code, 200)
        details = self.client.get(f"/api/projects/{project_id}").json
        self.assertEqual(details["location"], "Dallas")
        self.assertEqual(details["status"], "in_progress")
        self.assertEqual(self.client.patch(f"/api/projects/{project_id}", json={"status": "bogus"}).status_code, 400)
        self.assertEqual(self.client.post("/api/projects", json={"location": "x"}).status_code, 400)

    def test_only_owner_can_edit_settings(self):
        project_id = self.ids_by_name["Alpha Tower"]
        self.login("editor")
        self.assertEqual(self.client.patch(f"/api/projects/{project_id}", json={"name": "Hacked"}).status_code, 403)
        page = self.client.get(f"/dashboard/projects/{project_id}/settings/").get_data(as_text=True)
        self.assertNotIn("Save changes", page)
        self.login("stranger")
        self.assertEqual(self.client.patch(f"/api/projects/{project_id}", json={"name": "Hacked"}).status_code, 404)

    def test_notifications_list_unconfirmed_reports_only(self):
        html = self.client.get("/dashboard/").get_data(as_text=True)
        self.assertIn("No new notifications.", html)
        self.assertNotIn("notification-dot", html)

    def test_notification_shows_project_name_without_lazy_loading(self):
        project_id = self.ids_by_name["Alpha Tower"]
        with self.app.app_context():
            db.session.add(Document(
                project_id=project_id, uploaded_by_id=self.ids["owner"], file_name="site.pdf",
                storage_key="k/site.pdf", content_type="application/pdf", byte_size=10,
            ))
            db.session.commit()
        html = self.client.get("/dashboard/").get_data(as_text=True)
        self.assertIn("notification-dot", html)
        self.assertIn("site.pdf", html)
        self.assertIn("Alpha Tower · needs review", html)

    def test_database_error_page_makes_no_further_queries(self):
        failure = OperationalError("SELECT", {}, Exception("boom"))
        with mock.patch("siteintel.dashboard.routes.accessible_projects", side_effect=failure), \
                mock.patch("siteintel.dashboard.routes.pending_reviews",
                           side_effect=AssertionError("error page queried notifications")):
            response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 500)
        html = response.get_data(as_text=True)
        self.assertIn("A database error occurred", html)
        self.assertNotIn("Notifications", html)
        self.assertNotIn("boom", html)

    def test_error_page_survives_a_failing_template(self):
        failure = OperationalError("SELECT", {}, Exception("boom"))
        with mock.patch("siteintel.dashboard.routes.accessible_projects", side_effect=failure), \
                mock.patch("siteintel.render_template", side_effect=RuntimeError("template broke")):
            response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 500)

    def test_migration_chain_adds_project_location(self):
        config = AlembicConfig(str(Path(__file__).resolve().parents[1] / "migrations/alembic.ini"))
        config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
        scripts = ScriptDirectory.from_config(config)
        self.assertEqual(scripts.get_heads(), ["7d2e8f31b4c5"])
        revision = scripts.get_revision("6c1d4e7a9b20")
        self.assertEqual(revision.down_revision, "5b86d3042fa1")
        source = Path(revision.path).read_text()
        self.assertIn('add_column("projects", sa.Column("location"', source)
        self.assertIn('op.drop_column("projects", "location")', source)


if __name__ == "__main__":
    unittest.main()
