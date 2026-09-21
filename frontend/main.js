// Uses Pipecat's own client SDK instead of hand-rolled WebRTC signaling — it
// performs the full connection sequence the server actually expects: SDP
// offer/answer AND the post-connect data-channel "ready" handshake (client
// signals ready, server signals ready back) before the bot starts talking.
// Loaded from esm.sh (a CDN that serves npm packages as native ES modules) so
// this still needs no npm install / build step.
import { PipecatClient, RTVIEvent } from "https://esm.sh/@pipecat-ai/client-js@1.7.0";
import { SmallWebRTCTransport } from "https://esm.sh/@pipecat-ai/small-webrtc-transport@1.7.0";

// Mode switcher elements
const tabWebRtc = document.getElementById("tab-webrtc");
const tabVobiz = document.getElementById("tab-vobiz");
const panelWebRtc = document.getElementById("panel-webrtc");
const panelVobiz = document.getElementById("panel-vobiz");

// WebRTC mode elements
const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
const webrtcCallerNameInput = document.getElementById("webrtc-caller-name");
const connectBtn = document.getElementById("connect-btn");
const disconnectBtn = document.getElementById("disconnect-btn");
const remoteAudio = document.getElementById("remote-audio");
const userActivityEl = document.getElementById("user-activity");
const botActivityEl = document.getElementById("bot-activity");

// Vobiz telephony mode elements
const vobizStatusDot = document.getElementById("vobiz-status-dot");
const vobizStatusText = document.getElementById("vobiz-status-text");
const vobizPhoneInput = document.getElementById("vobiz-phone");
const vobizCallerNameInput = document.getElementById("vobiz-caller-name");
const vobizFromNumberInput = document.getElementById("vobiz-from-number");
const vobizCallBtn = document.getElementById("vobiz-call-btn");
const vobizHangupBtn = document.getElementById("vobiz-hangup-btn");
const vobizCallInfoBox = document.getElementById("vobiz-call-info");
const infoCallId = document.getElementById("info-call-id");
const infoCallUuid = document.getElementById("info-call-uuid");

// Shared & Transcript elements
const backendUrlInput = document.getElementById("backend-url");
const logEl = document.getElementById("log");
const transcriptEl = document.getElementById("transcript");
const transcriptEndBanner = document.getElementById("transcript-end-banner");
const copyTranscriptBtn = document.getElementById("copy-transcript-btn");
const downloadTranscriptBtn = document.getElementById("download-transcript-btn");
const clearTranscriptBtn = document.getElementById("clear-transcript-btn");

let client = null;
let activeVobizCallId = null;

// ---------------------------------------------------------------------------
// Mode switching (WebRTC vs Telephony)
// ---------------------------------------------------------------------------

function setMode(mode) {
  if (mode === "webrtc") {
    tabWebRtc.classList.add("active");
    tabWebRtc.setAttribute("aria-selected", "true");
    tabVobiz.classList.remove("active");
    tabVobiz.setAttribute("aria-selected", "false");
    panelWebRtc.classList.add("active");
    panelVobiz.classList.remove("active");
    log("Switched to Browser (WebRTC) mode");
  } else {
    tabVobiz.classList.add("active");
    tabVobiz.setAttribute("aria-selected", "true");
    tabWebRtc.classList.remove("active");
    tabWebRtc.setAttribute("aria-selected", "false");
    panelVobiz.classList.add("active");
    panelWebRtc.classList.remove("active");
    log("Switched to Telephony (Vobiz Outbound) mode");
  }
}

tabWebRtc.addEventListener("click", () => setMode("webrtc"));
tabVobiz.addEventListener("click", () => setMode("vobiz"));

// ---------------------------------------------------------------------------
// Connection log & status
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

function setVobizStatus(state, label) {
  vobizStatusDot.className = `dot dot-${state}`;
  vobizStatusText.textContent = label;
}

// ---------------------------------------------------------------------------
// Speaking activity indicators
// ---------------------------------------------------------------------------

function setSpeaking(el, isSpeaking) {
  el.classList.toggle("speaking", isSpeaking);
}

// ---------------------------------------------------------------------------
// Transcript rendering
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
  if (data.will_be_spoken === false) return;

  clearEmptyState();
  if (!currentBotTurnEl) {
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
// Browser WebRTC Connect / Disconnect
// ---------------------------------------------------------------------------

async function connect() {
  connectBtn.disabled = true;
  setStatus("connecting", "Connecting...");
  resetTranscript();

  const backendUrl = backendUrlInput.value.trim().replace(/\/$/, "");
  const webrtcUrl = `${backendUrl}/api/offer`;
  const callerName = webrtcCallerNameInput.value.trim();

  client = new PipecatClient({
    transport: new SmallWebRTCTransport({
      iceServers: [],
    }),
    enableMic: true,
    enableCam: false,
    callbacks: {
      onTransportStateChanged: (state) => log(`[WebRTC] Transport state: ${state}`),
      onConnected: () => log("[WebRTC] Client connected"),
      onBotConnected: () => log("[WebRTC] Bot connected"),
      onBotReady: () => {
        log("[WebRTC] Bot ready — say hello");
        setStatus("connected", "Connected — say hello");
        disconnectBtn.disabled = false;
      },
      onDisconnected: () => {
        log("[WebRTC] Disconnected");
        setStatus("idle", "Idle");
        connectBtn.disabled = false;
        disconnectBtn.disabled = true;
        remoteAudio.srcObject = null;
        setSpeaking(userActivityEl, false);
        setSpeaking(botActivityEl, false);
        finalizeTranscriptOnDisconnect();
      },
      onError: (message) => {
        log(`[WebRTC] Error: ${JSON.stringify(message)}`, true);
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
    log("[WebRTC] Receiving bot audio track");
    remoteAudio.srcObject = new MediaStream([track]);
  });

  try {
    const requestData = callerName ? { caller_name: callerName } : undefined;
    await client.connect({ webrtcUrl, requestData });
    if (callerName) {
      log(`[WebRTC] Joining as caller: "${callerName}"`);
    }
  } catch (err) {
    log(`[WebRTC] Connect failed: ${err.message || err}`, true);
    setStatus("error", "Connect failed");
    connectBtn.disabled = false;
  }
}

async function disconnect() {
  log("[WebRTC] Disconnecting...");
  disconnectBtn.disabled = true;
  try {
    await client?.disconnect();
  } catch (err) {
    log(`[WebRTC] Disconnect error: ${err.message || err}`, true);
  }
  client = null;
}

connectBtn.addEventListener("click", connect);
disconnectBtn.addEventListener("click", disconnect);

// ---------------------------------------------------------------------------
// Vobiz Telephony (Outbound Call & Hangup)
// ---------------------------------------------------------------------------

async function placeVobizCall() {
  const phone = vobizPhoneInput.value.trim();
  if (!phone) {
    alert("Please enter a destination phone number (e.g. +1234567890)");
    vobizPhoneInput.focus();
    return;
  }

  const backendUrl = backendUrlInput.value.trim().replace(/\/$/, "");
  const callerName = vobizCallerNameInput.value.trim();
  const fromNumber = vobizFromNumberInput.value.trim();

  vobizCallBtn.disabled = true;
  setVobizStatus("connecting", "Dispatching call...");
  log(`[Vobiz] Placing outbound call to ${phone}...`);

  try {
    const resp = await fetch(`${backendUrl}/vobiz/calls`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        phone_number: phone,
        caller_name: callerName || null,
        from_number: fromNumber || null,
      }),
    });

    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || `Server returned ${resp.status}`);
    }

    activeVobizCallId = data.call_id;
    infoCallId.textContent = data.call_id;
    infoCallUuid.textContent = data.call_uuid || "N/A";
    vobizCallInfoBox.hidden = false;

    setVobizStatus("connected", "Call dispatched (ringing/active)");
    vobizHangupBtn.disabled = false;
    log(`[Vobiz] Call dispatched! call_id=${data.call_id} uuid=${data.call_uuid}`);

    // Add notice to transcript panel
    clearEmptyState();
    const noteDiv = document.createElement("div");
    noteDiv.className = "transcript-turn bot";
    const label = document.createElement("span");
    label.className = "turn-label";
    label.textContent = "Vobiz Telephony";
    const text = document.createElement("span");
    text.className = "turn-text";
    text.textContent = `Outbound phone call triggered to ${phone} (${callerName || "Caller"}). When answered, Vobiz will connect to /ws to stream audio into the voice pipeline. Transcript is logged in PostgreSQL.`;
    noteDiv.appendChild(label);
    noteDiv.appendChild(text);
    transcriptEl.appendChild(noteDiv);
    scrollTranscriptToEnd();

  } catch (err) {
    log(`[Vobiz] Call failed: ${err.message || err}`, true);
    setVobizStatus("error", "Call failed");
    vobizCallBtn.disabled = false;
    alert(`Failed to place outbound call: ${err.message || err}`);
  }
}

async function hangupVobizCall() {
  if (!activeVobizCallId) return;

  const backendUrl = backendUrlInput.value.trim().replace(/\/$/, "");
  vobizHangupBtn.disabled = true;
  setVobizStatus("connecting", "Disconnecting call...");
  log(`[Vobiz] Terminating call ${activeVobizCallId}...`);

  try {
    const resp = await fetch(`${backendUrl}/vobiz/calls/${activeVobizCallId}/hangup`, {
      method: "POST",
    });
    const data = await resp.json();
    log(`[Vobiz] Hangup response: ${JSON.stringify(data)}`);
    setVobizStatus("idle", "Call disconnected");
  } catch (err) {
    log(`[Vobiz] Hangup error: ${err.message || err}`, true);
    setVobizStatus("idle", "Disconnect requested");
  } finally {
    vobizCallBtn.disabled = false;
    vobizHangupBtn.disabled = true;
    activeVobizCallId = null;
  }
}

vobizCallBtn.addEventListener("click", placeVobizCall);
vobizHangupBtn.addEventListener("click", hangupVobizCall);
