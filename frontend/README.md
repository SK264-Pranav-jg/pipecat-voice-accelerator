# Test client

Minimal vanilla HTML/JS page for testing the WebRTC (browser) call path
against `POST /api/offer`. Uses Pipecat's own client SDK
(`@pipecat-ai/client-js` + `@pipecat-ai/small-webrtc-transport`, loaded from
the esm.sh CDN as native ES modules — no `npm install` or build step) instead
of hand-rolled WebRTC signaling, so it performs the exact connection sequence
the backend expects: SDP offer/answer, then a post-connect "ready" handshake
over the data channel before the bot starts talking.

## This is two separate processes, not one

The backend (`python -m backend.main`) and this frontend are two independent
servers. Starting the backend does **not** start the frontend, and there is
no single command that runs both — you need two terminals:

**Terminal 1 — backend:**
```
cd pipecat-voice-accelerator
uv run python -m backend.main
```
Confirm it's actually up: `curl http://127.0.0.1:8080/` should return
`{"message":"Pipecat Voice Accelerator - Health check"}`. If that fails, fix
that first — nothing else will work until the backend answers this.

**Terminal 2 — frontend (static file server):**
```
cd pipecat-voice-accelerator/frontend
python -m http.server 5500
```
Then open **http://127.0.0.1:5500** in your browser (not `file://` —
opening `index.html` directly can silently block the mic permission prompt
and, on some browsers, block the fetch to the backend entirely).

Confirm the "Backend URL" field on the page matches Terminal 1's address
(default `http://127.0.0.1:8080`), then click **Connect** and grant
microphone permission when prompted.

## If you see no requests in the backend terminal at all

That means the request never left the browser — it's not a backend problem.
Check, in this order:

1. **Backend actually running and reachable** — `curl` it (above). If nothing
   answers, this is the whole problem; the frontend was never going to reach
   it.
2. **Browser console (F12 → Console/Network tab)** — the page's own "Log"
   panel only shows what the SDK's callbacks report; JS errors (e.g. the CDN
   import itself failing) show up in the browser console, not the log panel.
3. **Backend URL field** — must match exactly where the backend is actually
   listening, including protocol and port.
4. **Something already using port 8080** — if a stray process from an
   earlier run is still bound to that port, your new backend may have failed
   to start (or you're unknowingly talking to the old one). `netstat -ano |
   grep 8080` (or Task Manager) to check.

## Notes

- Only tests the WebRTC/browser path (`/api/offer`). The telephony path (`/ws`,
  Vobiz) needs a real phone call and isn't something a browser client can
  exercise.
- No ICE servers are configured — fine for same-machine/same-LAN testing. Add
  a STUN entry in `main.js` (`new SmallWebRTCTransport({ iceServers: [{ urls:
  "stun:stun.l.google.com:19302" }] })`) if testing across networks.
- The backend already allows all CORS origins (`backend/src/api/app.py`), so
  this can be served from any port/host during development.
- If the esm.sh CDN import ever breaks (network restrictions, CDN outage),
  the more bulletproof alternative is `npm install @pipecat-ai/client-js
  @pipecat-ai/small-webrtc-transport` with a real bundler (Vite is what
  Pipecat's own example apps use) — worth moving to that once this is a real
  frontend rather than a test client.
