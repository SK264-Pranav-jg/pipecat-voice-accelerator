"""
Vobiz CPaaS Telephony REST API Client.

Client helper for dispatching outbound phone calls and managing SIP call state via Vobiz REST API.
"""
import json
import logging
import xml.sax.saxutils
import aiohttp

from backend.src.config.settings import settings

logger = logging.getLogger(__name__)


async def trigger_outbound_call(
    session: aiohttp.ClientSession,
    to_number: str,
    answer_url: str,
    from_number: str | None = None,
) -> dict:
    """Place an outbound call via Vobiz's REST API.

    Args:
        session: Active aiohttp ClientSession.
        to_number: Destination phone number to dial.
        answer_url: Webhook URL Vobiz hits when the call is answered.
        from_number: Sender caller ID number. Defaults to settings.vobiz_phone_number.

    Returns:
        JSON response dict from Vobiz API containing call_uuid / request_uuid.
    """
    auth_id = settings.vobiz_auth_id
    auth_token = settings.vobiz_auth_token.get_secret_value()
    caller_id = from_number or settings.vobiz_phone_number

    if not (auth_id and auth_token):
        raise ValueError("Vobiz auth id and auth token must be configured in the environment")
    if not caller_id:
        raise ValueError(
            "You must provide either from_number to trigger_outbound_call or set VOBIZ_PHONE_NUMBER in the environment"
        )

    url = f"https://api.vobiz.ai/api/v1/Account/{auth_id}/Call/"
    headers = {
        "Content-Type": "application/json",
        "X-Auth-ID": auth_id,
        "X-Auth-Token": auth_token,
    }
    payload = {
        "to": to_number,
        "from": caller_id,
        "answer_url": answer_url,
        "answer_method": "POST",
    }

    logger.info(f"[vobiz] Initiating call to {to_number} from {caller_id} with answer_url={answer_url}")

    try:
        async with session.post(url, headers=headers, json=payload) as resp:
            text = await resp.text()
            logger.info(f"[vobiz] API response status = {resp.status} body = {text[:500]}")
            if resp.status not in (200, 201, 202):
                logger.error(f"[vobiz] API error ({resp.status}): {text}")
                raise RuntimeError(f"Vobiz API error ({resp.status}): {text}")
            data = json.loads(text)
            logger.info(f"[vobiz] Call initiated: {data}")
            return data
    except aiohttp.ClientError as e:
        logger.error(f"[vobiz] Network error calling vobiz: {e}")
        raise RuntimeError(f"Network error calling vobiz API: {e}") from e
    except json.JSONDecodeError as e:
        logger.error(f"[vobiz] Invalid json response from Vobiz: {e}")
        raise RuntimeError(f"Invalid Json response from vobiz: {e}") from e


async def disconnect_call(
    session: aiohttp.ClientSession,
    call_uuid: str,
) -> dict:
    """Disconnect an active Vobiz call via the call UUID."""
    auth_id = settings.vobiz_auth_id
    auth_token = settings.vobiz_auth_token.get_secret_value()

    if not (auth_id and auth_token):
        raise ValueError("Vobiz auth id and auth token must be configured in the environment")

    url = f"https://api.vobiz.ai/api/v1/Account/{auth_id}/Call/{call_uuid}/"
    headers = {
        "X-Auth-ID": auth_id,
        "X-Auth-Token": auth_token,
    }

    logger.info(f"[vobiz] Terminating call with uuid: {call_uuid}")
    async with session.delete(url, headers=headers) as resp:
        text = await resp.text()
        if resp.status == 404:
            logger.warning(f"[vobiz] Call with uuid {call_uuid} not found or already terminated")
            return {"message": "call not found or already terminated"}
        if resp.status not in (200, 201, 202):
            logger.info(f"[vobiz] Call disconnect failed (status={resp.status} body={text})")
            raise RuntimeError(f"Vobiz disconnect error ({resp.status}): {text}")
        return json.loads(text) if text else {}


def build_stream_xml(
    public_url: str,
    call_id: str,
    sample_rate: int = 8000,
    encoding: str = "audio/x-mulaw",
) -> str:
    """Build the Vobiz Answer XML instructing Vobiz to connect the call to our WebSocket.

    Args:
        public_url: Base HTTP/HTTPS URL (e.g. https://abcd.ngrok-free.app).
        call_id: Call correlation id to append to the WebSocket URL.
        sample_rate: Wire sample rate override (defaults to 8000).
        encoding: Audio encoding override (defaults to "audio/x-mulaw").
    """
    ws_base = public_url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")
    raw_ws_url = f"{ws_base}/ws?provider=vobiz&call_id={call_id}" if call_id else f"{ws_base}/ws?provider=vobiz"
    ws_url = xml.sax.saxutils.escape(raw_ws_url)

    wire_encoding = settings.vobiz_encoding or encoding
    wire_rate = settings.vobiz_sample_rate or sample_rate
    content_type = f"{wire_encoding};rate={wire_rate}"

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Response>\n'
        f'    <Stream bidirectional="true" audioTrack="inbound" contentType="{content_type}" keepCallAlive="true">\n'
        f'        {ws_url}\n'
        '    </Stream>\n'
        '</Response>'
    )
