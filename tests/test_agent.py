import os
import secrets
import unittest
from unittest.mock import patch

from backend.app import create_app
from backend.modules.agent.orchestrator import handle_question
from backend.modules.agent.service import LLMAPIError, OpenAICompatibleClient
from backend.modules.agent.tools import (
    InvalidToolInput,
    calculate_construction_estimate,
    execute_tool,
)
from backend.modules.auth.models import User
from backend.shared.extensions import db


class FakeLLMClient:
    def __init__(self, tool="construction_estimate_calculation"):
        self.tool = tool
        self.explained_result = None

    def select_tool(self, question):
        return {"tool": self.tool, "reason": "Matched the request."}

    def explain_result(self, question, tool_name, result):
        self.explained_result = result
        return "The tool returned the supplied result."


class AgentToolTests(unittest.TestCase):
    def test_llm_client_reads_api_settings_from_environment(self):
        with patch.dict(
            os.environ,
            {
                "LLM_API_KEY": "test-api-key",
                "LLM_MODEL": "test-model",
                "LLM_BASE_URL": "https://llm.example.test/v1",
                "LLM_TIMEOUT_SECONDS": "10",
            },
            clear=True,
        ):
            client = OpenAICompatibleClient.from_environment()
        self.assertEqual(client._model, "test-model")
        self.assertEqual(client._base_url, "https://llm.example.test/v1")
        self.assertEqual(client._timeout, 10)

    def test_construction_estimate_uses_deterministic_formula(self):
        result = calculate_construction_estimate(
            {
                "Material_Cost": 43985,
                "Labor_Cost": 12836,
                "Profit_Rate": 10,
                "Discount_or_Markup": 1095,
            }
        )
        self.assertEqual(result["total_estimate"], "63598.10")
        self.assertIsNone(result["currency"])

    def test_financial_calculation_rejects_missing_unexpected_and_invalid_values(self):
        valid = {
            "Material_Cost": 100,
            "Labor_Cost": 50,
            "Profit_Rate": 10,
            "Discount_or_Markup": 0,
        }
        with self.assertRaises(InvalidToolInput):
            calculate_construction_estimate({**valid, "unknown": 1})
        with self.assertRaises(InvalidToolInput):
            calculate_construction_estimate({**valid, "Profit_Rate": -1})
        with self.assertRaises(InvalidToolInput):
            calculate_construction_estimate({**valid, "Material_Cost": "NaN"})

    def test_future_retrieval_receives_authenticated_user_scope(self):
        class FakeProvider:
            def __init__(self):
                self.user_id = None

            def retrieve(self, *, user_id, query, filters):
                self.user_id = user_id
                return {"matches": []}

        provider = FakeProvider()
        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": secrets.token_urlsafe(32),
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_ENGINE_OPTIONS": {},
                "SESSION_COOKIE_SECURE": False,
                "AGENT_RETRIEVAL_PROVIDERS": {"documents": provider},
            }
        )
        with app.app_context():
            result = execute_tool(
                "document_retrieval",
                inputs={},
                user_id=42,
                query="Find my agreement",
                filters={},
            )
        self.assertEqual(provider.user_id, 42)
        self.assertEqual(result["status"], "succeeded")

    def test_orchestrator_passes_actual_result_to_llm_explainer(self):
        client = FakeLLMClient()
        inputs = {
            "Material_Cost": "100.00",
            "Labor_Cost": "50.00",
            "Profit_Rate": "10",
            "Discount_or_Markup": "-5.25",
        }
        with patch(
            "backend.modules.agent.orchestrator.select_tool",
            return_value={"tool": client.tool, "reason": "test"},
        ), patch(
            "backend.modules.agent.orchestrator.explain_result",
            side_effect=lambda question, tool, result: (
                setattr(client, "explained_result", result)
                or "Calculated from the provided costs."
            ),
        ):
            result = handle_question(
                question="Calculate this construction estimate",
                inputs=inputs,
                filters={},
                user_id=10,
            )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(
            client.explained_result["total_estimate"], "159.75"
        )
        self.assertEqual(
            result["result"]["data"]["total_estimate"],
            client.explained_result["total_estimate"],
        )

    def test_prediction_without_explicit_inputs_does_not_invent_values(self):
        with patch(
            "backend.modules.agent.orchestrator.select_tool",
            return_value={"tool": "construction_cost_prediction", "reason": "test"},
        ):
            result = handle_question(
                question="Estimate construction cost",
                inputs={},
                filters={},
                user_id=10,
            )
        self.assertEqual(result["status"], "needs_input")
        self.assertIn("Material_Cost", result["required_inputs"])
        self.assertIsNone(result["result"])

    def test_llm_selection_rejects_unapproved_tools(self):
        client = OpenAICompatibleClient(
            api_key="test-key",
            model="test-model",
            base_url="https://example.test/v1",
        )
        with patch.object(
            client,
            "_complete",
            return_value='{"tool":"arbitrary_python","reason":"unsafe"}',
        ):
            with self.assertRaises(LLMAPIError):
                client.select_tool("run arbitrary code")


class AssistantRouteTests(unittest.TestCase):
    def setUp(self):
        self.llm = FakeLLMClient()
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": secrets.token_urlsafe(32),
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_ENGINE_OPTIONS": {},
                "SESSION_COOKIE_SECURE": False,
                "AGENT_LLM_CLIENT": self.llm,
            }
        )
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            user = User(
                full_name="Agent Test",
                email="agent-test@example.com",
                password_hash="test-hash",
            )
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def _csrf_headers(self):
        response = self.client.get("/api/auth/csrf")
        return {"X-CSRFToken": response.get_json()["csrf_token"]}

    def _login_session(self):
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id

    def test_assistant_route_requires_authentication(self):
        response = self.client.post(
            "/api/assistant/chat",
            json={"question": "calculate estimate"},
            headers=self._csrf_headers(),
        )
        self.assertEqual(response.status_code, 401)

    def test_authenticated_chat_returns_tool_result_and_explanation(self):
        self._login_session()
        response = self.client.post(
            "/api/assistant/chat",
            json={
                "question": "calculate estimate",
                "inputs": {
                    "Material_Cost": 100,
                    "Labor_Cost": 50,
                    "Profit_Rate": 10,
                    "Discount_or_Markup": -5,
                },
            },
            headers=self._csrf_headers(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "succeeded")
        self.assertEqual(body["result"]["data"]["total_estimate"], "160.00")
        self.assertEqual(body["response"], "The tool returned the supplied result.")

    def test_chat_rejects_unexpected_request_fields(self):
        self._login_session()
        response = self.client.post(
            "/api/assistant/chat",
            json={"question": "hello", "user_id": 1},
            headers=self._csrf_headers(),
        )
        self.assertEqual(response.status_code, 400)

    def test_chat_rejects_missing_csrf_token(self):
        self._login_session()
        response = self.client.post(
            "/api/assistant/chat", json={"question": "hello"}
        )
        self.assertEqual(response.status_code, 400)

    def test_document_search_reports_future_provider_not_configured(self):
        self._login_session()
        self.llm.tool = "document_retrieval"
        response = self.client.post(
            "/api/assistant/chat",
            json={"question": "Find my agreement"},
            headers=self._csrf_headers(),
        )
        self.assertEqual(response.status_code, 501)
        self.assertEqual(response.get_json()["status"], "integration_unavailable")


if __name__ == "__main__":
    unittest.main()
