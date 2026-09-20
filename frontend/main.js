// Uses Pipecat's own client SDK instead of hand-rolled WebRTC signaling — it
// performs the full connection sequence the server actually expects: SDP
// offer/answer AND the post-connect data-channel "ready" handshake (client
// signals ready, server signals ready back) before the bot starts talking.
// Loaded from esm.sh (a CDN that serves npm packages as native ES modules) so
// this still needs no npm install / build step.
import { PipecatClient, RTVIEvent } from "https://esm.sh/@pipecat-ai/client-js@1.7.0";
import { SmallWebRTCTransport } from "https://esm.sh/@pipecat-ai/small-webrtc-transport@1.7.0";

const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
const connectBtn = document.getElementById("connect-btn");
const disconnectBtn = document.getElementById("disconnect-btn");
const backendUrlInput = document.getElementById("backend-url");
const remoteAudio = document.getElementById("remote-audio");
const logEl = document.getElementById("log");
const userActivityEl = document.getElementById("user-activity");
const botActivityEl = document.getElementById("bot-activity");
const transcriptEl = document.getElementById("transcript");
const transcriptEndBanner = document.getElementById("transcript-end-banner");
const copyTranscriptBtn = document.getElementById("copy-transcript-btn");
const downloadTranscriptBtn = document.getElementById("download-transcript-btn");
const clearTranscriptBtn = document.getElementById("clear-transcript-btn");

let client = null;

// ---------------------------------------------------------------------------
// Connection log
// ---------------------------------------------------------------------------

function log(message, isError = false) {
  const li = document.createElement("li");
  const time = new Date().toLocaleTimeString();
  li.textContent = `[${time}] ${message}`;
  if (isError) li.classList.add("error");
  logEl.appendChild(li);
  logEl.scrollTop = logEl.scrollHeight;
  console.log(message);
}

function setStatus(state, label) {
  statusDot.className = `dot dot-${state}`;
  statusText.textContent = label;
}

// ---------------------------------------------------------------------------
// Speaking activity indicators
// ---------------------------------------------------------------------------

function setSpeaking(el, isSpeaking) {
  el.classList.toggle("speaking", isSpeaking);
}

// ---------------------------------------------------------------------------
// Transcript rendering
//
// Turns are built from RTVI events, not raw partial text: user turns track
// interim vs. final transcription state, bot turns are bracketed by
// bot-llm-started/stopped and accumulate bot-output segments in place (keyed
// by segment_id, so a growing word-level segment updates rather than
// duplicates).
// ---------------------------------------------------------------------------

let currentUserTurnEl = null;
let currentBotTurnEl = null;
let currentBotSegments = new Map(); // segment_id -> text, in insertion order
let hasTranscriptContent = false;

function clearEmptyState() {
  if (!hasTranscriptContent) {
    transcriptEl.innerHTML = "";
    hasTranscriptContent = true;
    copyTranscriptBtn.disabled = false;
    downloadTranscriptBtn.disabled = false;
    clearTranscriptBtn.disabled = false;
  }
}

function scrollTranscriptToEnd() {
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function createTurnEl(role) {
  const div = document.createElement("div");
  div.className = `transcript-turn ${role}`;
  const label = document.createElement("span");
  label.className = "turn-label";
  label.textContent = role === "user" ? "You" : "Bot";
  const text = document.createElement("span");
  text.className = "turn-text";
  div.appendChild(label);
  div.appendChild(text);
  transcriptEl.appendChild(div);
  return div;
}

function setTurnText(turnEl, text) {
  turnEl.querySelector(".turn-text").textContent = text;
}

function resetTranscript() {
  transcriptEl.innerHTML = '<p class="transcript-empty">Connect and start talking — the live transcript will appear here.</p>';
  hasTranscriptContent = false;
  currentUserTurnEl = null;
  currentBotTurnEl = null;
  currentBotSegments = new Map();
  copyTranscriptBtn.disabled = true;
  downloadTranscriptBtn.disabled = true;
  clearTranscriptBtn.disabled = true;
  transcriptEndBanner.hidden = true;
}

function handleUserTranscript(data) {
  clearEmptyState();
  if (!currentUserTurnEl) {
    currentUserTurnEl = createTurnEl("user");
    currentUserTurnEl.classList.add("interim");
  }
  setTurnText(currentUserTurnEl, data.text);
  if (data.final) {
    currentUserTurnEl.classList.remove("interim");
    currentUserTurnEl = null;
  }
  scrollTranscriptToEnd();
}

function handleBotLlmStarted() {
  clearEmptyState();
  currentBotSegments = new Map();
  currentBotTurnEl = createTurnEl("bot");
  currentBotTurnEl.classList.add("interim");
}

function handleBotOutput(data) {
  // Only render what the bot will actually say — skip unspoken side-channel content.
  // `spoken` is a deprecated v1-protocol field that is only true on the
  // push_text_frames path (e.g. Cartesia); ElevenLabs and other word-timestamp
  // TTS services report via `will_be_spoken` instead, so that's the one to trust.
  if (data.will_be_spoken === false) return;

  clearEmptyState();
  if (!currentBotTurnEl) {
    // Bot output arrived without a preceding bot-llm-started (can happen for
    // very short/cached responses) — start a turn on demand.
    currentBotSegments = new Map();
    currentBotTurnEl = createTurnEl("bot");
    currentBotTurnEl.classList.add("interim");
  }

  const segmentId = data.segment_id ?? `_${currentBotSegments.size}`;
  currentBotSegments.set(segmentId, data.text);
  const fullText = Array.from(currentBotSegments.values()).join(" ");
  setTurnText(currentBotTurnEl, fullText);
  scrollTranscriptToEnd();
}

function handleBotLlmStopped() {
  if (currentBotTurnEl) {
    currentBotTurnEl.classList.remove("interim");
  }
  currentBotTurnEl = null;
  currentBotSegments = new Map();
}

function finalizeTranscriptOnDisconnect() {
  // Whatever was mid-flight when the call ended is still worth keeping —
  // just drop the "in progress" styling rather than discarding the text.
  if (currentUserTurnEl) {
    currentUserTurnEl.classList.remove("interim");
    currentUserTurnEl = null;
  }
  if (currentBotTurnEl) {
    currentBotTurnEl.classList.remove("interim");
    currentBotTurnEl = null;
  }
  if (hasTranscriptContent) {
    transcriptEndBanner.hidden = false;
  }
}

function transcriptAsText() {
  const turns = transcriptEl.querySelectorAll(".transcript-turn");
  return Array.from(turns)
    .map((turn) => {
      const role = turn.classList.contains("user") ? "You" : "Bot";
      const text = turn.querySelector(".turn-text").textContent.trim();
      return `${role}: ${text}`;
    })
    .filter((line) => !line.endsWith(": "))
    .join("\n");
}

copyTranscriptBtn.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(transcriptAsText());
    log("Transcript copied to clipboard");
  } catch (err) {
    log(`Copy failed: ${err.message || err}`, true);
  }
});

downloadTranscriptBtn.addEventListener("click", () => {
  const blob = new Blob([transcriptAsText()], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  a.href = url;
  a.download = `transcript-${stamp}.txt`;
  a.click();
  URL.revokeObjectURL(url);
});

clearTranscriptBtn.addEventListener("click", () => {
  resetTranscript();
});

// ---------------------------------------------------------------------------
// Connect / disconnect
// ---------------------------------------------------------------------------

async function connect() {
  connectBtn.disabled = true;
  setStatus("connecting", "Connecting...");
  resetTranscript();

  const backendUrl = backendUrlInput.value.trim().replace(/\/$/, "");
  const webrtcUrl = `${backendUrl}/api/offer`;

  client = new PipecatClient({
    transport: new SmallWebRTCTransport({
      // Same-machine/same-LAN testing works with no ICE servers. Add a STUN
      // entry here if you're testing across networks (see frontend/README.md).
      iceServers: [],
    }),
    enableMic: true,
    enableCam: false,
    callbacks: {
      onTransportStateChanged: (state) => log(`Transport state: ${state}`),
      onConnected: () => log("Client connected"),
      onBotConnected: () => log("Bot connected"),
      onBotReady: () => {
        log("Bot ready — say hello");
        setStatus("connected", "Connected — say hello");
        disconnectBtn.disabled = false;
      },
      onDisconnected: () => {
        log("Disconnected");
        setStatus("idle", "Idle");
        connectBtn.disabled = false;
        disconnectBtn.disabled = true;
        remoteAudio.srcObject = null;
        setSpeaking(userActivityEl, false);
        setSpeaking(botActivityEl, false);
        finalizeTranscriptOnDisconnect();
      },
      onError: (message) => {
        log(`Error: ${JSON.stringify(message)}`, true);
        setStatus("error", "Error — see log");
      },

      // speaking activity
      onUserStartedSpeaking: () => setSpeaking(userActivityEl, true),
      onUserStoppedSpeaking: () => setSpeaking(userActivityEl, false),
      onBotStartedSpeaking: () => setSpeaking(botActivityEl, true),
      onBotStoppedSpeaking: () => setSpeaking(botActivityEl, false),

      // live transcript
      onUserTranscript: (data) => handleUserTranscript(data),
      onBotLlmStarted: () => handleBotLlmStarted(),
      onBotOutput: (data) => handleBotOutput(data),
      onBotLlmStopped: () => handleBotLlmStopped(),
    },
  });

  client.on(RTVIEvent.TrackStarted, (track, participant) => {
    if (track.kind !== "audio" || participant?.local) return;
    log("Receiving bot audio track");
    remoteAudio.srcObject = new MediaStream([track]);
  });

  try {
    await client.connect({ webrtcUrl });
  } catch (err) {
    log(`Connect failed: ${err.message || err}`, true);
    setStatus("error", "Connect failed");
    connectBtn.disabled = false;
  }
}

async function disconnect() {
  log("Disconnecting...");
  disconnectBtn.disabled = true;
  try {
    await client?.disconnect();
  } catch (err) {
    log(`Disconnect error: ${err.message || err}`, true);
  }
  client = null;
}

connectBtn.addEventListener("click", connect);
disconnectBtn.addEventListener("click", disconnect);
