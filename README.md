# Mem Controller (phone → PC touchpad/gamepad)

Run a small Flask web app on your Windows PC, open it in your phone’s browser, and use it as:

- a touchpad + mouse buttons
- a typing bridge (phone keyboard → PC)
- an optional virtual Xbox 360 controller via ViGEm (vgamepad)

This is split into:

- `app.py`: serves the phone UI + receives Socket.IO events
- `host.py`: runs on the PC and injects mouse/keyboard/gamepad input (waits for a phone connection before enabling input)

## Setup

From `C:\Users\tai2l\Documents\Mem Controller`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python host.py
```

On your phone (same Wi‑Fi), open:

- `http://<PC-IP>:5000/`

If Windows Firewall prompts you, allow access on your private network.

In a second terminal (same venv), start the web UI:

```powershell
python app.py
```

If the phone gets stuck on “Connecting…”, open `http://<PC-IP>:5000/diag` to confirm the server is reachable and check Windows Firewall.

## Optional: require a token

```powershell
$env:MEMCTRL_TOKEN = "change-me"
python host.py
python app.py
```

Then open:

- `http://<PC-IP>:5000/?token=change-me`

## Optional: virtual Xbox controller (ViGEm)

1. Install the ViGEmBus driver (Nefarius ViGEmBus).
2. Install the Python package:

```powershell
pip install -r requirements-gamepad.txt
```

3. Enable it:

```powershell
$env:MEMCTRL_ENABLE_GAMEPAD = "1"
python host.py
python app.py
```

If the UI shows `no-pad`, either `vgamepad` isn’t installed or ViGEmBus isn’t working.

## Tuning

Environment variables:

- `MEMCTRL_MOUSE_SENS` (default `1.0`)
- `MEMCTRL_JOYSTICK_SENS` (default `1.0`)
- `MEMCTRL_MAX_MOVE_PX` (default `200`)
- `MEMCTRL_MAX_SCROLL` (default `120`)
- `MEMCTRL_HOST` (default `0.0.0.0`)
- `MEMCTRL_PORT` (default `5000`)
- `MEMCTRL_RELAY_HOST` (default `127.0.0.1`) for `app.py` → `host.py`
- `MEMCTRL_RELAY_PORT` (default `8765`) for `app.py` → `host.py`
- `MEMCTRL_AUTOSTART_HOST` (default `0`) set to `1` to auto-launch `host.py` from `app.py`
