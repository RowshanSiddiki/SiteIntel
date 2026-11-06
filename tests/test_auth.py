import re
import unittest
from unittest.mock import patch

from sqlalchemy.exc import OperationalError
from werkzeug.security import check_password_hash

from siteintel import create_app
from siteintel.auth.models import User
from siteintel.extensions import db


class AuthenticationTests(unittest.TestCase):
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
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def _csrf_token(self, path="/auth/register"):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(
            r'name="csrf_token"[^>]*value="([^"]+)"|'
            r'name="csrf_token"[^>]*value="([^"]+)"',
            response.get_data(as_text=True),
        )
        self.assertIsNotNone(match)
        return next(value for value in match.groups() if value is not None)

    def _register(self, email="member@example.com", password="StrongPass123!"):
        return self.client.post(
            "/auth/register",
            data={
                "csrf_token": self._csrf_token(),
                "full_name": "Test Member",
                "email": email,
                "password": password,
                "password_confirmation": password,
            },
            follow_redirects=True,
        )

    def _login(self, email="member@example.com", password="StrongPass123!"):
        return self.client.post(
            "/auth/login",
            data={
                "csrf_token": self._csrf_token("/auth/login"),
                "email": email,
                "password": password,
            },
            follow_redirects=True,
        )

    def test_registration_persists_user_and_hashes_password(self):
        response = self._register()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Your account was created", response.data)
        with self.app.app_context():
            user = db.session.execute(
                db.select(User).where(User.email == "member@example.com")
            ).scalar_one()
            self.assertNotEqual(user.password_hash, "StrongPass123!")
            self.assertTrue(check_password_hash(user.password_hash, "StrongPass123!"))
            self.assertTrue(user.password_hash.startswith("scrypt:"))
            self.assertIsNotNone(user.created_at)
            self.assertIsNotNone(user.updated_at)

    def test_duplicate_email_is_rejected(self):
        self._register()
        response = self._register()
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"already exists", response.data)

    def test_invalid_email_is_rejected(self):
        response = self.client.post(
            "/auth/register",
            data={
                "csrf_token": self._csrf_token(),
                "full_name": "Test Member",
                "email": "not-an-email",
                "password": "StrongPass123!",
                "password_confirmation": "StrongPass123!",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"valid email", response.data)

    def test_weak_password_and_mismatch_are_rejected(self):
        weak = self.client.post(
            "/auth/register",
            data={
                "csrf_token": self._csrf_token(),
                "full_name": "Test Member",
                "email": "member@example.com",
                "password": "weak",
                "password_confirmation": "weak",
            },
        )
        self.assertEqual(weak.status_code, 400)
        self.assertIn(b"Password must be between", weak.data)

        mismatch = self.client.post(
            "/auth/register",
            data={
                "csrf_token": self._csrf_token(),
                "full_name": "Test Member",
                "email": "member@example.com",
                "password": "StrongPass123!",
                "password_confirmation": "OtherPass123!",
            },
        )
        self.assertEqual(mismatch.status_code, 400)
        self.assertIn(b"Passwords do not match", mismatch.data)

    def test_login_dashboard_and_logout(self):
        self._register()
        response = self._login()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Welcome, Test Member", response.data)
        self.assertIn(b"member@example.com", response.data)

        response = self.client.post(
            "/auth/logout",
            data={"csrf_token": self._csrf_token("/dashboard/")},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"You have been logged out", response.data)
        self.assertIn(b"Log in to SiteIntel", response.data)

    def test_incorrect_login_is_rejected(self):
        self._register()
        response = self._login(password="WrongPassword123!")
        self.assertEqual(response.status_code, 401)
        self.assertIn(b"Invalid email or password", response.data)

    def test_dashboard_redirects_when_unauthenticated(self):
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response.headers["Location"])

    def test_missing_csrf_token_rejects_registration(self):
        response = self.client.post(
            "/auth/register",
            data={
                "full_name": "Test Member",
                "email": "member@example.com",
                "password": "StrongPass123!",
                "password_confirmation": "StrongPass123!",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"could not be verified", response.data)

    def test_registration_database_error_is_handled_and_rolled_back(self):
        with patch.object(
            db.session,
            "commit",
            side_effect=OperationalError("insert", {}, Exception("db unavailable")),
        ):
            response = self.client.post(
                "/auth/register",
                data={
                    "csrf_token": self._csrf_token(),
                    "full_name": "Test Member",
                    "email": "member@example.com",
                    "password": "StrongPass123!",
                    "password_confirmation": "StrongPass123!",
                },
            )
        self.assertEqual(response.status_code, 500)
        self.assertIn(b"could not create your account", response.data)
        with self.app.app_context():
            self.assertIsNone(
                db.session.execute(
                    db.select(User).where(User.email == "member@example.com")
                ).scalar_one_or_none()
            )


if __name__ == "__main__":
    unittest.main()
