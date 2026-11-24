from sqlalchemy import DateTime, ForeignKey, Index, String, func

from siteintel.extensions import db


class AgentConversation(db.Model):
    __tablename__ = "agent_conversations"
    __table_args__ = (
        Index("ix_agent_conversations_project_user", "project_id", "user_id"),
        Index("ix_agent_conversations_updated_at", "updated_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(
        db.Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title = db.Column(String(160), nullable=False)
    messages = db.Column(db.JSON, nullable=False, default=list)
    created_at = db.Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = db.Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
