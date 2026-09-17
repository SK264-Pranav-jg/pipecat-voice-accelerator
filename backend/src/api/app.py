import asyncio
import logging
from contextlib import asynccontextmanager

# fastapi imports
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

# pipecat imports
from pipecat.workers.runner import WorkerRunner
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequestHandler, SmallWebRTCRequest
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport
from pipecat.serializers.vobiz import VobizFrameSerializer, parse_vobiz_start

# backend imports
from backend.src.config.settings import settings
from backend.src.pipecat.pipeline import build_pipeline, transport_params
from backend.src.db.session import init_db, close_db

# logging — without this, every logging.getLogger(...).info(...) call in the app
# (provider selection, call lifecycle, latency) is silently dropped: the root
# logger has no handler by default, so its effective level is WARNING.
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
    yield
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
        transport = SmallWebRTCTransport(
            webrtc_connection=connection,
            params=transport_params["webrtc"](),
        )
        worker = await build_pipeline(
            transport,
            call_id=connection.pc_id,
            transport_type="webrtc",
            provider="browser",
        )
        asyncio.create_task(_run_worker(worker))

    answer = await webrtc_request_handler.handle_web_request(webrtc_request, on_new_connection)
    return answer


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Telephony endpoint (Vobiz). Reads the Vobiz `start` event first so the
    serializer is built with the wire format Vobiz actually negotiated, rather
    than guessing from env defaults."""
    await websocket.accept()

    start_info = await parse_vobiz_start(websocket)
    stream_id = start_info["stream_id"]
    call_id = start_info["call_id"] or stream_id
    negotiated_rate = start_info["sample_rate"] or settings.vobiz_sample_rate

    serializer = VobizFrameSerializer(
        stream_id=stream_id,
        call_id=call_id,
        auth_id=settings.vobiz_auth_id or None,
        auth_token=settings.vobiz_auth_token.get_secret_value() or None,
        params=VobizFrameSerializer.InputParams(
            vobiz_sample_rate=negotiated_rate,
            encoding=start_info["encoding"] or settings.vobiz_encoding,
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
        phone_number=settings.vobiz_phone_number or None,
    )

    await _run_worker(worker)
