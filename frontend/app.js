const API = "/api";
const WS_KEY = "helpdesk_workspace";
const COLORS = { accent: "#1d5a47", steel: "#3a6b8c", sand: "#ab8560", muted: "#767369", line: "#e3dfd6", ink: "#14161b" };

const state = {
  user: null,
  workspaces: [],
  workspaceId: Number(localStorage.getItem(WS_KEY)) || null,
  workspace: null,
  member: null,
  ticket: null,
  charts: {},
  mode: "login",
  confirm: null,
  wsOpen: false,
  renderedMessages: new Set(),
  apiKeys: [],
};

const $ = (id) => document.getElementById(id);

const icon = (name, cls = "") => `<svg class="icon ${cls}" aria-hidden="true"><use href="#i-${name}"></use></svg>`;

// transport

const CSRF_COOKIE = "aisd_csrf";

const readCookie = (name) => {
  const hit = document.cookie.split("; ").find((part) => part.startsWith(`${name}=`));
  return hit ? decodeURIComponent(hit.slice(name.length + 1)) : "";
};

const api = async (path, options = {}) => {
  const method = (options.method || "GET").toUpperCase();
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const csrf = readCookie(CSRF_COOKIE);
  if (csrf && !["GET", "HEAD"].includes(method)) headers["X-CSRF-Token"] = csrf;
  if (state.workspaceId) headers["X-Workspace-Id"] = String(state.workspaceId);
  const response = await fetch(`${API}${path}`, { ...options, headers, credentials: "same-origin" });
  if (response.status === 401) {
    if (path.startsWith("/auth") || path === "/auth/me") {
      logout(false);
      throw new Error("Требуется вход");
    }
  }
  if (response.status === 204) return null;
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) throw new Error(detail(data, response.statusText));
  return data;
};

const detail = (data, fallback) => {
  if (!data || !data.detail) return fallback;
  return typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
};

const esc = (value) =>
  String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");

const initials = (value) => {
  const parts = String(value || "").trim().split(/[\s@._-]+/).filter(Boolean);
  if (!parts.length) return "?";
  return (parts[0][0] + (parts[1]?.[0] || "")).toUpperCase();
};

const fmtTime = (iso) => {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
};

const fmtDay = (iso) => {
  if (!iso) return "";
  const date = new Date(iso);
  const today = new Date();
  const same = date.toDateString() === today.toDateString();
  if (same) return "Сегодня";
  return date.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
};

const toast = (message, kind = "info") => {
  const node = document.createElement("div");
  node.className = `toast ${kind === "info" ? "" : kind}`;
  node.textContent = message;
  $("toasts").appendChild(node);
  setTimeout(() => {
    node.classList.add("leaving");
    setTimeout(() => node.remove(), 320);
  }, 3800);
};

const copyText = async (text, button) => {
  try {
    await navigator.clipboard.writeText(text);
  } catch (error) {
    const field = document.createElement("textarea");
    field.value = text;
    document.body.appendChild(field);
    field.select();
    document.execCommand("copy");
    field.remove();
  }
  if (button) {
    const label = button.querySelector("span");
    const use = button.querySelector("use");
    const originalLabel = label?.textContent;
    const originalHref = use?.getAttribute("href");
    button.classList.add("copied");
    if (label) label.textContent = "Скопировано";
    if (use) use.setAttribute("href", "#i-check");
    setTimeout(() => {
      button.classList.remove("copied");
      if (label && originalLabel !== undefined) label.textContent = originalLabel;
      if (use && originalHref) use.setAttribute("href", originalHref);
    }, 1600);
  }
  toast("Скопировано в буфер обмена", "ok");
};

const celebrate = (node) => {
  if (prefersReduced() || !node) return;
  const rect = node.getBoundingClientRect();
  const palette = [COLORS.accent, COLORS.steel, COLORS.sand, "#3ecf8e"];
  for (let i = 0; i < 14; i += 1) {
    const dot = document.createElement("span");
    dot.className = "confetti-dot";
    const angle = (Math.PI * 2 * i) / 14 + Math.random() * 0.4;
    const distance = 60 + Math.random() * 90;
    dot.style.left = `${rect.left + rect.width / 2}px`;
    dot.style.top = `${rect.top + rect.height / 2}px`;
    dot.style.background = palette[i % palette.length];
    dot.style.setProperty("--dx", `${Math.cos(angle) * distance}px`);
    dot.style.setProperty("--dy", `${Math.sin(angle) * distance}px`);
    document.body.appendChild(dot);
    setTimeout(() => dot.remove(), 1200);
  }
};

// layers

// access guards

const AUTH_DRAWERS = new Set(["drawer-tickets", "drawer-knowledge", "drawer-team", "drawer-integrations"]);

const requireAuth = (message) => {
  if (state.user) return true;
  setAuthMode("login");
  openLayer("modal-auth");
  toast(message || "Войдите, чтобы пользоваться этим разделом", "error");
  return false;
};

const requireWorkspace = (message) => {
  if (!requireAuth(message)) return false;
  if (state.workspaceId) return true;
  toast("Сначала создайте свою команду", "error");
  $("workspace-error").textContent = "";
  openLayer("modal-workspace");
  return false;
};

const syncLayerState = () => {
  document.body.classList.toggle("layer-open", Boolean(document.querySelector(".drawer.open, .modal.open")));
};

const openLayer = (id) => {
  const node = $(id);
  if (!node) return;
  node.classList.add("open");
  node.setAttribute("aria-hidden", "false");
  $("backdrop").classList.add("show");
  syncLayerState();
};

const closeLayer = (id) => {
  const node = $(id);
  if (!node) return;
  if (id === "drawer-integrations") stopTelegramWatch();
  node.classList.remove("open");
  node.setAttribute("aria-hidden", "true");
  if (!document.querySelector(".drawer.open")) $("backdrop").classList.remove("show");
  syncLayerState();
};

const closeAllLayers = () => {
  stopTelegramWatch();
  document.querySelectorAll(".drawer.open, .modal.open").forEach((node) => {
    node.classList.remove("open");
    node.setAttribute("aria-hidden", "true");
  });
  $("backdrop").classList.remove("show");
  $("ws-switch").classList.remove("open");
  state.wsOpen = false;
  syncLayerState();
};

// auth

const setAuthMode = (mode) => {
  state.mode = mode;
  const isRegister = mode === "register";
  $("auth-title").textContent = isRegister ? "Создать аккаунт" : "Вход";
  $("auth-submit").textContent = isRegister ? "Создать аккаунт" : "Войти";
  $("field-name").classList.toggle("hidden", !isRegister);
  $("auth-hint").classList.toggle("hidden", !isRegister);
  $("auth-underline").classList.toggle("right", isRegister);
  $("auth-password").setAttribute("autocomplete", isRegister ? "new-password" : "current-password");
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.authTab === mode));
  $("auth-error").textContent = "";
};

const submitAuth = async (event) => {
  event.preventDefault();
  const email = $("auth-email").value.trim();
  const password = $("auth-password").value;
  $("auth-error").textContent = "";
  const submit = $("auth-submit");
  const original = submit.textContent;
  submit.disabled = true;
  submit.innerHTML = `<span class="spinner"></span>`;
  try {
    const payload = state.mode === "register"
      ? { email, password, full_name: $("auth-name").value.trim() }
      : { email, password };
    const data = await api(`/auth/${state.mode}`, { method: "POST", body: JSON.stringify(payload) });
    closeLayer("modal-auth");
    $("auth-form").reset();
    toast(state.mode === "register" ? "Аккаунт создан — ваша комната готова" : "Вы вошли", "ok");
    await openApp();
  } catch (error) {
    $("auth-error").textContent = error.message;
  } finally {
    submit.disabled = false;
    submit.textContent = original;
  }
};

const logout = async (notify = true) => {
  try {
    await api("/auth/logout", { method: "POST" });
  } catch (error) {
    /* сессия уже могла истечь */
  }
  state.user = null;
  state.workspace = null;
  state.workspaces = [];
  state.workspaceId = null;
  state.ticket = null;
  localStorage.removeItem(WS_KEY);
  closeAllLayers();
  render();
  if (notify) toast("Вы вышли из аккаунта");
};

const openApp = async () => {
  try {
    state.user = await api("/auth/me");
  } catch (error) {
    logout(false);
    return;
  }
  render();
  await loadWorkspaces();
};

// render shell

const render = () => {
  const authed = Boolean(state.user);
  $("user-chip").textContent = authed
    ? `${state.user.email} · ${state.workspace ? state.workspace.role : state.user.role}`
    : "гость";
  $("auth-trigger").classList.toggle("hidden", authed);
  $("logout").classList.toggle("hidden", !authed);
  $("gate").classList.toggle("hidden", authed);
  $("kpis").classList.toggle("hidden", !authed);
  document.querySelector(".charts").classList.toggle("hidden", !authed);
  $("ws-switch").classList.toggle("hidden", !authed);
  $("hero").classList.toggle("hidden", authed);
  $("view-overview").classList.toggle("hidden", !authed);
  document.querySelectorAll(".navlink[data-open]").forEach((node) => {
    node.classList.toggle("locked", !authed);
    node.setAttribute("aria-disabled", String(!authed));
    const use = node.querySelector("use");
    if (use) use.setAttribute("href", authed ? node.dataset.icon : "#i-lock");
  });
  if (state.workspace) {
    $("view-subtitle").textContent =
      `${state.workspace.name} · метрики по каналам: Telegram, веб-виджет, API`;
  }
};

// workspaces

const loadWorkspaces = async () => {
  try {
    state.workspaces = await api("/workspaces");
  } catch (error) {
    state.workspaces = [];
  }
  renderWsList();
  if (!state.workspaces.length) {
    state.workspace = null;
    state.workspaceId = null;
    $("ws-name").textContent = "создать команду";
    render();
    return;
  }
  if (!state.workspaceId || !state.workspaces.some((w) => w.id === state.workspaceId)) {
    state.workspaceId = state.workspaces[0].id;
  }
  localStorage.setItem(WS_KEY, String(state.workspaceId));
  await selectWorkspace(state.workspaceId, { silent: true });
};

const renderWsList = () => {
  const list = $("ws-list");
  if (!state.workspaces.length) {
    list.innerHTML = `<div class="empty" style="padding:18px">У вас пока нет команд</div>`;
    return;
  }
  list.innerHTML = state.workspaces
    .map((ws) => `
      <button class="ws-item ${ws.id === state.workspaceId ? "active" : ""}" data-ws="${ws.id}" type="button">
        <span class="ws-title">${esc(ws.name)}</span>
        <span class="ws-sub">${ws.members} сотр. · ${ws.open_tickets} открытых</span>
        <span class="ws-role ${esc(ws.role)}">${esc(ws.role)}</span>
      </button>`)
    .join("");
  list.querySelectorAll("[data-ws]").forEach((node) =>
    node.addEventListener("click", () => selectWorkspace(Number(node.dataset.ws)))
  );
};

const selectWorkspace = async (id, options = {}) => {
  state.workspaceId = id;
  localStorage.setItem(WS_KEY, String(id));
  state.renderedMessages.clear();
  renderWsList();
  $("ws-switch").classList.remove("open");
  state.wsOpen = false;
  try {
    state.workspace = await api("/workspaces/current");
    $("ws-name").textContent = state.workspace.name;
  } catch (error) {
    toast(error.message, "error");
    return;
  }
  render();
  renderIntegrations();
  if (!options.silent) toast(`Команда: ${state.workspace.name}`, "ok");
  await loadOverview();
  if (document.querySelector("#drawer-team.open")) loadTeam();
};

const submitWorkspace = async (event) => {
  event.preventDefault();
  $("workspace-error").textContent = "";
  try {
    const created = await api("/workspaces", {
      method: "POST",
      body: JSON.stringify({ name: $("workspace-name").value.trim() }),
    });
    closeLayer("modal-workspace");
    $("workspace-form").reset();
    celebrate($("ws-trigger"));
    toast(`Комната «${created.name}» создана`, "ok");
    await loadWorkspaces();
    await selectWorkspace(created.id);
  } catch (error) {
    $("workspace-error").textContent = error.message;
  }
};

// motion helpers

const prefersReduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const finePointer = () => window.matchMedia("(hover: hover) and (pointer: fine)").matches;

const animateNumbers = (root) => {
  if (prefersReduced()) return;
  root.querySelectorAll("[data-value]").forEach((node) => {
    const raw = node.dataset.value;
    const target = Number(String(raw).replace(/[^\d.-]/g, ""));
    if (!Number.isFinite(target) || target === 0) return;
    const suffix = String(raw).replace(/[\d.\s-]/g, "");
    const started = performance.now();
    node.textContent = `0${suffix}`;
    const step = (now) => {
      const progress = Math.min(1, (now - started) / 800);
      const eased = 1 - Math.pow(1 - progress, 3);
      node.textContent = `${Math.round(target * eased)}${suffix}`;
      if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
};

const attachTilt = (nodes, max = 5) => {
  if (prefersReduced() || !finePointer()) return;
  nodes.filter(Boolean).forEach((node) => {
    let frame = null;
    node.addEventListener("pointermove", (event) => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = null;
        const rect = node.getBoundingClientRect();
        const dx = (event.clientX - rect.left) / rect.width - 0.5;
        const dy = (event.clientY - rect.top) / rect.height - 0.5;
        node.style.transform =
          `perspective(1100px) rotateY(${(dx * max).toFixed(2)}deg) rotateX(${(-dy * max).toFixed(2)}deg) ` +
          `translateY(-7px) translateZ(18px)`;
        node.style.boxShadow =
          `0 ${Math.round(16 - dy * 9)}px ${Math.round(30 - dy * 10)}px -14px rgba(20,22,27,.42), ` +
          `0 ${Math.round(2 - dy * 2)}px 4px -2px rgba(20,22,27,.18)`;
      });
    });
    node.addEventListener("pointerleave", () => {
      node.style.transform = "";
      node.style.boxShadow = "";
    });
  });
};

const initHeroTilt = () => {
  const hero = $("hero");
  const orbit = document.querySelector(".hero-orbit");
  if (!hero || !orbit || prefersReduced() || !finePointer()) return;
  let frame = null;
  hero.addEventListener("pointermove", (event) => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      const rect = hero.getBoundingClientRect();
      const dx = (event.clientX - rect.left) / rect.width - 0.5;
      const dy = (event.clientY - rect.top) / rect.height - 0.5;
      orbit.style.setProperty("--hrx", `${(9 - dy * 13).toFixed(1)}deg`);
      orbit.style.setProperty("--hry", `${(dx * 15).toFixed(1)}deg`);
    });
  });
  hero.addEventListener("pointerleave", () => {
    orbit.style.setProperty("--hrx", "9deg");
    orbit.style.setProperty("--hry", "0deg");
  });
};

const initCursorLight = () => {
  const light = $("cursor-light");
  if (!light || prefersReduced() || !finePointer()) return;
  let x = 0;
  let y = 0;
  let frame = null;
  window.addEventListener("pointermove", (event) => {
    x = event.clientX;
    y = event.clientY;
    light.classList.add("on");
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      light.style.transform = `translate3d(${x - 320}px, ${y - 320}px, 0)`;
    });
  }, { passive: true });
  document.addEventListener("pointerleave", () => light.classList.remove("on"));
};

const revealSvg = (svg) => {
  requestAnimationFrame(() => requestAnimationFrame(() => svg.classList.add("ready")));
};

// overview

const CHANNEL_ICON = { telegram: "telegram", web: "globe", api: "code" };

const relTime = (iso) => {
  if (!iso) return "";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!Number.isFinite(diff) || diff < 0) return "";
  if (diff < 60) return "только что";
  if (diff < 3600) return `${Math.floor(diff / 60)} мин`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} ч`;
  return `${Math.floor(diff / 86400)} дн`;
};

const renderRecent = (items) => {
  const box = $("recent-tickets");
  if (!items.length) {
    box.innerHTML = `<div class="empty">Обращений пока нет</div>`;
    return;
  }
  box.innerHTML = items
    .map(
      (ticket, index) => `
      <article class="dense-row" style="--i:${index}" data-id="${ticket.id}">
        <span class="dense-ico ${esc(ticket.channel)}">${icon(CHANNEL_ICON[ticket.channel] || "i-tickets")}</span>
        <span class="dense-main">
          <b>${esc(ticket.subject)}</b>
          <span>${esc(ticket.public_id)} · ${esc(ticket.customer_name || ticket.customer_ref)}</span>
        </span>
        <span class="dense-side">
          <span class="tag ${esc(ticket.status)}">${esc(ticket.status)}</span>
          <time>${esc(relTime(ticket.created_at))}</time>
        </span>
      </article>`
    )
    .join("");
  box.querySelectorAll("[data-id]").forEach((node) =>
    node.addEventListener("click", () => loadTicketDetail(Number(node.dataset.id)))
  );
};

const renderMeters = (overview) => {
  const total = overview.total_tickets || 0;
  const escalationShare = total ? Math.round((overview.escalated_tickets / total) * 100) : 0;
  const meters = [
    {
      label: "Первый ответ",
      value: `${Math.round(overview.avg_first_response_minutes)} мин`,
      target: "цель ≤ 15 мин",
      pct: Math.min(100, (overview.avg_first_response_minutes / 15) * 100),
      good: overview.avg_first_response_minutes <= 15,
      tone: "",
    },
    {
      label: "Время решения",
      value: `${Number(overview.avg_resolution_hours).toFixed(1)} ч`,
      target: "цель ≤ 24 ч",
      pct: Math.min(100, (overview.avg_resolution_hours / 24) * 100),
      good: overview.avg_resolution_hours <= 24,
      tone: "steel",
    },
    {
      label: "Доля ИИ",
      value: `${Math.round(overview.ai_handled_share * 100)}%`,
      target: "цель ≥ 70%",
      pct: Math.round(overview.ai_handled_share * 100),
      good: overview.ai_handled_share >= 0.7,
      tone: "",
    },
    {
      label: "Эскалации",
      value: `${escalationShare}%`,
      target: "цель < 10%",
      pct: Math.min(100, escalationShare * 10),
      good: escalationShare < 10,
      tone: "sand",
    },
  ];
  $("speed-meters").innerHTML = meters
    .map(
      (meter, index) => `
      <div class="meter" style="--i:${index}">
        <div class="meter-head">
          <b>${esc(meter.label)}</b>
          <span class="${meter.good ? "ok" : "warn"}">${esc(meter.value)} · ${esc(meter.target)}</span>
        </div>
        <div class="meter-track">
          <span class="meter-fill ${meter.tone}" style="--w:${Math.max(3, Math.round(meter.pct))}%"></span>
          <span class="meter-target"></span>
        </div>
      </div>`
    )
    .join("");
};

const loadOverview = async () => {
  if (!state.user || !state.workspaceId) return;
  try {
    const [overview, series, recent] = await Promise.all([
      api("/analytics/overview"),
      api("/analytics/timeseries?days=14"),
      api("/tickets?limit=6"),
    ]);
    const cards = [
      ["Всего обращений", overview.total_tickets],
      ["Открытых", overview.open_tickets],
      ["Эскалаций", overview.escalated_tickets],
      ["Решено", overview.resolved_tickets],
      ["Доля ИИ", `${Math.round(overview.ai_handled_share * 100)}%`],
      ["Сообщений", overview.messages_total],
      ["Документов", overview.documents_total],
      ["Первый ответ, мин", overview.avg_first_response_minutes],
    ];
    $("kpis").innerHTML = cards
      .map(([label, value], index) => `<div class="kpi rise" style="--i:${index}"><div class="value" data-value="${esc(value)}">${esc(value)}</div><div class="label">${esc(label)}</div></div>`)
      .join("");
    animateNumbers($("kpis"));
    attachTilt([...$("kpis").querySelectorAll(".kpi"), ...document.querySelectorAll(".chart-card"), ...document.querySelectorAll(".panel")]);
    drawTimeline(series);
    drawChannels(overview.by_channel);
    renderRecent(recent.items || []);
    renderMeters(overview);
  } catch (error) {
    toast(error.message, "error");
  }
};

const svgEl = (tag, attrs = {}) => {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
};

const legendRow = (items) =>
  `<div class="legend">${items
    .map(([label, color, count]) => `<span><i style="background:${color}"></i>${esc(label)}${count === undefined ? "" : `<b>${count}</b>`}</span>`)
    .join("")}</div>`;

const drawTimeline = (points) => {
  const box = $("chart-timeline");
  const W = 660;
  const H = 240;
  const pad = { t: 18, r: 16, b: 32, l: 36 };
  const innerW = W - pad.l - pad.r;
  const innerH = H - pad.t - pad.b;
  const maxV = Math.max(1, ...points.map((p) => Math.max(p.created, p.resolved)));
  const step = points.length > 1 ? innerW / (points.length - 1) : innerW;
  const px = (i) => pad.l + i * step;
  const py = (v) => pad.t + innerH - (v / maxV) * innerH;
  const line = (key) => points.map((p, i) => `${i ? "L" : "M"}${px(i).toFixed(1)},${py(p[key]).toFixed(1)}`).join(" ");
  const area = (key) => `${line(key)} L${px(points.length - 1).toFixed(1)},${(pad.t + innerH).toFixed(1)} L${pad.l},${(pad.t + innerH).toFixed(1)} Z`;

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Динамика обращений" });
  const defs = svgEl("defs");
  const grad = svgEl("linearGradient", { id: "tl-fill", x1: "0", y1: "0", x2: "0", y2: "1" });
  grad.appendChild(svgEl("stop", { offset: "0%", "stop-color": "rgba(29,90,71,0.30)" }));
  grad.appendChild(svgEl("stop", { offset: "100%", "stop-color": "rgba(29,90,71,0)" }));
  defs.appendChild(grad);
  svg.appendChild(defs);

  for (let g = 0; g <= 3; g += 1) {
    const y = pad.t + (innerH / 3) * g;
    svg.appendChild(svgEl("line", { x1: pad.l, y1: y, x2: W - pad.r, y2: y, stroke: COLORS.line, "stroke-width": 1 }));
    const tick = svgEl("text", { x: 6, y: y + 4, fill: COLORS.muted, "font-size": 10 });
    tick.textContent = Math.round(maxV - (maxV / 3) * g);
    svg.appendChild(tick);
  }

  svg.appendChild(svgEl("path", { d: area("created"), fill: "url(#tl-fill)", "data-fade": "1" }));
  svg.appendChild(svgEl("path", { d: line("created"), fill: "none", stroke: COLORS.accent, "stroke-width": 2.4, "stroke-linejoin": "round", "stroke-linecap": "round", pathLength: 100, "data-draw": "1" }));
  svg.appendChild(svgEl("path", { d: line("resolved"), fill: "none", stroke: COLORS.steel, "stroke-width": 1.8, "stroke-linejoin": "round", pathLength: 100, "data-draw": "1", style: "transition-delay:.18s" }));

  points.forEach((point, index) => {
    if (index % 3 === 0 || index === points.length - 1) {
      const tick = svgEl("text", { x: px(index), y: H - 10, fill: COLORS.muted, "font-size": 10, "text-anchor": "middle" });
      tick.textContent = point.day.slice(5);
      svg.appendChild(tick);
    }
  });

  box.innerHTML = "";
  box.appendChild(svg);
  box.insertAdjacentHTML("beforeend", legendRow([["Создано", COLORS.accent], ["Решено", COLORS.steel]]));
  revealSvg(svg);
};

const drawChannels = (byChannel) => {
  const box = $("chart-channels");
  const total = byChannel.reduce((sum, item) => sum + item.count, 0);
  if (!total) {
    box.innerHTML = `<div class="empty">Нет данных</div>`;
    return;
  }
  const palette = [COLORS.accent, COLORS.steel, COLORS.sand, "#948f86"];
  const size = 190;
  const cx = size / 2;
  const cy = size / 2;
  const radius = size / 2 - 16;
  const svg = svgEl("svg", { viewBox: `0 0 ${size} ${size}`, class: "donut", role: "img", "aria-label": "Каналы" });
  let angle = -Math.PI / 2;
  byChannel.forEach((item, index) => {
    const sweep = (item.count / total) * Math.PI * 2;
    const end = Math.min(angle + sweep, -Math.PI / 2 + Math.PI * 2);
    const x1 = cx + radius * Math.cos(angle);
    const y1 = cy + radius * Math.sin(angle);
    const x2 = cx + radius * Math.cos(end);
    const y2 = cy + radius * Math.sin(end);
    const large = end - angle > Math.PI ? 1 : 0;
    svg.appendChild(svgEl("path", {
      d: `M${x1.toFixed(2)},${y1.toFixed(2)} A${radius},${radius} 0 ${large} 1 ${x2.toFixed(2)},${y2.toFixed(2)}`,
      fill: "none",
      stroke: palette[index % palette.length],
      "stroke-width": 24,
      pathLength: 100,
      class: "donut-seg",
      style: `transition-delay:${(index * 0.12).toFixed(2)}s`,
    }));
    angle = end;
  });
  const centre = svgEl("text", { x: cx, y: cy + 8, "text-anchor": "middle", fill: COLORS.ink, "font-size": 26, "font-family": "Georgia, serif" });
  centre.textContent = total;
  svg.appendChild(centre);
  box.innerHTML = "";
  box.appendChild(svg);
  box.insertAdjacentHTML("beforeend", legendRow(byChannel.map((item, index) => [item.key, palette[index % palette.length], item.count])));
  revealSvg(svg);
};

// tickets

const loadTickets = async () => {
  if (!requireWorkspace()) return;
  const list = $("tickets-list");
  list.innerHTML = `<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>`;
  const params = new URLSearchParams();
  if ($("filter-status").value) params.set("status", $("filter-status").value);
  if ($("filter-channel").value) params.set("channel", $("filter-channel").value);
  if ($("filter-search").value.trim()) params.set("search", $("filter-search").value.trim());
  try {
    const page = await api(`/tickets?${params.toString()}`);
    $("tickets-count").textContent = `найдено ${page.total}`;
    if (!page.items.length) { list.innerHTML = `<div class="empty">Обращений по фильтру нет</div>`; return; }
    list.innerHTML = page.items
      .map((ticket, index) => `
        <article class="ticket-item rise" style="--i:${index}" data-id="${ticket.id}">
          <div class="row">
            <span class="subject">${esc(ticket.subject)}</span>
            <span class="tag ${esc(ticket.status)}">${esc(ticket.status)}</span>
          </div>
          <div class="row meta">
            <span>${esc(ticket.public_id)} · ${esc(ticket.channel)}</span>
            <span>${esc(ticket.customer_name || ticket.customer_ref)}</span>
          </div>
        </article>`)
      .join("");
    list.querySelectorAll(".ticket-item").forEach((node) =>
      node.addEventListener("click", () => loadTicketDetail(Number(node.dataset.id)))
    );
  } catch (error) {
    list.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  }
};

const messageTicks = (message) => {
  const meta = message.meta || {};
  const delivered = meta.delivered !== false;
  const read = meta.read === true || message.sender === "assistant";
  if (!delivered) {
    return `<span class="ticks failed" title="Не отправлено клиенту — бот недоступен">!</span>`;
  }
  if (read) return `<span class="ticks read" title="Прочитано">✓✓</span>`;
  return `<span class="ticks" title="Доставлено">✓</span>`;
};

const learnedBadge = (message) => {
  const learned = (message.meta || {}).learned;
  if (!learned || !learned.learned) return "";
  const text = learned.created ? "В базу знаний" : "База знаний обновлена";
  return `<span class="learned-chip" title="${esc(text)}: ${esc(learned.title || "")}">
            ${icon("doc")}${esc(text)}</span>`;
};

const messageMarkup = (message, index) => {
  const sender = message.sender || "customer";
  const out = sender === "agent" || sender === "assistant";
  const dir = sender === "system" ? "centered system" : out ? "out" : "in";
  const anim = state.renderedMessages.has(message.id) ? "msg-enter-done" : "";
  const ticks = out ? messageTicks(message) : "";
  const who = sender === "assistant" ? "ИИ" : sender === "agent" ? "оператор" : "";
  return `
    <div class="chat-msg ${dir} ${esc(sender)} ${anim}" style="--i:${index}" data-mid="${message.id}">
      <div class="chat-bubble">${esc(message.content)}</div>
      ${sender === "system" ? "" : `<div class="chat-meta">${who ? `<span class="who">${who}</span>` : ""}${sender === "agent" ? learnedBadge(message) : ""}<span>${esc(fmtTime(message.created_at))}</span>${ticks}</div>`}
    </div>`;
};

const renderChat = (messages) => {
  const box = $("ticket-messages");
  box.classList.add("chat-thread");
  if (!messages.length) {
    box.innerHTML = `<div class="empty">Сообщений пока нет — напишите первым</div>`;
    return;
  }
  let lastDay = "";
  const parts = [];
  messages.forEach((message, index) => {
    const day = fmtDay(message.created_at);
    if (day && day !== lastDay) {
      parts.push(`<div class="chat-day">${esc(day)}</div>`);
      lastDay = day;
    }
    parts.push(messageMarkup(message, index));
  });
  box.innerHTML = parts.join("");
  messages.forEach((message) => state.renderedMessages.add(message.id));
  box.scrollTop = box.scrollHeight;
};

const showTyping = () => {
  const box = $("ticket-messages");
  box.querySelector(".typing")?.remove();
  const node = document.createElement("div");
  node.className = "chat-msg in assistant typing";
  node.innerHTML = `<div class="chat-bubble typing"><span></span><span></span><span></span></div>`;
  box.appendChild(node);
  box.scrollTop = box.scrollHeight;
  return node;
};

const loadTicketDetail = async (ticketId) => {
  try {
    const ticket = await api(`/tickets/${ticketId}`);
    state.ticket = ticket;
    $("ticket-title").textContent = ticket.subject;
    $("ticket-meta").textContent = `${ticket.public_id} · ${ticket.channel} · ${ticket.customer_name || ticket.customer_ref}`;
    $("ticket-status").className = `tag ${ticket.status}`;
    $("ticket-status").textContent = ticket.status;
    $("ticket-priority").className = `tag ${ticket.priority}`;
    $("ticket-priority").textContent = ticket.priority;
    renderChat(ticket.messages);
    openLayer("drawer-ticket");
  } catch (error) {
    toast(error.message, "error");
  }
};

const saveTicketActions = async (event) => {
  event.preventDefault();
  if (!state.ticket) return;
  try {
    await api(`/tickets/${state.ticket.id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: $("action-status").value, priority: $("action-priority").value }),
    });
    closeLayer("modal-actions");
    toast("Обращение обновлено", "ok");
    await loadTicketDetail(state.ticket.id);
    await loadTickets();
  } catch (error) {
    toast(error.message, "error");
  }
};

const sendReply = async (event) => {
  event.preventDefault();
  const content = $("reply-text").value.trim();
  if (!content || !state.ticket) return;
  const box = $("ticket-messages");
  $("reply-text").value = "";

  // optimistic outgoing bubble with a "sending" state
  const optimistic = document.createElement("div");
  optimistic.className = "chat-msg out agent";
  optimistic.innerHTML = `
    <div class="chat-bubble">${esc(content)}</div>
    <div class="chat-meta"><span class="who">оператор</span><span>${esc(fmtTime(new Date().toISOString()))}</span><span class="ticks sending">◌</span></div>`;
  box.appendChild(optimistic);
  box.scrollTop = box.scrollHeight;
  const typing = showTyping();

  try {
    const saved = await api(`/tickets/${state.ticket.id}/messages`, {
      method: "POST",
      body: JSON.stringify({ content, sender: "agent" }),
    });
    state.renderedMessages.add(saved.id);
    optimistic.remove();
    typing.remove();
    const ticket = await api(`/tickets/${state.ticket.id}`);
    state.ticket = ticket;
    renderChat(ticket.messages);
    const meta = saved.meta || {};
    if (meta.delivered === false) {
      toast("Ответ сохранён в панели, но клиенту не отправлен — Telegram-бот недоступен", "error");
    } else {
      const learned = meta.learned;
      if (learned && learned.learned) {
        toast(
          learned.created ? "Ответ отправлен и сохранён в базу знаний" : "Отправлено, база знаний обновлена",
          "ok"
        );
      } else {
        toast("Ответ отправлен", "ok");
      }
    }
    await loadTickets();
  } catch (error) {
    optimistic.remove();
    typing.remove();
    toast(error.message, "error");
  }
};

// knowledge

const loadDocuments = async () => {
  if (!requireWorkspace()) return;
  const box = $("kb-documents");
  box.innerHTML = `<div class="skeleton"></div>`;
  try {
    const documents = await api("/knowledge/documents");
    $("kb-count").textContent = `${documents.length} документов`;
    if (!documents.length) { box.innerHTML = `<div class="empty">База знаний пуста</div>`; return; }
    box.innerHTML = documents
      .map((doc, index) => `
        <div class="doc-row rise" style="--i:${index}">
          <span>${esc(doc.title)}<br><span class="muted">${esc(doc.status)} · ${doc.chunk_count} чанков</span></span>
          <button class="btn btn-ghost" data-del="${doc.id}" type="button">Удалить</button>
        </div>`)
      .join("");
    box.querySelectorAll("[data-del]").forEach((button) =>
      button.addEventListener("click", () => {
        const title = documents.find((d) => d.id === Number(button.dataset.del))?.title || "документ";
        askConfirm("Удалить документ", `«${title}» будет удалён вместе с векторами.`, async () => {
          await api(`/knowledge/documents/${button.dataset.del}`, { method: "DELETE" });
          toast("Документ удалён", "ok");
          loadDocuments();
        });
      })
    );
  } catch (error) {
    box.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  }
};

const searchKnowledge = async () => {
  if (!requireWorkspace()) return;
  const query = $("kb-query").value.trim();
  if (!query) return;
  const box = $("kb-results");
  box.innerHTML = `<div class="skeleton"></div>`;
  try {
    const data = await api("/knowledge/search", { method: "POST", body: JSON.stringify({ query }) });
    box.innerHTML = data.hits.length
      ? data.hits
          .map((hit, index) => `
            <div class="kb-hit" style="--i:${index}">
              <strong>${esc(hit.document_title)}</strong> <span class="score">score ${hit.score}</span>
              <p class="muted">${esc(hit.content.slice(0, 260))}</p>
            </div>`)
          .join("")
      : `<div class="empty">Ничего не найдено</div>`;
  } catch (error) {
    box.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  }
};

const uploadDocument = async (event) => {
  event.preventDefault();
  if (!requireWorkspace()) return;
  $("doc-status").textContent = "Индексирую...";
  try {
    const document = await api("/knowledge/documents", {
      method: "POST",
      body: JSON.stringify({
        title: $("doc-title").value,
        content: $("doc-content").value,
        source: $("doc-source").value || "manual",
      }),
    });
    $("doc-status").textContent = "";
    $("doc-form").reset();
    closeLayer("modal-doc");
    toast(`Документ загружен: ${document.chunk_count} чанков`, "ok");
    loadDocuments();
  } catch (error) {
    $("doc-status").textContent = "";
    toast(error.message, "error");
  }
};

// team

const canManage = () => state.workspace && state.workspace.role !== "agent";

const loadTeam = async () => {
  if (!requireWorkspace()) return;
  const box = $("team-list");
  box.innerHTML = `<div class="skeleton"></div>`;
  try {
    const members = await api("/workspaces/current/members");
    $("team-count").textContent = `${members.length} сотрудников`;
    $("team-add").classList.toggle("hidden", !canManage());
    box.innerHTML = members
      .map((member, index) => `
        <div class="member-row" style="--i:${index}" data-member="${member.id}">
          <div class="member-avatar">${esc(initials(member.full_name || member.email))}</div>
          <div class="member-meta">
            <span class="name">${esc(member.full_name || "Без имени")}</span>
            <span class="email">${esc(member.email)}</span>
          </div>
          <div class="member-tools">
            ${member.role === "owner"
              ? `<span class="ws-role owner">владелец</span>`
              : canManage()
                ? `<select data-role="${member.id}" ${member.user_id === state.user.id ? "disabled" : ""}>
                     <option value="agent" ${member.role === "agent" ? "selected" : ""}>agent</option>
                     <option value="admin" ${member.role === "admin" ? "selected" : ""}>admin</option>
                   </select>
                   <button class="icon-btn" data-remove="${member.id}" title="Удалить" ${member.user_id === state.user.id ? "disabled" : ""}>✕</button>`
                : `<span class="ws-role ${esc(member.role)}">${esc(member.role)}</span>`}
          </div>
        </div>`)
      .join("");

    box.querySelectorAll("[data-role]").forEach((select) =>
      select.addEventListener("change", async () => {
        try {
          await api(`/workspaces/current/members/${select.dataset.role}`, {
            method: "PATCH",
            body: JSON.stringify({ role: select.value }),
          });
          toast("Роль обновлена", "ok");
        } catch (error) {
          toast(error.message, "error");
          loadTeam();
        }
      })
    );
    box.querySelectorAll("[data-remove]").forEach((button) =>
      button.addEventListener("click", () => {
        const row = button.closest(".member-row");
        const name = row?.querySelector(".name")?.textContent || "сотрудника";
        askConfirm("Удалить сотрудника", `${name} потеряет доступ к этой комнате.`, async () => {
          await api(`/workspaces/current/members/${button.dataset.remove}`, { method: "DELETE" });
          toast("Сотрудник удалён", "ok");
          loadTeam();
        });
      })
    );
  } catch (error) {
    box.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  }
};

const submitMember = async (event) => {
  event.preventDefault();
  $("member-error").textContent = "";
  const payload = {
    email: $("member-email").value.trim(),
    role: $("member-role").value,
    full_name: $("member-name").value.trim(),
  };
  const password = $("member-password").value;
  if (password) payload.password = password;
  try {
    await api("/workspaces/current/members", { method: "POST", body: JSON.stringify(payload) });
    closeLayer("modal-member");
    $("member-form").reset();
    celebrate($("team-add"));
    toast("Сотрудник добавлен в команду", "ok");
    loadTeam();
  } catch (error) {
    $("member-error").textContent = error.message;
  }
};

// integrations

const TG_STATUS = {
  off: { label: "не подключён", cls: "" },
  connecting: { label: "подключается…", cls: "wait" },
  online: { label: "на связи", cls: "on" },
  invalid: { label: "токен отклонён", cls: "bad" },
  error: { label: "ошибка запуска", cls: "bad" },
};

const tgStatusInfo = (ws) => TG_STATUS[ws.telegram_status] || TG_STATUS.off;

const telegramBlock = (ws) => {
  const info = tgStatusInfo(ws);
  const username = ws.telegram_bot_username || "";
  const deep = ws.telegram_deep_link || "";
  const when = ws.telegram_last_seen
    ? new Date(ws.telegram_last_seen).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" })
    : "";

  const spinner =
    ws.telegram_status === "connecting" ? `<span class="tg-spinner" aria-hidden="true"></span>` : "";
  const lines = [
    `<div class="tg-line"><span class="int-status ${info.cls}"><i></i> ${info.label}${username ? ` · @${esc(username)}` : ""}</span>${spinner}</div>`,
  ];
  if (ws.telegram_error && ws.telegram_status !== "online") {
    lines.push(`<p class="tg-error">${esc(ws.telegram_error)}</p>`);
  }
  if (when && (ws.telegram_status === "online" || ws.telegram_status === "error")) {
    lines.push(`<p class="muted">Последняя связь с ботом: ${esc(when)}</p>`);
  }
  if (deep) {
    lines.push(`
       <div class="int-field"><label>Ссылка для вашего сайта</label>
         <code class="int-code" id="tg-link">${esc(deep)}</code>
         <div class="int-actions">
           <button class="btn copy-btn" data-copy="tg-link" type="button">${icon("copy")}<span>Скопировать ссылку</span></button>
           <a class="btn btn-ghost" href="${esc(deep)}" target="_blank" rel="noopener">${icon("arrow")}<span>Открыть бота</span></a>
         </div>
       </div>
       <div class="int-field"><label>Кнопка «Написать в Telegram» для вставки на сайт</label>
         <code class="int-code" id="tg-button">${esc(`<a href="${deep}" target="_blank">Написать в поддержку</a>`)}</code>
         <div class="int-actions">
           <button class="btn btn-ghost copy-btn" data-copy="tg-button" type="button">${icon("copy")}<span>Скопировать HTML</span></button>
         </div>
       </div>`);
  }
  return `<div class="tg-state">${lines.join("")}</div>`;
};

const maskKey = (value) => {
  const text = String(value || "");
  if (!text) return "не задан";
  if (text.length <= 10) return text.slice(0, 3) + "••••";
  return `${text.slice(0, 6)}${"•".repeat(10)}${text.slice(-4)}`;
};

const keysBlock = () => {
  const items = state.apiKeys || [];
  const rows = items.length
    ? items.map((key) => {
        const dead = key.revoked;
        return `<li class="key-row ${dead ? "dead" : ""}">
          <span class="key-name">${esc(key.name)}</span>
          <code class="key-mask">${esc(key.masked)}</code>
          <span class="key-meta">${dead ? "отозван" : key.last_used_at ? `использован ${esc(fmtTime(key.last_used_at))}` : "не использовался"}</span>
          ${dead ? "" : `<button class="btn btn-ghost key-revoke" data-key="${key.id}" type="button">${icon("power")}<span>Отозвать</span></button>`}
        </li>`;
      }).join("")
    : `<li class="muted">Именованных ключей пока нет</li>`;

  return `<div class="int-field keys-field">
    <label>Ключи для интеграций</label>
    <ul class="key-list">${rows}</ul>
    <div class="int-actions">
      <button class="btn" id="key-create" type="button">${icon("plus")}<span>Выпустить ключ</span></button>
    </div>
    <p class="muted">Ключ показывается один раз и хранится в виде хэша. Отзыв не влияет на остальные интеграции.</p>
  </div>`;
};

const loadApiKeys = async () => {
  if (!state.user) return;
  try {
    state.apiKeys = await api("/workspaces/current/keys");
  } catch (error) {
    state.apiKeys = [];
  }
};

const createApiKey = async () => {
  const name = window.prompt("Название ключа (например, «CRM», «Лендинг»):", "Интеграция");
  if (!name) return;
  try {
    const created = await api("/workspaces/current/keys", {
      method: "POST",
      body: JSON.stringify({ name, scope: "ingest" }),
    });
    await loadApiKeys();
    renderIntegrations();
    await copyText(created.key);
    toast("Ключ создан и скопирован — сохраните его сейчас", "ok");
  } catch (error) {
    toast(error.message, "error");
  }
};

const revokeApiKey = async (id) => {
  askConfirm(
    "Отозвать ключ",
    "Интеграция, которая использует этот ключ, перестанет работать сразу.",
    async () => {
      await api(`/workspaces/current/keys/${id}`, { method: "DELETE" });
      await loadApiKeys();
      renderIntegrations();
      toast("Ключ отозван", "ok");
    }
  );
};

const intCard = (index, iconName, title, subtitle, body) => `
  <section class="int-card" style="--i:${index}">
    <div class="int-head"><span class="int-icon">${icon(iconName)}</span><h3>${title}</h3></div>
    <p>${subtitle}</p>
    ${body}
  </section>`;

const renderIntegrations = () => {
  const box = $("int-body");
  if (!state.user) {
    box.innerHTML = `<div class="empty">Войдите или зарегистрируйтесь, чтобы получить ключи интеграции</div>`;
    return;
  }
  if (!state.workspace) {
    box.innerHTML = `<div class="empty">Выберите или создайте команду, чтобы получить ключи интеграции</div>`;
    return;
  }
  const ws = state.workspace;
  const manage = canManage();
  const owner = ws.role === "owner";

  box.innerHTML = [
    intCard(
      0,
      "globe",
      "Веб-виджет",
      "Вставьте один тег на свой сайт — и посетители смогут писать в ИИ-поддержку прямо со страницы.",
      `<code class="int-code" id="widget-snippet">${esc(ws.widget_snippet)}</code>
       <div class="int-actions">
         <button class="btn copy-btn" data-copy="widget-snippet" type="button">${icon("copy")}<span>Скопировать код</span></button>
         <button class="btn btn-ghost copy-btn" data-copy-value="${esc(ws.widget_url)}" type="button">${icon("arrow")}<span>Ссылка на widget.js</span></button>
       </div>`
    ),
    intCard(
      1,
      "code",
      "REST API",
      "Отправляйте сообщения из своей CRM, лендинга или мобильного приложения через один запрос.",
      `<div class="int-value"><span>${esc(ws.api_base_url)}</span></div>
       <div class="int-field"><label>Ключ канала (X-Workspace-Key)</label>
         <div class="int-value"><span id="channel-key" class="masked">${esc(maskKey(ws.channel_key))}</span>
           ${manage ? `<button class="btn btn-ghost" id="reveal-channel" type="button">${icon("eye")}<span>Показать</span></button>` : ""}</div>
         <p class="muted">Ключ хранится только в виде хэша — открытым он показывается лишь по кнопке выше. Для интеграций надёжнее выпустить именованный ключ ниже: его можно отозвать отдельно, не ломая другие подключения.</p>
       </div>
       <code class="int-code" id="curl-sample">curl -X POST ${esc(ws.api_base_url)}/ingest/message \\
  -H "Content-Type: application/json" \\
  -H "X-Channel-Key: ВАШ_КЛЮЧ" \\
  -d '{"channel":"api","content":"Здравствуйте!","customer_ref":"client-1"}'</code>
       <div class="int-actions">
         <button class="btn btn-ghost copy-btn" data-copy="curl-sample" type="button">${icon("copy")}<span>Скопировать пример</span></button>
       </div>
       ${manage ? keysBlock() : ""}
       ${owner ? `<div class="int-actions"><button class="btn btn-ghost" id="rotate-channel" type="button">${icon("refresh")}<span>Перевыпустить ключ</span></button></div>` : ""}`
    ),
    intCard(
      2,
      "telegram",
      "Telegram",
      "Вставьте токен бота — система сама проверит его, запустит и выдаст готовую ссылку для сайта.",
      `${telegramBlock(ws)}
       <div class="int-field"><label>Токен бота</label>
         <input id="telegram-token" placeholder="${ws.telegram_enabled ? "токен сохранён — вставьте новый, чтобы заменить" : "123456:ABC-DEF..."}" value="" ${manage ? "" : "disabled"} />
         <p class="muted">Создайте бота у @BotFather и скопируйте токен. Ничего больше настраивать не нужно.</p>
       </div>
       ${manage
         ? `<div class="int-actions">
              <button class="btn" id="telegram-save" type="button">${icon("check")}<span>Подключить бота</span></button>
              <button class="btn btn-ghost" id="telegram-disable" type="button">${icon("power")}<span>Отключить</span></button>
            </div>`
         : `<p class="muted">Подключение Telegram доступно администраторам команды.</p>`}`
    ),
    intCard(
      3,
      "rocket",
      "Как подключить за 3 шага",
      "Всё, что нужно, чтобы включить поддержку на своём бизнесе.",
      `<ol class="steps">
         <li>Скопируйте код веб-виджета и вставьте перед <b>&lt;/body&gt;</b> на сайте.</li>
         <li>Или добавьте ключ канала в свою систему и вызывайте REST API.</li>
         <li>Подключите Telegram-бота — сообщения попадут в эту же комнату.</li>
       </ol>
       <div class="int-actions"><button class="btn btn-ghost" id="rotate-widget" type="button">${icon("refresh")}<span>Перевыпустить ключ виджета</span></button></div>
       <p class="muted">Все обращения собираются здесь: следите за метриками на «Обзоре» и отвечайте в «Обращениях».</p>`
    ),
  ].join("");

  bindIntegrationActions(owner);
  if (["connecting", "online", "error"].includes(ws.telegram_status)) watchTelegram();
};

const bindIntegrationActions = (owner) => {
  $("int-body").querySelectorAll("[data-copy]").forEach((button) =>
    button.addEventListener("click", () => {
      const node = $(button.dataset.copy);
      if (node) copyText(node.textContent, button);
    })
  );
  $("int-body").querySelectorAll("[data-copy-value]").forEach((button) =>
    button.addEventListener("click", () => copyText(button.dataset.copyValue, button))
  );

  const telegramSave = $("telegram-save");
  if (telegramSave) {
    telegramSave.addEventListener("click", () => saveTelegram(true));
    $("telegram-disable").addEventListener("click", () => saveTelegram(false));
  }
  const keyCreate = $("key-create");
  if (keyCreate) keyCreate.addEventListener("click", createApiKey);
  $("int-body").querySelectorAll(".key-revoke").forEach((button) =>
    button.addEventListener("click", () => revokeApiKey(Number(button.dataset.key)))
  );
  const reveal = $("reveal-channel");
  if (reveal) {
    reveal.addEventListener("click", () => {
      const node = $("channel-key");
      if (!node || !state.workspace) return;
      const full = state.workspace.channel_key || "";
      if (full.includes("•")) {
        toast("Открытый ключ доступен только администратору команды", "error");
        return;
      }
      const revealed = node.textContent.includes("•");
      node.textContent = revealed ? full : maskKey(full);
      reveal.querySelector("span").textContent = revealed ? "Скрыть" : "Показать";
    });
  }
  const rotateChannel = $("rotate-channel");
  if (rotateChannel) rotateChannel.addEventListener("click", () => rotateKey("channel"));
  const rotateWidget = $("rotate-widget");
  if (rotateWidget && owner) rotateWidget.addEventListener("click", () => rotateKey("widget"));
};

const saveTelegram = async (enabled) => {
  const token = $("telegram-token").value.trim();
  const alreadyOn = state.workspace && state.workspace.telegram_enabled;
  if (enabled && !token) {
    // Токен не хранится в открытом виде — если он уже подключён, пустое поле не ошибка
    if (alreadyOn) { toast("Бот уже подключён — вставьте токен, чтобы заменить его", "error"); return; }
    toast("Вставьте токен бота", "error");
    return;
  }
  try {
    state.workspace = await api("/workspaces/current/telegram", {
      method: "PUT",
      body: JSON.stringify({ bot_token: token, enabled }),
    });
    renderIntegrations();
    const info = tgStatusInfo(state.workspace);
    if (info.cls === "bad") {
      toast(state.workspace.telegram_error || "Telegram отклонил токен", "error");
    } else if (!enabled) {
      toast("Telegram отключён", "ok");
    } else {
      toast("Токен принят — подключаю бота…", "ok");
      watchTelegram();
    }
  } catch (error) {
    toast(error.message, "error");
  }
};

/* Следим за статусом бота, пока открыт раздел «Интеграции»: пока идёт connecting — часто,
   после — редко, чтобы поймать падение. Опрос останавливается при закрытии шторки. */
let telegramTimer = null;

const tgPollDelay = (status) => (status === "connecting" ? 2000 : 15000);

const watchTelegram = () => {
  stopTelegramWatch();
  const tick = async () => {
    if (!state.user || !$("int-body")) { stopTelegramWatch(); return; }
    let delay = tgPollDelay("connecting");
    try {
      const live = await api("/workspaces/current/telegram/status");
      const before = state.workspace ? state.workspace.telegram_status : "";
      // Эндпоинт отдаёт короткие имена (status/error), комната ждёт telegram_*
      state.workspace = {
        ...state.workspace,
        telegram_enabled: live.enabled,
        telegram_status: live.status,
        telegram_bot_username: live.bot_username,
        telegram_bot_name: live.bot_name,
        telegram_error: live.error,
        telegram_deep_link: live.deep_link,
        telegram_last_seen: live.last_seen,
      };
      if (state.workspace.telegram_status !== before) {
        renderIntegrations();
        if (before === "connecting") {
          const info = tgStatusInfo(state.workspace);
          toast(
            info.cls === "bad" ? state.workspace.telegram_error || info.label : "Бот на связи — принимает обращения",
            info.cls === "bad" ? "error" : "ok"
          );
        }
      }
      delay = tgPollDelay(state.workspace.telegram_status);
    } catch (error) {
      console.warn("telegram status poll failed", error);
    }
    telegramTimer = setTimeout(tick, delay);
  };
  telegramTimer = setTimeout(tick, 0);
};

const stopTelegramWatch = () => {
  clearTimeout(telegramTimer);
  telegramTimer = null;
};

const rotateKey = async (target) => {
  askConfirm(
    "Перевыпустить ключ",
    "Старый ключ перестанет работать сразу — обновите его в своих настройках.",
    async () => {
      state.workspace = await api(`/workspaces/current/keys/rotate?target=${target}`, { method: "POST" });
      renderIntegrations();
      toast("Ключ перевыпущен", "ok");
    }
  );
};

// confirm

const askConfirm = (title, text, handler) => {
  $("confirm-title").textContent = title;
  $("confirm-text").textContent = text;
  state.confirm = handler;
  openLayer("modal-confirm");
};

// wiring

const toggleWsMenu = (force) => {
  state.wsOpen = force === undefined ? !state.wsOpen : force;
  $("ws-switch").classList.toggle("open", state.wsOpen);
};

const bind = () => {
  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-open]");
    if (opener) {
      const id = opener.dataset.open;
      if (opener.dataset.authMode) setAuthMode(opener.dataset.authMode);
      if (AUTH_DRAWERS.has(id)) {
        if (!state.user) {
          setAuthMode("login");
          openLayer("modal-auth");
          toast(`Войдите или зарегистрируйтесь, чтобы открыть «${(opener.textContent || "").trim()}»`, "error");
          return;
        }
        if (!state.workspaceId) {
          $("workspace-error").textContent = "";
          openLayer("modal-workspace");
          toast("Сначала создайте свою команду", "error");
          return;
        }
      }
      if (id.startsWith("drawer-")) {
        document.querySelectorAll(".drawer.open").forEach((node) => {
          if (node.id !== id && node.id !== "drawer-ticket") {
            node.classList.remove("open");
            node.setAttribute("aria-hidden", "true");
          }
        });
        if (id === "drawer-tickets") loadTickets();
        if (id === "drawer-knowledge") loadDocuments();
        if (id === "drawer-team") loadTeam();
        if (id === "drawer-integrations") { loadApiKeys().then(renderIntegrations); renderIntegrations(); }
      }
      openLayer(id);
      return;
    }
    const closer = event.target.closest("[data-close]");
    if (closer) { closeLayer(closer.dataset.close); return; }
    if (event.target.id === "backdrop") closeAllLayers();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const modal = [...document.querySelectorAll(".modal.open")].pop();
    if (modal) { closeLayer(modal.id); return; }
    if (state.wsOpen) { toggleWsMenu(false); return; }
    closeAllLayers();
  });

  // workspace switcher
  $("ws-trigger").addEventListener("click", (event) => { event.stopPropagation(); toggleWsMenu(); });
  $("ws-create").addEventListener("click", (event) => {
    event.stopPropagation();
    toggleWsMenu(false);
    $("workspace-error").textContent = "";
    openLayer("modal-workspace");
  });
  document.addEventListener("click", (event) => {
    if (state.wsOpen && !event.target.closest("#ws-switch")) toggleWsMenu(false);
  });

  document.querySelectorAll(".tab").forEach((tab) =>
    tab.addEventListener("click", () => setAuthMode(tab.dataset.authTab))
  );
  document.querySelectorAll("[data-view]").forEach((button) =>
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-view]").forEach((node) => node.classList.toggle("active", node === button));
      closeAllLayers();
    })
  );
  $("auth-trigger").addEventListener("click", () => { setAuthMode("login"); openLayer("modal-auth"); });
  $("auth-form").addEventListener("submit", submitAuth);
  $("logout").addEventListener("click", () => logout());
  $("workspace-form").addEventListener("submit", submitWorkspace);
  $("qa-new-room").addEventListener("click", () => { $("workspace-error").textContent = ""; openLayer("modal-workspace"); });
  $("member-form").addEventListener("submit", submitMember);
  $("team-add").addEventListener("click", () => { $("member-error").textContent = ""; openLayer("modal-member"); });
  $("team-refresh").addEventListener("click", loadTeam);
  $("tickets-refresh").addEventListener("click", loadTickets);
  ["filter-status", "filter-channel"].forEach((id) => $(id).addEventListener("change", loadTickets));
  $("filter-search").addEventListener("keydown", (event) => { if (event.key === "Enter") loadTickets(); });
  $("ticket-actions").addEventListener("click", () => {
    if (!state.ticket) return;
    $("action-status").value = state.ticket.status;
    $("action-priority").value = state.ticket.priority;
    openLayer("modal-actions");
  });
  $("actions-form").addEventListener("submit", saveTicketActions);
  $("reply-form").addEventListener("submit", sendReply);
  $("reply-text").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("reply-form").requestSubmit(); }
  });
  $("kb-search").addEventListener("click", searchKnowledge);
  $("kb-query").addEventListener("keydown", (event) => { if (event.key === "Enter") searchKnowledge(); });
  $("doc-form").addEventListener("submit", uploadDocument);
  $("confirm-ok").addEventListener("click", async () => {
    const handler = state.confirm;
    state.confirm = null;
    closeLayer("modal-confirm");
    if (handler) {
      try { await handler(); } catch (error) { toast(error.message, "error"); }
    }
  });
};

bind();
initCursorLight();
initHeroTilt();
setAuthMode("login");
render();
openApp();
