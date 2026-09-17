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

let client = null;

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

async function connect() {
  connectBtn.disabled = true;
  setStatus("connecting", "Connecting...");

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
      },
      onError: (message) => {
        log(`Error: ${JSON.stringify(message)}`, true);
        setStatus("error", "Error — see log");
      },
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
