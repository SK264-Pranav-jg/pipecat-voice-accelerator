import asyncio
import logging
from contextlib import asynccontextmanager

import aiohttp

# fastapi imports
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

# pipecat imports
from pipecat.workers.runner import WorkerRunner
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCRequestHandler,
    SmallWebRTCRequest,
    SmallWebRTCPatchRequest,
)
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport
from pipecat.serializers.vobiz import VobizFrameSerializer, parse_vobiz_start

# backend imports
from backend.src.config.settings import settings
from backend.src.pipecat.pipeline import build_pipeline, transport_params
from backend.src.db.session import init_db, close_db
from backend.src.api.vobiz_telephony import vobiz_telephony_router, get_active_vobiz_call

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# manages SmallWebRTC peer connections (offer/answer, renegotiation, cleanup on close)
webrtc_request_handler = SmallWebRTCRequestHandler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up...")
    await init_db()
    app.state.session = aiohttp.ClientSession()
    yield
    if hasattr(app.state, "session"):
        await app.state.session.close()
    await webrtc_request_handler.close()
    await close_db()
    logger.info("Shutting down...")


# app factory
app = FastAPI(
    title="Pipecat Voice Accelerator",
    version="0.1.0",
    description="Real-time voice accelerator template application built for reusability and jumpstarting projects",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# mount telephony router
app.include_router(vobiz_telephony_router)


# runs the pipeline worker 
async def _run_worker(worker):
    """Run a single call's pipeline to completion. handle_sigint/term stay off — this
    process hosts many concurrent calls, and uvicorn already owns process signals."""
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    await runner.add_workers(worker)
    await runner.run()


@app.get("/")
async def root():
    return {"message": "Pipecat Voice Accelerator - Health check"}


@app.post("/api/offer")
async def offer(request: dict):
    """WebRTC signaling endpoint for browser calls. Builds and starts the pipeline
    only for genuinely new connections; renegotiation of an existing pc_id is handled
    internally by SmallWebRTCRequestHandler and does not re-run this callback."""
    webrtc_request = SmallWebRTCRequest.from_dict(request)

    async def on_new_connection(connection):
        req_data = webrtc_request.request_data if isinstance(webrtc_request.request_data, dict) else {}
        caller_name = req_data.get("caller_name") or None

        transport = SmallWebRTCTransport(
            webrtc_connection=connection,
            params=transport_params["webrtc"](),
        )
        worker = await build_pipeline(
            transport,
            call_id=connection.pc_id,
            transport_type="webrtc",
            provider="browser",
            caller_name=caller_name,
        )
        asyncio.create_task(_run_worker(worker))

    answer = await webrtc_request_handler.handle_web_request(webrtc_request, on_new_connection)
    return answer


@app.patch("/api/offer")
async def offer_ice_candidate(request: SmallWebRTCPatchRequest):
    """Trickle ICE candidates for an existing peer connection. The client SDK sends
    these as they're discovered, separately from the initial offer/answer above."""
    await webrtc_request_handler.handle_patch_request(request)
    return {"status": "success"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Telephony endpoint (Vobiz). Reads the Vobiz `start` event first so the
    serializer is built with the wire format Vobiz actually negotiated, rather
    than guessing from env defaults."""
    await websocket.accept()

    start_info = await parse_vobiz_start(websocket)
    stream_id = start_info["stream_id"]

    # Prefer call_id passed as query parameter in the Stream XML, fall back to start packet or streamId.
    query_call_id = websocket.query_params.get("call_id") or websocket.query_params.get("amp;call_id")
    call_id = query_call_id or start_info["call_id"] or stream_id

    # Lookup any stashed outbound call metadata
    active_call_meta = get_active_vobiz_call(call_id) if call_id else None
    caller_name = active_call_meta.get("caller_name") if active_call_meta else None
    phone_number = (
        active_call_meta.get("phone_number")
        if active_call_meta
        else (settings.vobiz_phone_number or None)
    )

    # The serializer's REST auto-hangup needs Vobiz's own call identifier, not our internal call_id
    # (which is only used for local correlation/DB keying). For outbound calls that's the call_uuid
    # returned by trigger_outbound_call; for inbound calls, fall back to whatever Vobiz's start event
    # reports as callId. The WS "stop" event half of auto-hangup still works even if this is unset.
    vobiz_call_uuid = (
        (active_call_meta.get("call_uuid") if active_call_meta else None)
        or start_info["call_id"]
        or None
    )

    negotiated_rate = start_info["sample_rate"] or settings.vobiz_sample_rate
    negotiated_encoding = start_info["encoding"] or settings.vobiz_encoding

    logger.info(
        f"[ws] Vobiz call_id={call_id} stream_id={stream_id} "
        f"declared_encoding={start_info['encoding']!r} declared_sample_rate={start_info['sample_rate']!r} "
        f"(env defaults: encoding={settings.vobiz_encoding!r} sample_rate={settings.vobiz_sample_rate!r}) "
        f"-> using encoding={negotiated_encoding!r} sample_rate={negotiated_rate}"
    )

    serializer = VobizFrameSerializer(
        stream_id=stream_id,
        call_id=vobiz_call_uuid,
        auth_id=settings.vobiz_auth_id or None,
        auth_token=settings.vobiz_auth_token.get_secret_value() or None,
        params=VobizFrameSerializer.InputParams(
            vobiz_sample_rate=negotiated_rate,
            encoding=negotiated_encoding,
        ),
    )

    vobiz_params = transport_params["vobiz"]()
    vobiz_params.serializer = serializer
    vobiz_params.audio_in_sample_rate = negotiated_rate
    vobiz_params.audio_out_sample_rate = negotiated_rate

    transport = FastAPIWebsocketTransport(websocket=websocket, params=vobiz_params)

    worker = await build_pipeline(
        transport,
        call_id=call_id,
        transport_type="websocket",
        provider="vobiz",
        caller_name=caller_name,
        phone_number=phone_number,
    )

    await _run_worker(worker)
