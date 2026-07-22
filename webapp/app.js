// MAX WebApp SDK (MAX Bridge): глобальный объект window.WebApp.
const wa = window.WebApp;
const initData = wa?.initData || "";

const T = {
    greet: "Здравствуйте! Задайте медицинский вопрос — я постараюсь помочь.",
    aiUnavailable: "Чат временно недоступен.",
    errGeneric: "Что-то пошло не так. Попробуйте позже.",
};

const els = {
    loading: document.getElementById("loading"),
    register: document.getElementById("screen-register"),
    app: document.getElementById("app"),
    registerBtn: document.getElementById("register-btn"),
    // под-экраны
    screenSearch: document.getElementById("screen-search"),
    screenHistory: document.getElementById("screen-history"),
    screenChat: document.getElementById("screen-chat"),
    // поиск
    messages: document.getElementById("messages"),
    chatForm: document.getElementById("chat-form"),
    chatInput: document.getElementById("chat-input"),
    chatSend: document.getElementById("chat-send"),
    chatReset: document.getElementById("chat-reset"),
    promptChips: document.querySelector(".prompt-chips"),
    // история
    historyBtn: document.getElementById("history-btn"),
    historyBack: document.getElementById("history-back"),
    historyChats: document.getElementById("history-chats"),
    // просмотр чата
    chatviewBack: document.getElementById("chatview-back"),
    chatviewTitle: document.getElementById("chatview-title"),
    chatviewMessages: document.getElementById("chatview-messages"),
};

let state = { registered: false, aiEnabled: false, screen: "loading", sub: "search" };

// ---------- Обёртки над MAX WebApp ----------

function waColorScheme() {
    if (wa?.colorScheme) return wa.colorScheme;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark" : "light";
}
function waOpenLink(url) { if (wa?.openLink) wa.openLink(url); else window.open(url, "_blank"); }
function waClose() { try { wa?.close?.(); } catch (e) { /* игнор */ } }
function waHaptic(kind) { try { wa?.HapticFeedback?.impactOccurred?.(kind); } catch (e) { /* игнор */ } }

// ---------- HTTP ----------

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

// ---------- Экраны ----------

function showScreen(name) {
    state.screen = name;
    els.loading.hidden = name !== "loading";
    els.register.hidden = name !== "register";
    els.app.hidden = name !== "app";
}

// Под-экраны внутри приложения: поиск / история / просмотр чата
function showSub(name) {
    state.sub = name;
    els.screenSearch.hidden = name !== "search";
    els.screenHistory.hidden = name !== "history";
    els.screenChat.hidden = name !== "chat";
}

function openApp() {
    showScreen("app");
    showSub("search");
    greetChat();
}

// ---------- Инициализация ----------

async function init() {
    if (wa) { wa.ready?.(); wa.expand?.(); }
    initTheme();
    try {
        const me = await api("/api/me");
        state.registered = !!me.registered;
        state.aiEnabled = !!me.ai_enabled;
    } catch (e) { /* дефолт: не зарегистрирован */ }
    if (state.registered) openApp(); else showScreen("register");
}

// ---------- Регистрация: переход на сайт ----------

let pendingReg = false;
function openSite() {
    pendingReg = true;
    waOpenLink(window.location.origin + "/link");
}
function closeAfterSiteVisit() {
    if (!pendingReg) return;
    pendingReg = false;
    waClose();
}
document.addEventListener("visibilitychange", () => { if (document.hidden) closeAfterSiteVisit(); });
window.addEventListener("blur", closeAfterSiteVisit);

// ---------- Чат ----------

function greetChat() {
    if (els.messages.querySelector(".bubble")) return;   // уже есть сообщения
    const row = document.createElement("div");
    row.className = "greet-row";
    const img = document.createElement("img");
    img.className = "greet-avatar";
    img.src = "/robot.png"; img.alt = "";
    img.onerror = () => { img.style.display = "none"; };
    const bub = document.createElement("div");
    bub.className = "bubble ai";
    bub.textContent = state.aiEnabled === false ? T.aiUnavailable : T.greet;
    row.append(img, bub);
    addToMessages(row);
    scrollToBottom();
}

// Сообщения вставляем ПЕРЕД чипсами — чипсы остаются последним элементом в скролле.
function addToMessages(el) {
    if (els.promptChips && els.promptChips.parentNode === els.messages) {
        els.messages.insertBefore(el, els.promptChips);
    } else {
        els.messages.appendChild(el);
    }
}

function addBubble(kind, text) {
    const el = document.createElement("div");
    el.className = "bubble " + kind;
    el.textContent = text;
    addToMessages(el);
    scrollToBottom();
    return el;
}

function addTyping() {
    const el = document.createElement("div");
    el.className = "bubble ai";
    el.innerHTML = '<span class="typing"><span></span><span></span><span></span></span>';
    addToMessages(el);
    scrollToBottom();
    return el;
}

function scrollToBottom() { els.messages.scrollTop = els.messages.scrollHeight; }

// ---------- Лёгкий рендер markdown ----------

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

    let bubble = null;
    let pending = "";
    const lineQueue = [];
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
        if (bubble && gotText) addAnswerActions(bubble, text);
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
        if (!lineQueue.length) { if (streamDone) finish(); return; }
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
                } else if (obj.kind === "suggestions") {
                    if (Array.isArray(obj.value) && obj.value.length) {
                        renderChips(obj.value.map((q) => ({ label: q, insert: q })));
                    }
                }
            }
        }
    } catch (e) {
        typing.remove();
        if (!gotText) addBubble("error", e.message || T.errGeneric);
    } finally {
        streamDone = true;
        if (gotText) {
            if (pending.trim()) lineQueue.push(pending);
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
    els.messages.querySelectorAll(".bubble, .greet-row").forEach((b) => b.remove());
    renderStaticChips();   // новый чат — снова статичные подсказки
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
    return Array.from(bubble.querySelectorAll(".ai-line")).map((el) => el.innerText).join("\n").trim();
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
    if (wasActive) { btn.classList.remove("active"); return; }
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
    const dislikeBtn = actBtn("dislike", ICONS.like, "Ответ не помог");  // перевёрнутый лайк (CSS)
    likeBtn.addEventListener("click", () => rate(question, "like", likeBtn, dislikeBtn));
    dislikeBtn.addEventListener("click", () => rate(question, "dislike", dislikeBtn, likeBtn));
    bar.append(copyBtn, likeBtn, dislikeBtn);
    bubble.appendChild(bar);
    scrollToBottom();
}

// ---------- Чипсы-подсказки ----------

const STATIC_CHIPS = [
    { label: "Клинические рекомендации по…", insert: "Клинические рекомендации по " },
    { label: "Инструкция по применению…", insert: "Инструкция по применению " },
    { label: "Схема применения…", insert: "Схема применения " },
];

function chipInsert(text) {
    const box = els.chatInput;
    box.value = text;
    box.focus();
    const len = box.value.length;
    box.setSelectionRange(len, len);
    autoGrow();
}

function renderChips(items) {
    const box = els.promptChips;
    if (!box) return;
    box.innerHTML = "";
    items.forEach((it) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "prompt-chip";
        b.textContent = it.label;
        b.title = it.label;
        b.addEventListener("click", () => chipInsert(it.insert));
        box.appendChild(b);
    });
    scrollToBottom();
}
function renderStaticChips() { renderChips(STATIC_CHIPS); }

// ---------- История: список чатов и просмотр переписки (серверная) ----------

async function openHistoryPage() {
    // История НЕ грузится при старте — запрос только сейчас, по нажатию кнопки.
    showSub("history");
    els.historyChats.innerHTML = '<div class="history-empty">Загрузка…</div>';
    try {
        const data = await api("/api/chats");
        renderChatList(data.chats || []);
    } catch (e) {
        els.historyChats.innerHTML = '<div class="history-empty">Не удалось загрузить</div>';
    }
}

function renderChatList(chats) {
    const box = els.historyChats;
    box.innerHTML = "";
    if (!chats.length) {
        box.innerHTML = '<div class="history-empty">Пока нет чатов</div>';
        return;
    }
    chats.forEach((c) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "chat-item";
        item.textContent = c.title || "Без названия";
        item.title = c.title || "";
        item.addEventListener("click", () => openChatView(c));
        box.appendChild(item);
    });
}

async function openChatView(chat) {
    showSub("chat");
    els.chatviewTitle.textContent = chat.title || "Чат";
    els.chatviewMessages.innerHTML = '<div class="history-empty">Загрузка…</div>';
    try {
        const data = await api("/api/chat/messages", { chat_id: chat.id });
        renderChatMessages(data.messages || []);
    } catch (e) {
        els.chatviewMessages.innerHTML = '<div class="history-empty">Не удалось загрузить</div>';
    }
}

function renderChatMessages(messages) {
    const box = els.chatviewMessages;
    box.innerHTML = "";
    messages.forEach((m) => {
        const el = document.createElement("div");
        el.className = "bubble " + (m.role === "user" ? "user" : "ai");
        if (m.role === "user") el.textContent = m.content || "";
        else { el.style.whiteSpace = "normal"; el.innerHTML = mdToHtml(m.content || ""); }
        box.appendChild(el);
    });
    box.scrollTop = 0;
}

// ---------- События ----------

els.registerBtn.addEventListener("click", openSite);

els.chatForm.addEventListener("submit", (e) => { e.preventDefault(); sendChat(); });
els.chatReset.addEventListener("click", resetChat);
els.chatInput.addEventListener("input", autoGrow);
els.chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
});
document.querySelectorAll(".theme-toggle").forEach((b) => b.addEventListener("click", toggleTheme));

els.historyBtn.addEventListener("click", openHistoryPage);
els.historyBack.addEventListener("click", () => showSub("search"));
els.chatviewBack.addEventListener("click", () => showSub("history"));

renderStaticChips();   // стартовое состояние — статичные подсказки
init();
