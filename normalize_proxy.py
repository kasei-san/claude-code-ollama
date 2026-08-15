"""Sit between Claude Code and Ollama and fix one incompatibility.

Claude Code injects a system-role message *inside* the messages array
(messages[1], the system-reminder mechanism). Strict GGUF chat templates --
Qwen3.6's among them -- raise

    Jinja Exception: System message must be at the beginning.

and every request 500s. This proxy folds any non-leading system message into a
user turn and merges adjacent same-role turns, then forwards upstream
untouched otherwise.

Usage:  python normalize_proxy.py [listen_port] [upstream_url]
Default: 127.0.0.1:11435 -> http://localhost:11434
"""
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 11435
UPSTREAM = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:11434"

# Thinking costs output tokens on every turn (measured: 136 vs 2 for a
# one-word answer) without changing that answer. Off by default; set
# CCL_THINK=1 to leave the client's own setting alone.
NO_THINK = os.environ.get("CCL_THINK", "") != "1"


LOGPATH = os.environ.get("CCL_LOG", "")


def _log(msg):
    """Structure only -- never message content (it is the user's source code)."""
    if not LOGPATH:
        return
    with open(LOGPATH, "a", encoding="utf-8") as f:
        f.write(msg)


def _shape(body):
    parts = []
    for m in body.get("messages") or []:
        c = m.get("content")
        if isinstance(c, str):
            desc = "str"
        else:
            desc = "+".join(
                (b.get("type", "?") + ("(%d)" % len(b.get("content") or []))
                 if b.get("type") == "tool_result" else b.get("type", "?"))
                for b in c)
        parts.append("%s[%s]" % (m.get("role"), desc))
    return " ".join(parts)


def normalize(body):
    if NO_THINK:
        body["thinking"] = {"type": "disabled"}
    msgs = body.get("messages") or []
    out = []
    changed = False
    for m in msgs:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        was_system = role == "system"
        if was_system:
            role = "user"
            changed = True
        if out and out[-1]["role"] == role:
            # A folded system message goes in FRONT of the turn it merges into.
            # Appending it leaves the reminder as the last thing the model sees
            # and it answers that instead of the user's actual question
            # (measured: the model greeted instead of answering "2+2").
            if was_system:
                out[-1]["content"] = content + out[-1]["content"]
            else:
                out[-1]["content"] = out[-1]["content"] + content
            changed = True
        else:
            out.append({"role": role, "content": content})
    if changed:
        body["messages"] = out
    return body, changed


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _relay(self, raw=None):
        try:
            if raw is None:
                req = urllib.request.Request(UPSTREAM + self.path, method="GET")
            else:
                req = urllib.request.Request(
                    UPSTREAM + self.path, data=raw, method="POST",
                    headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=900) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except Exception as e:
            msg = json.dumps({"type": "error", "error": {
                "type": "api_error", "message": "proxy: %s" % e}})
            return 502, msg.encode("utf-8")

    def _send(self, code, data):
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length", 0)))
        try:
            body = json.loads(raw)
            before = _shape(body)
            body, changed = normalize(body)
            if changed:
                raw = json.dumps(body).encode("utf-8")
            _log("POST %s\n  before: %s\n  after:  %s\n"
                 % (self.path, before, _shape(body)))
        except Exception:
            pass  # not JSON we understand; pass it through untouched
        self._send(*self._relay(raw))

    def do_GET(self):
        self._send(*self._relay())


if __name__ == "__main__":
    print("[normalize_proxy] %d -> %s" % (PORT, UPSTREAM))
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
