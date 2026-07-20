// MAX WebApp SDK (MAX Bridge): глобальный объект window.WebApp.
// По возможности сохраняем ту же логику, что и в Telegram-версии; методы,
// которых может не быть в MAX (showAlert/showConfirm/colorScheme), вызываем
// защищённо с браузерным фолбэком.
const wa = window.WebApp;
const initData = wa?.initData || "";

const T = {
    searcher: "Медицинский поисковик",
    submit: "Зарегистрироваться",
    submitting: "Отправка…",
    greet: "Здравствуйте! Задайте медицинский вопрос — я постараюсь помочь.",
    aiUnavailable: "Чат временно недоступен.",
    errGeneric: "Что-то пошло не так. Попробуйте позже.",
    sources: "Источники:",
    emptyAnswer: "Пустой ответ.",
};

const els = {
    loading: document.getElementById("loading"),
    register: document.getElementById("screen-register"),
    app: document.getElementById("app"),
    siteLink: document.getElementById("site-link"),
    messages: document.getElementById("messages"),
    chatForm: document.getElementById("chat-form"),
    chatInput: document.getElementById("chat-input"),
    chatSend: document.getElementById("chat-send"),
    chatReset: document.getElementById("chat-reset"),
};

let state = { registered: false, aiEnabled: false, user: null, screen: "loading", tab: "search" };

// ---------- Обёртки над MAX WebApp (с фолбэками) ----------

function waColorScheme() {
    if (wa?.colorScheme) return wa.colorScheme;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark" : "light";
}

function waOpenLink(url) {
    if (wa?.openLink) wa.openLink(url); else window.open(url, "_blank");
}

function waClose() { try { wa?.close?.(); } catch (e) { /* игнор */ } }

function waAlert(msg) {
    if (wa?.showAlert) wa.showAlert(msg); else alert(msg);
}

function waConfirm(msg, cb) {
    if (wa?.showConfirm) wa.showConfirm(msg, cb);
    else cb(confirm(msg));
}

function waHaptic(kind) {
    try { wa?.HapticFeedback?.impactOccurred?.(kind); } catch (e) { /* игнор */ }
}

// ---------- Общее ----------

async function api(path, extra = {}) {
    const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initData, ...extra }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) throw new Error(data.error || T.errGeneric);
    return data;
}

// ---------- Тема ----------

function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("mi_theme", theme); } catch (e) { /* игнор */ }
    const icon = theme === "dark" ? "☀️" : "🌙";
    document.querySelectorAll(".theme-toggle").forEach((b) => { b.textContent = icon; });
}

function initTheme() {
    let theme;
    try { theme = localStorage.getItem("mi_theme"); } catch (e) { theme = null; }
    if (!theme) theme = waColorScheme() === "dark" ? "dark" : "light";
    applyTheme(theme);
}

function toggleTheme() {
    const cur = document.documentElement.getAttribute("data-theme");
    applyTheme(cur === "dark" ? "light" : "dark");
}

// ---------- Экраны и вкладки ----------

function showScreen(name) {
    state.screen = name;
    els.loading.hidden = name !== "loading";
    els.register.hidden = name !== "register";
    els.app.hidden = name !== "app";
    wa?.BackButton?.hide?.();
}

function switchTab(name) {
    state.tab = name;
    document.getElementById("tab-search").hidden = name !== "search";
    document.getElementById("tab-profile").hidden = name !== "profile";
    document.getElementById("tab-info").hidden = name !== "info";
    document.querySelectorAll(".nav-btn").forEach((b) => {
        b.classList.toggle("active", b.dataset.tab === name);
    });
    if (name === "search" && els.messages.childElementCount === 0) greetChat();
    if (name === "profile") renderProfile();
}

function openApp() {
    showScreen("app");
    switchTab("search");
}

// ---------- Инициализация ----------

async function init() {
    if (wa) { wa.ready?.(); wa.expand?.(); }
    initTheme();
    try {
        const me = await api("/api/me");
        state.registered = !!me.registered;
        state.aiEnabled = !!me.ai_enabled;
        state.user = me.user || null;
    } catch (e) { /* дефолт: не зарегистрирован */ }
    if (state.registered) openApp(); else showScreen("register");
}

// ---------- Регистрация: переход на сайт ----------

let pendingReg = false;   // ушли на страницу /link для регистрации

function openSite() {
    pendingReg = true;
    const url = window.location.origin + "/link";
    waOpenLink(url);
}

// После ухода на страницу регистрации мини-апп больше не нужен: как только он
// уходит в фон (открылся браузер/чат) — закрываем его. Пользователь откроет заново.
function closeAfterSiteVisit() {
    if (!pendingReg) return;
    pendingReg = false;
    waClose();
}

document.addEventListener("visibilitychange", () => {
    if (document.hidden) closeAfterSiteVisit();
});
window.addEventListener("blur", closeAfterSiteVisit);

// ---------- Личный кабинет ----------

function maxDisplayName() {
    const u = wa?.initDataUnsafe?.user;
    const name = u ? [u.first_name, u.last_name].filter(Boolean).join(" ") : "";
    return name || "Пользователь";
}

function renderProfile() {
    const u = state.user || {};
    const name = maxDisplayName();
    document.getElementById("pf-name").textContent = name;
    document.getElementById("pf-initial").textContent = (name.trim()[0] || "?");
    document.getElementById("pf-fio").textContent = u.full_name || "—";
    document.getElementById("pf-specialty").textContent = u.specialty || "—";
    document.getElementById("pf-position").textContent = u.position || "—";
    const tariff = u.tariff || "Обычный";
    document.getElementById("pf-tariff").textContent = tariff;
    document.getElementById("pf-tariff-name").textContent = tariff;
}

// ---------- Чат ----------

function greetChat() {
    if (els.messages.childElementCount > 0) return;
    addBubble("ai", state.aiEnabled === false ? T.aiUnavailable : T.greet);
}

function addBubble(kind, text) {
    const el = document.createElement("div");
    el.className = "bubble " + kind;
    el.textContent = text;
    els.messages.appendChild(el);
    scrollToBottom();
    return el;
}

function addTyping() {
    const el = document.createElement("div");
    el.className = "bubble ai";
    el.innerHTML = '<span class="typing"><span></span><span></span><span></span></span>';
    els.messages.appendChild(el);
    scrollToBottom();
    return el;
}

function scrollToBottom() { els.messages.scrollTop = els.messages.scrollHeight; }

// ---------- Лёгкий рендер markdown (для потокового ответа) ----------

function escapeHtml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function mdToHtml(md) {
    let s = escapeHtml(md);
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
    const lines = s.split("\n");
    let out = "", inList = false;
    const closeList = () => { if (inList) { out += "</ul>"; inList = false; } };
    for (const line of lines) {
        const h = line.match(/^(#{1,6})\s+(.*)$/);
        const li = line.match(/^\s*[-•]\s+(.*)$/);
        if (h) { closeList(); out += '<div class="md-h">' + h[2] + "</div>"; }
        else if (li) { if (!inList) { out += "<ul>"; inList = true; } out += "<li>" + li[1] + "</li>"; }
        else if (line.trim()) { closeList(); out += "<div>" + line + "</div>"; }
        else { closeList(); }
    }
    closeList();
    return out;
}

function setStatus(el, text) {
    el.innerHTML = '<span class="status-line"></span> '
        + '<span class="typing"><span></span><span></span><span></span></span>';
    el.querySelector(".status-line").textContent = text;
}

let sending = false;
async function sendChat() {
    const text = els.chatInput.value.trim();
    if (!text || sending) return;
    sending = true;
    els.chatSend.disabled = true;
    els.chatInput.value = "";
    autoGrow();
    addBubble("user", text);
    const typing = addTyping();

    let bubble = null;       // пузырь ответа (создаётся при первом тексте)
    let pending = "";        // незавершённая строка (копится, пока не придёт \n)
    const lineQueue = [];    // готовые строки, ждущие плавного появления
    let gotText = false;
    let streamDone = false;
    let animating = false;

    function ensureBubble() {
        if (!bubble) {
            typing.remove();
            bubble = addBubble("ai", "");
            bubble.style.whiteSpace = "normal";
        }
    }

    function finish() {
        sending = false;
        els.chatSend.disabled = false;
    }

    function enqueue(chunk) {
        pending += chunk;
        let i;
        while ((i = pending.indexOf("\n")) !== -1) {
            const ln = pending.slice(0, i);
            pending = pending.slice(i + 1);
            if (ln.trim()) lineQueue.push(ln);
        }
        pump();
    }

    function pump() {
        if (animating) return;
        if (!lineQueue.length) {
            if (streamDone) finish();
            return;
        }
        animating = true;
        ensureBubble();
        const ln = lineQueue.shift();
        const el = document.createElement("div");
        el.className = "ai-line";
        el.innerHTML = mdToHtml(ln);
        bubble.appendChild(el);
        scrollToBottom();
        const delay = Math.max(80, 300 - lineQueue.length * 24);
        setTimeout(() => { animating = false; pump(); }, delay);
    }

    try {
        const res = await fetch("/api/ai/message/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ initData, message: text }),
        });
        if (!res.ok || !res.body) {
            const d = await res.json().catch(() => ({}));
            throw new Error(d.error || T.errGeneric);
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            let i;
            while ((i = buf.indexOf("\n\n")) !== -1) {
                const evt = buf.slice(0, i).trim();
                buf = buf.slice(i + 2);
                if (!evt.startsWith("data:")) continue;
                let obj;
                try { obj = JSON.parse(evt.slice(5).trim()); } catch (e) { continue; }
                if (obj.kind === "action" && !bubble) {
                    setStatus(typing, obj.value);
                    scrollToBottom();
                } else if (obj.kind === "text") {
                    gotText = true;
                    enqueue(obj.value);
                } else if (obj.kind === "error") {
                    if (!bubble) typing.remove();
                    addBubble("error", obj.value);
                }
            }
        }
    } catch (e) {
        typing.remove();
        if (!gotText) addBubble("error", e.message || T.errGeneric);
    } finally {
        streamDone = true;
        if (gotText) {
            if (pending.trim()) lineQueue.push(pending);   // остаток — последняя строка
            pending = "";
            pump();
        } else {
            typing.remove();
            finish();
        }
    }
}

async function resetChat() {
    if (sending) return;
    try { await api("/api/ai/reset"); } catch (e) { /* не критично */ }
    els.messages.innerHTML = "";
    greetChat();
    waHaptic("light");
}

function autoGrow() {
    const box = els.chatInput;
    box.style.height = "auto";
    box.style.height = Math.min(box.scrollHeight, 120) + "px";
}

// ---------- Информация ----------

function comingSoon(what) {
    waAlert(what + " — скоро будет доступно.");
}

function logout() {
    waConfirm("Выйти из аккаунта?", async (ok) => {
        if (!ok) return;
        // Деактивируем аккаунт на сервере (данные сохраняются) — бот вернёт
        // исходное сообщение с предложением зарегистрироваться заново.
        try { await api("/api/logout"); } catch (e) { /* всё равно закрываем */ }
        waClose();
    });
}

// ---------- События ----------

els.siteLink.addEventListener("click", (e) => { e.preventDefault(); openSite(); });

els.chatForm.addEventListener("submit", (e) => { e.preventDefault(); sendChat(); });
els.chatReset.addEventListener("click", resetChat);
els.chatInput.addEventListener("input", autoGrow);
els.chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
});
document.querySelectorAll(".theme-toggle").forEach((b) => b.addEventListener("click", toggleTheme));

document.querySelectorAll(".nav-btn").forEach((b) => {
    b.addEventListener("click", () => switchTab(b.dataset.tab));
});

// Чипсы-подсказки: вставляют начало запроса в поле ввода (без троеточия)
document.querySelectorAll(".prompt-chip").forEach((b) => {
    b.addEventListener("click", () => {
        const box = els.chatInput;
        box.value = b.dataset.prompt;
        box.focus();
        const len = box.value.length;
        box.setSelectionRange(len, len);   // курсор в конец
        autoGrow();
    });
});

document.getElementById("upgrade-btn").addEventListener("click", () => comingSoon("Тариф «Плюс»"));
document.getElementById("logout-btn").addEventListener("click", logout);

// Модалка «Как пользоваться»
const howtoModal = document.getElementById("howto-modal");
document.getElementById("howto-btn").addEventListener("click", () => { howtoModal.hidden = false; });
document.getElementById("howto-close").addEventListener("click", () => { howtoModal.hidden = true; });
howtoModal.addEventListener("click", (e) => { if (e.target === howtoModal) howtoModal.hidden = true; });

init();
