/* global io */

const statusEl = document.getElementById("status");
const touchpad = document.getElementById("touchpad");
const vizCanvas = document.getElementById("viz");
const hiddenInput = document.getElementById("hidden-input");
const keyboardBtn = document.getElementById("btn-keyboard");
const selectWindowBtn = document.getElementById("btn-select-window");
const gamepadToggle = document.getElementById("toggle-gamepad");
const kbmToggle = document.getElementById("toggle-kbm");
const lockFocusToggle = document.getElementById("toggle-lock-focus");
const padResetBtn = document.getElementById("btn-pad-reset");
const selectedWindowEl = document.getElementById("selected-window");
const selectedWindowKbmEl = document.getElementById("selected-window-kbm");
const selectWindowBtnKbm = document.getElementById("btn-select-window-kbm");
const padResetBtnKbm = document.getElementById("btn-pad-reset-kbm");
const gamepadToggleKbm = document.getElementById("toggle-gamepad-kbm");
const lockFocusToggleKbm = document.getElementById("toggle-lock-focus-kbm");
const cameraDragToggle = document.getElementById("toggle-camera-drag");
const layoutVigem = document.getElementById("layout-vigem");
const layoutKbm = document.getElementById("layout-kbm");
const kbmMovePad = document.getElementById("kbm-move-pad");
const kbmCamPad = document.getElementById("kbm-cam-pad");

if (typeof io === "undefined") {
  statusEl.textContent = "Error: Socket.IO client missing (/static/vendor/socket.io.min.js)";
  throw new Error("Socket.IO client missing");
}

let socket;
try {
  socket = io({
    auth: { token: window.MEMCTRL_TOKEN || "" },
    // Use polling first for maximum compatibility; it will upgrade to websocket when possible.
    transports: ["polling", "websocket"],
    timeout: 8000,
    reconnection: true,
    reconnectionDelay: 300,
    reconnectionDelayMax: 1500,
  });
} catch (e) {
  statusEl.textContent = `Error: Socket.IO init failed (${String(e)})`;
  throw e;
}

socket.on("connect", () => {
  const t = socket.io && socket.io.engine && socket.io.engine.transport ? socket.io.engine.transport.name : "?";
  statusEl.textContent = `Connected (${t})`;

  socket.emit("get_selected_window", {}, (resp) => {
    if (resp && resp.ok && resp.result) applyHostState(resp.result);
  });
  // Ask host for current mode/status (populates KBM + gamepad enabled flags).
  socket.emit("get_selected_window", {}, () => {});
});
socket.on("disconnect", () => {
  statusEl.textContent = "Disconnected";
});
socket.on("connect_error", (err) => {
  const msg = err && err.message ? err.message : String(err);
  let hint = "";
  if (msg.toLowerCase().includes("timeout")) {
    hint = " (check PC IP/Wi-Fi + Windows Firewall; start with `python app.py`)";
  }
  statusEl.textContent = `Error: ${msg}${hint}`;
});
socket.on("server_status", (s) => {
  const parts = [];
  if (!window.MEMCTRL_TOKEN) parts.push("no-token");
  parts.push(s.mouse ? "mouse" : "no-mouse");
  parts.push(s.keyboard ? "kbd" : "no-kbd");
  parts.push(s.gamepad ? "pad" : "no-pad");
  parts.push(s.relay ? "host" : "no-host");
  const t = socket.io && socket.io.engine && socket.io.engine.transport ? socket.io.engine.transport.name : s.transport || "?";
  const padErr = s.gamepad ? "" : s.gamepad_error ? " (pad err)" : "";
  statusEl.textContent = `Connected (${t}; ${parts.join(", ")})${padErr}`;
  if (s.selected_window || typeof s.focus_lock === "boolean") applyHostState(s);
  if (!s.gamepad && s.gamepad_error && selectedWindowEl && selectedWindowEl.textContent === "No window selected") {
    selectedWindowEl.textContent = `Pad error: ${String(s.gamepad_error).slice(0, 60)}`;
  }

  if (!s.mouse) {
    touchpad.classList.add("disabled");
    document.querySelectorAll("[data-click]").forEach((b) => b.setAttribute("disabled", "disabled"));
  }
  if (!s.keyboard) {
    keyboardBtn.setAttribute("disabled", "disabled");
  }
  if (!s.gamepad) {
    document.querySelectorAll("[data-pad]").forEach((b) => b.setAttribute("disabled", "disabled"));
    document.querySelectorAll("[data-stick]").forEach((a) => a.classList.add("disabled"));
    document.querySelectorAll("[data-triggerbtn]").forEach((t) => t.setAttribute("disabled", "disabled"));
  }
});

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

// Generic touch tracer for landscape pads.
function createTracer(container, kind) {
  if (!container) return null;
  container.style.position = container.style.position || "relative";
  const canvas = document.createElement("canvas");
  canvas.className = "trace-canvas";
  canvas.setAttribute("aria-hidden", "true");
  container.appendChild(canvas);

  const state = {
    kind,
    canvas,
    points: [],
    active: new Map(),
    running: false,
  };

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const r = container.getBoundingClientRect();
    canvas.width = Math.max(1, Math.floor(r.width * dpr));
    canvas.height = Math.max(1, Math.floor(r.height * dpr));
    canvas.style.width = `${r.width}px`;
    canvas.style.height = `${r.height}px`;
  }

  function local(clientX, clientY) {
    const r = container.getBoundingClientRect();
    return { x: clientX - r.left, y: clientY - r.top };
  }

  function start() {
    if (state.running) return;
    state.running = true;
    window.requestAnimationFrame(render);
  }

  function addPoint(clientX, clientY) {
    const p = local(clientX, clientY);
    state.points.push({ ...p, t: performance.now() });
    if (state.points.length > 240) state.points.splice(0, state.points.length - 240);
    start();
  }

  function setActive(id, clientX, clientY) {
    state.active.set(id, local(clientX, clientY));
    start();
  }

  function clearActive(id) {
    state.active.delete(id);
  }

  function render() {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const now = performance.now();
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    const maxAge = 700;
    state.points = state.points.filter((p) => now - p.t < maxAge);
    const pts = state.points;

    const baseColor = kind === "cam" ? [167, 139, 250] : [96, 165, 250];

    function drawSmooth(seg) {
      if (seg.length < 2) return;
      const last = seg[seg.length - 1];
      const age = now - last.t;
      const alpha = clamp(1 - age / maxAge, 0, 1);
      ctx.strokeStyle = `rgba(${baseColor[0]},${baseColor[1]},${baseColor[2]},${0.55 * alpha})`;
      ctx.lineWidth = 3 * dpr * (0.6 + 0.4 * alpha);
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.beginPath();
      ctx.moveTo(seg[0].x * dpr, seg[0].y * dpr);
      for (let i = 1; i < seg.length - 1; i++) {
        const p = seg[i];
        const n = seg[i + 1];
        const mx = (p.x + n.x) / 2;
        const my = (p.y + n.y) / 2;
        ctx.quadraticCurveTo(p.x * dpr, p.y * dpr, mx * dpr, my * dpr);
      }
      const end = seg[seg.length - 1];
      ctx.lineTo(end.x * dpr, end.y * dpr);
      ctx.stroke();
    }
    drawSmooth(pts);

    state.active.forEach((p) => {
      ctx.fillStyle = `rgba(${baseColor[0]},${baseColor[1]},${baseColor[2]},0.85)`;
      ctx.beginPath();
      ctx.arc(p.x * dpr, p.y * dpr, 6 * dpr, 0, Math.PI * 2);
      ctx.fill();
    });

    if (state.points.length || state.active.size) {
      window.requestAnimationFrame(render);
    } else {
      state.running = false;
    }
  }

  resize();
  window.addEventListener("resize", resize);
  window.addEventListener("orientationchange", resize);

  return { addPoint, setActive, clearActive };
}

const moveTracer = createTracer(kbmMovePad, "move");
const camTracer = createTracer(kbmCamPad, "cam");

// Trigger hold manager (prevents different UI elements fighting over LT/RT).
const triggerHolds = { lt: new Set(), rt: new Set() };
function updateTrigger(which) {
  const held = triggerHolds[which] && triggerHolds[which].size > 0;
  socket.emit("pad_trigger", { which, value: held ? 1.0 : 0.0 });
}
function setTriggerHold(which, source, down) {
  if (!triggerHolds[which]) return;
  if (down) triggerHolds[which].add(source);
  else triggerHolds[which].delete(source);
  updateTrigger(which);
}

function bindHoldButton(btn, onDown, onUp) {
  if (!btn) return;
  if (window.PointerEvent) {
    const active = new Set();
    const down = (e) => {
      // Only left button for mouse.
      if (e.pointerType === "mouse" && e.button !== 0) return;
      e.preventDefault();
      active.add(e.pointerId);
      try {
        btn.setPointerCapture(e.pointerId);
      } catch (_) {}
      if (active.size === 1) onDown();
    };
    const up = (e) => {
      if (!active.has(e.pointerId)) return;
      e.preventDefault();
      active.delete(e.pointerId);
      if (active.size === 0) onUp();
    };
    btn.addEventListener("pointerdown", down);
    btn.addEventListener("pointerup", up);
    btn.addEventListener("pointercancel", up);
    btn.addEventListener("lostpointercapture", up);
    return;
  }

  // Fallback (older browsers): touch + mouse.
  btn.addEventListener("touchstart", (e) => {
    e.preventDefault();
    onDown();
  });
  btn.addEventListener("touchend", (e) => {
    e.preventDefault();
    onUp();
  });
  btn.addEventListener("touchcancel", (e) => {
    e.preventDefault();
    onUp();
  });
  btn.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    onDown();
  });
  btn.addEventListener("mouseup", onUp);
  btn.addEventListener("mouseleave", onUp);
}

// Automatic UI switching:
// - Portrait: touchpad mode
// - Landscape: gamepad mode
const portraitPane = document.getElementById("portrait-pane");
const landscapePane = document.getElementById("landscape-pane");
function updateMode() {
  const isLandscape = window.matchMedia && window.matchMedia("(orientation: landscape)").matches;
  if (portraitPane && landscapePane) {
    portraitPane.style.display = isLandscape ? "none" : "";
    landscapePane.style.display = isLandscape ? "grid" : "none";
  }
}
window.addEventListener("resize", updateMode);
window.addEventListener("orientationchange", updateMode);
updateMode();

function applyHostState(s) {
  const sel = s.selected_window || (s.result && s.result.selected_window) || null;
  const lock = typeof s.focus_lock === "boolean" ? s.focus_lock : (s.result && s.result.focus_lock);
  const gp = typeof s.gamepad_enabled === "boolean" ? s.gamepad_enabled : (s.result && s.result.gamepad_enabled);
  const mode = typeof s.input_mode === "number" ? s.input_mode : (s.result && s.result.input_mode);
  const camDrag =
    typeof s.kbm_camera_drag === "boolean" ? s.kbm_camera_drag : (s.result && s.result.kbm_camera_drag);
  if (lockFocusToggle && typeof lock === "boolean") lockFocusToggle.checked = lock;
  if (gamepadToggle && typeof gp === "boolean") gamepadToggle.checked = gp;
  if (kbmToggle && (mode === 0 || mode === 1)) kbmToggle.checked = mode === 1;
  if (gamepadToggleKbm && typeof gp === "boolean") gamepadToggleKbm.checked = gp;
  if (lockFocusToggleKbm && typeof lock === "boolean") lockFocusToggleKbm.checked = lock;
  if (cameraDragToggle && typeof camDrag === "boolean") cameraDragToggle.checked = camDrag;
  if (selectedWindowEl) {
    if (sel && sel.title) selectedWindowEl.textContent = sel.title;
    else if (sel && sel.hwnd) selectedWindowEl.textContent = `HWND ${sel.hwnd}`;
    else selectedWindowEl.textContent = "No window selected";
  }
  if (selectedWindowKbmEl) {
    if (sel && sel.title) selectedWindowKbmEl.textContent = sel.title;
    else if (sel && sel.hwnd) selectedWindowKbmEl.textContent = `HWND ${sel.hwnd}`;
    else selectedWindowKbmEl.textContent = "No window selected";
  }

  // Switch landscape layout based on KBM checkbox.
  if (layoutVigem && layoutKbm && kbmToggle) {
    const showKbm = !!kbmToggle.checked;
    layoutVigem.style.display = showKbm ? "none" : "";
    layoutKbm.style.display = showKbm ? "grid" : "none";
  }
}

function rpc(event, data, cb) {
  socket.emit(event, data || {}, (resp) => {
    if (resp && resp.ok && resp.result) applyHostState(resp.result);
    if (typeof cb === "function") cb(resp);
  });
}

let reconnectInFlight = false;
function resetAndReconnect() {
  if (reconnectInFlight) return;
  reconnectInFlight = true;

  // Try to reset host-side state first.
  try {
    rpc("pad_reset", {});
  } catch (_) {}

  // Stop any active intervals so they don't flood after reconnect.
  try {
    stopCam();
  } catch (_) {}
  try {
    if (moveInterval) {
      window.clearInterval(moveInterval);
      moveInterval = null;
    }
    pendingDx = 0;
    pendingDy = 0;
    lastX = null;
    lastY = null;
  } catch (_) {}

  // Release any trigger holds + camera hold.
  try {
    socket.emit("kbm_cam_hold", { down: false });
    triggerHolds.lt.clear();
    triggerHolds.rt.clear();
    updateTrigger("lt");
    updateTrigger("rt");
  } catch (_) {}

  // Force reconnect even if already connected.
  try {
    socket.disconnect();
  } catch (_) {}
  window.setTimeout(() => {
    try {
      socket.connect();
    } catch (_) {}
    reconnectInFlight = false;
  }, 350);
}

if (selectWindowBtn) {
  selectWindowBtn.addEventListener("click", () => {
    rpc("select_window", {}, () => {
      // Selecting a window implies gaming mode.
      if (gamepadToggle) gamepadToggle.checked = true;
      rpc("set_gamepad_enabled", { enabled: true });
    });
  });
  selectWindowBtn.addEventListener("touchstart", (e) => {
    e.preventDefault();
    rpc("select_window", {}, () => {
      if (gamepadToggle) gamepadToggle.checked = true;
      rpc("set_gamepad_enabled", { enabled: true });
    });
  });
}
if (gamepadToggle) {
  gamepadToggle.addEventListener("change", () => rpc("set_gamepad_enabled", { enabled: gamepadToggle.checked }));
}
if (kbmToggle) {
  kbmToggle.addEventListener("change", () => rpc("set_input_mode", { mode: kbmToggle.checked ? 1 : 0 }));
}
if (lockFocusToggle) {
  lockFocusToggle.addEventListener("change", () => rpc("set_focus_lock", { enabled: lockFocusToggle.checked }));
}
if (padResetBtn) {
  padResetBtn.addEventListener("click", () => resetAndReconnect());
  padResetBtn.addEventListener("touchstart", (e) => {
    e.preventDefault();
    resetAndReconnect();
  });
}

// KBM layout controls.
if (selectWindowBtnKbm) {
  selectWindowBtnKbm.addEventListener("click", () => {
    rpc("select_window", {}, () => {
      if (gamepadToggleKbm) gamepadToggleKbm.checked = true;
      rpc("set_gamepad_enabled", { enabled: true });
    });
  });
  selectWindowBtnKbm.addEventListener("touchstart", (e) => {
    e.preventDefault();
    rpc("select_window", {}, () => {
      if (gamepadToggleKbm) gamepadToggleKbm.checked = true;
      rpc("set_gamepad_enabled", { enabled: true });
    });
  });
}
if (padResetBtnKbm) {
  padResetBtnKbm.addEventListener("click", () => resetAndReconnect());
  padResetBtnKbm.addEventListener("touchstart", (e) => {
    e.preventDefault();
    resetAndReconnect();
  });
}
if (gamepadToggleKbm) {
  gamepadToggleKbm.addEventListener("change", () => rpc("set_gamepad_enabled", { enabled: gamepadToggleKbm.checked }));
}
if (lockFocusToggleKbm) {
  lockFocusToggleKbm.addEventListener("change", () => rpc("set_focus_lock", { enabled: lockFocusToggleKbm.checked }));
}
if (cameraDragToggle) {
  cameraDragToggle.addEventListener("change", () => rpc("set_kbm_camera_drag", { enabled: cameraDragToggle.checked }));
}

// Touch visualization (trails + ripples).
const viz = {
  points: [],
  ripples: [],
  active: new Map(),
  running: false,
};

function resizeViz() {
  if (!vizCanvas) return;
  const dpr = window.devicePixelRatio || 1;
  const rect = touchpad.getBoundingClientRect();
  vizCanvas.width = Math.max(1, Math.floor(rect.width * dpr));
  vizCanvas.height = Math.max(1, Math.floor(rect.height * dpr));
  vizCanvas.style.width = `${rect.width}px`;
  vizCanvas.style.height = `${rect.height}px`;
}

function localPoint(clientX, clientY) {
  const rect = touchpad.getBoundingClientRect();
  const x = clientX - rect.left;
  const y = clientY - rect.top;
  return { x, y };
}

function addVizPoint(clientX, clientY, kind) {
  const p = localPoint(clientX, clientY);
  viz.points.push({ ...p, kind, t: performance.now() });
  if (viz.points.length > 240) viz.points.splice(0, viz.points.length - 240);
  startViz();
}

function addRipple(clientX, clientY, kind) {
  const p = localPoint(clientX, clientY);
  viz.ripples.push({ ...p, kind, t: performance.now(), r: 0 });
  if (viz.ripples.length > 40) viz.ripples.splice(0, viz.ripples.length - 40);
  startViz();
}

function colorFor(kind) {
  if (kind === "scroll") return [52, 211, 153];
  if (kind === "click") return [244, 114, 182];
  if (kind === "rclick") return [251, 191, 36];
  return [96, 165, 250];
}

function startViz() {
  if (viz.running) return;
  viz.running = true;
  window.requestAnimationFrame(renderViz);
}

function renderViz() {
  if (!vizCanvas) return;
  const ctx = vizCanvas.getContext("2d");
  if (!ctx) return;

  const dpr = window.devicePixelRatio || 1;
  const now = performance.now();
  const w = vizCanvas.width;
  const h = vizCanvas.height;

  ctx.clearRect(0, 0, w, h);

  // Trails (last ~700ms).
  const maxAge = 700;
  const pts = viz.points.filter((p) => now - p.t < maxAge);
  viz.points = pts;

  function drawSmoothSegment(seg) {
    if (seg.length < 2) return;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    const last = seg[seg.length - 1];
    const age = now - last.t;
    const alpha = clamp(1 - age / maxAge, 0, 1);
    const [r, g, bl] = colorFor(last.kind);
    ctx.strokeStyle = `rgba(${r},${g},${bl},${0.55 * alpha})`;
    ctx.lineWidth = 3 * dpr * (0.6 + 0.4 * alpha);

    ctx.beginPath();
    ctx.moveTo(seg[0].x * dpr, seg[0].y * dpr);
    for (let i = 1; i < seg.length - 1; i++) {
      const p = seg[i];
      const n = seg[i + 1];
      const mx = (p.x + n.x) / 2;
      const my = (p.y + n.y) / 2;
      ctx.quadraticCurveTo(p.x * dpr, p.y * dpr, mx * dpr, my * dpr);
    }
    const end = seg[seg.length - 1];
    ctx.lineTo(end.x * dpr, end.y * dpr);
    ctx.stroke();
  }

  let current = [];
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i];
    if (!current.length || current[current.length - 1].kind === p.kind) {
      current.push(p);
    } else {
      drawSmoothSegment(current);
      current = [p];
    }
  }
  drawSmoothSegment(current);

  // Active touch points.
  viz.active.forEach((p) => {
    const [r, g, bl] = colorFor(p.kind);
    ctx.fillStyle = `rgba(${r},${g},${bl},0.85)`;
    ctx.beginPath();
    ctx.arc(p.x * dpr, p.y * dpr, 6 * dpr, 0, Math.PI * 2);
    ctx.fill();
  });

  // Ripples (last ~450ms).
  const rippleAge = 450;
  viz.ripples = viz.ripples.filter((rp) => now - rp.t < rippleAge);
  for (const rp of viz.ripples) {
    const age = now - rp.t;
    const alpha = clamp(1 - age / rippleAge, 0, 1);
    const [r, g, bl] = colorFor(rp.kind);
    const radius = (10 + 70 * (age / rippleAge)) * dpr;
    ctx.strokeStyle = `rgba(${r},${g},${bl},${0.55 * alpha})`;
    ctx.lineWidth = 2 * dpr;
    ctx.beginPath();
    ctx.arc(rp.x * dpr, rp.y * dpr, radius, 0, Math.PI * 2);
    ctx.stroke();
  }

  const shouldKeepRunning = viz.points.length || viz.ripples.length || viz.active.size;
  if (shouldKeepRunning) {
    window.requestAnimationFrame(renderViz);
  } else {
    viz.running = false;
  }
}

window.addEventListener("resize", resizeViz);
window.addEventListener("orientationchange", resizeViz);
resizeViz();

// Touchpad: send dx/dy at ~60Hz, aggregated.
let lastX = null;
let lastY = null;
let pendingDx = 0;
let pendingDy = 0;
let touchStartX = null;
let touchStartY = null;
let touchStartT = 0;
let moveInterval = null;
// Very high send rates can flood Socket.IO (especially on mobile polling) and cause disconnects.
// Host side already applies mouse movement at high Hz, so ~125Hz send rate is usually enough.
const MOVE_FLUSH_MS = 8; // ~125Hz target

function flushMove() {
  const dx = pendingDx;
  const dy = pendingDy;
  pendingDx = 0;
  pendingDy = 0;
  if (dx || dy) socket.volatile.emit("move", { dx, dy });
}

touchpad.addEventListener(
  "touchstart",
  (e) => {
    e.preventDefault();
    const t = e.touches[0];
    lastX = t.clientX;
    lastY = t.clientY;
    touchStartX = t.clientX;
    touchStartY = t.clientY;
    touchStartT = Date.now();
    viz.active.set(t.identifier, { ...localPoint(t.clientX, t.clientY), kind: "move" });
    addVizPoint(t.clientX, t.clientY, "move");

    if (!moveInterval) {
      moveInterval = window.setInterval(flushMove, MOVE_FLUSH_MS);
    }
  },
  { passive: false },
);

touchpad.addEventListener(
  "touchmove",
  (e) => {
    e.preventDefault();
    if (e.touches.length !== 1) return;
    if (!e.touches.length) return;
    const t = e.touches[0];
    if (lastX == null || lastY == null) {
      lastX = t.clientX;
      lastY = t.clientY;
      return;
    }
    pendingDx += t.clientX - lastX;
    pendingDy += t.clientY - lastY;
    lastX = t.clientX;
    lastY = t.clientY;
    // No flush here; interval handles it.
    viz.active.set(t.identifier, { ...localPoint(t.clientX, t.clientY), kind: "move" });
    addVizPoint(t.clientX, t.clientY, "move");
  },
  { passive: false },
);

touchpad.addEventListener(
  "touchend",
  (e) => {
    e.preventDefault();
    viz.active.clear();
    // Tap-to-click: short + small movement => left click.
    if (touchStartX != null && touchStartY != null) {
      const dt = Date.now() - touchStartT;
      const moved =
        Math.abs((lastX ?? touchStartX) - touchStartX) +
        Math.abs((lastY ?? touchStartY) - touchStartY);
      if (dt < 250 && moved < 10) {
        socket.emit("click", { button: "left", down: true });
        socket.emit("click", { button: "left", down: false });
        addRipple(touchStartX, touchStartY, "click");
      }
    }
    lastX = null;
    lastY = null;
    touchStartX = null;
    touchStartY = null;
    if (moveInterval) {
      window.clearInterval(moveInterval);
      moveInterval = null;
    }
  },
  { passive: false },
);

touchpad.addEventListener(
  "touchcancel",
  (e) => {
    e.preventDefault();
    viz.active.clear();
    lastX = null;
    lastY = null;
    touchStartX = null;
    touchStartY = null;
    if (moveInterval) {
      window.clearInterval(moveInterval);
      moveInterval = null;
    }
  },
  { passive: false },
);

// Two-finger scroll (vertical only).
let lastScrollY = null;
touchpad.addEventListener(
  "touchmove",
  (e) => {
    if (e.touches.length !== 2) {
      lastScrollY = null;
      return;
    }
    e.preventDefault();
    const y = (e.touches[0].clientY + e.touches[1].clientY) / 2;
    if (lastScrollY == null) {
      lastScrollY = y;
      return;
    }
    const dy = y - lastScrollY;
    lastScrollY = y;
    socket.volatile.emit("scroll", { dy: -dy });
    // Visualize scroll midpoint and both fingers.
    viz.active.set(e.touches[0].identifier, { ...localPoint(e.touches[0].clientX, e.touches[0].clientY), kind: "scroll" });
    viz.active.set(e.touches[1].identifier, { ...localPoint(e.touches[1].clientX, e.touches[1].clientY), kind: "scroll" });
    addVizPoint((e.touches[0].clientX + e.touches[1].clientX) / 2, y, "scroll");
  },
  { passive: false },
);

// Click buttons (press/release for better drag potential).
document.querySelectorAll("[data-click]").forEach((btn) => {
  const which = btn.getAttribute("data-click");
  const down = () => socket.emit("click", { button: which, down: true });
  const up = () => socket.emit("click", { button: which, down: false });
  const show = () => {
    const r = touchpad.getBoundingClientRect();
    const cx = which === "right" ? r.right - 26 : r.left + 26;
    const cy = r.bottom - 26;
    addRipple(cx, cy, which === "right" ? "rclick" : "click");
  };
  btn.addEventListener("touchstart", (e) => {
    e.preventDefault();
    down();
    show();
  });
  btn.addEventListener("touchend", (e) => {
    e.preventDefault();
    up();
  });
  btn.addEventListener("touchcancel", (e) => {
    e.preventDefault();
    up();
  });
  btn.addEventListener("mousedown", () => {
    down();
    show();
  });
  btn.addEventListener("mouseup", up);
  btn.addEventListener("mouseleave", up);
});

// Keyboard: focus hidden input to pop up phone keyboard.
keyboardBtn.addEventListener("click", () => {
  hiddenInput.focus({ preventScroll: true });
});
keyboardBtn.addEventListener("touchstart", (e) => {
  e.preventDefault();
  hiddenInput.focus({ preventScroll: true });
});

hiddenInput.addEventListener("input", () => {
  const val = hiddenInput.value || "";
  if (!val) return;
  socket.emit("type_text", { text: val });
  hiddenInput.value = "";
});

hiddenInput.addEventListener("keydown", (e) => {
  const special = {
    Enter: "enter",
    Backspace: "backspace",
    Tab: "tab",
    Escape: "esc",
    " ": "space",
    ArrowUp: "up",
    ArrowDown: "down",
    ArrowLeft: "left",
    ArrowRight: "right",
  };
  const name = special[e.key];
  if (!name) return;
  socket.emit("key", { name, down: true });
  socket.emit("key", { name, down: false });
});

// Gamepad buttons (press/release).
document.querySelectorAll("[data-pad]").forEach((btn) => {
  const name = btn.getAttribute("data-pad");
  const down = () => socket.emit("pad_button", { name, down: true });
  const up = () => socket.emit("pad_button", { name, down: false });
  btn.addEventListener("touchstart", (e) => {
    e.preventDefault();
    down();
  });
  btn.addEventListener("touchend", (e) => {
    e.preventDefault();
    up();
  });
  btn.addEventListener("touchcancel", (e) => {
    e.preventDefault();
    up();
  });
  btn.addEventListener("mousedown", down);
  btn.addEventListener("mouseup", up);
  btn.addEventListener("mouseleave", up);
});

// Triggers as press/hold buttons (0 or 1).
let triggerBtnId = 0;
document.querySelectorAll("[data-triggerbtn]").forEach((btn) => {
  const which = btn.getAttribute("data-triggerbtn");
  const src = `btn-${triggerBtnId++}`;
  const down = () => setTriggerHold(which, src, true);
  const up = () => setTriggerHold(which, src, false);
  bindHoldButton(btn, down, up);
});

// Sticks (touch areas).
function bindStick(area, eventName) {
  const rect = () => area.getBoundingClientRect();
  let pending = null;
  let raf = 0;

  function compute(x, y) {
    const r = rect();
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    const nx = (x - cx) / (r.width / 2);
    const ny = (y - cy) / (r.height / 2);
    return { x: clamp(nx, -1, 1), y: clamp(-ny, -1, 1) };
  }

  function scheduleEmit(v) {
    pending = v;
    if (raf) return;
    raf = window.requestAnimationFrame(() => {
      raf = 0;
      if (!pending) return;
      socket.volatile.emit(eventName, pending);
      pending = null;
    });
  }

  // Prefer Pointer Events for reliable multi-touch + simultaneous buttons.
  if (window.PointerEvent) {
    let activePointer = null;
    const down = (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      e.preventDefault();
      activePointer = e.pointerId;
      try {
        area.setPointerCapture(e.pointerId);
      } catch (_) {}
      const v = compute(e.clientX, e.clientY);
      scheduleEmit(v);
      if (area === kbmMovePad && moveTracer) {
        moveTracer.setActive(e.pointerId, e.clientX, e.clientY);
        moveTracer.addPoint(e.clientX, e.clientY);
      }
    };
    const move = (e) => {
      if (activePointer == null || e.pointerId !== activePointer) return;
      e.preventDefault();
      const v = compute(e.clientX, e.clientY);
      scheduleEmit(v);
      if (area === kbmMovePad && moveTracer) {
        moveTracer.setActive(e.pointerId, e.clientX, e.clientY);
        moveTracer.addPoint(e.clientX, e.clientY);
      }
    };
    const up = (e) => {
      if (activePointer == null || e.pointerId !== activePointer) return;
      e.preventDefault();
      activePointer = null;
      pending = null;
      if (raf) {
        window.cancelAnimationFrame(raf);
        raf = 0;
      }
      socket.emit(eventName, { x: 0, y: 0 });
      if (area === kbmMovePad && moveTracer) {
        moveTracer.clearActive(e.pointerId);
      }
    };
    area.addEventListener("pointerdown", down);
    area.addEventListener("pointermove", move);
    area.addEventListener("pointerup", up);
    area.addEventListener("pointercancel", up);
    area.addEventListener("lostpointercapture", up);
    return;
  }

  // Fallback touch events.
  let activeId = null;
  area.addEventListener(
    "touchstart",
    (e) => {
      e.preventDefault();
      const t = e.changedTouches[0];
      activeId = t.identifier;
      const v = compute(t.clientX, t.clientY);
      scheduleEmit(v);
      if (area === kbmMovePad && moveTracer) {
        moveTracer.setActive(t.identifier, t.clientX, t.clientY);
        moveTracer.addPoint(t.clientX, t.clientY);
      }
    },
    { passive: false },
  );
  area.addEventListener(
    "touchmove",
    (e) => {
      e.preventDefault();
      const t = Array.from(e.touches).find((x) => x.identifier === activeId);
      if (!t) return;
      const v = compute(t.clientX, t.clientY);
      scheduleEmit(v);
      if (area === kbmMovePad && moveTracer) {
        moveTracer.setActive(t.identifier, t.clientX, t.clientY);
        moveTracer.addPoint(t.clientX, t.clientY);
      }
    },
    { passive: false },
  );
  const end = (e) => {
    e.preventDefault();
    if (area === kbmMovePad && moveTracer && activeId != null) {
      moveTracer.clearActive(activeId);
    }
    activeId = null;
    pending = null;
    if (raf) {
      window.cancelAnimationFrame(raf);
      raf = 0;
    }
    socket.emit(eventName, { x: 0, y: 0 });
  };
  area.addEventListener("touchend", end, { passive: false });
  area.addEventListener("touchcancel", end, { passive: false });
}

document.querySelectorAll("[data-stick]").forEach((area) => {
  const which = area.getAttribute("data-stick");
  bindStick(area, which === "right" ? "pad_right" : "pad_left");
});

// KBM move pad reuses the same stick events so host can map to WASD.
document.querySelectorAll("[data-kbm-stick]").forEach((area) => {
  const which = area.getAttribute("data-kbm-stick");
  bindStick(area, which === "right" ? "pad_right" : "pad_left");
});

// KBM camera pad: relative mouse deltas (touchpad-style).
const kbmCameraPad = document.querySelector("[data-kbm-camera]");
let camLastX = null;
let camLastY = null;
let camPendingDx = 0;
let camPendingDy = 0;
let camInterval = null;
const CAM_FLUSH_MS = 8; // ~125Hz
let camActive = false;

function flushCam() {
  const dx = camPendingDx;
  const dy = camPendingDy;
  camPendingDx = 0;
  camPendingDy = 0;
  if (dx || dy) socket.volatile.emit("kbm_cam_move", { dx, dy });
}

function stopCam() {
  camLastX = null;
  camLastY = null;
  camActive = false;
  // Release camera hold on stop.
  try {
    socket.emit("kbm_cam_hold", { down: false });
  } catch (_) {}
  if (camInterval) {
    window.clearInterval(camInterval);
    camInterval = null;
  }
  flushCam();
}

if (kbmCameraPad) {
  if (window.PointerEvent) {
    let activePointer = null;
    kbmCameraPad.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      e.preventDefault();
      activePointer = e.pointerId;
      camLastX = e.clientX;
      camLastY = e.clientY;
      camActive = true;
      if (cameraDragToggle && cameraDragToggle.checked) socket.emit("kbm_cam_hold", { down: true });
      if (camTracer) {
        camTracer.setActive(e.pointerId, e.clientX, e.clientY);
        camTracer.addPoint(e.clientX, e.clientY);
      }
      try {
        kbmCameraPad.setPointerCapture(e.pointerId);
      } catch (_) {}
      if (!camInterval) camInterval = window.setInterval(flushCam, CAM_FLUSH_MS);
    });
    kbmCameraPad.addEventListener("pointermove", (e) => {
      if (activePointer == null || e.pointerId !== activePointer) return;
      e.preventDefault();
      if (camLastX == null || camLastY == null) {
        camLastX = e.clientX;
        camLastY = e.clientY;
        return;
      }
      camPendingDx += e.clientX - camLastX;
      camPendingDy += e.clientY - camLastY;
      camLastX = e.clientX;
      camLastY = e.clientY;
      if (camTracer) {
        camTracer.setActive(e.pointerId, e.clientX, e.clientY);
        camTracer.addPoint(e.clientX, e.clientY);
      }
    });
    const end = (e) => {
      if (activePointer == null || e.pointerId !== activePointer) return;
      e.preventDefault();
      activePointer = null;
      if (camTracer) camTracer.clearActive(e.pointerId);
      stopCam();
    };
    kbmCameraPad.addEventListener("pointerup", end);
    kbmCameraPad.addEventListener("pointercancel", end);
    kbmCameraPad.addEventListener("lostpointercapture", end);
  } else {
    kbmCameraPad.addEventListener(
      "touchstart",
      (e) => {
        e.preventDefault();
        const t = e.touches[0];
        camLastX = t.clientX;
        camLastY = t.clientY;
        camActive = true;
        if (cameraDragToggle && cameraDragToggle.checked) socket.emit("kbm_cam_hold", { down: true });
        if (camTracer) {
          camTracer.setActive(t.identifier, t.clientX, t.clientY);
          camTracer.addPoint(t.clientX, t.clientY);
        }
        if (!camInterval) camInterval = window.setInterval(flushCam, CAM_FLUSH_MS);
      },
      { passive: false },
    );
    kbmCameraPad.addEventListener(
      "touchmove",
      (e) => {
        e.preventDefault();
        const t = e.touches[0];
        if (camLastX == null || camLastY == null) {
          camLastX = t.clientX;
          camLastY = t.clientY;
          return;
        }
        camPendingDx += t.clientX - camLastX;
        camPendingDy += t.clientY - camLastY;
        camLastX = t.clientX;
        camLastY = t.clientY;
        if (camTracer) {
          camTracer.setActive(t.identifier, t.clientX, t.clientY);
          camTracer.addPoint(t.clientX, t.clientY);
        }
      },
      { passive: false },
    );
    kbmCameraPad.addEventListener(
      "touchend",
      (e) => {
        if (camTracer && e.changedTouches && e.changedTouches[0]) camTracer.clearActive(e.changedTouches[0].identifier);
        stopCam();
      },
      { passive: false },
    );
    kbmCameraPad.addEventListener(
      "touchcancel",
      (e) => {
        if (camTracer && e.changedTouches && e.changedTouches[0]) camTracer.clearActive(e.changedTouches[0].identifier);
        stopCam();
      },
      { passive: false },
    );
  }
}

if (cameraDragToggle) {
  cameraDragToggle.addEventListener("change", () => {
    if (!camActive) return;
    socket.emit("kbm_cam_hold", { down: !!cameraDragToggle.checked });
  });
}

// KBM directional buttons (tap/hold) map to left stick at full deflection.
// KBM action buttons map to existing pad buttons (host maps in KBM mode).
const actionMap = { space: "a", crouch: "b", reload: "x", use: "y" };
document.querySelectorAll("[data-kbm-action]").forEach((btn) => {
  const act = btn.getAttribute("data-kbm-action");
  const name = actionMap[act];
  if (!name) return;
  const down = () => socket.emit("pad_button", { name, down: true });
  const up = () => socket.emit("pad_button", { name, down: false });
  btn.addEventListener("touchstart", (e) => {
    e.preventDefault();
    down();
  });
  btn.addEventListener("touchend", (e) => {
    e.preventDefault();
    up();
  });
  btn.addEventListener("touchcancel", (e) => {
    e.preventDefault();
    up();
  });
  btn.addEventListener("mousedown", down);
  btn.addEventListener("mouseup", up);
  btn.addEventListener("mouseleave", up);
});
