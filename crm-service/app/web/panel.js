const els = (id) => document.getElementById(id);
let token = localStorage.getItem("crm_token") || "";
let pollTimer = null;

// ---------- HTTP ----------

async function apiRaw(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (token) headers["Authorization"] = "Bearer " + token;
    const res = await fetch(path, { ...options, headers });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && path !== "/api/auth/login") {
        logout();
        throw new Error("Сессия истекла, войдите заново");
    }
    if (!res.ok || data.ok === false) throw new Error(data.error || "Ошибка запроса");
    return data;
}

// ---------- Вход ----------

function showLogin() {
    els("login-screen").hidden = false;
    els("panel-screen").hidden = true;
}
function showPanel() {
    els("login-screen").hidden = true;
    els("panel-screen").hidden = false;
    restoreBroadcast();
}

// Восстанавливает окно статуса последней рассылки после перезагрузки страницы
function restoreBroadcast() {
    const id = localStorage.getItem("crm_last_broadcast");
    if (!id) return;
    els("bcast-id").textContent = id;
    els("status-card").hidden = false;
    startPolling(id);
}

async function doLogin(e) {
    e.preventDefault();
    els("login-error").hidden = true;
    try {
        const data = await apiRaw("/api/auth/login", {
            method: "POST",
            body: JSON.stringify({
                email: els("login-email").value.trim(),
                password: els("login-password").value,
            }),
        });
        token = data.token;
        localStorage.setItem("crm_token", token);
        showPanel();
    } catch (err) {
        els("login-error").textContent = err.message;
        els("login-error").hidden = false;
    }
}

function logout() {
    token = "";
    localStorage.removeItem("crm_token");
    localStorage.removeItem("crm_last_broadcast");
    if (pollTimer) clearInterval(pollTimer);
    els("status-card").hidden = true;
    selectedIds = [];
    renderChips();
    hideSuggest();
    blocks = [{ type: "title", text: "" }];
    renderBlocks();
    showLogin();
}

// ---------- Фильтры ----------

let selectedIds = [];      // выбранные Telegram ID (числа)
const nickById = {};       // id -> ник (для отображения)

function collectFilters() {
    const f = {};
    if (selectedIds.length) f.tg_ids = selectedIds.slice();
    return f;
}

// ---------- Мультивыбор получателей (Telegram ID + ник) ----------

function addRecipient(id, nick) {
    id = Number(id);
    if (!Number.isFinite(id)) return;
    if (nick) nickById[id] = nick;
    if (selectedIds.indexOf(id) === -1) selectedIds.push(id);
    renderChips();
}

function chipLabel(id) {
    const nick = nickById[id];
    return nick ? id + " · " + nick : String(id);
}

function renderChips() {
    const box = els("medid-chips");
    box.innerHTML = "";
    selectedIds.forEach((id) => {
        const chip = document.createElement("span");
        chip.className = "chip";
        chip.textContent = chipLabel(id);
        const x = document.createElement("button");
        x.type = "button";
        x.textContent = "✕";
        x.addEventListener("click", () => {
            selectedIds = selectedIds.filter((e) => e !== id);
            renderChips();
        });
        chip.appendChild(x);
        box.appendChild(chip);
    });
}

function hideSuggest() {
    els("medid-suggest").hidden = true;
    els("medid-suggest").innerHTML = "";
}

function renderSuggest(users) {
    const box = els("medid-suggest");
    box.innerHTML = "";
    const list = (users || []).filter((u) => selectedIds.indexOf(Number(u.id)) === -1);
    if (!list.length) { hideSuggest(); return; }
    list.forEach((u) => {
        const d = document.createElement("div");
        d.textContent = u.id + " · " + u.nick;
        d.addEventListener("mousedown", (ev) => {
            ev.preventDefault();
            addRecipient(u.id, u.nick);
            const q = els("medid-input").value.trim();
            if (q) searchUsers(q); else hideSuggest();
            els("medid-input").focus();
        });
        box.appendChild(d);
    });
    box.hidden = false;
}

async function searchUsers(q) {
    try {
        const headers = {};
        if (token) headers["Authorization"] = "Bearer " + token;
        const res = await fetch("/api/segments/users?q=" + encodeURIComponent(q), { headers });
        if (res.status === 401) { logout(); return; }
        const data = await res.json().catch(() => ({}));
        renderSuggest(data.users);
    } catch (e) {
        hideSuggest();
    }
}

// ---------- Модалка: список всех пользователей ----------

async function openUserList() {
    els("medid-list").innerHTML = '<div class="empty">Загрузка…</div>';
    els("medid-modal").hidden = false;
    try {
        const headers = {};
        if (token) headers["Authorization"] = "Bearer " + token;
        const res = await fetch("/api/segments/users", { headers });
        if (res.status === 401) { logout(); return; }
        const data = await res.json().catch(() => ({}));
        renderUserList(data.users || []);
    } catch (e) {
        els("medid-list").innerHTML = '<div class="empty">Не удалось загрузить</div>';
    }
}

function renderUserList(users) {
    const listEl = els("medid-list");
    listEl.innerHTML = "";
    if (!users.length) {
        listEl.innerHTML = '<div class="empty">Пока нет зарегистрированных</div>';
        return;
    }
    users.forEach((u) => {
        const row = document.createElement("div");
        row.className = "medid-item";
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = u.id;
        cb.dataset.nick = u.nick || "";
        cb.checked = selectedIds.indexOf(Number(u.id)) !== -1;
        // Клик по тексту — карточка со всей информацией (чекбокс остаётся для выбора)
        const info = document.createElement("button");
        info.type = "button";
        info.className = "user-pick";
        info.textContent = u.id + " · " + u.nick;
        info.title = "Показать всю информацию";
        info.addEventListener("click", () => openUserDetails(u.id));
        row.appendChild(cb);
        row.appendChild(info);
        listEl.appendChild(row);
    });
}

// ---------- Карточка пользователя (вся информация) ----------

function fmtDateTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function detailRows(u) {
    return [
        ["MAX ID", u.telegram_id],
        ["Имя", u.full_name || "—"],
        ["Ник", u.username ? "@" + u.username : "—"],
        ["MedinternetID", u.med_id != null ? u.med_id : "—"],
        ["Дата регистрации", fmtDateTime(u.created_at)],
        ["Последнее действие в боте", fmtDateTime(u.last_bot_action_at)],
        ["Последний запрос в поисковике", fmtDateTime(u.last_search_at)],
        ["Заблокировал бота", u.blocked ? "Да" : "Нет"],
    ];
}

function renderUserDetails(u) {
    const box = els("user-detail");
    box.innerHTML = "";
    detailRows(u).forEach(([label, value]) => {
        const row = document.createElement("div");
        row.className = "detail-row";
        const l = document.createElement("span");
        l.textContent = label;
        const v = document.createElement("b");
        v.textContent = value;
        row.appendChild(l);
        row.appendChild(v);
        box.appendChild(row);
    });
}

async function openUserDetails(id) {
    els("user-detail").innerHTML = '<div class="empty">Загрузка…</div>';
    els("user-modal").hidden = false;
    try {
        const headers = {};
        if (token) headers["Authorization"] = "Bearer " + token;
        const res = await fetch("/api/segments/users/" + encodeURIComponent(id), { headers });
        if (res.status === 401) { logout(); return; }
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.ok === false) throw new Error(data.error || "Ошибка");
        renderUserDetails(data.user);
    } catch (e) {
        els("user-detail").innerHTML = '<div class="empty">Не удалось загрузить</div>';
    }
}

function applyUserSelection() {
    els("medid-list").querySelectorAll("input:checked").forEach((cb) => addRecipient(cb.value, cb.dataset.nick));
    els("medid-modal").hidden = true;
}

function setAllChecks(checked) {
    els("medid-list").querySelectorAll("input[type=checkbox]").forEach((cb) => { cb.checked = checked; });
}

// ---------- Конструктор сообщения ----------

let blocks = [{ type: "title", text: "" }];
const BLOCK_LABELS = { title: "Заголовок", subtitle: "Подзаголовок", text: "Текст", link: "Ссылка" };

function renderBlocks() {
    const ed = els("editor");
    ed.innerHTML = "";
    blocks.forEach((b, i) => ed.appendChild(renderBlock(b, i)));
}

function iconBtn(sym, fn) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "icon-btn";
    btn.textContent = sym;
    btn.addEventListener("click", fn);
    return btn;
}

function renderBlock(b, i) {
    const wrap = document.createElement("div");
    wrap.className = "block b-" + b.type;

    const head = document.createElement("div");
    head.className = "block-head";
    const lab = document.createElement("span");
    lab.className = "block-label";
    lab.textContent = BLOCK_LABELS[b.type] || "";
    head.appendChild(lab);
    if (b.type !== "title") {
        const ctr = document.createElement("div");
        ctr.className = "block-ctrls";
        ctr.appendChild(iconBtn("↑", () => moveBlock(i, -1)));
        ctr.appendChild(iconBtn("↓", () => moveBlock(i, 1)));
        ctr.appendChild(iconBtn("✕", () => removeBlock(i)));
        head.appendChild(ctr);
    }
    wrap.appendChild(head);

    if (b.type === "link") {
        const t = document.createElement("input");
        t.type = "text"; t.className = "blk-input"; t.placeholder = "Текст ссылки";
        t.value = b.text || "";
        t.addEventListener("input", () => { blocks[i].text = t.value; });
        const u = document.createElement("input");
        u.type = "url"; u.className = "blk-input"; u.placeholder = "https://…";
        u.value = b.url || "";
        u.addEventListener("input", () => { blocks[i].url = u.value; });
        wrap.appendChild(t);
        wrap.appendChild(u);
    } else if (b.type === "text") {
        const ta = document.createElement("textarea");
        ta.className = "blk-input"; ta.rows = 3; ta.placeholder = "Текст…";
        ta.value = b.text || "";
        ta.addEventListener("input", () => { blocks[i].text = ta.value; });
        wrap.appendChild(ta);
    } else {
        const inp = document.createElement("input");
        inp.type = "text";
        inp.className = "blk-input blk-" + b.type;
        inp.placeholder = b.type === "title" ? "Заголовок" : "Подзаголовок";
        inp.value = b.text || "";
        inp.addEventListener("input", () => { blocks[i].text = inp.value; });
        wrap.appendChild(inp);
    }
    return wrap;
}

function addBlock(type) {
    blocks.push(type === "link" ? { type, text: "", url: "" } : { type, text: "" });
    renderBlocks();
}

function removeBlock(i) {
    if (blocks[i] && blocks[i].type === "title") return; // заголовок не удаляем
    blocks.splice(i, 1);
    renderBlocks();
}

function moveBlock(i, dir) {
    const j = i + dir;
    if (j < 1 || j >= blocks.length) return; // заголовок всегда первый
    const tmp = blocks[i];
    blocks[i] = blocks[j];
    blocks[j] = tmp;
    renderBlocks();
}

async function doPreview() {
    const btn = els("preview-btn");
    btn.disabled = true;
    try {
        const data = await apiRaw("/api/segments/preview", {
            method: "POST",
            body: JSON.stringify({ filters: collectFilters() }),
        });
        els("preview-result").innerHTML =
            "Под фильтр попадает: <strong>" + data.count + "</strong> чел.";
        els("preview-result").hidden = false;
    } catch (err) {
        els("preview-result").innerHTML = '<span style="color:var(--danger)">' + err.message + "</span>";
        els("preview-result").hidden = false;
    } finally {
        btn.disabled = false;
    }
}

// ---------- Рассылка ----------

function showSendError(msg) {
    els("send-error").textContent = msg;
    els("send-error").hidden = false;
}

async function doSend() {
    els("send-error").hidden = true;
    const file = els("attachment").files[0];
    const hasContent = blocks.some((b) => (b.text && b.text.trim()) || (b.url && b.url.trim()));
    if (!hasContent && !file) {
        showSendError("Добавьте текст или прикрепите файл");
        return;
    }
    if (!confirm("Отправить рассылку выбранному сегменту?")) return;

    // multipart/form-data: блоки конструктора + фильтры + необязательный файл
    const fd = new FormData();
    fd.append("filters", JSON.stringify(collectFilters()));
    fd.append("blocks", JSON.stringify(blocks));
    if (file) fd.append("file", file);

    const btn = els("send-btn");
    btn.disabled = true;
    try {
        const headers = {};
        if (token) headers["Authorization"] = "Bearer " + token;
        const res = await fetch("/api/broadcast", { method: "POST", headers, body: fd });
        const data = await res.json().catch(() => ({}));
        if (res.status === 401) { logout(); throw new Error("Сессия истекла, войдите заново"); }
        if (!res.ok || data.ok === false) throw new Error(data.error || "Ошибка запроса");

        els("bcast-id").textContent = data.broadcast_id;
        els("status-card").hidden = false;
        localStorage.setItem("crm_last_broadcast", data.broadcast_id);
        startPolling(data.broadcast_id);
    } catch (err) {
        showSendError(err.message);
    } finally {
        btn.disabled = false;
    }
}

function startPolling(id) {
    if (pollTimer) clearInterval(pollTimer);
    const tick = async () => {
        try {
            const s = await apiRaw("/api/broadcast/" + id + "/status");
            els("s-sent").textContent = s.sent;
            els("s-pending").textContent = s.pending;
            els("s-blocked").textContent = s.blocked;
            els("s-failed").textContent = s.failed;
            els("s-total").textContent = s.total;
            if (s.pending === 0) clearInterval(pollTimer);
        } catch (err) {
            clearInterval(pollTimer);
        }
    };
    tick();
    pollTimer = setInterval(tick, 2000);
}

// ---------- Старт ----------

els("login-form").addEventListener("submit", doLogin);
els("logout").addEventListener("click", logout);
els("preview-btn").addEventListener("click", doPreview);
els("send-btn").addEventListener("click", doSend);

// Прикрепление файла
els("attachment").addEventListener("change", () => {
    const f = els("attachment").files[0];
    els("file-name").textContent = f ? f.name : "";
    els("file-clear").hidden = !f;
});
els("file-clear").addEventListener("click", () => {
    els("attachment").value = "";
    els("file-name").textContent = "";
    els("file-clear").hidden = true;
});

// Автодополнение получателей (по Telegram ID или нику)
let medidTimer = null;
els("medid-input").addEventListener("input", () => {
    const q = els("medid-input").value.trim();
    clearTimeout(medidTimer);
    if (q.length < 1) { hideSuggest(); return; }
    medidTimer = setTimeout(() => searchUsers(q), 250);
});
els("medid-input").addEventListener("blur", () => setTimeout(hideSuggest, 150));
els("medid-input").addEventListener("focus", () => {
    const q = els("medid-input").value.trim();
    if (q) searchUsers(q);
});
// Enter в поле — добавить введённый Telegram ID (если это число)
els("medid-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
        e.preventDefault();
        const v = els("medid-input").value.trim();
        if (v && /^\d+$/.test(v)) { addRecipient(v); els("medid-input").value = ""; hideSuggest(); }
    }
});

// Модалка со списком всех пользователей
els("medid-list-btn").addEventListener("click", openUserList);
els("medid-modal-close").addEventListener("click", () => { els("medid-modal").hidden = true; });
els("medid-modal").addEventListener("click", (e) => {
    if (e.target === els("medid-modal")) els("medid-modal").hidden = true; // клик по фону
});
els("medid-select-all").addEventListener("click", () => setAllChecks(true));
els("medid-clear-all").addEventListener("click", () => setAllChecks(false));
els("medid-add-selected").addEventListener("click", applyUserSelection);

// Карточка пользователя
els("user-modal-close").addEventListener("click", () => { els("user-modal").hidden = true; });
els("user-modal").addEventListener("click", (e) => {
    if (e.target === els("user-modal")) els("user-modal").hidden = true; // клик по фону
});

// Конструктор: меню «Добавить блок»
els("add-block-btn").addEventListener("click", () => {
    els("block-menu").hidden = !els("block-menu").hidden;
});
document.querySelectorAll("#block-menu [data-add]").forEach((btn) => {
    btn.addEventListener("click", () => {
        addBlock(btn.dataset.add);
        els("block-menu").hidden = true;
    });
});

renderBlocks();

if (token) showPanel(); else showLogin();
