import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import ctypes
from ctypes import wintypes

try:
    from pynput import keyboard, mouse
except Exception:  # pragma: no cover
    keyboard = None
    mouse = None


@dataclass(frozen=True)
class Settings:
    listen_host: str
    listen_port: int
    mouse_sensitivity: float
    joystick_sensitivity: float
    max_move_px: int
    max_scroll: int
    enable_gamepad: bool
    mouse_hz: int
    log_input: bool
    log_input_verbose: bool


def load_settings() -> Settings:
    return Settings(
        listen_host=os.getenv("MEMCTRL_RELAY_LISTEN_HOST", "127.0.0.1"),
        listen_port=int(os.getenv("MEMCTRL_RELAY_PORT", "8765")),
        mouse_sensitivity=float(os.getenv("MEMCTRL_MOUSE_SENS", "1.0")),
        joystick_sensitivity=float(os.getenv("MEMCTRL_JOYSTICK_SENS", "1.0")),
        max_move_px=int(os.getenv("MEMCTRL_MAX_MOVE_PX", "200")),
        max_scroll=int(os.getenv("MEMCTRL_MAX_SCROLL", "120")),
        enable_gamepad=os.getenv("MEMCTRL_ENABLE_GAMEPAD", "0") in {"1", "true", "True"},
        mouse_hz=int(os.getenv("MEMCTRL_MOUSE_HZ", "500")),
        log_input=os.getenv("MEMCTRL_LOG_INPUT", "0") in {"1", "true", "True"},
        log_input_verbose=os.getenv("MEMCTRL_LOG_INPUT_VERBOSE", "0") in {"1", "true", "True"},
    )


settings = load_settings()


mouse_controller = mouse.Controller() if mouse else None
keyboard_controller = keyboard.Controller() if keyboard else None


class GamepadAdapter:
    def __init__(self, enabled: bool) -> None:
        self._pad = None
        self._buttons = None
        self._ready = False
        self._error: Optional[str] = None

        if not enabled:
            self._error = "disabled"
            return
        try:
            import vgamepad as vg  # type: ignore

            self._pad = vg.VX360Gamepad()
            self._buttons = vg.XUSB_BUTTON
            self._pad.update()
            self._ready = True
        except Exception as e:
            self._pad = None
            self._buttons = None
            self._ready = False
            self._error = repr(e)

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def error(self) -> Optional[str]:
        return self._error

    def set_left_stick(self, x: float, y: float) -> None:
        if not self._ready:
            return
        x = max(-1.0, min(1.0, x * settings.joystick_sensitivity))
        y = max(-1.0, min(1.0, y * settings.joystick_sensitivity))
        self._pad.left_joystick_float(x_value_float=x, y_value_float=y)
        self._pad.update()

    def set_right_stick(self, x: float, y: float) -> None:
        if not self._ready:
            return
        x = max(-1.0, min(1.0, x * settings.joystick_sensitivity))
        y = max(-1.0, min(1.0, y * settings.joystick_sensitivity))
        self._pad.right_joystick_float(x_value_float=x, y_value_float=y)
        self._pad.update()

    def set_trigger(self, which: str, value: float) -> None:
        if not self._ready:
            return
        value = max(0.0, min(1.0, value))
        if which == "lt":
            self._pad.left_trigger_float(value_float=value)
        elif which == "rt":
            self._pad.right_trigger_float(value_float=value)
        self._pad.update()

    def set_button(self, name: str, pressed: bool) -> None:
        if not self._ready:
            return
        mapping = {
            "a": self._buttons.XUSB_GAMEPAD_A,
            "b": self._buttons.XUSB_GAMEPAD_B,
            "x": self._buttons.XUSB_GAMEPAD_X,
            "y": self._buttons.XUSB_GAMEPAD_Y,
            "lb": self._buttons.XUSB_GAMEPAD_LEFT_SHOULDER,
            "rb": self._buttons.XUSB_GAMEPAD_RIGHT_SHOULDER,
            "back": self._buttons.XUSB_GAMEPAD_BACK,
            "start": self._buttons.XUSB_GAMEPAD_START,
            "ls": self._buttons.XUSB_GAMEPAD_LEFT_THUMB,
            "rs": self._buttons.XUSB_GAMEPAD_RIGHT_THUMB,
            "dup": self._buttons.XUSB_GAMEPAD_DPAD_UP,
            "ddown": self._buttons.XUSB_GAMEPAD_DPAD_DOWN,
            "dleft": self._buttons.XUSB_GAMEPAD_DPAD_LEFT,
            "dright": self._buttons.XUSB_GAMEPAD_DPAD_RIGHT,
        }
        btn = mapping.get(name)
        if not btn:
            return
        if pressed:
            self._pad.press_button(button=btn)
        else:
            self._pad.release_button(button=btn)
        self._pad.update()


gamepad_enabled = bool(settings.enable_gamepad)
gamepad = GamepadAdapter(gamepad_enabled)


class MouseMover:
    def __init__(self, hz: int) -> None:
        self._hz = max(60, min(1000, int(hz)))
        self._dx = 0.0
        self._dy = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="mouse-mover", daemon=True)
        self._thread.start()

    def add(self, dx: float, dy: float) -> None:
        with self._lock:
            self._dx += dx
            self._dy += dy

    def _loop(self) -> None:
        if not mouse_controller:
            return
        period = 1.0 / float(self._hz)
        while not self._stop.is_set():
            time.sleep(period)
            with self._lock:
                dx = max(-settings.max_move_px, min(settings.max_move_px, self._dx))
                dy = max(-settings.max_move_px, min(settings.max_move_px, self._dy))
                mx = int(dx)
                my = int(dy)
                self._dx -= mx
                self._dy -= my
            if mx or my:
                mouse_controller.move(mx, my)

    def stop(self) -> None:
        self._stop.set()


mouse_mover = MouseMover(settings.mouse_hz)

_stats_lock = threading.Lock()
_stats: dict[str, int] = {"move": 0, "scroll": 0, "click": 0, "key": 0, "type": 0, "pad": 0}


def _bump(name: str) -> None:
    if not settings.log_input:
        return
    with _stats_lock:
        _stats[name] = int(_stats.get(name, 0)) + 1


def _stats_loop() -> None:
    if not settings.log_input:
        return
    while True:
        time.sleep(1.0)
        with _stats_lock:
            snap = dict(_stats)
            for k in _stats:
                _stats[k] = 0
        if any(v for v in snap.values()):
            sel = selected_window.get("title") or selected_window.get("hwnd") or "none"
            print(
                "1s stats: "
                + " ".join(f"{k}={v}" for k, v in snap.items())
                + f" focus_lock={int(focus_lock_enabled)} sel={sel}"
            )


if settings.log_input:
    threading.Thread(target=_stats_loop, name="stats-logger", daemon=True).start()


user32 = ctypes.WinDLL("user32", use_last_error=True)

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL

SW_RESTORE = 9


def _window_text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    if user32.GetWindowTextW(wintypes.HWND(hwnd), buf, len(buf)) <= 0:
        return ""
    return str(buf.value)


def _foreground_window_info() -> dict[str, Any]:
    hwnd = int(user32.GetForegroundWindow() or 0)
    pid = wintypes.DWORD(0)
    if hwnd:
        user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return {"hwnd": hwnd, "pid": int(pid.value), "title": _window_text(hwnd) if hwnd else ""}


def _focus_window(hwnd: int) -> bool:
    if not hwnd:
        return False
    try:
        user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
        return bool(user32.SetForegroundWindow(wintypes.HWND(hwnd)))
    except Exception:
        return False


selected_window: dict[str, Any] = {"hwnd": 0, "pid": 0, "title": ""}
focus_lock_enabled = False
_last_focus_attempt = 0.0


def _maybe_refocus() -> None:
    global _last_focus_attempt
    if not focus_lock_enabled:
        return
    hwnd = int(selected_window.get("hwnd") or 0)
    if not hwnd:
        return
    now = time.time()
    if now - _last_focus_attempt < 0.4:
        return
    _last_focus_attempt = now
    _focus_window(hwnd)


def _status_snapshot() -> dict[str, Any]:
    return {
        "mouse": bool(mouse_controller),
        "keyboard": bool(keyboard_controller),
        "gamepad": bool(gamepad.ready),
        "gamepad_enabled": bool(gamepad_enabled),
        "gamepad_error": gamepad.error,
        "selected_window": selected_window,
        "focus_lock": bool(focus_lock_enabled),
    }

def _status_payload() -> dict[str, Any]:
    payload = {"t": "status"}
    payload.update(_status_snapshot())
    return payload


def _set_gamepad_enabled(enabled: bool) -> dict[str, Any]:
    global gamepad_enabled, gamepad
    enabled = bool(enabled)
    if enabled == gamepad_enabled:
        # Still return current status.
        return _status_snapshot()
    gamepad_enabled = enabled
    if not gamepad_enabled:
        # Stop sending inputs; keep device state neutral if possible.
        try:
            if getattr(gamepad, "ready", False) and getattr(gamepad, "_pad", None) is not None:
                pad = getattr(gamepad, "_pad")
                if hasattr(pad, "reset"):
                    pad.reset()
                    pad.update()
        except Exception:
            pass
        return _status_snapshot()

    # Enabling: create/recreate the virtual device.
    gamepad = GamepadAdapter(True)
    return _status_snapshot()


def _handle_rpc(method: str, params: dict[str, Any]) -> dict[str, Any]:
    global focus_lock_enabled, selected_window, gamepad

    if method == "select_foreground_window":
        selected_window = _foreground_window_info()
        # Selecting a window implies "gaming mode".
        if int(selected_window.get("hwnd") or 0):
            focus_lock_enabled = True
            _set_gamepad_enabled(True)
        return _status_snapshot()

    if method == "get_selected_window":
        return _status_snapshot()

    if method == "set_focus_lock":
        focus_lock_enabled = bool(params.get("enabled", False))
        if focus_lock_enabled and int(selected_window.get("hwnd") or 0):
            _set_gamepad_enabled(True)
        return _status_snapshot()

    if method == "set_gamepad_enabled":
        return _set_gamepad_enabled(bool(params.get("enabled", False)))

    if method == "pad_reset":
        # Reset is meaningful only if enabled.
        if not gamepad_enabled:
            return _status_snapshot()
        gamepad = GamepadAdapter(True)
        return _status_snapshot()

    if method == "gamepad_status":
        return _status_snapshot()

    return {"error": "unknown_method"}


def _send_line(sock: socket.socket, payload: dict[str, Any]) -> None:
    sock.sendall((json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8"))


def _read_lines(sock: socket.socket) -> Any:
    buf = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if not line:
                continue
            try:
                yield json.loads(line.decode("utf-8"))
            except Exception:
                continue


def _handle_key(name: str) -> Optional[Any]:
    if not keyboard:
        return None
    special = {
        "enter": keyboard.Key.enter,
        "backspace": keyboard.Key.backspace,
        "tab": keyboard.Key.tab,
        "esc": keyboard.Key.esc,
        "space": keyboard.Key.space,
        "up": keyboard.Key.up,
        "down": keyboard.Key.down,
        "left": keyboard.Key.left,
        "right": keyboard.Key.right,
        "shift": keyboard.Key.shift,
        "ctrl": keyboard.Key.ctrl,
        "alt": keyboard.Key.alt,
        "cmd": keyboard.Key.cmd,
    }
    if name in special:
        return special[name]
    if len(name) == 1:
        return name
    return None


def _handle_input(event: str, data: dict[str, Any]) -> None:
    if event == "move" and mouse_controller:
        _maybe_refocus()
        dx = float(data.get("dx") or 0.0) * settings.mouse_sensitivity
        dy = float(data.get("dy") or 0.0) * settings.mouse_sensitivity
        mouse_mover.add(dx, dy)
        _bump("move")
        if settings.log_input_verbose:
            print(f"move dx={dx:.2f} dy={dy:.2f}")
        return

    if event == "scroll" and mouse_controller:
        _maybe_refocus()
        dy = float(data.get("dy") or 0.0)
        dy = max(-settings.max_scroll, min(settings.max_scroll, dy))
        mouse_controller.scroll(0, int(dy))
        _bump("scroll")
        if settings.log_input_verbose:
            print(f"scroll dy={dy:.2f}")
        return

    if event == "click" and mouse_controller and mouse:
        _maybe_refocus()
        button_name = str(data.get("button") or "left")
        down = bool(data.get("down", True))
        mapping = {"left": mouse.Button.left, "right": mouse.Button.right, "middle": mouse.Button.middle}
        btn = mapping.get(button_name, mouse.Button.left)
        if down:
            mouse_controller.press(btn)
        else:
            mouse_controller.release(btn)
        _bump("click")
        if settings.log_input_verbose:
            print(f"click button={button_name} down={down}")
        return

    if event == "type_text" and keyboard_controller:
        _maybe_refocus()
        text = str(data.get("text") or "")
        if text:
            keyboard_controller.type(text)
            _bump("type")
            if settings.log_input_verbose:
                print(f"type_text len={len(text)}")
        return

    if event == "key" and keyboard_controller:
        _maybe_refocus()
        name = str(data.get("name") or "")
        down = bool(data.get("down", True))
        key_obj = _handle_key(name)
        if key_obj is None:
            return
        if down:
            keyboard_controller.press(key_obj)
        else:
            keyboard_controller.release(key_obj)
        _bump("key")
        if settings.log_input_verbose:
            print(f"key name={name} down={down}")
        return

    if event == "pad_left":
        _maybe_refocus()
        if not gamepad_enabled:
            return
        gamepad.set_left_stick(float(data.get("x") or 0.0), float(data.get("y") or 0.0))
        _bump("pad")
        return

    if event == "pad_right":
        _maybe_refocus()
        if not gamepad_enabled:
            return
        gamepad.set_right_stick(float(data.get("x") or 0.0), float(data.get("y") or 0.0))
        _bump("pad")
        return

    if event == "pad_trigger":
        _maybe_refocus()
        if not gamepad_enabled:
            return
        gamepad.set_trigger(str(data.get("which") or ""), float(data.get("value") or 0.0))
        _bump("pad")
        return

    if event == "pad_button":
        _maybe_refocus()
        if not gamepad_enabled:
            return
        gamepad.set_button(str(data.get("name") or ""), bool(data.get("down", True)))
        _bump("pad")
        return


def serve_forever() -> None:
    armed = False
    current_client: dict[str, Any] = {}

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((settings.listen_host, settings.listen_port))
        server.listen(1)

        print(f"Host listening on {settings.listen_host}:{settings.listen_port}")
        print(f"mouse={bool(mouse_controller)} keyboard={bool(keyboard_controller)} gamepad={gamepad.ready}")
        if settings.enable_gamepad and not gamepad.ready:
            print(f"Gamepad not ready: {gamepad.error} (install ViGEmBus + `pip install -r requirements-gamepad.txt`)")
        if gamepad.ready:
            print("Tip: run `joy.cpl` to verify the virtual Xbox 360 controller exists.")
        print("Waiting for app.py to connect...")

        while True:
            conn, addr = server.accept()
            with conn:
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                print(f"Relay connected from {addr[0]}:{addr[1]}")
                _send_line(conn, _status_payload())

                armed = False
                current_client = {}

                try:
                    for msg in _read_lines(conn):
                        if not isinstance(msg, dict):
                            continue
                        t = msg.get("t")
                        if t == "hello":
                            _send_line(conn, _status_payload())
                            continue
                        if t == "rpc":
                            req_id = str(msg.get("id") or "")
                            method = str(msg.get("m") or "")
                            params = msg.get("p") if isinstance(msg.get("p"), dict) else {}
                            try:
                                result = _handle_rpc(method, params)
                                ok = "error" not in result
                                _send_line(conn, {"t": "rpc_result", "id": req_id, "ok": ok, "result": result, "error": result.get("error")})
                            except Exception as e:
                                _send_line(conn, {"t": "rpc_result", "id": req_id, "ok": False, "error": repr(e)})
                            continue
                        if t == "client":
                            state = str(msg.get("state") or "")
                            meta = msg.get("meta") if isinstance(msg.get("meta"), dict) else {}
                            if state == "connected":
                                current_client = meta
                                armed = True
                                who = meta.get("ip") or "unknown"
                                print(f"Phone connected ({who}); input enabled")
                            elif state == "disconnected":
                                armed = False
                                print("Phone disconnected; input disabled")
                            continue
                        if t == "input":
                            if not armed:
                                continue
                            event = str(msg.get("e") or "")
                            data = msg.get("d") if isinstance(msg.get("d"), dict) else {}
                            _handle_input(event, data)
                            continue
                except Exception:
                    pass
                finally:
                    armed = False
                    current_client = {}
                    print("Relay disconnected; waiting...")


if __name__ == "__main__":
    # Avoid pynput listener threads staying alive on Ctrl+C.
    threading.current_thread().name = "host-main"
    serve_forever()
