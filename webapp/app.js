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
    historyBtn: document.getElementById("history-btn"),
    historyPanel: document.getElementById("history-panel"),
    historyList: document.getElementById("history-list"),
    historyClear: document.getElementById("history-clear"),
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
    addHistory(text);
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

    let finished = false;
    function finish() {
        if (finished) return;
        finished = true;
        sending = false;
        els.chatSend.disabled = false;
        if (bubble && gotText) addAnswerActions(bubble, text);   // копировать / оценка
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

// ---------- Действия под ответом ИИ: копировать / лайк / дизлайк ----------

const ICONS = {
    copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
    like: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10v11"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/></svg>',
    dislike: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 14V3"/><path d="M9 5.88 10 10H4.17a2 2 0 0 0-1.92 2.56l2.33 8A2 2 0 0 0 6.5 22H20a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-2.76a2 2 0 0 1-1.79-1.11L12 2"/></svg>',
};

function actBtn(kind, svg, title) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "answer-act " + kind;
    b.title = title;
    b.setAttribute("aria-label", title);
    b.innerHTML = svg;
    return b;
}

function answerText(bubble) {
    return Array.from(bubble.querySelectorAll(".ai-line"))
        .map((el) => el.innerText).join("\n").trim();
}

async function copyAnswer(bubble, btn) {
    const txt = answerText(bubble);
    try {
        await navigator.clipboard.writeText(txt);
    } catch (e) {
        const ta = document.createElement("textarea");
        ta.value = txt; ta.style.position = "fixed"; ta.style.opacity = "0";
        document.body.appendChild(ta); ta.focus(); ta.select();
        try { document.execCommand("copy"); } catch (e2) { /* игнор */ }
        ta.remove();
    }
    btn.classList.add("copied");
    waHaptic("light");
    setTimeout(() => btn.classList.remove("copied"), 1200);
}

function rate(question, value, btn, otherBtn) {
    const wasActive = btn.classList.contains("active");
    otherBtn.classList.remove("active");
    if (wasActive) { btn.classList.remove("active"); return; }  // повторный клик — снять
    btn.classList.add("active");
    waHaptic("light");
    api("/api/ai/feedback", { message: question, rating: value }).catch(() => { /* не критично */ });
}

function addAnswerActions(bubble, question) {
    if (bubble.querySelector(".answer-actions")) return;
    const bar = document.createElement("div");
    bar.className = "answer-actions";
    const copyBtn = actBtn("copy", ICONS.copy, "Скопировать ответ");
    copyBtn.addEventListener("click", () => copyAnswer(bubble, copyBtn));
    const likeBtn = actBtn("like", ICONS.like, "Полезный ответ");
    const dislikeBtn = actBtn("dislike", ICONS.dislike, "Ответ не помог");
    likeBtn.addEventListener("click", () => rate(question, "like", likeBtn, dislikeBtn));
    dislikeBtn.addEventListener("click", () => rate(question, "dislike", dislikeBtn, likeBtn));
    bar.append(copyBtn, likeBtn, dislikeBtn);
    bubble.appendChild(bar);
    scrollToBottom();
}

// ---------- История запросов (в localStorage) ----------

const HISTORY_KEY = "mi_history";
const HISTORY_MAX = 30;

function loadHistory() {
    try { return JSON.parse(localStorage.getItem(HISTORY_KEY)) || []; }
    catch (e) { return []; }
}
function saveHistory(arr) {
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(arr)); } catch (e) { /* игнор */ }
}
function addHistory(q) {
    q = (q || "").trim();
    if (!q) return;
    let arr = loadHistory().filter((x) => x !== q);
    arr.unshift(q);
    if (arr.length > HISTORY_MAX) arr = arr.slice(0, HISTORY_MAX);
    saveHistory(arr);
}
function renderHistory() {
    const list = els.historyList;
    const arr = loadHistory();
    list.innerHTML = "";
    if (!arr.length) {
        const empty = document.createElement("div");
        empty.className = "history-empty";
        empty.textContent = "Пока нет запросов";
        list.appendChild(empty);
        return;
    }
    arr.forEach((q) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "history-item";
        item.textContent = q;
        item.title = q;
        item.addEventListener("click", () => {
            els.chatInput.value = q;
            els.chatInput.focus();
            const len = els.chatInput.value.length;
            els.chatInput.setSelectionRange(len, len);
            autoGrow();
            hideHistory();
        });
        list.appendChild(item);
    });
}
function toggleHistory() {
    if (els.historyPanel.hidden) { renderHistory(); els.historyPanel.hidden = false; }
    else els.historyPanel.hidden = true;
}
function hideHistory() { els.historyPanel.hidden = true; }
function clearHistory() {
    saveHistory([]);
    renderHistory();
    waHaptic("light");
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

// История запросов
els.historyBtn.addEventListener("click", (e) => { e.stopPropagation(); toggleHistory(); });
els.historyClear.addEventListener("click", clearHistory);
document.addEventListener("click", (e) => {
    if (els.historyPanel.hidden) return;
    if (els.historyPanel.contains(e.target) || els.historyBtn.contains(e.target)) return;
    hideHistory();   // клик вне панели — закрыть
});

document.getElementById("upgrade-btn").addEventListener("click", () => comingSoon("Тариф «Плюс»"));
document.getElementById("logout-btn").addEventListener("click", logout);

// Модалка «Как пользоваться»
const howtoModal = document.getElementById("howto-modal");
document.getElementById("howto-btn").addEventListener("click", () => { howtoModal.hidden = false; });
document.getElementById("howto-close").addEventListener("click", () => { howtoModal.hidden = true; });
howtoModal.addEventListener("click", (e) => { if (e.target === howtoModal) howtoModal.hidden = true; });

init();
