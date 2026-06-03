from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Route
from strands import Agent

# Friendly labels surfaced in the UI when a downstream agent (tool) is invoked.
_TOOL_LABELS = {
    "recommend_visiting_places": "Recommending places to visit (Agent 1)",
    "cluster_visiting_places": "Grouping places by day (Agent 2)",
    "schedule_single_day_plan": "Scheduling a day (Agent 3)",
}


class _Session:
    """Holds one chat session's Strands agent and a lock to serialise its calls."""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self.lock = asyncio.Lock()


class SessionStore:
    """Creates and retains one Strands agent per chat session id (in memory)."""

    def __init__(self, agent_factory: Callable[[], Agent]) -> None:
        self._agent_factory = agent_factory
        self._sessions: dict[str, _Session] = {}
        self._guard = asyncio.Lock()

    async def get(self, session_id: str) -> _Session:
        async with self._guard:
            session = self._sessions.get(session_id)
            if session is None:
                session = _Session(self._agent_factory())
                self._sessions[session_id] = session
            return session


def _sse(payload: dict[str, Any]) -> str:
    """Serialise a payload as a single Server-Sent Event."""
    return f"data: {json.dumps(payload)}\n\n"


def create_web_app(
    agent_factory: Callable[[], Agent],
    agent_card: dict[str, Any],
) -> Starlette:
    """Build the Starlette chat web UI for the orchestrator agent."""
    store = SessionStore(agent_factory)
    title = agent_card.get("name", "Travel Plan Orchestrator")
    page = _INDEX_HTML.replace("{{TITLE}}", title)

    async def index(_: Request) -> HTMLResponse:
        return HTMLResponse(page)

    async def chat(request: Request) -> StreamingResponse | JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)

        session_id = str(body.get("sessionId") or "").strip()
        message = str(body.get("message") or "").strip()
        if not session_id or not message:
            return JSONResponse(
                {"error": "sessionId and message are required"}, status_code=400
            )

        session = await store.get(session_id)

        async def event_stream():
            try:
                async with session.lock:
                    seen_tools: set[str] = set()
                    async for event in session.agent.stream_async(message):
                        tool_use = event.get("current_tool_use") or {}
                        name = tool_use.get("name")
                        tool_id = tool_use.get("toolUseId")
                        if name and tool_id and tool_id not in seen_tools:
                            seen_tools.add(tool_id)
                            yield _sse(
                                {
                                    "type": "tool",
                                    "label": _TOOL_LABELS.get(name, name),
                                }
                            )
                        text = event.get("data")
                        if isinstance(text, str) and text:
                            yield _sse({"type": "text", "text": text})
                yield _sse({"type": "done"})
            except Exception as exc:  # surface failures to the UI instead of hanging
                yield _sse({"type": "error", "message": str(exc)})

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return Starlette(
        routes=[
            Route("/", index, methods=["GET"]),
            Route("/api/chat", chat, methods=["POST"]),
        ]
    )


_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{{TITLE}}</title>
<style>
  :root { --bg:#0f172a; --panel:#1e293b; --me:#2563eb; --bot:#334155; --text:#e2e8f0; --muted:#94a3b8; }
  * { box-sizing: border-box; }
  body { margin:0; height:100vh; display:flex; flex-direction:column; background:var(--bg);
         color:var(--text); font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
  header { padding:14px 20px; background:var(--panel); border-bottom:1px solid #0b1220;
           font-weight:600; font-size:16px; display:flex; align-items:center; gap:10px; }
  header .dot { width:10px; height:10px; border-radius:50%; background:#22c55e; }
  #log { flex:1; overflow-y:auto; padding:20px; display:flex; flex-direction:column; gap:14px; }
  .msg { max-width:760px; padding:12px 16px; border-radius:14px; line-height:1.5; white-space:pre-wrap;
         word-wrap:break-word; }
  .me { align-self:flex-end; background:var(--me); border-bottom-right-radius:4px; }
  .bot { align-self:flex-start; background:var(--bot); border-bottom-left-radius:4px; }
  .status { align-self:flex-start; color:var(--muted); font-size:13px; font-style:italic; padding-left:6px; }
  .bot h2 { font-size:16px; margin:10px 0 4px; }
  .bot strong { color:#fff; }
  form { display:flex; gap:10px; padding:16px 20px; background:var(--panel); border-top:1px solid #0b1220; }
  #input { flex:1; resize:none; border:1px solid #475569; background:#0b1220; color:var(--text);
           border-radius:10px; padding:12px 14px; font-size:15px; font-family:inherit; outline:none; }
  #input:focus { border-color:var(--me); }
  button { background:var(--me); color:#fff; border:none; border-radius:10px; padding:0 20px;
           font-size:15px; font-weight:600; cursor:pointer; }
  button:disabled { opacity:0.5; cursor:default; }
  .hint { color:var(--muted); font-size:12px; padding:0 20px 10px; background:var(--panel); }
</style>
</head>
<body>
  <header><span class="dot"></span>{{TITLE}}</header>
  <div id="log">
    <div class="msg bot">Hi! I can plan a full multi-day trip for you. Tell me where you want to go,
when, and what you enjoy (history, food, art, nightlife&hellip;), and I'll build a day-by-day itinerary.</div>
  </div>
  <form id="form">
    <textarea id="input" rows="1" placeholder="e.g. Plan a 2-day trip to Milan, I like history and food, 2026-06-10 to 2026-06-11" autofocus></textarea>
    <button id="send" type="submit">Send</button>
  </form>
  <div class="hint">Powered by the Amazon Strands Agents SDK &middot; orchestrating Agents 1&ndash;3 over A2A.</div>

<script>
  const log = document.getElementById('log');
  const form = document.getElementById('form');
  const input = document.getElementById('input');
  const sendBtn = document.getElementById('send');

  let sessionId = localStorage.getItem('orchestratorSessionId');
  if (!sessionId) { sessionId = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()+Math.random()));
    localStorage.setItem('orchestratorSessionId', sessionId); }

  function escapeHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
  // Minimal, safe formatting: escape first, then bold (**x**) and ## headings.
  function format(s) {
    let h = escapeHtml(s);
    h = h.replace(/^##\\s?(.*)$/gm, '<h2>$1</h2>');
    h = h.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');
    return h;
  }
  function addMessage(cls) {
    const el = document.createElement('div');
    el.className = 'msg ' + cls;
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    return el;
  }
  function scroll() { log.scrollTop = log.scrollHeight; }

  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 160) + 'px';
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = ''; input.style.height = 'auto';
    sendBtn.disabled = true;

    const userEl = addMessage('me'); userEl.textContent = text;

    const statusEl = addMessage('status'); statusEl.textContent = 'Thinking\\u2026';
    let botEl = null;
    let botText = '';

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sessionId, message: text }),
      });
      if (!res.ok || !res.body) {
        statusEl.remove();
        const err = addMessage('bot'); err.textContent = 'Request failed (' + res.status + ').';
        sendBtn.disabled = false; return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\\n\\n');
        buffer = parts.pop();
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith('data:')) continue;
          let evt;
          try { evt = JSON.parse(line.slice(5).trim()); } catch { continue; }
          if (evt.type === 'tool') {
            statusEl.textContent = evt.label + '\\u2026';
            scroll();
          } else if (evt.type === 'text') {
            if (!botEl) { statusEl.remove(); botEl = addMessage('bot'); }
            botText += evt.text;
            botEl.innerHTML = format(botText);
            scroll();
          } else if (evt.type === 'error') {
            if (!botEl) { statusEl.remove(); botEl = addMessage('bot'); }
            botEl.innerHTML = format(botText + '\\n\\n[error] ' + evt.message);
            scroll();
          } else if (evt.type === 'done') {
            if (statusEl.parentNode) statusEl.remove();
          }
        }
      }
      if (statusEl.parentNode) statusEl.remove();
    } catch (err) {
      if (statusEl.parentNode) statusEl.remove();
      const e2 = addMessage('bot'); e2.textContent = 'Connection error: ' + err;
    } finally {
      sendBtn.disabled = false;
      input.focus();
    }
  });
</script>
</body>
</html>
"""
