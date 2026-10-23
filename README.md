# Pipecat Voice Accelerator

A real-time voice AI accelerator template built on [Pipecat](https://github.com/pipecat-ai/pipecat) (`pipecat-ai` 1.11). It ships a fully wired **STT → LLM → TTS** voice agent with two ways to talk to it — **WebRTC** (browser) and **phone calls via Vobiz** — plus a RAG knowledge base (pgvector or Amazon Bedrock Knowledge Bases), call and transcript persistence in Postgres, idle handling, latency logging, and a no-build HTML/JS test console.

Use it as a jumpstart for your own voice agents: most changes (providers, models, knowledge base) are a `.env` edit, and everything else lives in a small number of clearly-marked places documented in [What to change, and where](#what-to-change-and-where).

---

## Table of Contents

- [Features](#features)
- [Architecture Overview](#architecture-overview)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Application](#running-the-application)
  - [Backend](#backend)
  - [Frontend (Test Console)](#frontend-test-console)
  - [Phone Calls (Vobiz)](#phone-calls-vobiz)
- [Knowledge Base (RAG)](#knowledge-base-rag)
- [What to change, and where](#what-to-change-and-where)
  - [Config-only changes (`backend/.env`)](#config-only-changes-backendenv)
  - [Code-level changes](#code-level-changes)
- [Environment Variables Reference](#environment-variables-reference)
- [API Reference](#api-reference)
- [Database](#database)
- [Logs & Observability](#logs--observability)
- [Troubleshooting](#troubleshooting)
- [Known Limitations](#known-limitations)

---

## Features

- **Pluggable STT** — ElevenLabs, Sarvam, Cartesia, Deepgram (one env var)
- **Pluggable TTS** — ElevenLabs, Sarvam, Cartesia, Deepgram (one env var)
- **LLM** — AWS Bedrock (any model you have access to); Google Gemini wired in but commented out
- **Two transports** — SmallWebRTC for browser calls, Vobiz WebSocket for inbound/outbound phone calls
- **Pluggable knowledge base** — self-hosted pgvector *or* managed Amazon Bedrock Knowledge Base, switched with `KB_PROVIDER`
- **Spoken lookup acknowledgement** — an LLM-generated "let me check that" is spoken while the knowledge base is searched, always before the answer
- **Tools** — knowledge-base search, current date/time, and graceful end-of-call
- **Turn-taking** — Silero VAD + silence-timeout turn detection, greeting can't be talked over
- **Noise suppression** — RNNoise on both transports
- **Idle handling** — two check-ins, then a polite goodbye and hangup
- **Persistence** — every call and every turn stored in Postgres
- **Observability** — per-turn latency breakdown and startup timing in the backend logs
- **Test console** — no-build HTML/JS page with WebRTC and phone-call tabs and a live transcript

---

## Architecture Overview

```
 Browser (WebRTC)                      Phone (Vobiz)
       │  POST /api/offer                    │  Vobiz → POST /answer → Stream XML
       │                                     │  
       ▼                                     ▼
 SmallWebRTCTransport (16 kHz)     FastAPIWebsocketTransport + VobizFrameSerializer
       └──────────────────┬──────────────────┘
                          ▼
              build_pipeline()  — backend/src/pipecat/pipeline.py
  ┌─────────────────────────────────────────────────────────────────────┐
  │ transport.input()  (RNNoise filter)                                 │
  │   → STT            (STT_PROVIDER)                                   │
  │   → user aggregator (Silero VAD, turn strategy, idle, mute)         │
  │   → LLM            (AWS Bedrock, MAIN_MODEL_ID, system prompt)      │
  │        └─ tools: query_knowledge_base (KB_PROVIDER)                 │
  │                  get_current_datetime · end_call                    │
  │   → TTS            (TTS_PROVIDER)                                   │
  │   → transport.output()                                              │
  │   → assistant aggregator                                            │
  └─────────────────────────────────────────────────────────────────────┘
          │ event handlers                          │
          ▼                                         ▼
  Postgres: calls + call_messages        logs: latency, startup timing, transcript
```

- One pipeline instance per call; many calls run concurrently in one process.
- The sample rate is read from the transport (16 kHz WebRTC, 8 kHz Vobiz) and passed to STT, TTS, VAD and the pipeline, so nothing is hardcoded to one transport.
- The call is closed out in one place (`on_pipeline_finished`) whatever ended it: the `end_call` tool, the idle timeout, or the caller hanging up.

---

## Project Structure

```
pipecat-voice-accelerator/
├── README.md
├── backend/
│   ├── .env.example               # Environment variable template (copy to .env)
│   ├── main.py                    # Entry point — runs uvicorn
│   ├── pyproject.toml / uv.lock   # uv project definition (primary)
│   ├── requirements.txt           # pip-compatible dependency list
│   └── src/
│       ├── api/
│       │   ├── app.py             # FastAPI app: health, /api/offer (WebRTC), /ws (Vobiz)
│       │   └── vobiz_telephony.py # /vobiz/calls, /answer, hangup
│       ├── ai/
│       │   ├── prompts/
│       │   │   └── prompt_main.py # System prompt (persona "Jane", voice rules, tool rules)
│       │   └── tools/
│       │       ├── retrieval.py   # query_knowledge_base — pgvector backend (default)
│       │       ├── aws_kb.py      # query_knowledge_base — Amazon Bedrock Knowledge Base backend
│       │       ├── current.py     # get_current_datetime
│       │       └── end_call.py    # end_call (per-call, ends the pipeline gracefully)
│       ├── config/
│       │   ├── settings.py        # Pydantic settings — every env var lives here
│       │   └── aws.py             # Shared boto3 session + cached AWS clients
│       ├── db/
│       │   ├── models.py          # calls, call_messages
│       │   ├── session.py         # Async engine, init_db / close_db
│       │   └── repository.py      # create_call, add_message, end_call
│       ├── helpers/
│       │   ├── ingest.py          # CLI: load PDFs → chunk → embed → pgvector
│       │   └── resampler.py       # Offline utility: resample an mp3 to a target rate
│       ├── pipecat/
│       │   ├── pipeline.py        # Pipeline builder + transport params + event handlers
│       │   ├── call_session.py    # Per-call state (end reason, transcript sequence)
│       │   └── idle_handler.py    # Silence escalation (check-in → check-in → goodbye)
│       ├── utils/
│       │   └── vobiz.py           # Vobiz REST client + answer XML builder
│       └── assets/                # Sample FAQ PDF, background-ambience audio
└── frontend/
    ├── index.html / main.js / style.css   # Test console (WebRTC + Phone tabs)
    └── README.md                          # Frontend-specific notes
```

---

## Prerequisites

- **Python 3.13+** (pinned in `backend/.python-version`)
- **[uv](https://docs.astral.sh/uv/)** (recommended) or pip
- **PostgreSQL** with the **pgvector** extension available. It's needed even if you use the AWS knowledge base, because the app runs `CREATE EXTENSION IF NOT EXISTS vector` on every startup.
- **AWS account** with Bedrock access for your LLM model (and the embedding model if you use pgvector)
- API key for at least one STT/TTS provider
- For phone calls: a **Vobiz** account and a public HTTPS URL for the backend (e.g. ngrok)

---

## Installation

### Using uv (recommended)

Install uv:

```bash
# Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Install dependencies (creates `backend/.venv` from `pyproject.toml` + `uv.lock`):

```bash
cd pipecat-voice-accelerator/backend
uv sync
```

### Using pip

```bash
cd pipecat-voice-accelerator/backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## Configuration

All configuration is read from `backend/.env`. The path is fixed, so it works from any working directory.

```bash
cp backend/.env.example backend/.env
```

The minimum to get a browser call working:

| What | Required variables |
|---|---|
| STT | `STT_PROVIDER` + that provider's API key |
| TTS | `TTS_PROVIDER` + that provider's API key (+ voice ID for ElevenLabs / Cartesia) |
| LLM | `MAIN_MODEL_ID`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` |
| Database | `DATABASE_URL` |

Phone calls additionally need `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN`, `VOBIZ_PHONE_NUMBER`, `WEBHOOK_ENDPOINT`.

Settings are validated at startup. Invalid values (e.g. `KB_PROVIDER=awskb`, or `KB_PROVIDER=aws_kb` without `AWS_KB_ID`) stop the backend with a clear error instead of failing mid-call.

---

## Running the Application

The backend and the test console are **two separate processes**. Use two terminals.

### Backend

Run from the **project root** (`pipecat-voice-accelerator/`). Imports are `backend.src...`, so starting it from inside `backend/` doesn't work.

```bash
# uv — --project points uv at backend/pyproject.toml from the project root
uv run --project backend python -m backend.main

# or with the venv activated
python -m backend.main

# or uvicorn directly
uvicorn backend.src.api.app:app --host 0.0.0.0 --port 8080 --reload
```

`python -m backend.main` uses `HOST` / `PORT` from `.env` and always runs with hot reload.

Health check:

```bash
curl http://127.0.0.1:8080/
# {"message":"Pipecat Voice Accelerator - Health check"}
```

### Frontend (Test Console)

```bash
cd pipecat-voice-accelerator/frontend
python -m http.server 5500
```

Open **http://127.0.0.1:5500**. Serve it over HTTP: opening `index.html` via `file://` can silently block the mic prompt and the requests to the backend.

- **WebRTC tab:** check the **Backend URL** field, optionally enter a name (stored as `caller_name`), click **Connect**, allow the mic, and talk. The live transcript appears on the right; copy or download it before reconnecting.
- **Phone (Vobiz) tab:** enter a destination number (and optionally a recipient name / caller ID), then click **Call Phone** to place an outbound call through the backend. **Hangup** ends it. The transcript for phone calls is stored in Postgres, not shown live.

### Phone Calls (Vobiz)

1. Expose the backend publicly, e.g. `ngrok http 8080`.
2. Set `WEBHOOK_ENDPOINT` to that public URL (`https://abcd.ngrok-free.app`; a bare host gets `https://` added). Set `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN` and `VOBIZ_PHONE_NUMBER`, then restart.
3. **Outbound:** use the Phone tab, or:
   ```bash
   curl -X POST http://127.0.0.1:8080/vobiz/calls \
     -H "Content-Type: application/json" \
     -d '{"phone_number": "+911234567890", "caller_name": "Alice"}'
   ```
   Vobiz dials the number. On answer, it calls `{WEBHOOK_ENDPOINT}/answer?call_id=…`, which returns Stream XML pointing it at `wss://…/ws`, and the bot starts talking.
4. **Inbound:** in your Vobiz application, set the answer URL to `{WEBHOOK_ENDPOINT}/answer`. Inbound calls get the same flow; the Vobiz call UUID becomes the call ID.

The audio format is read from Vobiz's `start` event on the WebSocket. `VOBIZ_ENCODING` / `VOBIZ_SAMPLE_RATE` are what the answer XML asks for, and the fallback if Vobiz doesn't declare a format.

---

## Knowledge Base (RAG)

The LLM has one knowledge-base tool, `query_knowledge_base`. Two interchangeable implementations exist, selected by `KB_PROVIDER`. Both expose the same tool name, description, arguments and result format, so the system prompt and the rest of the pipeline don't change when you switch.

| | `KB_PROVIDER=pgvector` (default) | `KB_PROVIDER=aws_kb` |
|---|---|---|
| Implementation | `backend/src/ai/tools/retrieval.py` | `backend/src/ai/tools/aws_kb.py` |
| Where vectors live | Your Postgres (pgvector) | Amazon Bedrock Knowledge Base (managed) |
| Ingestion | `backend/src/helpers/ingest.py` (PDFs) | Bedrock console / data source sync (S3, web, Confluence, SharePoint, …) |
| Embeddings | `KB_EMBEDDING_MODEL_ID` via Bedrock | Configured on the KB itself |
| Required config | `DATABASE_URL` | `AWS_KB_ID` |
| Optional config | `KB_TOP_K`, `KB_TABLE_NAME` | `KB_TOP_K`, `AWS_KB_SEARCH_TYPE`, `AWS_KB_DATA_SOURCE_IDS` |

Shared behavior in both:
- A short LLM-generated acknowledgement is spoken while the search runs. It always plays before the answer and is skipped if it takes longer than 3 s.
- The search runs off the event loop and is cancelled if the caller interrupts.
- Errors are returned to the LLM as a graceful "couldn't confirm that" result instead of breaking the call.

### Option A — pgvector (default)

Ingest your PDFs (a single file or a folder, searched recursively), from the project root:

```bash
uv run --project backend python -m backend.src.helpers.ingest \
  --docs "backend/src/assets/BrandPulse Media Enterprise FAQ.pdf"

# options: --chunk-size 800  --chunk-overlap 100  --clear (drop the collection first)
```

Embeddings use `KB_EMBEDDING_MODEL_ID` (Titan Embed v2 by default) through Bedrock, in `AWS_REGION`. If you change the embedding model or `KB_TABLE_NAME`, re-ingest; vectors from different models aren't comparable.

### Option B — Amazon Bedrock Knowledge Base

1. Create a Knowledge Base in the Bedrock console, attach your data source(s) and run a **sync**.
2. In `backend/.env`:
   ```env
   KB_PROVIDER=aws_kb
   AWS_KB_ID=ABCDEFGHIJ
   # optional
   KB_TOP_K=4
   AWS_KB_SEARCH_TYPE=SEMANTIC          # or HYBRID; blank lets Bedrock choose
   AWS_KB_DATA_SOURCE_IDS=DS1,DS2       # restrict to these data sources; blank = all
   ```
3. Restart the backend. The log shows `[pipeline] aws knowledge base retrieval chosen`.

Notes:
- **No data source ID is required.** Retrieval searches every data source in the KB; data source IDs only narrow it.
- The KB must be in the **same region** the app uses (`AWS_REGION`; see the shell-override note above), and the credentials need `bedrock:Retrieve` on it.
- `HYBRID` search is only supported by some vector stores (e.g. OpenSearch Serverless). On others (e.g. S3 Vectors) Bedrock rejects it; leave it blank or use `SEMANTIC`.
- Only text results are passed to the LLM. Structured-row or image results are skipped.

---

## What to change, and where

### Config-only changes (`backend/.env`)


| Config  | Description | Read in |
|---|---|---|
| `STT_PROVIDER` | Which speech-to-text service transcribes the caller: `sarvam` (saaras:v3), `cartesia` (ink-2), `deepgram` (nova-2), or `elevenlabs` (scribe_v2_realtime). **Any other value silently falls back to ElevenLabs.** The value is also stored on each `calls` row. | `pipeline.py` → `build_pipeline()` STT block |
| `TTS_PROVIDER` | Which voice speaks the bot's replies: `sarvam` (bulbul:v3, voice `priya`), `cartesia` (sonic-3), `deepgram` (Flux, voice `flux-meena-en`), or `elevenlabs` (eleven_flash_v2_5). Unknown values fall back to ElevenLabs. Stored on each `calls` row. | `pipeline.py` → TTS block |
| `ELEVENLABS_VOICE_ID` / `CARTESIA_VOICE_ID` | The bot's voice for ElevenLabs / Cartesia TTS. Sarvam and Deepgram voices are set in code (see below). | `pipeline.py` → TTS block |
| `*_API_KEY` | Credentials for the selected STT/TTS provider. Only the selected providers' keys are used. | `pipeline.py` |
| `MAIN_MODEL_ID` | The Bedrock LLM that drives the conversation and generates the lookup acknowledgement. Use a model or inference-profile ID enabled in your region. | `pipeline.py` → `AWSBedrockLLMService` |
| `AWS_REGION` | Region for the LLM, the pgvector embeddings and the AWS knowledge base (default `ap-south-1`). | `pipeline.py`, `config/aws.py` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` | AWS credentials for everything above. | `pipeline.py`, `config/aws.py` |
| `KB_PROVIDER` | Which knowledge-base backend `query_knowledge_base` uses: `pgvector` or `aws_kb`. | `pipeline.py` → KB selection |
| `KB_TOP_K` | How many passages each lookup returns to the LLM (both backends). More context, but more tokens and slightly slower replies. | `retrieval.py`, `aws_kb.py` |
| `KB_EMBEDDING_MODEL_ID` / `KB_TABLE_NAME` | pgvector only: embedding model and collection name used by both ingestion and search. Re-ingest after changing. | `retrieval.py`, `ingest.py` |
| `AWS_KB_ID` / `AWS_KB_SEARCH_TYPE` / `AWS_KB_DATA_SOURCE_IDS` | aws_kb only: which KB is searched, the search type, and an optional data-source filter. | `aws_kb.py` |
| `VOBIZ_AUTH_ID` / `VOBIZ_AUTH_TOKEN` | Vobiz credentials for placing and hanging up calls, and for hanging up from the bot's side. | `utils/vobiz.py`, `api/app.py` |
| `VOBIZ_PHONE_NUMBER` | Default caller ID for outbound calls. Also recorded as the phone number for inbound calls. | `vobiz_telephony.py`, `app.py` |
| `VOBIZ_ENCODING` / `VOBIZ_SAMPLE_RATE` | Audio format requested in the answer XML, and the fallback if Vobiz's `start` event doesn't declare one. | `utils/vobiz.py`, `app.py` |
| `WEBHOOK_ENDPOINT` | Public base URL Vobiz uses to reach `/answer` and `/ws`. | `vobiz_telephony.py`, `utils/vobiz.py` |
| `DATABASE_URL` / `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | Postgres for call records, transcripts and pgvector. A plain `postgresql://` URL is upgraded to `postgresql+asyncpg://` automatically. | `db/session.py` |
| `HOST` / `PORT` | Address `python -m backend.main` listens on. | `backend/main.py` |

### Code-level changes

These are deliberately not env vars, because they're tuning choices you'll usually make once per project. Each is in one marked place.

**Voice & models**

| If you want to change… | Edit | Effect |
|---|---|---|
| STT / TTS model, language, speed, Sarvam voice, Deepgram voice | `backend/src/pipecat/pipeline.py` → the `if settings.stt_provider == …` / `if settings.tts_provider == …` blocks (`model=`, `voice=`, `speed=`, `pace=`, `language=`) | Different model/voice/pace for that provider |
| How text is streamed to TTS | `text_aggregation_mode=` on the ElevenLabs / Cartesia TTS services | `TOKEN` = lower latency; sentence mode = more natural prosody |
| LLM reply length cap | `pipeline.py` → `AWSBedrockLLMService.Settings(max_tokens=300)` | Longer or shorter maximum answers |
| Prompt caching (Claude / Nova on Bedrock) | Uncomment `enable_prompt_caching=True` in the same block | Lower cost and latency on long prompts |
| Use Gemini instead of Bedrock | `pipeline.py` → comment out `AWSBedrockLLMService`, uncomment the `GoogleLLMService` block, set `GEMINI_API_KEY` | Gemini drives the conversation |

**Persona & conversation**

| If you want to change… | Edit | Effect |
|---|---|---|
| Persona, tone, rules, how tools are used | `backend/src/ai/prompts/prompt_main.py` → `return_prompt()` | The bot's whole behavior; it's the LLM's system instruction |
| Opening line | `pipeline.py` → `on_pipeline_started` → `TTSSpeakFrame("Hello this is Jane how may I help you ?")` | What the bot says first. The commented `greeting_instruction` lets the LLM generate it instead. |
| Letting callers interrupt the greeting | `pipeline.py` → remove `MuteUntilFirstBotCompleteUserMuteStrategy()` from `user_mute_strategies` | Caller can barge in during the greeting |
| Lookup acknowledgement wording / timeout | `_ACK_SYSTEM_INSTRUCTION` and `_ACK_TIMEOUT_SECS` in **both** `ai/tools/retrieval.py` and `ai/tools/aws_kb.py` | How the "let me check that" line sounds, and how long to wait for it |
| When the LLM looks things up | The docstring of `query_knowledge_base` (both files). The docstring *is* the tool description the LLM sees. | When and how it searches |
| When the call ends | `ai/tools/end_call.py` docstring + the "ENDING THE CONVERSATION" section in `prompt_main.py` | How eagerly the bot hangs up |
| Default timezone for date/time | `ai/tools/current.py` → `timezone_str="Asia/Kolkata"` | What "today" / "now" mean when the caller doesn't say |
| Adding a new tool | Write `async def my_tool(params: FunctionCallParams, arg: str)` with a descriptive docstring, return via `await params.result_callback(...)`, then add it to `LLMContext(tools=[...])` in `pipeline.py` | The LLM can call it |
| Summarizing long conversations | `pipeline.py` → uncomment `assistant_params=LLMAssistantAggregatorParams(...)` | Older turns are compressed to stay within `max_context_tokens` |

**Turn-taking, silence & audio**

| If you want to change… | Edit | Effect |
|---|---|---|
| How long a pause ends the caller's turn | `pipeline.py` → `SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.3)` | Higher = fewer cut-offs mid-sentence, slower replies |
| Smarter end-of-turn detection | Swap the stop strategy for `TurnAnalyzerUserTurnStopStrategy` with `LocalSmartTurnAnalyzerV3` (already imported) | Model-based turn detection; more natural, adds per-turn compute |
| Voice activity sensitivity | `pipeline.py` → `VADParams(confidence=0.7, start_secs=0.2, stop_secs=0.2, min_volume=0.6)` | Higher confidence / volume = ignores more background noise but may miss quiet speakers |
| Silence before the bot checks in | `pipeline.py` → `user_idle_timeout=5.0` | Seconds of silence before each idle step |
| What the bot says when the caller is silent | `backend/src/pipecat/idle_handler.py` → the three messages (1st check-in, 2nd check-in, goodbye + hangup) | Wording, or number of steps before hanging up |
| "Please repeat that" when STT returns nothing | `pipeline.py` → `user_turn_stop_timeout=5.0` and the `on_user_turn_stop_timeout` message | How long to wait and what is said |
| Noise suppression | `pipeline.py` → `transport_params` → `audio_in_filter=RNNoiseFilter()` (per transport) | Remove to disable, or swap the filter |
| Background office ambience | `pipeline.py` → uncomment `AUDIO_DIR` and `audio_out_mixer=SoundfileMixer(...)`. The file's sample rate must match the transport; `helpers/resampler.py` converts mp3s. | Ambient sound mixed under the bot's voice |
| Transport sample rates | `pipeline.py` → `transport_params["webrtc"]` (16 kHz). Vobiz rates come from the call itself. | All STT/TTS/VAD components follow automatically |
| How fast a dead connection is detected | `audio_out_write_timeout_secs=3.0` in `transport_params` | Faster teardown vs tolerance for flaky networks |

**Everything else**

| If you want to change… | Edit | Effect |
|---|---|---|
| Logged events / latency output | `pipeline.py` → `on_latency_measured`, `on_latency_breakdown`, `on_startup_timing_report` handlers | What gets printed per turn |
| What is stored per call | `db/models.py` + the `create_call` / `add_message` calls in `pipeline.py` | Schema changes are applied by `create_all`, which only creates *new* tables, so altering an existing table needs a manual migration |
---

## Environment Variables Reference

Defined in [`backend/src/config/settings.py`](./backend/src/config/settings.py). Names are case-insensitive.

| Variable | Default | Required | Purpose |
|---|---|---|---|
| `HOST` | `127.0.0.1` | | Bind address for `python -m backend.main` |
| `PORT` | `8080` | | Port for `python -m backend.main` |
| `STT_PROVIDER` | `elevenlabs` | ✔ | `sarvam` \| `cartesia` \| `deepgram` \| `elevenlabs` |
| `TTS_PROVIDER` | `elevenlabs` | ✔ | `sarvam` \| `cartesia` \| `deepgram` \| `elevenlabs` |
| `ELEVENLABS_API_KEY` | — | if ElevenLabs | ElevenLabs STT/TTS |
| `ELEVENLABS_VOICE_ID` | — | if ElevenLabs TTS | ElevenLabs voice |
| `SARVAM_API_KEY` | — | if Sarvam | Sarvam STT/TTS |
| `CARTESIA_API_KEY` | — | if Cartesia | Cartesia STT/TTS |
| `CARTESIA_VOICE_ID` | — | if Cartesia TTS | Cartesia voice |
| `DEEPGRAM_API_KEY` | — | if Deepgram | Deepgram STT/TTS |
| `MAIN_MODEL_ID` | — | ✔ | Bedrock model / inference-profile ID |
| `AWS_ACCESS_KEY_ID` | — | ✔ | AWS credentials |
| `AWS_SECRET_ACCESS_KEY` | — | ✔ | AWS credentials |
| `AWS_SESSION_TOKEN` | — | | Temporary/federated credentials |
| `AWS_REGION` | `ap-south-1` (fallback) | ✔ | Region for Bedrock LLM, embeddings, AWS KB |
| `GEMINI_API_KEY` | — | if Gemini | Only used if the Gemini block is enabled |
| `DATABASE_URL` (or `POSTGRES_URL`) | `postgresql+asyncpg://postgres:1234@localhost:5432/pipecat-accelerator` | ✔ | Postgres connection |
| `DB_POOL_SIZE` | `5` | | Base connection pool size |
| `DB_MAX_OVERFLOW` | `10` | | Extra connections under load |
| `KB_PROVIDER` | `pgvector` | | `pgvector` \| `aws_kb` |
| `KB_TOP_K` | `4` | | Passages returned per lookup (both backends) |
| `KB_EMBEDDING_MODEL_ID` | `amazon.titan-embed-text-v2:0` | | pgvector: embedding model (ingest + search) |
| `KB_TABLE_NAME` | `kb_chunks` | | pgvector: collection name (rows live in `langchain_pg_embedding`) |
| `KB_EMBEDDING_DIMENSIONS` | `1024` | | Informational only, not read by the code |
| `AWS_KB_ID` | — | if `aws_kb` | Bedrock Knowledge Base ID |
| `AWS_KB_SEARCH_TYPE` | — | | `HYBRID` \| `SEMANTIC`; blank lets Bedrock choose |
| `AWS_KB_DATA_SOURCE_IDS` | — | | Comma-separated data source IDs to search; blank = all |
| `VOBIZ_AUTH_ID` | — | for phone | Vobiz account auth ID |
| `VOBIZ_AUTH_TOKEN` | — | for phone | Vobiz account auth token |
| `VOBIZ_PHONE_NUMBER` | — | for outbound | Default caller ID |
| `VOBIZ_ENCODING` | `audio/x-mulaw` | | Requested / fallback audio encoding |
| `VOBIZ_SAMPLE_RATE` | `8000` | | Requested / fallback sample rate |
| `WEBHOOK_ENDPOINT` (or `WEBHOOK_URL`) | — | for phone | Public base URL of the backend |

---

## API Reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Health check |
| `POST` | `/api/offer` | WebRTC SDP offer; starts a new browser call. Optional `request_data.caller_name`. |
| `PATCH` | `/api/offer` | Trickle ICE candidates for an existing peer connection |
| `WS` | `/ws` | Vobiz bidirectional audio stream (reached via the answer XML, not called directly) |
| `POST` | `/vobiz/calls` | Place an outbound call. Body: `{"phone_number": "+91…", "caller_name": "…", "from_number": "…"}` (only `phone_number` required). Returns `call_id`, `call_uuid`. |
| `GET` / `POST` | `/answer` (alias `/vobiz/answer`) | Vobiz answer webhook; returns Stream XML pointing to `/ws` |
| `POST` | `/vobiz/calls/{call_id}/hangup` | Hang up a call by `call_id` (or Vobiz call UUID) |

Interactive docs: `http://127.0.0.1:8080/docs`.

---

## Database

Tables are created automatically on startup (`init_db` in `db/session.py`). There are no migrations yet; `create_all` only creates missing tables.

| Table | Contents |
|---|---|
| `calls` | One row per call: `call_id`, `transport_type` (`webrtc` / `websocket`), `provider` (`browser` / `vobiz`), `caller_name`, `phone_number`, `stt_provider`, `tts_provider`, `status`, `end_reason`, `started_at`, `ended_at` |
| `call_messages` | One row per turn: `sequence`, `role` (`user` / `assistant`), `content`, `interrupted`, `created_at` |
| `langchain_pg_collection` / `langchain_pg_embedding` | pgvector knowledge base, created by ingestion (pgvector backend only) |

`end_reason` is one of `user_ended` (the bot's `end_call` tool), `idle_timeout`, `client_disconnected`, or `disconnected` (default).

Read a transcript:

```sql
SELECT m.sequence, m.role, m.content, m.interrupted
FROM call_messages m JOIN calls c ON c.id = m.call_id
WHERE c.call_id = '<call_id>'
ORDER BY m.sequence;
```

---

## Logs & Observability

The backend terminal shows:

- `[pipeline] … chosen`: which STT, TTS and knowledge-base backend this call uses
- `Total startup: …s` plus per-processor setup times: how long the call took to become ready
- `[latency] call=… turn response time: …ms` and a per-service breakdown: user-stopped-speaking to bot-started-speaking, the delay callers actually feel
- `User said : …` / `Assistant said : …`: the transcript as it happens
- `[rag] …`: knowledge-base queries, passage counts, skipped acknowledgements, errors
- `[pipeline] call=… error from …`: provider errors (bad keys, expired credentials)

---

## Troubleshooting

**Backend won't start**
- Run from the project root. With uv, use `uv run --project backend …`. Plain `uv run` from the root can't find the project (`No module named 'pipecat'`).
- A settings validation error names the bad variable (e.g. `KB_PROVIDER=aws_kb requires AWS_KB_ID to be set`).
- Postgres must be reachable, and the `vector` extension must be installable, even with `KB_PROVIDER=aws_kb`.

**No audio / browser can't connect**
1. `curl http://127.0.0.1:8080/` must return the health check. Fix this first.
2. The Backend URL field must match exactly (protocol, host, port).
3. Make sure nothing else is on port 8080 (`lsof -i :8080`, or `netstat -ano | findstr 8080` on Windows).

**Bot stops responding / errors mid-call**
- Look for `[pipeline] call=… error from` lines: usually a wrong API key or expired AWS credentials.
- Make sure the keys you set match the selected `STT_PROVIDER` / `TTS_PROVIDER`.

**AWS behaves differently from `.env`** (wrong region, model not found, KB not found)
- Shell variables override `.env`. Run `env | grep AWS_` and unset or align them.
- The model and the knowledge base must both exist in the region the app actually uses.

**Knowledge base answers "I don't have that"**
- pgvector: make sure you ingested with the same `KB_EMBEDDING_MODEL_ID` and `KB_TABLE_NAME` the app uses.
- aws_kb: make sure the data source is synced. `AWS_KB_DATA_SOURCE_IDS` must contain IDs from *that* KB. A `ValidationException … HYBRID search type is not supported` in the logs means you should use `SEMANTIC` or leave `AWS_KB_SEARCH_TYPE` blank.

**Phone call connects but no audio / call never reaches the bot**
- `WEBHOOK_ENDPOINT` must be the *current* public URL (ngrok URLs change on restart).
- Check the logs for `[answer] Vobiz answer webhook triggered` and `[ws] Vobiz call_id=…`.

**WebRTC across different networks**
- No STUN server is configured. Add one in `frontend/main.js`: `new SmallWebRTCTransport({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] })`.

---
