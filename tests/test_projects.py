import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from siteintel import create_app
from siteintel.auth.models import User
from siteintel.extensions import db
from siteintel.projects import (
    ConstructionRecord,
    Document,
    Expense,
    Project,
    ProjectMember,
    QuotationItem,
    get_accessible_project,
    get_owned_project,
)


class ProjectModelTests(unittest.TestCase):
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
        with self.app.app_context():
            with db.engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def _create_project_and_members(self):
        owner = User(
            full_name="Project Owner",
            email="owner@example.com",
            password_hash="not-used-in-this-test",
        )
        viewer = User(
            full_name="Project Viewer",
            email="viewer@example.com",
            password_hash="not-used-in-this-test",
        )
        editor = User(
            full_name="Project Editor",
            email="editor@example.com",
            password_hash="not-used-in-this-test",
        )
        stranger = User(
            full_name="Outside User",
            email="stranger@example.com",
            password_hash="not-used-in-this-test",
        )
        db.session.add_all([owner, viewer, editor, stranger])
        db.session.flush()
        project = Project(owner=owner, name="North Site")
        project.members.extend(
            [
                ProjectMember(user=viewer, role="viewer"),
                ProjectMember(user=editor, role="editor"),
            ]
        )
        db.session.add(project)
        db.session.commit()
        return project, owner, viewer, editor, stranger

    def test_project_access_is_scoped_to_owner_and_member_role(self):
        with self.app.app_context():
            project, owner, viewer, editor, stranger = (
                self._create_project_and_members()
            )

            self.assertEqual(
                get_accessible_project(owner.id, project.id),
                project,
            )
            self.assertEqual(
                get_accessible_project(owner.id, project.id, "editor"),
                project,
            )
            self.assertEqual(get_owned_project(owner.id, project.id), project)
            self.assertEqual(
                get_accessible_project(viewer.id, project.id),
                project,
            )
            self.assertIsNone(
                get_accessible_project(viewer.id, project.id, "editor")
            )
            self.assertEqual(
                get_accessible_project(editor.id, project.id, "editor"),
                project,
            )
            self.assertIsNone(
                get_accessible_project(stranger.id, project.id)
            )
            self.assertIsNone(get_owned_project(editor.id, project.id))
            self.assertIsNone(get_owned_project(stranger.id, project.id))

    def test_access_rejects_unsupported_permission(self):
        with self.app.app_context():
            with self.assertRaisesRegex(ValueError, "permission must be"):
                Project.accessible_to(1, "admin")

    def test_all_project_data_models_are_related(self):
        with self.app.app_context():
            project, owner, _, _, _ = self._create_project_and_members()
            document = Document(
                project=project,
                uploaded_by=owner,
                file_name="plan.pdf",
                storage_key="projects/1/plan.pdf",
                content_type="application/pdf",
                byte_size=128,
            )
            record = ConstructionRecord(
                project=project,
                created_by=owner,
                work_date=date(2026, 1, 21),
                record_type="daily",
                title="Foundation pour",
                worker_count=8,
                equipment_count=2,
                progress_percent=Decimal("25.00"),
            )
            expense = Expense(
                project=project,
                created_by=owner,
                expense_date=date(2026, 1, 21),
                category="materials",
                description="Cement delivery",
                amount=Decimal("1250.00"),
                currency="USD",
            )
            quote = QuotationItem(
                project=project,
                created_by=owner,
                quotation_reference="Q-100",
                item_name="Cement",
                quantity=Decimal("10.000"),
                unit="tonne",
                unit_price=Decimal("125.00"),
                currency="USD",
            )
            db.session.add_all([document, record, expense, quote])
            db.session.commit()

            self.assertEqual(project.documents, [document])
            self.assertEqual(project.construction_records, [record])
            self.assertEqual(project.expenses, [expense])
            self.assertEqual(project.quotation_items, [quote])
            self.assertEqual(owner.projects_owned, [project])

    def test_invalid_member_role_is_rejected_by_database_constraint(self):
        with self.app.app_context():
            project, _, _, _, stranger = self._create_project_and_members()
            db.session.add(
                ProjectMember(
                    project_id=project.id,
                    user_id=stranger.id,
                    role="owner",
                )
            )
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_project_deletion_is_restricted_while_data_exists(self):
        with self.app.app_context():
            project, owner, _, _, _ = self._create_project_and_members()
            db.session.add(
                Document(
                    project=project,
                    uploaded_by=owner,
                    file_name="plan.pdf",
                    storage_key="projects/1/plan.pdf",
                    content_type="application/pdf",
                    byte_size=128,
                )
            )
            db.session.commit()
            db.session.delete(project)
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()


if __name__ == "__main__":
    unittest.main()
