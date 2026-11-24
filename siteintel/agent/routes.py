"""Authenticated API for project-scoped agent conversations."""
import logging
from datetime import datetime, timezone

from flask import jsonify, request, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from siteintel.agent import agent_bp
from siteintel.agent.models import AgentConversation
from siteintel.agent.service import run_agent
from siteintel.documents import llm
from siteintel.documents.settings import MAX_QUESTION_CHARS
from siteintel.extensions import db
from siteintel.projects.access import get_accessible_project
from siteintel.projects.models import Document

logger = logging.getLogger(__name__)
MAX_STORED_MESSAGES = 40


def _error(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


@agent_bp.before_request
def _require_project_access():
    if not current_user.is_authenticated:
        return _error("authentication_required", "Please log in to continue.", 401)
    if get_accessible_project(int(current_user.get_id()), request.view_args["project_id"]) is None:
        return _error("project_not_found", "Project not found.", 404)
    return None


def _owned_conversation(project_id: int, conversation_id: int):
    return db.session.scalar(
        db.select(AgentConversation).where(
            AgentConversation.id == conversation_id,
            AgentConversation.project_id == project_id,
            AgentConversation.user_id == int(current_user.get_id()),
        )
    )


def _conversation_summary(conversation: AgentConversation) -> dict:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
    }


def _decorate_actions(project_id: int, actions: list[dict]) -> list[dict]:
    for action in actions:
        result = action.get("result")
        if not isinstance(result, dict):
            continue
        sources = result.get("sources")
        if isinstance(sources, list):
            for source in sources:
                if not isinstance(source, dict) or not isinstance(source.get("document_id"), int):
                    continue
                document = db.session.scalar(
                    db.select(Document.id).where(
                        Document.id == source["document_id"],
                        Document.project_id == project_id,
                        Document.category.is_not(None),
                    )
                )
                if document is not None and isinstance(source.get("page_number"), int):
                    source["view_url"] = (
                        url_for(
                            "documents.view_file",
                            project_id=project_id,
                            document_id=document,
                        )
                        + f"#page={source['page_number']}"
                    )
        reports = result.get("reports")
        if isinstance(reports, list):
            for report in reports:
                if isinstance(report, dict) and isinstance(report.get("id"), int):
                    report["url"] = url_for(
                        "dashboard.daily_report_detail",
                        project_id=project_id,
                        report_id=report["id"],
                    )
        for key in ("from_report_id", "to_report_id"):
            if isinstance(result.get(key), int):
                result[key.removesuffix("_id") + "_url"] = url_for(
                    "dashboard.daily_report_detail",
                    project_id=project_id,
                    report_id=result[key],
                )
        recent_reports = result.get("recent_reports")
        if isinstance(recent_reports, list):
            for report in recent_reports:
                if isinstance(report, dict) and isinstance(report.get("id"), int):
                    report["url"] = url_for(
                        "dashboard.daily_report_detail",
                        project_id=project_id,
                        report_id=report["id"],
                    )
    return actions


@agent_bp.get("/conversations")
def list_conversations(project_id: int):
    conversations = db.session.scalars(
        db.select(AgentConversation)
        .where(
            AgentConversation.project_id == project_id,
            AgentConversation.user_id == int(current_user.get_id()),
        )
        .order_by(AgentConversation.updated_at.desc(), AgentConversation.id.desc())
        .limit(50)
    ).all()
    return jsonify({"conversations": [_conversation_summary(item) for item in conversations]})


@agent_bp.get("/conversations/<int:conversation_id>")
def get_conversation(project_id: int, conversation_id: int):
    conversation = _owned_conversation(project_id, conversation_id)
    if conversation is None:
        return _error("conversation_not_found", "Conversation not found.", 404)
    return jsonify({
        "conversation": _conversation_summary(conversation),
        "messages": conversation.messages,
    })


@agent_bp.post("/chat")
def chat(project_id: int):
    payload = request.get_json(silent=True) if request.is_json else None
    if not isinstance(payload, dict) or set(payload) - {"message", "conversation_id"}:
        return _error("invalid_request", "Send a message and optional conversation_id as JSON.", 400)
    message = payload.get("message")
    if (
        not isinstance(message, str)
        or not message.strip()
        or len(message.strip()) > MAX_QUESTION_CHARS
    ):
        return _error(
            "invalid_message",
            f"Enter a message of 1 to {MAX_QUESTION_CHARS} characters.",
            400,
        )
    conversation_id = payload.get("conversation_id")
    if conversation_id is not None and (
        isinstance(conversation_id, bool)
        or not isinstance(conversation_id, int)
        or conversation_id < 1
    ):
        return _error("invalid_conversation", "conversation_id must be a positive integer.", 400)
    conversation = (
        _owned_conversation(project_id, conversation_id)
        if conversation_id is not None else None
    )
    if conversation_id is not None and conversation is None:
        return _error("conversation_not_found", "Conversation not found.", 404)

    history = conversation.messages if conversation else []
    try:
        answer, actions = run_agent(project_id, message.strip(), history)
        actions = _decorate_actions(project_id, actions)
        now = datetime.now(timezone.utc).isoformat()
        if conversation is None:
            conversation = AgentConversation(
                project_id=project_id,
                user_id=int(current_user.get_id()),
                title=message.strip()[:160],
                messages=[],
            )
            db.session.add(conversation)
        messages = list(conversation.messages or [])
        messages.extend([
            {"role": "user", "content": message.strip(), "created_at": now},
            {
                "role": "assistant",
                "content": answer,
                "created_at": now,
                "actions": actions,
            },
        ])
        conversation.messages = messages[-MAX_STORED_MESSAGES:]
        conversation.updated_at = datetime.now(timezone.utc)
        db.session.commit()
    except llm.LLMError as error:
        logger.warning("Agent language model call failed: %s", error)
        return _error("agent_unavailable", str(error), 503)
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not save an agent conversation")
        return _error("agent_storage_error", "The conversation could not be saved.", 500)

    return jsonify({
        "conversation": _conversation_summary(conversation),
        "message": {
            "role": "assistant",
            "content": answer,
            "created_at": now,
            "actions": actions,
        },
    })
