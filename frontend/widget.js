(function () {
  const DEFAULTS = {
    title: "Поддержка",
    subtitle: "ИИ-ассистент отвечает мгновенно",
    placeholder: "Опишите вопрос...",
    accent: "#1f5f4b",
    workspaceKey: "",
    apiBase: "",
    autoOpen: false,
    autoload: true,
  };

  const currentScript = document.currentScript;

  function readConfig() {
    const config = { ...DEFAULTS };
    const params = new URLSearchParams(window.location.search);
    const queryKey = params.get("wk") || params.get("workspace") || "";
    if (queryKey) config.workspaceKey = queryKey;

    if (currentScript) {
      const dataset = currentScript.dataset || {};
      if (dataset.workspaceKey) config.workspaceKey = dataset.workspaceKey;
      if (dataset.workspace) config.workspaceKey = dataset.workspace;
      if (dataset.title) config.title = dataset.title;
      if (dataset.subtitle) config.subtitle = dataset.subtitle;
      if (dataset.accent) config.accent = dataset.accent;
      if (dataset.apiBase) config.apiBase = dataset.apiBase;
      if (dataset.autoOpen === "true") config.autoOpen = true;
    }
    return config;
  }

  // Виджет живёт на сайте клиента, поэтому стучиться к window.location нельзя:
  // адрес API задан в разметке (data-api-base), иначе берём текущий домен.
  function resolveApiBase(config) {
    const configured = (config.apiBase || "").trim();
    if (configured) return configured.replace(/\/+$/, "");
    return `${window.location.origin}/api`;
  }

  function buildSocketUrl(config) {
    const apiBase = resolveApiBase(config);
    let parsed;
    try {
      parsed = new URL(apiBase);
    } catch (error) {
      parsed = new URL(`${window.location.origin}/api`);
    }
    const scheme = parsed.protocol === "https:" || parsed.protocol === "wss:" ? "wss" : "ws";
    const path = `${parsed.pathname.replace(/\/+$/, "")}/ws/chat`;
    const query = config.workspaceKey ? `?key=${encodeURIComponent(config.workspaceKey)}` : "";
    return `${scheme}://${parsed.host}${path}${query}`;
  }

  function init(options) {
    const config = { ...readConfig(), ...(options || {}) };
    if (document.getElementById("aisd-widget")) return;

    const style = document.createElement("style");
    style.textContent = `
      #aisd-widget{position:fixed;right:24px;bottom:24px;z-index:2147483000;font-family:Inter,Segoe UI,system-ui,sans-serif}
      #aisd-widget *{box-sizing:border-box;border-radius:0}
      #aisd-launcher{width:56px;height:56px;display:grid;place-items:center;background:#15171c;color:#f2f0eb;border:1px solid #15171c;cursor:pointer;box-shadow:0 12px 28px -18px rgba(21,23,28,.65);transition:transform .3s cubic-bezier(.22,.61,.36,1),background .25s;position:relative}
      #aisd-launcher:hover{background:${config.accent};border-color:${config.accent};transform:translateY(-2px)}
      #aisd-launcher .aisd-ico{width:24px;height:24px;fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}
      #aisd-launcher .aisd-ping{position:absolute;inset:0;border:1px solid rgba(255,255,255,.45);animation:aisd-ping 3.6s cubic-bezier(.16,1,.3,1) infinite}
      @keyframes aisd-ping{0%{transform:scale(1);opacity:.5}70%{transform:scale(2.2);opacity:0}100%{opacity:0}}
      #aisd-panel{position:absolute;right:0;bottom:70px;width:370px;height:540px;max-height:80vh;background:#fff;border:1px solid #cdc9bf;box-shadow:0 22px 48px -30px rgba(21,23,28,.55);flex-direction:column;opacity:0;visibility:hidden;transform:translateY(14px) scale(.98);transform-origin:bottom right;transition:opacity .38s cubic-bezier(.16,1,.3,1),transform .42s cubic-bezier(.16,1,.3,1),visibility .38s}
      #aisd-panel.open{display:flex;opacity:1;visibility:visible;transform:translateY(0) scale(1)}
      #aisd-head{background:#15171c;color:#f2f0eb;padding:16px;position:relative;overflow:hidden}
      #aisd-head::after{content:"";position:absolute;inset:0;background:linear-gradient(100deg,transparent 34%,rgba(255,255,255,.16) 48%,transparent 62%);transform:translateX(-130%);animation:aisd-sheen 7s cubic-bezier(.22,.61,.36,1) infinite}
      @keyframes aisd-sheen{0%,64%{transform:translateX(-130%)}90%,100%{transform:translateX(130%)}}
      #aisd-head b{display:block;font-size:15px;letter-spacing:-.01em}
      #aisd-head span{font-size:12px;opacity:.7}
      #aisd-body{flex:1;overflow:auto;padding:16px;background:#faf9f6;display:flex;flex-direction:column;gap:11px;scroll-behavior:smooth}
      #aisd-widget .b{max-width:84%;padding:11px 13px;font-size:13px;line-height:1.45;white-space:pre-wrap;word-break:break-word;border:1px solid #e4e1d9;animation:aisd-rise .55s cubic-bezier(.16,1,.3,1) both;transition:transform .4s cubic-bezier(.16,1,.3,1),box-shadow .4s}
      #aisd-widget .row{display:flex;flex-direction:column;gap:4px;max-width:84%}
      #aisd-widget .row.user{align-self:flex-end;align-items:flex-end}
      #aisd-widget .row.bot{align-self:flex-start;align-items:flex-start}
      #aisd-widget .row .b{max-width:100%}
      #aisd-widget .b.user{align-self:flex-end;background:${config.accent};border-color:${config.accent};color:#fff}
      #aisd-widget .b.bot{align-self:flex-start;background:#fff;border-left:3px solid ${config.accent};color:#15171c}
      #aisd-widget .b.sys{align-self:center;background:transparent;border:none;color:#78756e;font-size:11px;text-align:center}
      #aisd-widget .meta{font-size:10px;color:#9a978f;display:flex;gap:6px;align-items:center;padding:0 2px}
      #aisd-widget .ticks{letter-spacing:-3px}
      #aisd-widget .ticks.read{color:${config.accent}}
      #aisd-widget .ticks.sending{opacity:.6;animation:aisd-pulse 1.1s ease-in-out infinite}
      @keyframes aisd-pulse{0%,100%{opacity:.35}50%{opacity:.9}}
      #aisd-widget .typing{display:inline-flex;gap:5px;align-items:center}
      #aisd-widget .typing i{width:6px;height:6px;background:${config.accent};opacity:.35;animation:aisd-typing 1.25s cubic-bezier(.22,.61,.36,1) infinite}
      #aisd-widget .typing i:nth-child(2){animation-delay:.16s}
      #aisd-widget .typing i:nth-child(3){animation-delay:.32s}
      @keyframes aisd-typing{0%,60%,100%{transform:translateY(0);opacity:.35}30%{transform:translateY(-5px);opacity:1}}
      #aisd-foot{display:flex;gap:8px;padding:12px;border-top:1px solid #e4e1d9;background:#fff}
      #aisd-input{flex:1;border:1px solid #cdc9bf;padding:10px;font-size:13px;resize:none;font-family:inherit;transition:border-color .2s,box-shadow .2s}
      #aisd-input:focus{outline:none;border-color:${config.accent};box-shadow:0 0 0 3px rgba(29,90,71,.13)}
      #aisd-send{display:grid;place-items:center;width:44px;background:${config.accent};color:#fff;border:1px solid ${config.accent};cursor:pointer;transition:background .22s,transform .18s}
      #aisd-send .aisd-ico{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
      #aisd-send:hover{background:#17483a}
      #aisd-send:active{transform:translateY(1px)}
      @keyframes aisd-rise{0%{opacity:0;transform:translateY(12px) scale(.97);filter:blur(8px)}60%{opacity:1;filter:blur(1px)}100%{opacity:1;transform:translateY(0) scale(1);filter:blur(0)}}
      @media (max-width:520px){#aisd-panel{width:calc(100vw - 32px);right:-8px}}
    `;
    document.head.appendChild(style);

    const root = document.createElement("div");
    root.id = "aisd-widget";
    root.innerHTML = `
      <div id="aisd-panel" aria-hidden="true">
        <div id="aisd-head"><b>${config.title}</b><span>${config.subtitle}</span></div>
        <div id="aisd-body"></div>
        <div id="aisd-foot">
          <textarea id="aisd-input" rows="1" placeholder="${config.placeholder}"></textarea>
          <button id="aisd-send" aria-label="Отправить"><svg class="aisd-ico" viewBox="0 0 24 24"><path d="M20.5 3.5 10.6 13.4"/><path d="M20.5 3.5 14 20.7l-3.4-7.3-7.3-3.4 17.2-6.5Z"/></svg></button>
        </div>
      </div>
      <button id="aisd-launcher" aria-label="Открыть чат"><span class="aisd-ping"></span><svg class="aisd-ico" viewBox="0 0 24 24"><path d="M20.6 11.8c0 4.3-3.8 7.8-8.6 7.8-1 0-2-.14-2.9-.4L4.2 21l1.5-3.7c-1.4-1.4-2.2-3.2-2.2-5.5 0-4.3 3.8-7.8 8.6-7.8s8.5 3.5 8.5 7.8Z"/><path d="M8.8 11.9h6.4M8.8 14.6h4"/></svg></button>`;
    document.body.appendChild(root);

    const panel = root.querySelector("#aisd-panel");
    const body = root.querySelector("#aisd-body");
    const input = root.querySelector("#aisd-input");

    let socket = null;
    let queue = [];

    function scroll() {
      body.scrollTop = body.scrollHeight;
    }

    function append(text, kind) {
      const bubble = document.createElement("div");
      bubble.className = `b ${kind}`;
      bubble.textContent = text;
      body.appendChild(bubble);
      scroll();
      return bubble;
    }

    function appendUser(text) {
      const row = document.createElement("div");
      row.className = "row user";
      row.innerHTML = `<div class="b user">${text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]))}</div>
        <div class="meta"><span class="ticks sending">◌</span></div>`;
      body.appendChild(row);
      scroll();
      return row;
    }

    function appendBot(text) {
      const row = document.createElement("div");
      row.className = "row bot";
      row.innerHTML = `<div class="b bot">${text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]))}</div>
        <div class="meta"><span class="ticks read">✓✓</span></div>`;
      body.appendChild(row);
      scroll();
      return row;
    }

    function showTyping() {
      const node = document.createElement("div");
      node.className = "b bot typing";
      node.innerHTML = `<i></i><i></i><i></i>`;
      body.appendChild(node);
      scroll();
      return node;
    }

    let noticeShown = false;
    let retryDelay = 2000;

    function connect() {
      socket = new WebSocket(buildSocketUrl(config));
      socket.addEventListener("open", () => {
        noticeShown = false;
        retryDelay = 2000;
        append("Чем можем помочь?", "sys");
        queue.forEach((message) => socket.send(message));
        queue = [];
      });
      socket.addEventListener("message", (event) => {
        let data;
        try {
          data = JSON.parse(event.data);
        } catch (error) {
          append("Не удалось разобрать ответ сервера.", "sys");
          return;
        }
        const pending = body.querySelector(".typing");
        if (pending) pending.remove();
        const sent = body.querySelector(".ticks.sending");
        if (sent) { sent.classList.remove("sending"); sent.classList.add("read"); sent.textContent = "✓✓"; }
        if (data.type === "reply") {
          appendBot(data.reply);
          append(`Обращение ${data.ticket_ref}`, "sys");
        } else if (data.type === "operator_reply") {
          // Живой ответ оператора приходит по тому же сокету
          append("Оператор ответил:", "sys");
          appendBot(data.reply);
        } else if (data.type === "error") {
          append(`Ошибка: ${data.detail}`, "sys");
        }
      });
      socket.addEventListener("close", () => {
        // Подсказку показываем один раз, а попытки отодвигаем всё дальше:
        // иначе недоступный сервер забьёт весь чат сообщениями.
        if (!noticeShown) {
          noticeShown = true;
          append("Соединение с поддержкой потеряно, переподключаюсь…", "sys");
        }
        retryDelay = Math.min(retryDelay * 2, 15000);
        setTimeout(connect, retryDelay);
      });

      socket.addEventListener("error", () => {
        if (socket && socket.readyState !== WebSocket.OPEN) socket.close();
      });
    }

    function send() {
      const text = input.value.trim();
      if (!text) return;
      appendUser(text);
      input.value = "";
      showTyping();
      const payload = JSON.stringify({
        content: text,
        customer_name: "Веб-гость",
        workspace_key: config.workspaceKey || undefined,
      });
      if (socket && socket.readyState === WebSocket.OPEN) socket.send(payload);
      else queue.push(payload);
    }

    root.querySelector("#aisd-launcher").addEventListener("click", () => {
      const willOpen = !panel.classList.contains("open");
      panel.classList.toggle("open");
      panel.setAttribute("aria-hidden", String(!willOpen));
      if (willOpen && !socket) connect();
      if (willOpen) input.focus();
    });
    root.querySelector("#aisd-send").addEventListener("click", send);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        send();
      }
    });

    if (config.autoOpen) {
      panel.classList.add("open");
      connect();
    }
  }

  window.AISupportWidget = { init };
  if (currentScript && currentScript.dataset.autoload !== "false") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", () => init());
    } else {
      init();
    }
  }
})();
