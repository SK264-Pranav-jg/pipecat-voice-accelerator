import logging
import uuid
from typing import Optional

import aiohttp
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.src.config.settings import settings
from backend.src.utils import vobiz

logger = logging.getLogger(__name__)

vobiz_telephony_router = APIRouter(tags=["telephony"])

# In-memory tracking of active Vobiz calls (keyed by call_id)
# Maps call_id -> {"call_uuid": str, "phone_number": str, "caller_name": Optional[str]}
_active_vobiz_calls: dict[str, dict] = {}


def get_active_vobiz_call(call_id: str) -> Optional[dict]:
    """Retrieve metadata for an active Vobiz call."""
    return _active_vobiz_calls.get(call_id)


def pop_active_vobiz_call(call_id: str) -> Optional[dict]:
    """Remove and retrieve metadata for an active Vobiz call."""
    return _active_vobiz_calls.pop(call_id, None)


class VobizOutboundCallRequest(BaseModel):
    phone_number: str = Field(
        ..., description="Destination phone number to call (E.164 format recommended)"
    )
    caller_name: Optional[str] = Field(
        default=None, description="Optional caller/recipient name for session tracking"
    )
    from_number: Optional[str] = Field(
        default=None, description="Override sender caller ID. Defaults to VOBIZ_PHONE_NUMBER."
    )


@vobiz_telephony_router.post("/vobiz/calls")
async def create_vobiz_call(payload: VobizOutboundCallRequest, request: Request):
    """Place an outbound phone call via Vobiz CPaaS.

    Generates a unique call_id, calls the Vobiz REST API with the webhook answer_url,
    and stashes the call metadata in memory. When answered, Vobiz will hit /answer,
    which returns Stream XML connecting the audio stream to /ws.
    """
    if not payload.phone_number:
        raise HTTPException(status_code=400, detail="phone_number is required")

    try:
        public_url = settings.public_url
    except ValueError as exc:
        logger.error(f"[vobiz/calls] Configuration error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    call_id = str(uuid.uuid4())
    answer_url = f"{public_url}/answer?call_id={call_id}"

    # Use persistent aiohttp ClientSession from app lifespan, or fall back to temporary session
    session = getattr(request.app.state, "session", None)
    should_close_session = False
    if session is None:
        session = aiohttp.ClientSession()
        should_close_session = True

    try:
        logger.info(f"[vobiz/calls] Initiating outbound call to {payload.phone_number} with answer_url={answer_url}")
        vobiz_res = await vobiz.trigger_outbound_call(
            session=session,
            to_number=payload.phone_number,
            answer_url=answer_url,
            from_number=payload.from_number or settings.vobiz_phone_number,
        )

        call_uuid = vobiz_res.get("request_uuid") or vobiz_res.get("call_uuid")
        _active_vobiz_calls[call_id] = {
            "call_uuid": call_uuid,
            "phone_number": payload.phone_number,
            "caller_name": payload.caller_name,
        }

        logger.info(f"[vobiz/calls] Successfully queued outbound call call_id={call_id} call_uuid={call_uuid}")
        return {
            "call_id": call_id,
            "call_uuid": call_uuid,
            "vobiz_response": vobiz_res,
        }

    except ValueError as exc:
        logger.error(f"[vobiz/calls] Configuration error: {exc}")
        raise HTTPException(status_code=500, detail=f"Configuration error: {exc}") from exc
    except Exception as exc:
        logger.exception(f"[vobiz/calls] Failed to place outbound call for call_id={call_id}: {exc}")
        raise HTTPException(status_code=502, detail=f"Failed to initiate Vobiz call: {exc}") from exc
    finally:
        if should_close_session:
            await session.close()


async def _handle_answer_webhook(
    request: Request,
    call_id: Optional[str] = None,
    CallUUID: Optional[str] = None,
):
    """Internal handler for Vobiz answer webhook."""
    effective_call_id = (
        call_id
        or request.query_params.get("call_id")
        or request.query_params.get("amp;call_id")
        or CallUUID
        or ""
    )

    if not effective_call_id and request.method == "POST":
        try:
            form = await request.form()
            effective_call_id = form.get("call_id") or form.get("CallUUID") or ""
        except Exception:
            pass

    logger.info(f"[answer] Vobiz answer webhook triggered | call_id={effective_call_id}")

    try:
        public_url = settings.public_url
        xml_content = vobiz.build_stream_xml(
            public_url=public_url,
            call_id=effective_call_id,
        )
        return Response(content=xml_content, media_type="application/xml")
    except Exception as exc:
        logger.exception(f"[answer] Failed to build answer XML: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to generate answer XML: {exc}") from exc


@vobiz_telephony_router.get("/answer", summary="Vobiz Answer Webhook (GET)")
async def vobiz_answer_get(request: Request, call_id: Optional[str] = None, CallUUID: Optional[str] = None):
    return await _handle_answer_webhook(request, call_id=call_id, CallUUID=CallUUID)


@vobiz_telephony_router.post("/answer", summary="Vobiz Answer Webhook (POST)")
async def vobiz_answer_post(request: Request, call_id: Optional[str] = None, CallUUID: Optional[str] = None):
    return await _handle_answer_webhook(request, call_id=call_id, CallUUID=CallUUID)


# additional endpoints for fallback to the answer webhook 
@vobiz_telephony_router.get("/vobiz/answer", include_in_schema=False)
@vobiz_telephony_router.post("/vobiz/answer", include_in_schema=False)
async def vobiz_answer_alias(request: Request, call_id: Optional[str] = None, CallUUID: Optional[str] = None):
    return await _handle_answer_webhook(request, call_id=call_id, CallUUID=CallUUID)


@vobiz_telephony_router.post("/vobiz/calls/{call_id}/hangup")
async def hangup_vobiz_call(call_id: str, request: Request):
    """Disconnect an active Vobiz call programmatically by call_id or call_uuid."""
    active = _active_vobiz_calls.pop(call_id, {})
    call_uuid = active.get("call_uuid") or call_id

    logger.info(f"[vobiz/hangup] Terminating call call_id={call_id} call_uuid={call_uuid}")

    session = getattr(request.app.state, "session", None)
    should_close_session = False
    if session is None:
        session = aiohttp.ClientSession()
        should_close_session = True

    try:
        res = await vobiz.disconnect_call(session, call_uuid)
        logger.info(f"[vobiz/hangup] Disconnected call_id={call_id} call_uuid={call_uuid}")
        return {
            "call_id": call_id,
            "status": "disconnect_requested",
            "vobiz_response": res,
        }
    except Exception as exc:
        logger.warning(f"[vobiz/hangup] Disconnect response for call_id={call_id}: {exc}")
        return {
            "call_id": call_id,
            "status": "disconnect_attempted",
            "detail": str(exc),
        }
    finally:
        if should_close_session:
            await session.close()
