import json
import math
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
    input_mode: int
    kbm_cam_sens: float


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
        # 0 = ViGEm virtual Xbox (vgamepad), 1 = custom KBM mapping for games.
        input_mode=int(os.getenv("MEMCTRL_INPUT_MODE", "1")),
        # Multiplier for KBM camera pad deltas.
        kbm_cam_sens=float(os.getenv("MEMCTRL_KBM_CAM_SENS", "5.0")),
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
input_mode = 0 if int(settings.input_mode) == 0 else 1

# In KBM mode, the "gamepad" UI becomes a key/mouse mapper.
gamepad = GamepadAdapter(gamepad_enabled and input_mode == 0)


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
                # Accumulate fractional motion until it crosses whole pixels.
                mx = int(math.floor(dx)) if dx >= 0 else int(math.ceil(dx))
                my = int(math.floor(dy)) if dy >= 0 else int(math.ceil(dy))
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


class KbmMapper:
    def __init__(self) -> None:
        self._w = False
        self._a = False
        self._s = False
        self._d = False
        self._lmb = False
        self._rmb = False
        self._rmb_trigger = False
        self._camera_active = False
        self.camera_drag = False
        self._shift = False
        self._ctrl = False

        self._right_x = 0.0
        self._right_y = 0.0
        self._last_cam_move = 0.0

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._camera_loop, name="kbm-camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _press(self, key: Any) -> None:
        if keyboard_controller:
            keyboard_controller.press(key)

    def _release(self, key: Any) -> None:
        if keyboard_controller:
            keyboard_controller.release(key)

    def _mouse_down(self, btn: Any) -> None:
        if mouse_controller:
            mouse_controller.press(btn)

    def _mouse_up(self, btn: Any) -> None:
        if mouse_controller:
            mouse_controller.release(btn)

    def _update_rmb(self) -> None:
        if not mouse:
            return
        want = bool(self._rmb_trigger or (self._camera_active and self.camera_drag))
        if want and not self._rmb:
            self._rmb = True
            self._mouse_down(mouse.Button.right)
        if not want and self._rmb:
            self._rmb = False
            self._mouse_up(mouse.Button.right)

    def set_left_stick(self, x: float, y: float) -> None:
        # WASD mapping with deadzone/threshold.
        deadzone = 0.18
        thresh = 0.35
        x = 0.0 if abs(x) < deadzone else x
        y = 0.0 if abs(y) < deadzone else y
        want_w = y > thresh
        want_s = y < -thresh
        want_d = x > thresh
        want_a = x < -thresh

        def flip(state: bool, want: bool, key: str) -> bool:
            if want and not state:
                self._press(key)
                return True
            if not want and state:
                self._release(key)
                return False
            return state

        self._w = flip(self._w, want_w, "w")
        self._s = flip(self._s, want_s, "s")
        self._a = flip(self._a, want_a, "a")
        self._d = flip(self._d, want_d, "d")

    def set_right_stick(self, x: float, y: float) -> None:
        with self._lock:
            self._right_x = float(x)
            self._right_y = float(y)

    def camera_move(self, dx: float, dy: float) -> None:
        if not mouse_controller:
            return
        dx = float(dx) * float(settings.kbm_cam_sens)
        dy = float(dy) * float(settings.kbm_cam_sens)
        mouse_mover.add(dx, dy)

    def set_camera_active(self, active: bool) -> None:
        self._camera_active = bool(active)
        self._update_rmb()

    def set_trigger(self, which: str, value: float) -> None:
        # RT = LMB, LT = RMB
        down = float(value) > 0.5
        if not mouse:
            return
        if which == "rt":
            if down and not self._lmb:
                self._lmb = True
                self._mouse_down(mouse.Button.left)
            if not down and self._lmb:
                self._lmb = False
                self._mouse_up(mouse.Button.left)
        elif which == "lt":
            self._rmb_trigger = bool(down)
            self._update_rmb()

    def set_button(self, name: str, pressed: bool) -> None:
        # Reasonable default mapping; can be refined later.
        mapping = {
            "a": "space",
            "b": "c",
            "x": "r",
            "y": "e",
            "back": "esc",
            "start": "enter",
            "dup": "up",
            "ddown": "down",
            "dleft": "left",
            "dright": "right",
        }
        modmap = {"lb": "shift", "rb": "ctrl"}

        if name in modmap:
            key = modmap[name]
            if not keyboard:
                return
            key_obj = keyboard.Key.shift if key == "shift" else keyboard.Key.ctrl
            if pressed:
                self._press(key_obj)
            else:
                self._release(key_obj)
            return

        keyname = mapping.get(name)
        if not keyname:
            return
        key_obj = _handle_key(keyname)
        if key_obj is None:
            return
        if pressed:
            self._press(key_obj)
        else:
            self._release(key_obj)

    def release_all(self) -> None:
        # Release movement + mouse buttons.
        for key, state in (("w", self._w), ("a", self._a), ("s", self._s), ("d", self._d)):
            if state:
                self._release(key)
        self._w = self._a = self._s = self._d = False

        if mouse and self._lmb:
            self._mouse_up(mouse.Button.left)
        self._rmb_trigger = False
        self._camera_active = False
        self._update_rmb()
        self._lmb = self._rmb = False

        if keyboard and self._shift:
            self._release(keyboard.Key.shift)
        if keyboard and self._ctrl:
            self._release(keyboard.Key.ctrl)
        self._shift = self._ctrl = False

        with self._lock:
            self._right_x = 0.0
            self._right_y = 0.0
    def _camera_loop(self) -> None:
        # Continuous camera movement from right stick.
        if not mouse_controller:
            return
        hz = 120.0
        period = 1.0 / hz
        speed = float(os.getenv("MEMCTRL_KBM_CAM_SPEED", "18.0"))
        deadzone = 0.12
        while not self._stop.is_set():
            time.sleep(period)
            with self._lock:
                x = self._right_x
                y = self._right_y
            moving = not (abs(x) < deadzone and abs(y) < deadzone)
            if not moving:
                continue
            mouse_mover.add(x * speed, -y * speed)


kbm = KbmMapper()


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
kbm_camera_drag_enabled = False


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
        "input_mode": int(input_mode),
        "kbm_camera_drag": bool(kbm_camera_drag_enabled),
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
        if input_mode == 1:
            kbm.release_all()
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

    # Enabling: create/recreate the virtual device (only in ViGEm mode).
    if input_mode == 0:
        gamepad = GamepadAdapter(True)
    return _status_snapshot()


def _handle_rpc(method: str, params: dict[str, Any]) -> dict[str, Any]:
    global focus_lock_enabled, selected_window, gamepad, input_mode, kbm_camera_drag_enabled

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

    if method == "set_input_mode":
        new_mode = int(params.get("mode", 0))
        new_mode = 0 if new_mode == 0 else 1
        if new_mode != input_mode:
            input_mode = new_mode
            # Recreate/destroy virtual device depending on mode.
            if input_mode == 0 and gamepad_enabled:
                gamepad = GamepadAdapter(True)
            if input_mode == 1:
                gamepad = GamepadAdapter(False)
                kbm.release_all()
        return _status_snapshot()

    if method == "set_kbm_camera_drag":
        kbm_camera_drag_enabled = bool(params.get("enabled", False))
        kbm.camera_drag = bool(kbm_camera_drag_enabled)
        return _status_snapshot()

    if method == "pad_reset":
        # Reset is meaningful only if enabled.
        if not gamepad_enabled:
            return _status_snapshot()
        if input_mode == 0:
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
        x = float(data.get("x") or 0.0)
        y = float(data.get("y") or 0.0)
        if input_mode == 0:
            gamepad.set_left_stick(x, y)
        else:
            kbm.set_left_stick(x, y)
        _bump("pad")
        return

    if event == "pad_right":
        _maybe_refocus()
        if not gamepad_enabled:
            return
        x = float(data.get("x") or 0.0)
        y = float(data.get("y") or 0.0)
        if input_mode == 0:
            gamepad.set_right_stick(x, y)
        else:
            kbm.set_right_stick(x, y)
        _bump("pad")
        return

    if event == "pad_trigger":
        _maybe_refocus()
        if not gamepad_enabled:
            return
        which = str(data.get("which") or "")
        value = float(data.get("value") or 0.0)
        if input_mode == 0:
            gamepad.set_trigger(which, value)
        else:
            kbm.set_trigger(which, value)
        _bump("pad")
        return

    if event == "pad_button":
        _maybe_refocus()
        if not gamepad_enabled:
            return
        name = str(data.get("name") or "")
        down = bool(data.get("down", True))
        if input_mode == 0:
            gamepad.set_button(name, down)
        else:
            kbm.set_button(name, down)
        _bump("pad")
        return

    if event == "kbm_cam_move":
        _maybe_refocus()
        if not gamepad_enabled or input_mode != 1:
            return
        dx = float(data.get("dx") or 0.0)
        dy = float(data.get("dy") or 0.0)
        # Clamp spikes; phone can sometimes produce large deltas on touch resume.
        dx = max(-120.0, min(120.0, dx))
        dy = max(-120.0, min(120.0, dy))
        kbm.camera_move(dx, dy)
        _bump("pad")
        if settings.log_input_verbose:
            print(f"kbm_cam_move dx={dx:.2f} dy={dy:.2f} drag={int(kbm.camera_drag)}")
        return

    if event == "kbm_cam_hold":
        _maybe_refocus()
        if not gamepad_enabled or input_mode != 1:
            return
        down = bool(data.get("down", False))
        # Only hold RMB during camera use (as requested).
        kbm.set_camera_active(down)
        if settings.log_input_verbose:
            print(f"kbm_cam_hold down={int(down)} enabled={int(kbm.camera_drag)}")
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
                                kbm.release_all()
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
                    kbm.release_all()
                    print("Relay disconnected; waiting...")


if __name__ == "__main__":
    # Avoid pynput listener threads staying alive on Ctrl+C.
    threading.current_thread().name = "host-main"
    serve_forever()
