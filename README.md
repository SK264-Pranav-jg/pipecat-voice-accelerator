# Pipecat Voice Accelerator

A real-time voice AI accelerator template built on top of [Pipecat](https://github.com/pipecat-ai/pipecat). It provides a fully wired STT to LLM to TTS pipeline with two transport paths — **WebRTC** (browser) and **Telephony via Vobiz** — along with call-session persistence, a RAG knowledge base, idle handling, and a vanilla HTML/JS test client.

Use this as a jumpstart to build your own voice AI agents without rebuilding the plumbing from scratch.

---

## Table of Contents

- [Features](#features)
- [Architecture Overview](#architecture-overview)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
  - [Using uv (Recommended)](#using-uv-recommended)
  - [Using pip](#using-pip)
- [Configuration](#configuration)
- [Running the Application](#running-the-application)
  - [Backend](#backend)
  - [Frontend (Test Client)](#frontend-test-client)
- [API Reference](#api-reference)
- [Supported Providers](#supported-providers)
- [Key Modules](#key-modules)
- [Environment Variables Reference](#environment-variables-reference)
- [Troubleshooting](#troubleshooting)

---

## Features

- **Pluggable STT** — ElevenLabs, Sarvam, Cartesia, Deepgram (switch via a single env var)
- **Pluggable TTS** — ElevenLabs, Sarvam, Cartesia, Deepgram (same pattern)
- **LLM** — AWS Bedrock (Claude / Titan / etc.); Google Gemini
- **Dual transport** — SmallWebRTC for browser calls, Vobiz WebSocket for telephony
- **VAD** — Silero VAD with tunable confidence, start/stop timing, and volume gating
- **Noise filtering** — RNNoise on WebRTC path; can be enabled for Vobiz too
- **RAG / Knowledge base** — pgvector-backed retrieval via AWS Bedrock embeddings
- **Persistent transcripts** — Every call and its turns are stored in Postgres via asyncpg
- **Idle handling** — Configurable silence reminders with final hangup fallback
- **Latency observability** — Per-turn STT to LLM to TTS breakdown logged automatically
- **Test client** — No-build HTML + JS frontend using Pipecat's own client SDK

---

---

## Project Structure

```
pipecat-voice-accelerator/
├── backend/
│   ├── .env.example              # Environment variable template
│   ├── .env                      # Your local environment config (not committed)
│   ├── main.py                   # Entry point — runs uvicorn
│   ├── pyproject.toml            # uv / Python project definition
│   ├── requirements.txt          # pip-compatible dependency list
│   └── src/
│       ├── api/
│       │   ├── app.py            # FastAPI app, WebRTC + WebSocket routes
│       │   └── vobiz_telephony.py  # Vobiz outbound call router
│       ├── ai/
│       │   ├── prompts/          # System prompt modules
│       │   └── tools/            # LLM tool definitions (RAG, datetime, end_call)
│       ├── config/
│       │   └── settings.py       # Pydantic settings (reads from backend/.env)
│       ├── db/
│       │   ├── models.py         # SQLAlchemy ORM models (calls, messages)
│       │   ├── session.py        # Async DB engine + init/teardown
│       │   └── repository.py     # DB access functions used by the pipeline
│       ├── helpers/              # Shared utility helpers
│       ├── pipecat/
│       │   ├── pipeline.py       # Pipeline builder — STT/LLM/TTS wiring + transport params
│       │   ├── call_session.py   # Per-call runtime state (end reason, sequence counter)
│       │   └── idle_handler.py   # Silence detection + staged reminder / hangup logic
│       └── utils/                # General utilities
└── frontend/
    ├── index.html                # Test client HTML
    ├── main.js                   # Pipecat client SDK integration + transcript logic
    ├── style.css                 # UI styling
    └── README.md                 # Frontend-specific notes
```

---

## Prerequisites

- **Python 3.13+**
- **PostgreSQL** with the **pgvector** extension (for RAG and call persistence)
- API credentials for at least one STT/TTS provider and AWS Bedrock (LLM)

---

## Installation

### Using uv (Recommended)

`uv` is the primary package manager for this project and provides faster, reproducible installs.

**Install uv:**

Windows (PowerShell):
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Linux / macOS:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Install project dependencies:**
```bash
cd pipecat-voice-accelerator/backend
uv sync
```

This reads `pyproject.toml` and `uv.lock` to create a fully reproducible virtual environment in `backend/.venv`.

---

### Using pip

If you prefer pip, a `requirements.txt` is included:

```bash
cd pipecat-voice-accelerator/backend
pip install -r requirements.txt
```

---

## Configuration

The backend reads all configuration from `backend/.env`. Copy the example file and fill in the values for the providers you intend to use:

```bash
cp backend/.env.example backend/.env
```

Then open `backend/.env` and fill in the relevant fields. At minimum you need:

| What | Required fields |
|---|---|
| Server address | `HOST`, `PORT` |
| STT provider | `STT_PROVIDER` + the matching API key |
| TTS provider | `TTS_PROVIDER` + the matching API key and voice ID |
| LLM (AWS Bedrock) | `MAIN_MODEL_ID`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` |
| Database | `DATABASE_URL` |
| Telephony (optional) | `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN`, `VOBIZ_PHONE_NUMBER`, `WEBHOOK_URL` |

> **Note:** Only the credentials for the provider chosen via `STT_PROVIDER` / `TTS_PROVIDER` are required. Keys for providers you are not using can be left blank.

---

## Running the Application

These are **two independent servers**. You need two separate terminals.

### Backend

Run from the **project root** (`pipecat-voice-accelerator/`), not from inside `backend/`:

With uv:
```bash
uv run python -m backend.main
```

With pip / standard Python:
```bash
python -m backend.main
```

Or directly with uvicorn:
```bash
uvicorn backend.src.api.app:app --host 0.0.0.0 --port 8080 --reload
```

Confirm it is up — this should return a JSON health check:
```bash
curl http://127.0.0.1:8080/
# -> {"message":"Pipecat Voice Accelerator - Health check"}
```

> The backend uses hot-reload (`--reload`) by default during development.

---

### Frontend (Test Client)

The frontend is a no-build, static HTML/JS page. Serve it with Python's built-in HTTP server:

```bash
cd pipecat-voice-accelerator/frontend
python -m http.server 5500
```

Then open **http://127.0.0.1:5500** in your browser.

> **Do not open `index.html` directly via `file://`.** This can silently block microphone permissions and CORS requests to the backend on some browsers. Always serve it through a local HTTP server.

1. Confirm the **Backend URL** field on the page shows `http://127.0.0.1:8080` (or wherever your backend is running).
2. Click **Connect** and grant microphone permission when prompted.

The test client only exercises the WebRTC / browser path (`POST /api/offer`). The Vobiz telephony path (`/ws`) requires an actual phone call and cannot be tested from the browser.

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Health check |
| `POST` | `/api/offer` | WebRTC SDP offer — creates a new peer connection and starts the pipeline |
| `PATCH` | `/api/offer` | Trickle ICE candidates for an existing peer connection |
| `WS` | `/ws` | Vobiz telephony WebSocket — bidirectional 8 kHz audio stream |

Additional routes from the telephony router (`vobiz_telephony.py`) handle outbound call triggering and active-call lifecycle management.

---

## Supported Providers

### Speech-to-Text (`STT_PROVIDER`)

| Value | Provider | Model |
|---|---|---|
| `elevenlabs` | ElevenLabs (default) | `scribe_v2_realtime` |
| `sarvam` | Sarvam AI | `saaras:v3` |
| `cartesia` | Cartesia | `ink-whisper` |
| `deepgram` | Deepgram | configurable |

### Text-to-Speech (`TTS_PROVIDER`)

| Value | Provider | Default Model / Voice |
|---|---|---|
| `elevenlabs` | ElevenLabs (default) | `eleven_flash_v2_5` |
| `sarvam` | Sarvam AI | `bulbul:v3` / `priya` |
| `cartesia` | Cartesia | `sonic-3` |
| `deepgram` | Deepgram | `aura-2-helena-en` |

### LLM

- **AWS Bedrock** — set `MAIN_MODEL_ID` to any Bedrock model ID you have access to (e.g. `anthropic.claude-3-5-haiku-20241022-v1:0`)
- **Google Gemini** — wired in the pipeline but commented out; set `GEMINI_API_KEY` and uncomment the Gemini block in `backend/src/pipecat/pipeline.py`

---

## Key Modules

### `backend/src/config/settings.py`
Central Pydantic settings class. All environment variables are declared here with types, defaults, and descriptions. The `.env` file is anchored to `backend/.env` regardless of which directory you run from.

### `backend/src/pipecat/pipeline.py`
The core pipeline builder (`build_pipeline`). This is where you:
- Choose and configure STT / LLM / TTS services
- Tune VAD parameters (`confidence`, `start_secs`, `stop_secs`)
- Configure turn-taking strategy (`SpeechTimeoutUserTurnStopStrategy`)
- Add tools to the LLM context
- Wire transport event handlers (greeting, disconnection, transcript capture)
- Adjust transport parameters via the `transport_params` dict at the top of the file

### `backend/src/pipecat/idle_handler.py`
Handles extended silences during a call — sends configurable staged reminders and eventually ends the call if the user remains unresponsive.

### `backend/src/ai/prompts/`
System prompt modules. Modify `prompt_main.py` to change the bot's persona, behaviour, and instructions.

### `backend/src/ai/tools/`
LLM tool definitions registered in `LLMContext`:
- `get_current_datetime` — returns the current date/time to the LLM
- `create_end_call_tool` — lets the LLM end the call gracefully
- `query_knowledge_base` — RAG retrieval against the pgvector knowledge base

### `backend/src/db/`
Async Postgres persistence layer:
- **`models.py`** — `Call` and `Message` ORM models
- **`session.py`** — Async engine lifecycle (`init_db` / `close_db`)
- **`repository.py`** — `create_call`, `add_message`, `end_call` used directly in the pipeline

---

## Environment Variables Reference

```env
# Server
HOST=127.0.0.1
PORT=8080

# Provider selection — one of: elevenlabs, sarvam, cartesia, deepgram
STT_PROVIDER=elevenlabs
TTS_PROVIDER=elevenlabs

# ElevenLabs
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=

# Sarvam
SARVAM_API_KEY=

# Cartesia
CARTESIA_API_KEY=
CARTESIA_VOICE_ID=

# Deepgram
DEEPGRAM_API_KEY=

# LLM — AWS Bedrock
MAIN_MODEL_ID=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_SESSION_TOKEN=        # optional, for temporary/federated credentials
AWS_REGION=ap-south-1

# Telephony (Vobiz) — only needed for the /ws call path
VOBIZ_AUTH_ID=
VOBIZ_AUTH_TOKEN=
VOBIZ_PHONE_NUMBER=
VOBIZ_ENCODING=audio/x-mulaw
VOBIZ_SAMPLE_RATE=8000
WEBHOOK_URL=              # public HTTPS URL the Vobiz platform calls back to

# Database (Postgres + pgvector)
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/voice_accelerator
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
```

For the full list of settings and their descriptions, see [`backend/src/config/settings.py`](./backend/src/config/settings.py).

---

## Troubleshooting

### Backend will not start
- Ensure `backend/.env` exists and is populated (copy from `.env.example`).
- Run from the **project root** (`pipecat-voice-accelerator/`), not from inside `backend/`.
- Verify the Postgres connection: `DATABASE_URL` must be reachable and the `pgvector` extension must be installed in the target database.

### No audio / connection fails in browser
1. Confirm the backend is running: `curl http://127.0.0.1:8080/` must return the health check JSON. Fix this first before investigating anything else.
2. Open the browser DevTools (F12 → Console / Network). SDK or CDN errors appear here, not in the page's log panel.
3. Verify the **Backend URL** field matches exactly where the backend is listening (protocol + host + port).
4. Check nothing else is using port 8080:
   - Windows: `netstat -ano | findstr 8080`
   - Linux/macOS: `lsof -i :8080`

### Bot stops responding mid-call
- Usually caused by a bad API key or expired AWS credentials. Check the backend terminal for `[pipeline] error from` log lines.
- Confirm that the keys set in `.env` correspond to the currently selected `STT_PROVIDER` / `TTS_PROVIDER`.

### ICE / WebRTC connectivity issues across networks
No STUN servers are configured by default, which is fine for same-machine or LAN testing. For cross-network testing, add a STUN entry in `frontend/main.js`:
```js
new SmallWebRTCTransport({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] })

or go with websocket transport for the browser calls too 
```
