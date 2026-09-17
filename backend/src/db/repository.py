""" data-access helpers for call metadata and transcript rows """
import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select

from backend.src.db.models import Call, CallMessage
from backend.src.db.session import get_session


async def create_call(
    call_id: str,
    transport_type: str,
    stt_provider: str,
    tts_provider: str,
    provider: str | None = None,
    caller_name: str | None = None,
    phone_number: str | None = None,
) -> uuid.UUID:
    async with get_session() as session:
        call = Call(
            call_id=call_id,
            transport_type=transport_type,
            provider=provider,
            caller_name=caller_name,
            phone_number=phone_number,
            stt_provider=stt_provider,
            tts_provider=tts_provider,
        )
        session.add(call)
        await session.commit()
        return call.id


async def add_message(
    call_id: uuid.UUID, sequence: int, role: str, content: str, interrupted: bool = False
) -> None:
    """Fire-and-forget target: swallow errors so a DB hiccup never breaks the call."""
    try:
        async with get_session() as session:
            session.add(
                CallMessage(
                    call_id=call_id,
                    sequence=sequence,
                    role=role,
                    content=content,
                    interrupted=interrupted,
                )
            )
            await session.commit()
    except Exception:
        logger.exception(f"[db] failed to persist {role} message for call {call_id}")


async def end_call(call_id: uuid.UUID, end_reason: str, status: str = "completed") -> None:
    try:
        async with get_session() as session:
            result = await session.execute(select(Call).where(Call.id == call_id))
            call = result.scalar_one_or_none()
            if call is None:
                return
            call.status = status
            call.end_reason = end_reason
            call.ended_at = datetime.now(timezone.utc)
            await session.commit()
    except Exception:
        logger.exception(f"[db] failed to close out call {call_id}")
