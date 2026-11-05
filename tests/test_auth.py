import secrets
import unittest

from werkzeug.security import check_password_hash

from backend.app import create_app
from backend.modules.auth.models import User
from backend.shared.extensions import db


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": secrets.token_urlsafe(32),
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

    def _csrf_headers(self):
        response = self.client.get("/api/auth/csrf")
        self.assertEqual(response.status_code, 200)
        return {"X-CSRFToken": response.get_json()["csrf_token"]}

    def _register(self, *, email="user@example.com", password="StrongPass123!"):
        return self.client.post(
            "/api/auth/register",
            json={
                "full_name": "Test User",
                "email": email,
                "password": password,
                "password_confirmation": password,
            },
            headers=self._csrf_headers(),
        )

    def _login(self, *, password="StrongPass123!"):
        return self.client.post(
            "/api/auth/login",
            json={"email": "user@example.com", "password": password},
            headers=self._csrf_headers(),
        )

    def test_successful_registration_and_hashed_password(self):
        response = self._register()
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("password_hash", response.get_json()["user"])
        with self.app.app_context():
            user = db.session.query(User).filter_by(email="user@example.com").one()
            self.assertNotEqual(user.password_hash, "StrongPass123!")
            self.assertTrue(check_password_hash(user.password_hash, "StrongPass123!"))
            self.assertTrue(user.password_hash.startswith("scrypt:"))

    def test_duplicate_registration_is_rejected(self):
        self.assertEqual(self._register().status_code, 201)
        response = self._register()
        self.assertEqual(response.status_code, 409)

    def test_invalid_email_is_rejected(self):
        response = self._register(email="not-an-email")
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.get_json()["error"]["details"])

    def test_weak_password_is_rejected(self):
        response = self._register(password="short")
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.get_json()["error"]["details"])

    def test_password_confirmation_must_match(self):
        response = self.client.post(
            "/api/auth/register",
            json={
                "full_name": "Test User",
                "email": "user@example.com",
                "password": "StrongPass123!",
                "password_confirmation": "DifferentPass123!",
            },
            headers=self._csrf_headers(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "password_confirmation", response.get_json()["error"]["details"]
        )

    def test_successful_login_and_protected_route(self):
        self.assertEqual(self._register().status_code, 201)
        self.assertEqual(self._login().status_code, 200)
        response = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["user"]["email"], "user@example.com")

    def test_incorrect_password_is_rejected(self):
        self.assertEqual(self._register().status_code, 201)
        response = self._login(password="WrongPassword123!")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["message"], "Invalid email or password.")

    def test_logout_clears_authenticated_session(self):
        self.assertEqual(self._register().status_code, 201)
        self.assertEqual(self._login().status_code, 200)
        response = self.client.post(
            "/api/auth/logout", headers=self._csrf_headers()
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_protected_route_requires_login(self):
        response = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 401)

    def test_mutating_request_requires_csrf_token(self):
        response = self.client.post(
            "/api/auth/register",
            json={
                "full_name": "Test User",
                "email": "user@example.com",
                "password": "StrongPass123!",
                "password_confirmation": "StrongPass123!",
            },
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
