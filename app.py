import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from flask import Flask, abort, render_template, request
from flask_socketio import SocketIO, emit


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    token: Optional[str]
    cors_origins: str
    relay_host: str
    relay_port: int
    autostart_host: bool
    socketio_debug: bool


def load_settings() -> Settings:
    return Settings(
        host=os.getenv("MEMCTRL_HOST", "0.0.0.0"),
        port=int(os.getenv("MEMCTRL_PORT", "5000")),
        token=os.getenv("MEMCTRL_TOKEN") or None,
        cors_origins=os.getenv("MEMCTRL_CORS_ORIGINS", "*"),
        relay_host=os.getenv("MEMCTRL_RELAY_HOST", "127.0.0.1"),
        relay_port=int(os.getenv("MEMCTRL_RELAY_PORT", "8765")),
        autostart_host=os.getenv("MEMCTRL_AUTOSTART_HOST", "0") in {"1", "true", "True"},
        socketio_debug=os.getenv("MEMCTRL_SOCKETIO_DEBUG", "0") in {"1", "true", "True"},
    )


settings = load_settings()

logging.getLogger("werkzeug").setLevel(logging.ERROR)

def _log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


class RelayClient:
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._rx_buf = b""

        self._pending_lock = threading.Lock()
        self._pending: dict[str, tuple[threading.Event, dict[str, Any]]] = {}
        self._next_id = 1

        self.connected = False
        self.capabilities = {"mouse": False, "keyboard": False, "gamepad": False}
        self.last_status: dict[str, Any] = {}

        self._thread = threading.Thread(target=self._run, name="relay-client", daemon=True)
        self._thread.start()

    def _close(self) -> None:
        with self._lock:
            s = self._sock
            self._sock = None
            self.connected = False
        if s:
            try:
                s.close()
            except Exception:
                pass

    def _send_line(self, payload: dict[str, Any]) -> bool:
        data = (json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            if not self._sock:
                return False
            try:
                self._sock.sendall(data)
                return True
            except Exception:
                self._sock = None
                self.connected = False
                return False

    def _read_line(self, timeout_s: float) -> Optional[dict[str, Any]]:
        with self._lock:
            s = self._sock
        if not s:
            return None
        s.settimeout(timeout_s)
        try:
            while b"\n" not in self._rx_buf:
                chunk = s.recv(4096)
                if not chunk:
                    return None
                self._rx_buf += chunk
            line, self._rx_buf = self._rx_buf.split(b"\n", 1)
            return json.loads(line.decode("utf-8"))
        except Exception:
            return None
        finally:
            try:
                s.settimeout(None)
            except Exception:
                pass

    def _handle_incoming(self, msg: dict[str, Any]) -> None:
        t = msg.get("t")
        if t == "status":
            self.last_status = msg
            self.capabilities = {
                "mouse": bool(msg.get("mouse")),
                "keyboard": bool(msg.get("keyboard")),
                "gamepad": bool(msg.get("gamepad")),
            }
            return
        if t == "rpc_result":
            req_id = str(msg.get("id") or "")
            with self._pending_lock:
                pending = self._pending.get(req_id)
                if not pending:
                    return
                ev, box = pending
                box.update(msg)
                ev.set()
            return

    def _connect_once(self) -> None:
        s = socket.create_connection((self._host, self._port), timeout=1.5)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with self._lock:
            self._sock = s
            self.connected = True
            self._rx_buf = b""

        # Handshake.
        self._send_line({"t": "hello", "v": 1})
        msg = self._read_line(timeout_s=1.5)
        if msg and isinstance(msg, dict):
            self._handle_incoming(msg)
        _log(f"Connected to host relay {self._host}:{self._port}")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._connect_once()
            except Exception:
                self._close()
                time.sleep(0.5)
                continue

            while self.connected and not self._stop.is_set():
                msg = self._read_line(timeout_s=0.5)
                if not msg:
                    continue
                if isinstance(msg, dict):
                    self._handle_incoming(msg)
            _log("Host relay disconnected; reconnecting...")

    def stop(self) -> None:
        self._stop.set()
        self._close()

    def send_client_state(self, state: str, meta: dict[str, Any]) -> None:
        self._send_line({"t": "client", "state": state, "meta": meta})

    def send_input(self, event: str, data: dict[str, Any]) -> None:
        self._send_line({"t": "input", "e": event, "d": data})

    def rpc(self, method: str, params: dict[str, Any], timeout_s: float = 2.0) -> dict[str, Any]:
        if not self.connected:
            return {"ok": False, "error": "host_not_connected"}

        with self._pending_lock:
            req_id = str(self._next_id)
            self._next_id += 1
            ev = threading.Event()
            box: dict[str, Any] = {}
            self._pending[req_id] = (ev, box)

        sent = self._send_line({"t": "rpc", "id": req_id, "m": method, "p": params})
        if not sent:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            return {"ok": False, "error": "send_failed"}

        if not ev.wait(timeout_s):
            with self._pending_lock:
                self._pending.pop(req_id, None)
            return {"ok": False, "error": "timeout"}

        with self._pending_lock:
            _ev, result = self._pending.pop(req_id, (ev, box))

        return {
            "ok": bool(result.get("ok", False)),
            "error": result.get("error"),
            "result": result.get("result"),
        }


def maybe_autostart_host() -> None:
    if not settings.autostart_host:
        return
    try:
        subprocess.Popen([sys.executable, "host.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


maybe_autostart_host()
relay = RelayClient(settings.relay_host, settings.relay_port)


app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("MEMCTRL_SECRET_KEY", os.urandom(24).hex())

socketio = SocketIO(
    app,
    cors_allowed_origins=settings.cors_origins,
    async_mode="threading",
    logger=settings.socketio_debug,
    engineio_logger=settings.socketio_debug,
)


def require_token_or_403(presented: Optional[str]) -> None:
    if settings.token and presented != settings.token:
        abort(403)


@app.get("/")
def index() -> str:
    require_token_or_403(request.args.get("token"))
    return render_template("index.html", token=settings.token or "")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "relay_connected": relay.connected,
        "relay_capabilities": relay.capabilities,
    }


def _guess_lan_ip() -> str:
    # Best-effort: does not send packets, just asks OS for the chosen interface.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return str(s.getsockname()[0])
    except Exception:
        return "unknown"


@app.get("/diag")
def diag() -> dict[str, Any]:
    return {
        "request_ip": request.remote_addr,
        "lan_ip_guess": _guess_lan_ip(),
        "web_host": settings.host,
        "web_port": settings.port,
        "relay_host": settings.relay_host,
        "relay_port": settings.relay_port,
        "relay_connected": relay.connected,
        "relay_last_status": relay.last_status,
        "token_enabled": bool(settings.token),
        "note": "Start with `python app.py` (not `flask run`). Ensure Windows Firewall allows TCP on the web port.",
    }


@socketio.on("connect")
def on_connect(auth: Optional[dict[str, Any]] = None) -> Any:
    presented = ""
    if auth and isinstance(auth, dict):
        presented = str(auth.get("token") or "")
    if settings.token and presented != settings.token:
        return False

    _log(f"Phone connected ip={request.remote_addr}")
    relay.send_client_state(
        "connected",
        {
            "ip": request.remote_addr,
            "ua": request.headers.get("User-Agent", ""),
        },
    )
    transport = "?"
    try:
        transport = str(request.args.get("transport") or "?")
    except Exception:
        pass
    emit(
        "server_status",
        {
            "relay": relay.connected,
            "mouse": relay.capabilities.get("mouse", False),
            "keyboard": relay.capabilities.get("keyboard", False),
            "gamepad": relay.capabilities.get("gamepad", False),
            "transport": transport,
            "gamepad_error": relay.last_status.get("gamepad_error"),
            "selected_window": relay.last_status.get("selected_window"),
            "focus_lock": relay.last_status.get("focus_lock"),
        },
    )


@socketio.on("disconnect")
def on_disconnect() -> None:
    _log(f"Phone disconnected ip={request.remote_addr}")
    relay.send_client_state("disconnected", {"ip": request.remote_addr})


@socketio.on("move")
def on_move(data: dict[str, Any]) -> None:
    relay.send_input("move", {"dx": float(data.get("dx") or 0.0), "dy": float(data.get("dy") or 0.0)})


@socketio.on("scroll")
def on_scroll(data: dict[str, Any]) -> None:
    relay.send_input("scroll", {"dy": float(data.get("dy") or 0.0)})


@socketio.on("click")
def on_click(data: dict[str, Any]) -> None:
    relay.send_input(
        "click",
        {"button": str(data.get("button") or "left"), "down": bool(data.get("down", True))},
    )


@socketio.on("type_text")
def on_type_text(data: dict[str, Any]) -> None:
    relay.send_input("type_text", {"text": str(data.get("text") or "")})


@socketio.on("key")
def on_key(data: dict[str, Any]) -> None:
    relay.send_input("key", {"name": str(data.get("name") or ""), "down": bool(data.get("down", True))})


@socketio.on("pad_left")
def on_pad_left(data: dict[str, Any]) -> None:
    relay.send_input("pad_left", {"x": float(data.get("x") or 0.0), "y": float(data.get("y") or 0.0)})


@socketio.on("pad_right")
def on_pad_right(data: dict[str, Any]) -> None:
    relay.send_input("pad_right", {"x": float(data.get("x") or 0.0), "y": float(data.get("y") or 0.0)})


@socketio.on("pad_trigger")
def on_pad_trigger(data: dict[str, Any]) -> None:
    relay.send_input(
        "pad_trigger",
        {"which": str(data.get("which") or ""), "value": float(data.get("value") or 0.0)},
    )


@socketio.on("pad_button")
def on_pad_button(data: dict[str, Any]) -> None:
    relay.send_input("pad_button", {"name": str(data.get("name") or ""), "down": bool(data.get("down", True))})


@socketio.on("select_window")
def on_select_window(_data: dict[str, Any]) -> dict[str, Any]:
    return relay.rpc("select_foreground_window", {}, timeout_s=2.0)


@socketio.on("get_selected_window")
def on_get_selected_window(_data: dict[str, Any]) -> dict[str, Any]:
    return relay.rpc("get_selected_window", {}, timeout_s=2.0)


@socketio.on("set_focus_lock")
def on_set_focus_lock(data: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(data.get("enabled", False))
    return relay.rpc("set_focus_lock", {"enabled": enabled}, timeout_s=2.0)


@socketio.on("pad_reset")
def on_pad_reset(_data: dict[str, Any]) -> dict[str, Any]:
    return relay.rpc("pad_reset", {}, timeout_s=4.0)


@socketio.on("set_gamepad_enabled")
def on_set_gamepad_enabled(data: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(data.get("enabled", False))
    return relay.rpc("set_gamepad_enabled", {"enabled": enabled}, timeout_s=4.0)


if __name__ == "__main__":
    token_note = " (token disabled)" if not settings.token else " (token enabled)"
    print(f"Web UI: http://{settings.host}:{settings.port}/{token_note}")
    print(f"LAN (guess): http://{_guess_lan_ip()}:{settings.port}/")
    print(f"Diag: http://{_guess_lan_ip()}:{settings.port}/diag")
    print(f"Host relay: {settings.relay_host}:{settings.relay_port} connected={relay.connected}")
    if not settings.token:
        print("WARNING: MEMCTRL_TOKEN is not set; anyone on your LAN can control your PC if they find the URL.")
    print("Run host.py in another terminal for input injection.")
    socketio.run(app, host=settings.host, port=settings.port, debug=False, allow_unsafe_werkzeug=True)
