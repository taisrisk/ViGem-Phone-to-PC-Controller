import json
import os
import socket
import threading
from dataclasses import dataclass
from typing import Any, Optional

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


def load_settings() -> Settings:
    return Settings(
        listen_host=os.getenv("MEMCTRL_RELAY_LISTEN_HOST", "127.0.0.1"),
        listen_port=int(os.getenv("MEMCTRL_RELAY_PORT", "8765")),
        mouse_sensitivity=float(os.getenv("MEMCTRL_MOUSE_SENS", "1.0")),
        joystick_sensitivity=float(os.getenv("MEMCTRL_JOYSTICK_SENS", "1.0")),
        max_move_px=int(os.getenv("MEMCTRL_MAX_MOVE_PX", "200")),
        max_scroll=int(os.getenv("MEMCTRL_MAX_SCROLL", "120")),
        enable_gamepad=os.getenv("MEMCTRL_ENABLE_GAMEPAD", "0") in {"1", "true", "True"},
    )


settings = load_settings()


mouse_controller = mouse.Controller() if mouse else None
keyboard_controller = keyboard.Controller() if keyboard else None


class GamepadAdapter:
    def __init__(self, enabled: bool) -> None:
        self._pad = None
        self._buttons = None
        self._ready = False

        if not enabled:
            return
        try:
            import vgamepad as vg  # type: ignore

            self._pad = vg.VX360Gamepad()
            self._buttons = vg.XUSB_BUTTON
            self._ready = True
        except Exception:
            self._pad = None
            self._buttons = None
            self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

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


gamepad = GamepadAdapter(settings.enable_gamepad)


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
        dx = float(data.get("dx") or 0.0) * settings.mouse_sensitivity
        dy = float(data.get("dy") or 0.0) * settings.mouse_sensitivity
        dx = max(-settings.max_move_px, min(settings.max_move_px, dx))
        dy = max(-settings.max_move_px, min(settings.max_move_px, dy))
        mouse_controller.move(int(dx), int(dy))
        return

    if event == "scroll" and mouse_controller:
        dy = float(data.get("dy") or 0.0)
        dy = max(-settings.max_scroll, min(settings.max_scroll, dy))
        mouse_controller.scroll(0, int(dy))
        return

    if event == "click" and mouse_controller and mouse:
        button_name = str(data.get("button") or "left")
        down = bool(data.get("down", True))
        mapping = {"left": mouse.Button.left, "right": mouse.Button.right, "middle": mouse.Button.middle}
        btn = mapping.get(button_name, mouse.Button.left)
        if down:
            mouse_controller.press(btn)
        else:
            mouse_controller.release(btn)
        return

    if event == "type_text" and keyboard_controller:
        text = str(data.get("text") or "")
        if text:
            keyboard_controller.type(text)
        return

    if event == "key" and keyboard_controller:
        name = str(data.get("name") or "")
        down = bool(data.get("down", True))
        key_obj = _handle_key(name)
        if key_obj is None:
            return
        if down:
            keyboard_controller.press(key_obj)
        else:
            keyboard_controller.release(key_obj)
        return

    if event == "pad_left":
        gamepad.set_left_stick(float(data.get("x") or 0.0), float(data.get("y") or 0.0))
        return

    if event == "pad_right":
        gamepad.set_right_stick(float(data.get("x") or 0.0), float(data.get("y") or 0.0))
        return

    if event == "pad_trigger":
        gamepad.set_trigger(str(data.get("which") or ""), float(data.get("value") or 0.0))
        return

    if event == "pad_button":
        gamepad.set_button(str(data.get("name") or ""), bool(data.get("down", True)))
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
        print("Waiting for app.py to connect...")

        while True:
            conn, addr = server.accept()
            with conn:
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                print(f"Relay connected from {addr[0]}:{addr[1]}")
                _send_line(
                    conn,
                    {"t": "status", "mouse": bool(mouse_controller), "keyboard": bool(keyboard_controller), "gamepad": gamepad.ready},
                )

                armed = False
                current_client = {}

                try:
                    for msg in _read_lines(conn):
                        if not isinstance(msg, dict):
                            continue
                        t = msg.get("t")
                        if t == "hello":
                            _send_line(
                                conn,
                                {"t": "status", "mouse": bool(mouse_controller), "keyboard": bool(keyboard_controller), "gamepad": gamepad.ready},
                            )
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

