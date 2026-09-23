"""Local web server behind the Block Builder tab.

Tkinter can't host a web view, so the Blockly editor (web/block_builder.html)
is served from 127.0.0.1 and opened in the user's browser. Its Send / Run /
Stop buttons call back into this server, which relays them to the robot over
the Control Center's existing SSH connection -- no second login.

Because this server can run code on the robot, it refuses anything that
didn't come from the page we served:
  * the page URL carries a random per-session key, and every API call must
    send the same value as a header (a website in another tab can't read it,
    and a cross-origin request with a custom header needs a CORS preflight,
    which we never approve);
  * the Host header must be 127.0.0.1 / localhost on our port (blocks DNS
    rebinding);
  * the Origin header, when present, must be our own page.
"""

import hmac
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from constants import VENV_ACTIVATE, login_shell
from remote_scripts import REMOTE_BLOCKS_SCRIPT_PATH

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
PAGE_PATH = os.path.join(WEB_DIR, "block_builder.html")
BLOCKLY_DIR = os.path.join(WEB_DIR, "blockly")

TOKEN_HEADER = "X-GoPiGo-Token"
TOKEN_PLACEHOLDER = "__TOKEN__"
MAX_BODY_BYTES = 1_000_000

NOT_CONNECTED = "Not connected. Connect to the robot in the Control Center first."


# ---- Robot actions (one per button in the page) ----
def _write_script(client, code):
    sftp = client.open_sftp()
    try:
        with sftp.file(REMOTE_BLOCKS_SCRIPT_PATH, "w") as f:
            f.write(code)
    finally:
        sftp.close()


def send_code(client, code):
    if client is None:
        return {"ok": False, "message": NOT_CONNECTED}
    try:
        _write_script(client, code)
        return {"ok": True, "message": f"Sent to {REMOTE_BLOCKS_SCRIPT_PATH}"}
    except Exception as e:
        return {"ok": False, "message": f"Send failed: {e}"}


def send_and_run(client, code):
    if client is None:
        return {"ok": False, "message": NOT_CONNECTED, "output": ""}
    try:
        _write_script(client, code)
        command = f"{VENV_ACTIVATE} && python3 -u {REMOTE_BLOCKS_SCRIPT_PATH}"
        _, stdout, stderr = client.exec_command(login_shell(command), get_pty=True, timeout=30)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        output = out + ("\n[stderr]\n" + err if err.strip() else "")
        return {"ok": True, "message": "Run finished.", "output": output}
    except Exception as e:
        return {"ok": False, "message": f"Run failed: {e}", "output": ""}


def stop_robot(client):
    if client is None:
        return {"ok": False, "message": NOT_CONNECTED}
    try:
        client.exec_command(f"pkill -f {os.path.basename(REMOTE_BLOCKS_SCRIPT_PATH)}")
        return {"ok": True, "message": "Stop signal sent."}
    except Exception as e:
        return {"ok": False, "message": f"Stop failed: {e}"}


_ACTIONS = {"/api/send": send_code, "/api/run": send_and_run}


def _same(a, b):
    return hmac.compare_digest(a.encode(), b.encode())


class _Handler(BaseHTTPRequestHandler):
    server_version = "GoPiGoBlocks"

    def log_message(self, format, *args):
        pass  # keep the terminal quiet

    # -- responses --
    def _send(self, status, content_type, body, no_store=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if no_store:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, "application/json", json.dumps(obj).encode(), no_store=True)

    def _forbidden(self):
        self._json(403, {"ok": False, "message": "Forbidden."})

    # -- request checks --
    def _origin_ok(self):
        owner = self.server.owner
        if self.headers.get("Host") not in owner.allowed_hosts:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in owner.allowed_origins

    def _token_ok(self):
        return _same(self.headers.get(TOKEN_HEADER, ""), self.server.owner.token)

    # -- routes --
    def do_GET(self):
        if not self._origin_ok():
            return self._forbidden()
        url = urlsplit(self.path)

        if url.path == "/":
            key = parse_qs(url.query).get("k", [""])[0]
            if not _same(key, self.server.owner.token):
                return self._forbidden()
            with open(PAGE_PATH, encoding="utf-8") as f:
                page = f.read().replace(TOKEN_PLACEHOLDER, self.server.owner.token)
            return self._send(200, "text/html; charset=utf-8", page.encode(), no_store=True)

        if url.path.startswith("/blockly/"):
            name = url.path[len("/blockly/"):]
            path = os.path.join(BLOCKLY_DIR, name)
            if not name or name != os.path.basename(name) or name.startswith(".") or not os.path.isfile(path):
                return self._json(404, {"ok": False, "message": "Not found."})
            with open(path, "rb") as f:
                body = f.read()
            ctype = "application/javascript; charset=utf-8" if name.endswith(".js") else "text/plain; charset=utf-8"
            return self._send(200, ctype, body)

        if url.path == "/api/status":
            if not self._token_ok():
                return self._forbidden()
            return self._json(200, {"connected": self.server.owner.get_client() is not None})

        self._json(404, {"ok": False, "message": "Not found."})

    def do_POST(self):
        if not self._origin_ok() or not self._token_ok():
            return self._forbidden()
        path = urlsplit(self.path).path
        if path not in _ACTIONS and path != "/api/stop":
            return self._json(404, {"ok": False, "message": "Not found."})

        # Always consume the body, so the connection closes cleanly.
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY_BYTES:
            return self._json(400, {"ok": False, "message": "Bad request body."})
        body = self.rfile.read(length)

        code = ""
        if path in _ACTIONS:
            try:
                code = json.loads(body)["code"]
                if not isinstance(code, str):
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                return self._json(400, {"ok": False, "message": "Bad request body."})

        client = self.server.owner.get_client()
        if path == "/api/stop":
            result = stop_robot(client)
        else:
            result = _ACTIONS[path](client, code)
        self.server.owner.notify(f"Block Builder: {result['message']}")
        self._json(200, result)


class BlockBuilderServer:
    """Serves the Block Builder page on localhost and relays its buttons to the robot.

    get_client: callable returning the current paramiko SSHClient, or None.
    notify:     callable taking a status-bar message; called from server threads.
    """

    def __init__(self, get_client, notify):
        self.get_client = get_client
        self.notify = notify
        self.token = secrets.token_urlsafe(24)
        self.url = None
        self.allowed_hosts = set()
        self.allowed_origins = set()
        self._httpd = None

    def start(self):
        if self._httpd is not None:
            return
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)  # port 0 = pick a free one
        httpd.owner = self
        port = httpd.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.allowed_origins = {f"http://{host}" for host in self.allowed_hosts}
        self.url = f"http://127.0.0.1:{port}/?k={self.token}"
        self._httpd = httpd
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def stop(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
            self.url = None
