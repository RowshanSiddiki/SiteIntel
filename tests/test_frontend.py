from pathlib import Path
import secrets
import threading
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from werkzeug.serving import make_server

from backend.app import create_app
from backend.shared.extensions import db
from frontend.api_client import APIClient, APIError


class APIClientIntegrationTests(unittest.TestCase):
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
        with self.app.app_context():
            db.create_all()

        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.server_thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.server_thread.start()
        self.addCleanup(self._stop_server)
        self.api = APIClient(f"http://127.0.0.1:{self.server.server_port}")

    def _stop_server(self):
        self.server.shutdown()
        self.server_thread.join(timeout=5)
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def test_api_client_preserves_csrf_and_authenticated_cookies(self):
        registration = self.api.register(
            full_name="Test User",
            email="frontend@example.com",
            password="StrongPass123!",
            password_confirmation="StrongPass123!",
        )
        self.assertIn("user", registration)

        login = self.api.login(
            email="frontend@example.com", password="StrongPass123!"
        )
        self.assertEqual(login["user"]["email"], "frontend@example.com")
        self.assertEqual(
            self.api.current_user()["email"], "frontend@example.com"
        )

        self.api.logout()
        with self.assertRaises(APIError) as error:
            self.api.current_user()
        self.assertEqual(error.exception.status_code, 401)

    def test_api_client_translates_backend_validation_errors(self):
        with self.assertRaises(APIError) as error:
            self.api.login(email="not-an-email", password="bad")
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(error.exception.details["email"], "Enter a valid email address.")

    def test_registration_validation_details_are_preserved(self):
        with self.assertRaises(APIError) as error:
            self.api.register(
                full_name="A",
                email="not-an-email",
                password="short",
                password_confirmation="short",
            )
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(str(error.exception), "Invalid registration data.")
        self.assertIn("full_name", error.exception.details)
        self.assertIn("email", error.exception.details)
        self.assertIn("password", error.exception.details)

    def test_duplicate_registration_has_clear_backend_message(self):
        registration = {
            "full_name": "Test User",
            "email": "duplicate@example.com",
            "password": "StrongPass123!",
            "password_confirmation": "StrongPass123!",
        }
        self.api.register(**registration)
        with self.assertRaises(APIError) as error:
            self.api.register(**registration)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(
            str(error.exception), "An account with this email already exists."
        )

    def test_registration_page_displays_validation_details(self):
        error = APIError(
            "Invalid registration data.",
            status_code=400,
            details={"password": "Password must be between 12 and 128 characters."},
        )
        app_path = Path(__file__).resolve().parents[1] / "frontend" / "app.py"
        with patch.object(APIClient, "register", side_effect=error):
            app_test = AppTest.from_file(str(app_path)).run()
            app_test.sidebar.button[1].click().run()
            for element, value in zip(
                app_test.text_input,
                ["Test User", "valid@example.com", "short", "short"],
                strict=True,
            ):
                element.set_value(value)
            app_test.form_submit_button[0].click().run()

        self.assertFalse(app_test.exception)
        self.assertIn("Invalid registration data.", [item.value for item in app_test.error])
        self.assertIn(
            "Password: Password must be between 12 and 128 characters.",
            [item.value for item in app_test.error],
        )

    def test_dashboard_does_not_invent_unavailable_statistics(self):
        with self.assertRaises(APIError) as error:
            self.api.dashboard_stats()
        self.assertEqual(error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
