/* global io */

const statusEl = document.getElementById("status");
const touchpad = document.getElementById("touchpad");
const vizCanvas = document.getElementById("viz");
const hiddenInput = document.getElementById("hidden-input");
const keyboardBtn = document.getElementById("btn-keyboard");

if (typeof io === "undefined") {
  statusEl.textContent = "Error: Socket.IO client missing (/static/vendor/socket.io.min.js)";
  throw new Error("Socket.IO client missing");
}

const socket = io({
  auth: { token: window.MEMCTRL_TOKEN || "" },
  transports: ["polling", "websocket"],
  timeout: 8000,
});

socket.on("connect", () => {
  statusEl.textContent = "Connected";
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
  statusEl.textContent = `Connected (${parts.join(", ")})`;

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
    document.querySelectorAll("[data-trigger]").forEach((t) => t.setAttribute("disabled", "disabled"));
  }
});

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
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

  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1];
    const b = pts[i];
    if (a.kind !== b.kind) continue;
    const age = now - b.t;
    const alpha = clamp(1 - age / maxAge, 0, 1);
    const [r, g, bl] = colorFor(b.kind);
    ctx.strokeStyle = `rgba(${r},${g},${bl},${0.55 * alpha})`;
    ctx.lineWidth = 3 * dpr * (0.6 + 0.4 * alpha);
    ctx.beginPath();
    ctx.moveTo(a.x * dpr, a.y * dpr);
    ctx.lineTo(b.x * dpr, b.y * dpr);
    ctx.stroke();
  }

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
let rafScheduled = false;
let touchStartX = null;
let touchStartY = null;
let touchStartT = 0;

function flushMove() {
  rafScheduled = false;
  const dx = pendingDx;
  const dy = pendingDy;
  pendingDx = 0;
  pendingDy = 0;
  if (dx || dy) socket.emit("move", { dx, dy });
}

function scheduleFlush() {
  if (rafScheduled) return;
  rafScheduled = true;
  window.requestAnimationFrame(flushMove);
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
    scheduleFlush();
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
    socket.emit("scroll", { dy: -dy });
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
  btn.addEventListener("mousedown", down);
  btn.addEventListener("mouseup", up);
  btn.addEventListener("mouseleave", up);
});

// Triggers.
document.querySelectorAll("[data-trigger]").forEach((el) => {
  const which = el.getAttribute("data-trigger");
  const send = () => {
    const v = Number(el.value || 0) / 100;
    socket.emit("pad_trigger", { which, value: clamp(v, 0, 1) });
  };
  el.addEventListener("input", send);
  el.addEventListener("change", send);
});

// Sticks (touch areas).
function bindStick(area, eventName) {
  let activeId = null;
  const rect = () => area.getBoundingClientRect();

  function compute(x, y) {
    const r = rect();
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    const nx = (x - cx) / (r.width / 2);
    const ny = (y - cy) / (r.height / 2);
    return { x: clamp(nx, -1, 1), y: clamp(-ny, -1, 1) };
  }

  area.addEventListener(
    "touchstart",
    (e) => {
      e.preventDefault();
      const t = e.changedTouches[0];
      activeId = t.identifier;
      const v = compute(t.clientX, t.clientY);
      socket.emit(eventName, v);
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
      socket.emit(eventName, v);
    },
    { passive: false },
  );
  area.addEventListener(
    "touchend",
    (e) => {
      e.preventDefault();
      activeId = null;
      socket.emit(eventName, { x: 0, y: 0 });
    },
    { passive: false },
  );
}

document.querySelectorAll("[data-stick]").forEach((area) => {
  const which = area.getAttribute("data-stick");
  bindStick(area, which === "right" ? "pad_right" : "pad_left");
});
