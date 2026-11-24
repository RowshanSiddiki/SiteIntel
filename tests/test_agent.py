import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from siteintel import create_app
from siteintel.agent.models import AgentConversation
from siteintel.agent.service import run_agent
from siteintel.agent.tools import ToolArgumentError, execute_tool
from siteintel.auth.models import User
from siteintel.documents import llm
from siteintel.documents.llm import LLMError, ToolCall, ToolResponse
from siteintel.documents.settings import LLMSettings
from siteintel.extensions import db
from siteintel.ml.models import DocumentPrediction, PredictionHead
from siteintel.projects.models import (
    Document,
    Expense,
    ExpenseBill,
    Project,
    ProjectMember,
)
from siteintel.reports.models import DailyConstructionReport


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key-that-is-long-enough-for-siteintel",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SQLALCHEMY_ENGINE_OPTIONS": {},
            "WTF_CSRF_ENABLED": False,
            "REPORT_UPLOAD_ROOT": self.directory.name,
            "DOCUMENT_UPLOAD_ROOT": self.directory.name,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            users = [
                User(full_name=name, email=f"{name}@example.test", password_hash="unused")
                for name in ("owner", "viewer", "stranger")
            ]
            db.session.add_all(users)
            db.session.flush()
            self.user_ids = {user.full_name: user.id for user in users}
            project = Project(owner_id=self.user_ids["owner"], name="Agent project")
            other = Project(owner_id=self.user_ids["stranger"], name="Private project")
            db.session.add_all([project, other])
            db.session.flush()
            self.project_id = project.id
            self.other_project_id = other.id
            db.session.add(ProjectMember(
                project_id=project.id,
                user_id=self.user_ids["viewer"],
                role="viewer",
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
            session["_user_id"] = str(self.user_ids[name])
            session["_fresh"] = True

    def add_document(self, *, category="other", name="spec.pdf"):
        document = Document(
            project_id=self.project_id,
            uploaded_by_id=self.user_ids["owner"],
            file_name=name,
            storage_key=f"{self.project_id}/{name}",
            content_type="application/pdf",
            byte_size=10,
            sha256=(name.encode().hex() * 4)[:64],
            category=category,
            processing_status="completed",
        )
        db.session.add(document)
        db.session.flush()
        return document

    def post_chat(self, payload):
        return self.client.post(
            f"/api/projects/{self.project_id}/agent/chat",
            json=payload,
        )

    def test_structured_tools_run_and_conversation_history_is_persisted(self):
        responses = [
            ToolResponse(None, [ToolCall("overview-1", "get_project_overview", {})]),
            ToolResponse("This project has no daily reports yet.", []),
        ]
        with patch("siteintel.agent.service.settings.llm_settings", return_value=object()), \
             patch("siteintel.agent.service.llm.generate_with_tools", side_effect=responses) as generate:
            response = self.post_chat({"message": "Give me a project overview."})

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["message"]["content"], "This project has no daily reports yet.")
        self.assertEqual(response.json["message"]["actions"][0]["name"], "get_project_overview")
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(generate.call_args_list[0].args[2][0]["type"], "function")
        conversation_id = response.json["conversation"]["id"]
        history = self.client.get(
            f"/api/projects/{self.project_id}/agent/conversations/{conversation_id}"
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual([item["role"] for item in history.json["messages"]], ["user", "assistant"])
        self.assertEqual(
            history.json["messages"][1]["actions"][0]["result"]["name"],
            "Agent project",
        )

    def test_agent_page_renders_chat_and_conversation_history(self):
        response = self.client.get(
            f"/dashboard/projects/{self.project_id}/ai-agent/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="agent-form"', response.data)
        self.assertIn(b'id="agent-conversations"', response.data)

    def test_groq_compatible_tool_response_is_parsed_as_structured_arguments(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{
                        "message": {
                            "content": None,
                            "tool_calls": [{
                                "id": "tool-1",
                                "type": "function",
                                "function": {
                                    "name": "get_project_overview",
                                    "arguments": "{}",
                                },
                            }],
                        },
                    }],
                }).encode()

        with patch(
            "siteintel.documents.llm.urllib.request.build_opener",
        ) as build_opener:
            build_opener.return_value.open.return_value = FakeResponse()
            result = llm.generate_with_tools(
                LLMSettings("test-key", "test-model", "https://example.test/v1", 10),
                [{"role": "user", "content": "Overview"}],
                [],
            )

        self.assertEqual(result.tool_calls, [ToolCall("tool-1", "get_project_overview", {})])
        request = build_opener.return_value.open.call_args.args[0]
        self.assertEqual(
            json.loads(request.data)["messages"],
            [{"role": "user", "content": "Overview"}],
        )

    def test_rag_tool_uses_project_retrieval_and_returns_cited_source_link(self):
        with self.app.app_context():
            document = self.add_document(name="foundation-spec.pdf")
            document_id = document.id
            db.session.commit()

        from siteintel.documents.retrieval import SearchResult, SourceChunk

        search_result = SearchResult(
            question="What concrete grade is specified?",
            indexed_documents=1,
            sufficient=True,
            chunks=[SourceChunk(
                chunk_id=8,
                document_id=document_id,
                document_name="foundation-spec.pdf",
                category="other",
                page_number=3,
                text="The foundation concrete grade is C30.",
                excerpt="The foundation concrete grade is C30.",
                score=1.0,
                coverage=1.0,
            )],
        )
        responses = [
            ToolResponse(None, [ToolCall(
                "search-1",
                "search_project_documents",
                {"question": "What concrete grade is specified?"},
            )]),
            ToolResponse("The specification states C30 [1].", []),
        ]
        with patch(
            "siteintel.documents.retrieval.search_project_documents",
            return_value=search_result,
        ) as search, patch(
            "siteintel.agent.service.settings.llm_settings",
            return_value=object(),
        ), patch(
            "siteintel.agent.service.llm.generate_with_tools",
            side_effect=responses,
        ):
            response = self.post_chat({"message": "Find the concrete grade."})

        self.assertEqual(response.status_code, 200, response.json)
        search.assert_called_once()
        self.assertEqual(search.call_args.args[0], self.project_id)
        source = response.json["message"]["actions"][0]["result"]["sources"][0]
        self.assertEqual(source["number"], 1)
        self.assertIn(f"/documents/{document_id}/file#page=3", source["view_url"])

    def test_daily_report_tool_retrieves_reports_only_for_its_project(self):
        with self.app.app_context():
            document = self.add_document(name="daily-report.pdf")
            db.session.add(DailyConstructionReport(
                project_id=self.project_id,
                reporting_date=date(2026, 11, 20),
                created_by_id=self.user_ids["owner"],
                document_id=document.id,
                fields={"sections": []},
                report_text="DAILY CONSTRUCTION REPORT\nWork completed: footing pour.",
            ))
            db.session.commit()
            result = execute_tool(self.project_id, "get_daily_reports", {
                "start_date": "2026-11-20",
                "end_date": "2026-11-20",
            })

        self.assertEqual(result["total_matching"], 1)
        self.assertEqual(result["reports"][0]["reporting_date"], "2026-11-20")
        self.assertIn("footing pour", result["reports"][0]["report_text"])

    def test_progress_comparison_reports_changed_and_added_fields(self):
        with self.app.app_context():
            earlier = self.add_document(name="daily-early.pdf")
            later = self.add_document(name="daily-later.pdf")
            db.session.add_all([
                DailyConstructionReport(
                    project_id=self.project_id,
                    reporting_date=date(2026, 11, 19),
                    created_by_id=self.user_ids["owner"],
                    document_id=earlier.id,
                    fields={"sections": [{
                        "name": "Work completed",
                        "items": [{"label": "Footings", "value": "2 completed"}],
                    }]},
                    report_text="Earlier report",
                ),
                DailyConstructionReport(
                    project_id=self.project_id,
                    reporting_date=date(2026, 11, 20),
                    created_by_id=self.user_ids["owner"],
                    document_id=later.id,
                    fields={"sections": [{
                        "name": "Work completed",
                        "items": [
                            {"label": "Footings", "value": "4 completed"},
                            {"label": "Columns", "value": "1 started"},
                        ],
                    }]},
                    report_text="Later report",
                ),
            ])
            db.session.commit()
            result = execute_tool(self.project_id, "compare_project_progress", {
                "from_date": "2026-11-19",
                "to_date": "2026-11-20",
            })

        self.assertEqual(result["status"], "compared")
        self.assertEqual(result["changed"][0]["from"], "2 completed")
        self.assertEqual(result["changed"][0]["to"], "4 completed")
        self.assertEqual(result["added"], [{
            "field": "Work completed: Columns",
            "value": "1 started",
        }])

    def test_ml_tool_returns_saved_current_predictions_without_running_models(self):
        with self.app.app_context():
            document = self.add_document(name="prediction-report.pdf")
            record = DocumentPrediction(
                document_id=document.id,
                created_by_id=self.user_ids["owner"],
                model_key="cement_demand",
                input_hash="saved-inputs",
                status="complete",
                inputs={},
                missing={},
                result={"prediction": 12.5, "unit": "tonnes"},
            )
            db.session.add(record)
            db.session.flush()
            db.session.add(PredictionHead(
                document_id=document.id,
                model_key="cement_demand",
                prediction_id=record.id,
            ))
            db.session.commit()

            result = execute_tool(self.project_id, "get_ml_predictions", {
                "model_key": "cement_demand",
            })

        self.assertEqual(len(result["predictions"]), 1)
        self.assertEqual(result["predictions"][0]["result"]["prediction"], 12.5)
        self.assertIn("no model was run", result["note"])

    def test_expense_tool_calculates_payments_and_uploaded_bill_status(self):
        with self.app.app_context():
            expense = Expense(
                project_id=self.project_id,
                created_by_id=self.user_ids["owner"],
                expense_date=date(2026, 11, 18),
                category="Materials",
                description="Cement delivery",
                vendor="Supplier",
                amount=Decimal("100.00"),
                currency="USD",
                paid_amount=Decimal("40.00"),
                status="approved",
            )
            db.session.add(expense)
            db.session.flush()
            db.session.add(ExpenseBill(
                project_id=self.project_id,
                uploaded_by_id=self.user_ids["owner"],
                expense_id=expense.id,
                file_name="cement-bill.pdf",
                storage_id="bill-test",
                stored_name="bill.pdf",
                sha256="billhash",
            ))
            db.session.commit()
            result = execute_tool(self.project_id, "get_expense_summary", {
                "start_date": "2026-11-01",
                "end_date": "2026-11-30",
            })
            db.session.add(ExpenseBill(
                project_id=self.project_id,
                uploaded_by_id=self.user_ids["owner"],
                file_name="unmatched-bill.pdf",
                storage_id="bill-test-2",
                stored_name="bill-2.pdf",
                sha256="unlinked-bill",
            ))
            db.session.commit()
            all_bills = execute_tool(self.project_id, "get_expense_summary", {})

        self.assertEqual(result["expense_totals"][0]["amount"], "100.00")
        self.assertEqual(result["expense_totals"][0]["paid_amount"], "40.00")
        self.assertEqual(result["expense_totals"][0]["unpaid_amount"], "60.00")
        self.assertEqual(
            result["uploaded_bill_payment_counts"],
            [{"payment_status": "partial", "count": 1}],
        )
        self.assertEqual(
            {item["payment_status"] for item in all_bills["uploaded_bill_payment_counts"]},
            {"partial", "unlinked"},
        )

    def test_multi_step_task_executes_multiple_tools_before_answering(self):
        responses = [
            ToolResponse(None, [ToolCall("overview-1", "get_project_overview", {})]),
            ToolResponse(None, [ToolCall(
                "reports-1",
                "get_daily_reports",
                {"limit": 2},
            )]),
            ToolResponse("There are no generated reports for this project.", []),
        ]
        with patch("siteintel.agent.service.settings.llm_settings", return_value=object()), \
             patch("siteintel.agent.service.llm.generate_with_tools", side_effect=responses) as generate:
            with self.app.app_context():
                answer, actions = run_agent(self.project_id, "Summarize recent progress.")

        self.assertIn("no generated reports", answer)
        self.assertEqual([item["name"] for item in actions], [
            "get_project_overview",
            "get_daily_reports",
        ])
        self.assertEqual(generate.call_count, 3)

    def test_invalid_tool_arguments_are_rejected(self):
        with self.app.app_context():
            with self.assertRaises(ToolArgumentError):
                execute_tool(self.project_id, "get_daily_reports", {"limit": True})
            with self.assertRaises(ToolArgumentError):
                execute_tool(self.project_id, "search_project_documents", {})
            with self.assertRaises(ToolArgumentError):
                execute_tool(self.project_id, "compare_project_progress", {
                    "from_date": "2026-11-22",
                    "to_date": "2026-11-20",
                    "project_id": self.other_project_id,
                })

    def test_conversations_are_isolated_by_project_and_user(self):
        with patch(
            "siteintel.agent.service.settings.llm_settings",
            return_value=object(),
        ), patch(
            "siteintel.agent.service.llm.generate_with_tools",
            return_value=ToolResponse("A grounded response.", []),
        ):
            created = self.post_chat({"message": "Project summary"})
        conversation_id = created.json["conversation"]["id"]

        self.login("viewer")
        other_user = self.client.get(
            f"/api/projects/{self.project_id}/agent/conversations/{conversation_id}"
        )
        wrong_project = self.client.get(
            f"/api/projects/{self.other_project_id}/agent/conversations"
        )
        self.assertEqual(other_user.status_code, 404)
        self.assertEqual(wrong_project.status_code, 404)

    def test_unauthenticated_requests_are_rejected(self):
        with self.client.session_transaction() as session:
            session.clear()
        response = self.client.get(f"/api/projects/{self.project_id}/agent/conversations")
        self.assertEqual(response.status_code, 401)

    def test_llm_failure_is_reported_without_creating_a_conversation(self):
        with patch("siteintel.agent.service.settings.llm_settings", return_value=object()), \
             patch(
                 "siteintel.agent.service.llm.generate_with_tools",
                 side_effect=LLMError("provider unavailable"),
             ):
            response = self.post_chat({"message": "Summarize this project."})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["error"]["code"], "agent_unavailable")
        with self.app.app_context():
            self.assertEqual(db.session.query(AgentConversation).count(), 0)

    def test_sensitive_unregistered_actions_are_not_executable(self):
        with self.app.app_context():
            expense = Expense(
                project_id=self.project_id,
                created_by_id=self.user_ids["owner"],
                expense_date=date(2026, 11, 18),
                category="Materials",
                description="Cement delivery",
                amount=Decimal("100.00"),
                currency="USD",
                paid_amount=Decimal("0.00"),
                status="pending",
            )
            db.session.add(expense)
            db.session.commit()
            with self.assertRaises(ToolArgumentError):
                execute_tool(self.project_id, "delete_expense", {"expense_id": expense.id})
            db.session.refresh(expense)
            self.assertEqual(expense.amount, Decimal("100.00"))


if __name__ == "__main__":
    unittest.main()
