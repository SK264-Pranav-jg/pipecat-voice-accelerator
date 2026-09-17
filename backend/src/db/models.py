"""
Schema for call metadata and conversation transcripts.

Transcripts are stored as one row per turn in `call_messages` rather than a
single growing text/JSONB column on `calls`. Postgres has no true append —
every UPDATE rewrites the row via MVCC — so appending to a blob column on
every turn would rewrite the whole transcript-so-far each time. Row-per-turn
keeps writes cheap inserts and keeps the transcript queryable.
"""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    transport_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "webrtc" | "websocket"
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g. "browser", "vobiz"
    caller_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    stt_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    tts_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")  # active|completed|failed
    end_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[list["CallMessage"]] = relationship(
        back_populates="call", cascade="all, delete-orphan"
    )


class CallMessage(Base):
    __tablename__ = "call_messages"
    __table_args__ = (
        # covers "fetch this call's transcript in order" with no extra sort step,
        # regardless of how many other calls' rows are in the table
        Index("ix_call_messages_call_id_sequence", "call_id", "sequence"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    interrupted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    call: Mapped["Call"] = relationship(back_populates="messages")
