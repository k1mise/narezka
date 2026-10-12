"use strict";
/* narez — веб-интерфейс. Без сборки: один файл, чистый JS. */
const $ = (s, r = document) => r.querySelector(s);
const ICONS = {
  panel: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>',
  editor: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.1 15.9M14.5 14.5 20 20M8.1 8.1 12 12"/></svg>',
  accounts: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="4"/><path d="M2 21c0-4 3-6 7-6s7 2 7 6M17 4a4 4 0 0 1 0 8M22 21c0-3-2-5-4-5.6"/></svg>',
  proxies: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18"/></svg>',
  history: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></svg>',
};
const PAGES = [["panel", "Панель"], ["editor", "Редактор"], ["accounts", "Аккаунты"], ["history", "История"], ["proxies", "Прокси"]];
const PLAT = {youtube: ["YT", "YouTube Shorts"], tiktok: ["TT", "TikTok"], instagram: ["IG", "Instagram Reels"]};

const S = {
  page: "panel", groups: [], gid: null, cfg: null, health: null, clips: [], cid: null, banners: [], history: null,
  tasks: [], log: {}, trackId: null, lastDone: new Set(), accGid: null,
};
try { S.gid = Number(localStorage.getItem("gid")) || null; } catch (e) {}

/* ---------- утилиты ---------- */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) el.append(k.nodeType ? k : document.createTextNode(k));
  return el;
}
async function api(method, url, body, raw) {
  const opt = {method, headers: {}};
  if (raw !== undefined) opt.body = raw;
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  let r;
  try { r = await fetch(url, opt); } catch (e) { throw new Error("Нет связи с программой. Она запущена?"); }
  let data = null;
  try { data = await r.json(); } catch (e) {}
  if (!r.ok) {
    let d = data && data.detail;
    if (Array.isArray(d)) d = d.map(x => x.msg).join("; ");
    throw new Error(d || `Ошибка ${r.status}`);
  }
  return data;
}
function toast(msg, kind = "ok") {
  const t = h("div", {class: "toast " + kind}, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "err" ? 9000 : 4500);
}
const fail = e => toast(e.message || String(e), "err");
function dialog(content, actions) {
  const d = $("#dlg"); d.replaceChildren(content, h("div", {class: "acts"}, actions.map(a =>
    h("button", {class: "btn " + (a.cls || ""), onclick: () => { d.close(); a.fn && a.fn(); }}, a.text))));
  d.showModal();
}
const confirmBox = (title, text, okText, fn, danger = true) =>
  dialog(h("div", {}, h("h3", {}, title), h("div", {class: "muted"}, text)),
    [{text: "Отмена", cls: "ghost"}, {text: okText, cls: danger ? "danger" : "primary", fn}]);
function ask(title, label, value, okText, fn, area = false) {
  const inp = area ? h("textarea", {}) : h("input", {type: "text"});
  inp.value = value || "";
  dialog(h("div", {}, h("h3", {}, title), h("div", {class: "muted small", style: {marginBottom: "10px"}}, label), inp),
    [{text: "Отмена", cls: "ghost"}, {text: okText, cls: "primary", fn: () => fn(inp.value)}]);
  setTimeout(() => inp.focus(), 50);
}
const fmtNum = n => n == null ? "—" : Number(n).toLocaleString("ru-RU");
function fmtLocal(iso) {
  if (!iso) return "";
  const d = new Date(iso); if (isNaN(d)) return "";
  return d.toLocaleString("ru-RU", {day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit"});
}
function isoToInput(iso) {
  const d = new Date(iso); if (!iso || isNaN(d)) return "";
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
const inputToIso = v => v ? new Date(v).toISOString() : "";
const plural = (n, a, b, c) => { const m = n % 100, k = n % 10; return (m > 10 && m < 15) ? c : k === 1 ? a : (k > 1 && k < 5) ? b : c; };
/* как показывать названия клипов: «ai» — придуманное ИИ, «source» — «название исходника — клип N» */
try { S.nameMode = localStorage.getItem("nameMode") || "ai"; } catch (e) { S.nameMode = "ai"; }
const clipLabel = c => S.nameMode === "source" ? `${c.source || "Без названия"} — клип ${c.number}` : (c.title || "Без названия");
function nameToggle(onChange) {
  const seg = h("div", {class: "seg"}, [["ai", "Название от ИИ"], ["source", "Исходник — клип №"]].map(([k, t]) =>
    h("button", {class: S.nameMode === k ? "on" : "", onclick: () => {
      S.nameMode = k; try { localStorage.setItem("nameMode", k); } catch (e) {}
      seg.querySelectorAll("button").forEach((b, i) => b.classList.toggle("on", ["ai", "source"][i] === k)); onChange(); }}, t)));
  return seg;
}
const group = () => S.groups.find(g => g.id === S.gid);
const clip = () => S.clips.find(c => c.id === S.cid);

/* ---------- каркас ---------- */
function renderNav() {
  const nav = $("#nav"); nav.replaceChildren();
  for (const [id, name] of PAGES) {
    const cnt = id === "editor" && S.clips.length ? h("span", {class: "cnt"}, S.clips.length) : null;
    nav.append(h("button", {class: S.page === id ? "on" : "", onclick: () => go(id)}, h("span", {html: ICONS[id]}), name, cnt));
  }
}
function renderHealth() {
  const H = S.health; if (!H) return;
  const chip = (ok, text, warn) => h("span", {class: "chip " + (ok ? "ok" : warn ? "warn" : "")}, h("i"), text);
  const items = [chip(H.ffmpeg && H.ffprobe, "ffmpeg"),
    chip(H.ollama.ok && H.ollama.model_ok, H.ollama.ok ? (H.ollama.model_ok ? "Ollama" : "нет модели " + H.ollama.model) : "Ollama выключена", H.ollama.ok),
    chip(!!H.yt_dlp, "yt-dlp" + (H.yt_dlp ? " " + H.yt_dlp : ""))];
  for (const id of ["#health", "#health-m"]) $(id).replaceChildren(...items.map(n => n.cloneNode(true)));
}
function renderGroupPick() {
  const sel = $("#group"); sel.replaceChildren(...S.groups.map(g => h("option", {value: g.id}, g.name)));
  if (S.gid) sel.value = S.gid;
  $("#group-pick").hidden = !S.groups.length || ["accounts", "history", "proxies", "editor"].includes(S.page);
}
function renderPill() {
  const run = S.tasks.filter(t => t.status === "running");
  const pill = $("#pill");
  if (!run.length) { pill.hidden = true; return; }
  const t = run[0];
  pill.hidden = false;
  pill.replaceChildren(h("div", {class: "spin"}), h("div", {class: "grow"}, h("b", {}, t.title), h("div", {class: "muted small"}, t.last || "…")));
  pill.onclick = () => { if (t.kind === "process" || t.kind === "publish") go("panel"); };
}
function go(page) { if (location.hash !== "#" + page) location.hash = page; else render(); }
function render() {
  const p = (location.hash || "#panel").slice(1);
  S.page = PAGES.some(x => x[0] === p) ? p : "panel";
  $("#title").textContent = PAGES.find(x => x[0] === S.page)[1];
  document.title = $("#title").textContent + " — narez";
  renderNav(); renderGroupPick();
  const page = $("#page");
  page.replaceChildren();
  ({panel: pagePanel, editor: pageEditor, accounts: pageAccounts, history: pageHistory, proxies: pageProxies})[S.page](page);
}

/* ---------- данные ---------- */
async function loadGroups() {
  S.groups = await api("GET", "/api/groups");
  if (!S.groups.some(g => g.id === S.gid)) S.gid = S.groups.length ? S.groups[0].id : null;
  if (!S.groups.some(g => g.id === S.accGid)) S.accGid = S.gid;
  try { if (S.gid) localStorage.setItem("gid", S.gid); } catch (e) {}
}
async function loadClips() {
  S.clips = await api("GET", "/api/clips");   // очередь всех групп: группа клипа выбирается в блоке «Публикация»
  if (!S.clips.some(c => c.id === S.cid)) S.cid = S.clips.length ? S.clips[0].id : null;
  renderNav();
}
async function loadBanners() { S.banners = await api("GET", "/api/banners"); }
async function loadHistory() { S.history = await api("GET", "/api/history"); }

/* ---------- фоновые задачи ---------- */
async function pollTasks() {
  try {
    const list = await api("GET", "/api/tasks");
    const finished = [];
    for (const t of list) {
      if (t.status !== "running" && S.tracked && S.tracked.has(t.id) && !S.lastDone.has(t.id)) finished.push(t);
      if (t.status === "running" && S.tracked) S.tracked.add(t.id);
    }
    S.tasks = list;
    // лог активной задачи
    const act = list.find(t => t.status === "running") || list.find(t => t.id === S.trackId);
    if (act) {
      S.trackId = act.id;
      const buf = S.log[act.id] || (S.log[act.id] = {lines: [], next: 0});
      const d = await api("GET", `/api/tasks/${act.id}?after=${buf.next}`);
      buf.lines.push(...d.log); buf.next = d.next;
    }
    for (const t of finished) { S.lastDone.add(t.id); await onTaskDone(t); }
    renderPill();
    if (S.page === "panel" && !document.activeElement?.matches("input,textarea")) updateRunBox();
  } catch (e) { /* связь временно пропала — молча */ }
  setTimeout(pollTasks, S.tasks.some(t => t.status === "running") ? 1500 : 4000);
}
S.tracked = new Set();
async function onTaskDone(t) {
  if (t.status === "error" && t.kind === "login" && /отменён/.test(t.error || "")) { /* отмену пользователь сделал сам */ }
  else if (t.status === "error") toast(`${t.title}: ${t.error}`, "err");
  else if (t.kind === "process") toast(`Нарезка готова: клипов — ${t.result.clips ?? 0}`);
  else if (t.kind === "publish") toast("Публикация завершена");
  else if (t.kind === "login") toast("Вход выполнен");
  else if (t.kind === "stats") toast("Статистика обновлена");
  else if (t.kind === "import") toast(`Добавлено видео: ${t.result.added ?? 0}`);
  else if (t.kind === "proxycheck") toast(`Прокси проверены: рабочих ${t.result.ok ?? 0} из ${t.result.checked ?? 0}`);
  try { await loadGroups(); await loadClips(); if (t.kind === "stats" || t.kind === "import" || t.kind === "publish") await loadHistory(); } catch (e) {}
  if (t.kind === "preview" && S.page === "editor") S.previewTick = Date.now();
  render();
}
function startTracking(task) { S.tracked.add(task.id); S.trackId = task.id; S.tasks = [task, ...S.tasks.filter(t => t.id !== task.id)]; renderPill(); }

/* ---------- режим нарезки ---------- */
const cutDefaults = {mode: "moments", length: 45, overlap: 2.5, audio: "ru"};
function cutState() {
  if (!S.cut) { try { S.cut = {...cutDefaults, ...JSON.parse(localStorage.getItem("cut") || "{}")}; } catch (e) { S.cut = {...cutDefaults}; } }
  return S.cut;
}
const cutSave = () => { try { localStorage.setItem("cut", JSON.stringify(S.cut)); } catch (e) {} };
const cutPayload = () => { const c = cutState(); return {mode: c.mode, length: +c.length || 45, overlap: c.overlap === "" ? 2.5 : +c.overlap, audio: c.audio === "original" ? "original" : "ru"}; };
function cutBox() {
  const c = cutState();
  const num = (key, step, min, max) => { const i = h("input", {type: "number", step: String(step), min: String(min), max: String(max), value: String(c[key]), style: {width: "90px"}});
    i.oninput = () => { c[key] = i.value; cutSave(); }; return i; };
  const params = h("div", {class: "row", style: {marginTop: "10px", gap: "16px", alignItems: "end"}},
    h("label", {class: "muted small"}, "Длина клипа, с", h("div", {}, num("length", 1, 5, 180))),
    h("label", {class: "muted small"}, "Нахлёст, с", h("div", {}, num("overlap", 0.5, 0, 30))),
    h("div", {class: "grow muted small"}, "Каждый следующий клип начинается с последних секунд предыдущего. Видео режется подряд, целиком, без выбора моментов — заголовки «Часть N/M» можно поменять в редакторе."));
  const draw = () => { params.style.display = c.mode === "full" ? "" : "none"; seg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.k === c.mode)); };
  const seg = h("div", {class: "seg"}, [["moments", "Интересные моменты"], ["full", "Нарезать целиком"]].map(([k, t]) =>
    h("button", {class: "", "data-k": k, onclick: () => { c.mode = k; cutSave(); draw(); }}, t)));
  draw();
  const aud = h("select", {style: {width: "auto"}}, [["ru", "Звук: русский (если есть дубляж)"], ["original", "Звук: оригинал"]].map(([k, t]) => h("option", {value: k}, t)));
  aud.value = c.audio === "original" ? "original" : "ru"; aud.onchange = () => { c.audio = aud.value; cutSave(); };
  return h("div", {style: {marginTop: "12px"}}, h("div", {class: "row", style: {gap: "12px"}}, seg, aud), params);
}

/* ---------- панель ---------- */
const STEPS = [["Скачивание", /Скачиваю|Скачано/], ["Транскрипция и выбор моментов", /транскри|Whisper|моментов|LLM|Выбрано/i],
  ["Рендер клипов", /Рендерю клип/], ["Готово", /Готово!/]];
function stepState(lines) {
  let cur = -1;
  STEPS.forEach(([_, re], i) => { if (lines.some(l => re.test(l))) cur = i; });
  return cur;
}
function pagePanel(page) {
  const g = group();
  if (!S.groups.length) {
    page.append(h("div", {class: "card empty"}, h("b", {}, "Сначала создай группу аккаунтов"),
      "В группе — по одному аккаунту каждой платформы.", h("div", {style: {marginTop: "14px"}},
        h("button", {class: "btn primary", onclick: () => go("accounts")}, "Перейти в «Аккаунты»"))));
    return;
  }
  const url = h("input", {type: "text", placeholder: "Ссылка на видео (любой сайт), прямая ссылка на файл или путь к файлу", id: "url", autocomplete: "off", spellcheck: "false"});
  const testBox = h("input", {type: "checkbox", id: "testmode"});
  testBox.checked = !!S.testMode; testBox.onchange = () => { S.testMode = testBox.checked; };
  url.addEventListener("keydown", e => { if (e.key === "Enter") start(); });
  const startBtn = h("button", {class: "btn primary", id: "start", onclick: start}, "Нарезать");
  async function start() {
    try {
      const t = await api("POST", "/api/process", {url: url.value, group_id: S.gid, test: testBox.checked, ...cutPayload()});
      startTracking(t); url.value = ""; toast("Запустил нарезку"); updateRunBox();
    } catch (e) { fail(e); }
  }
  page.append(h("div", {class: "card"}, h("h2", {}, "Новое видео", h("small", {}, `группа «${g.name}»`)),
    h("div", {class: "hero-input"}, url, startBtn),
    cutBox(),
    h("label", {class: "check"}, testBox, h("span", {}, "Тестовый прогон — клипы сохранятся в папку тестов и не будут публиковаться"))));
  page.append(h("div", {id: "runbox"}));
  page.append(filesCard());
  const mine = S.clips.filter(c => c.group_id === S.gid), n = mine.length;
  page.append(h("div", {class: "card"}, h("h2", {}, "Готово к публикации", h("small", {}, `группа «${g.name}»`)),
    n ? [h("div", {class: "row"}, h("div", {class: "grow"}, h("b", {}, `${n} ${plural(n, "клип", "клипа", "клипов")} в очереди`),
        h("div", {class: "muted small"}, `Аккаунтов в группе: ${g.accounts.length}`)),
      h("button", {class: "btn", onclick: () => go("editor")}, "Открыть редактор"),
      h("button", {class: "btn primary", onclick: () => publish(null, "keep")}, "Опубликовать все")),
      h("div", {style: {marginTop: "12px"}}, nameToggle(() => render())),
      h("div", {style: {marginTop: "10px"}}, mine.map(c => h("div", {class: "task"},
        h("div", {class: "thumb", style: Object.assign({width: "34px", height: "60px"}, c.has_thumb ? {backgroundImage: `url(/api/clips/${c.id}/thumb)`} : {})}, c.has_thumb ? "" : "нет"),
        h("div", {class: "t"}, h("b", {}, clipLabel(c)), h("span", {class: "muted small"}, `${Math.round(c.duration)} с${c.test ? " · тест" : ""}${c.banner ? " · баннер" : ""}`)),
        h("button", {class: "btn sm", onclick: () => ask("Переименовать клип", "Название, с которым клип будет опубликован", c.title, "Сохранить", async v => {
          try { await api("PATCH", `/api/clips/${c.id}`, {title: v}); await loadClips(); render(); } catch (e) { fail(e); } })}, "Переименовать"),
        h("button", {class: "btn sm", onclick: () => { S.cid = c.id; go("editor"); }}, "Править"),
        h("button", {class: "btn sm danger", onclick: () => confirmBox("Удалить клип?", `«${c.title}» будет убран из очереди, файл удалён с диска.`, "Удалить", async () => {
          try { await api("DELETE", `/api/clips/${c.id}`); await loadClips(); toast("Клип удалён"); render(); } catch (e) { fail(e); } })}, "Удалить"))))]
      : h("div", {class: "muted"}, "Пока пусто — вставь ссылку выше.")));
  const hist = S.tasks.filter(t => t.status !== "running").slice(0, 6);
  if (hist.length) page.append(h("div", {class: "card"}, h("h2", {}, "Недавние задачи"), hist.map(t =>
    h("div", {class: "task"}, h("div", {class: "t"}, h("b", {}, t.title), h("span", {class: "muted small"}, t.status === "error" ? t.error : (t.last || ""))),
      h("span", {class: "badge " + (t.status === "done" ? "ok" : "err")}, t.status === "done" ? "готово" : "ошибка")))));
  updateRunBox();
}
function updateRunBox() {
  const box = $("#runbox"); if (!box) return;
  const t = S.tasks.find(x => x.id === S.trackId) || S.tasks.find(x => x.status === "running");
  if (!t || (t.status !== "running" && Date.now() / 1000 - t.finished > 60 && t.kind !== "process")) { box.replaceChildren(); return; }
  const buf = (S.log[t.id] && S.log[t.id].lines) || [];
  let body = [];
  if (t.kind === "process") {
    const cur = t.status === "done" ? STEPS.length : stepState(buf);
    body.push(h("div", {class: "steps"}, STEPS.map(([name], i) => h("div", {class: "step " + (i < cur ? "done" : i === cur ? "on" : "")}, (i < cur ? "✓ " : "") + name))));
    const m = [...buf].reverse().map(l => /Рендерю клип (\d+)\/(\d+)/.exec(l)).find(Boolean);
    if (m) body.push(h("div", {class: "bar"}, h("div", {style: {width: (Number(m[1]) / Number(m[2]) * 100) + "%"}})));
  }
  const lg = h("div", {class: "log"}, buf.slice(-200).join("\n") || "…");
  body.push(lg);
  const old = box.firstChild && $(".log", box);
  const stick = !old || old.scrollTop + old.clientHeight >= old.scrollHeight - 30;
  box.replaceChildren(h("div", {class: "card"}, h("h2", {}, t.title, h("small", {}, t.status === "running" ? "выполняется…" : t.status === "done" ? "готово" : "ошибка")), body));
  if (stick) lg.scrollTop = lg.scrollHeight;
}
const platName = p => (PLAT[p] || ["", p])[1];
async function publish(ids, mode, accIds, gid) {
  const g = S.groups.find(x => x.id === (gid || S.gid));
  if (!accIds && !g.accounts.length) return toast("В группе нет аккаунтов", "err");
  const n = ids ? ids.length : S.clips.filter(c => c.group_id === g.id).length;
  const all = S.groups.flatMap(x => x.accounts.map(a => ({...a, group: x.name})));
  const names = accIds ? accIds.map(i => { const a = all.find(x => x.id === i); return a ? `${platName(a.platform)} · ${a.label}` : i; }).join(", ")
                       : g.accounts.map(a => a.label).join(", ");
  const tail = accIds ? " Клип уйдёт только на выбранные аккаунты и исчезнет из очереди." : "";
  confirmBox("Опубликовать?", `${n} ${plural(n, "клип", "клипа", "клипов")} → ${names}. ` + (mode === "now" ? "Выйдут сразу." : "Время публикации клипов учтётся.") + tail, "Опубликовать", async () => {
    try { const t = await api("POST", "/api/publish", {group_id: g.id, clip_ids: ids, mode, account_ids: accIds || null}); startTracking(t); toast("Публикую…"); go("panel"); if (S.page === "panel") render(); }
    catch (e) { fail(e); }
  }, false);
}

/* ---------- файлы в папках ---------- */
const fmtSize = b => b > 1e9 ? (b / 1e9).toFixed(1) + " ГБ" : Math.max(1, Math.round(b / 1e6)) + " МБ";
function filesCard() {
  const card = h("div", {class: "card"}), body = h("div", {});
  const tab = S.folderTab || (S.folderTab = "sources");
  const draw = async () => {
    body.replaceChildren(h("div", {class: "muted small"}, "Загружаю…"));
    try {
      const [fl, folders] = await Promise.all([api("GET", "/api/files?folder_key=" + tab), S.folders ? S.folders : api("GET", "/api/folders")]);
      S.folders = folders;
      const f = folders.find(x => x.key === tab);
      const rows = fl.length ? fl.slice(0, 40).map(x => h("div", {class: "task"},
        h("div", {class: "t"}, h("b", {title: x.name}, x.name), h("span", {class: "muted small"}, `${fmtSize(x.size)} · ${fmtLocal(x.mtime * 1000)}`)),
        x.in_editor ? h("span", {class: "badge acc"}, "в редакторе") : null,
        tab === "clips" || (tab === "test" && /(^|\/)job\d+\//.test(x.name))
          ? h("button", {class: "btn sm", disabled: x.in_editor, onclick: () => act("to-editor", x)}, "В редактор")
          : h("button", {class: "btn sm primary", onclick: () => act("slice", x)}, (S.testMode ? "Нарезать (тест)" : "Нарезать") + (cutState().mode === "full" ? " целиком" : "")),
        h("button", {class: "btn sm", onclick: () => ask("Переименовать видео", "Новое название файла (расширение сохранится)", x.name.split("/").pop().replace(/\.[^.]+$/, ""), "Переименовать", async v => {
          try { await api("POST", "/api/files/rename", {folder: tab, name: x.name, new_name: v}); toast("Переименовано"); await loadClips(); renderNav(); draw(); } catch (e) { fail(e); } })}, "Переименовать"),
        h("button", {class: "btn sm danger", title: "Удалить файл с диска", onclick: () => confirmBox("Удалить видео?", `«${x.name}» (${fmtSize(x.size)}) будет удалено с диска без возможности восстановления.` + (x.in_editor ? " Клип пропадёт и из редактора." : ""), "Удалить", async () => {
          try { await api("DELETE", `/api/files?folder_key=${tab}&name=${encodeURIComponent(x.name)}`); toast("Удалено"); await loadClips(); renderNav(); draw(); } catch (e) { fail(e); } })}, "Удалить")))
        : [h("div", {class: "muted"}, "В этой папке пока нет видео.")];
      body.replaceChildren(h("div", {class: "row", style: {marginBottom: "10px"}},
        h("div", {class: "grow muted small", style: {wordBreak: "break-all"}}, f ? f.path : ""),
        h("button", {class: "btn sm", onclick: async () => { try { await api("POST", `/api/folders/${tab}/open`); } catch (e) { fail(e); } }}, "Открыть папку")), ...rows);
    } catch (e) { body.replaceChildren(h("div", {class: "muted"}, e.message)); }
  };
  async function act(kind, x) {
    try {
      const payload = {folder: tab, name: x.name, group_id: S.gid, test: !!S.testMode, ...cutPayload()};
      const r = await api("POST", "/api/files/" + kind, payload);
      if (kind === "slice") { startTracking(r); toast("Запустил нарезку"); }
      else { toast("Клип добавлен в редактор"); await loadClips(); S.cid = r.id; renderNav(); draw(); }
    } catch (e) { fail(e); }
  }
  const seg = h("div", {class: "seg"}, [["sources", "Исходники"], ["clips", "Клипы"], ["test", "Тесты"]].map(([k, t]) =>
    h("button", {class: tab === k ? "on" : "", onclick: () => { S.folderTab = k; card.replaceWith(filesCard()); }}, t)));
  card.append(h("h2", {}, "Видео из папок", h("small", {}, "исходник → нарезать, готовый клип → в редактор")), seg, h("div", {style: {height: "12px"}}), body);
  draw();
  return card;
}

/* ---------- редактор ---------- */
function pageEditor(page) {
  if (!S.groups.length || !S.clips.length) {
    page.append(h("div", {class: "card empty"}, h("b", {}, "Нет клипов для редактирования"), "Нарежь видео на вкладке «Панель»."));
    return;
  }
  const list = h("div", {class: "clips"}, S.clips.map(c => h("button", {class: "clip" + (c.id === S.cid ? " on" : ""), onclick: () => { S.cid = c.id; render(); }},
    h("div", {class: "thumb", style: c.has_thumb ? {backgroundImage: `url(/api/clips/${c.id}/thumb)`} : {}}, c.has_thumb ? "" : "нет"),
    h("div", {class: "info"}, h("b", {}, clipLabel(c)), h("div", {class: "tags"},
      S.groups.length > 1 ? h("span", {class: "badge acc"}, c.group) : null,
      h("span", {class: "badge"}, Math.round(c.duration) + " с"), c.banner ? h("span", {class: "badge acc"}, "баннер") : null, c.test ? h("span", {class: "badge warn"}, "тест") : null,
      c.publish_at ? h("span", {class: "badge warn"}, fmtLocal(c.publish_at).slice(0, 5) + " " + fmtLocal(c.publish_at).slice(-5)) : null)))));
  const right = h("div", {id: "ed"});
  const listWrap = h("div", {class: "clips"}, h("div", {class: "namemode"}, nameToggle(() => { render(); })), ...list.children);
  page.append(h("div", {class: "editor"}, listWrap, right));
  buildEditor(right);
}
async function buildEditor(root) {
  const c = clip(); if (!c) return;
  if (!S.banners.length) { try { await loadBanners(); } catch (e) {} }
  try { S.presets = await api("GET", "/api/presets"); } catch (e) { S.presets = S.presets || []; }
  const cfg = S.cfg;
  let bannerSel = c.banner ? {...c.banner} : null;
  const title = h("input", {type: "text", maxlength: 200, value: c.title});
  const desc = h("textarea", {}); desc.value = c.description;
  const tags = h("input", {type: "text", placeholder: "#тег1 #тег2"}); tags.value = c.tags;
  const when = h("input", {type: "datetime-local", value: isoToInput(c.publish_at)});
  const pvTitle = h("div", {class: "note", id: "bn-info"}, "");
  const bnBox = h("div", {});
  const video = h("video", {controls: true, playsinline: true, preload: "auto", poster: `/api/clips/${c.id}/thumb`, src: `/api/clips/${c.id}/video?t=${S.previewTick || 0}`});
  video.addEventListener("loadedmetadata", () => { if (video.currentTime === 0) video.currentTime = 0.1; });
  video.addEventListener("error", () => toast("Браузер не смог загрузить видео клипа (превью-кадр при этом виден).", "err"));
  const frame = h("div", {class: "frame"}, video);
  const ov = h("div", {class: "bn-ov"}); frame.append(ov);
  let live = true;   // живой предпросмотр: баннер рисуется поверх плеера (приблизительно); «точный» — настоящий рендер ffmpeg
  const pvBtn = h("button", {class: "btn", onclick: preview}, "Точный предпросмотр (рендер)");
  async function preview() {
    if (!live) {   // вернуться к быстрому предпросмотру
      live = true; video.src = `/api/clips/${c.id}/video?t=${Date.now()}`; pvBtn.textContent = "Точный предпросмотр (рендер)"; return;
    }
    try {
      await save(true);
      if (!bannerSel) return toast("Баннер не выбран", "err");
      pvBtn.disabled = true; pvBtn.textContent = "Собираю предпросмотр…";
      const t = await api("POST", `/api/clips/${c.id}/preview`); startTracking(t);
      for (;;) {
        await new Promise(r => setTimeout(r, 1000));
        const s = await api("GET", `/api/tasks/${t.id}`);
        if (s.status === "running") continue;
        if (s.status === "error") throw new Error(s.error);
        break;
      }
      live = false; hideOv();
      video.src = `/api/clips/${c.id}/video?variant=final&t=${Date.now()}`; video.play().catch(() => {});
      pvBtn.disabled = false; pvBtn.textContent = "Вернуться к быстрому предпросмотру"; return;
    } catch (e) { fail(e); }
    pvBtn.disabled = false; pvBtn.textContent = "Точный предпросмотр (рендер)";
  }

  /* ---- живой предпросмотр баннера поверх плеера ---- */
  const GW = 1080, GH = 1920;
  let ovEl = null, ovName = "", frozen = null, frozenDone = false, lastBn = 0;
  // громкость плеера принадлежит пользователю; мы трогаем её только на время приглушения звука под баннер
  let userVol = video.volume, ducked = 0, lastSet = -1;
  function setVol(v) { lastSet = v; video.volume = Math.min(1, Math.max(0, v)); }
  video.addEventListener("volumechange", () => {
    if (Math.abs(video.volume - lastSet) < 0.002) return;       // это наше приглушение, не движение ползунка
    userVol = video.volume;   // пользователь сам выбрал громкость — она и остаётся после баннера
  });
  function duck(level) {   // level: во сколько раз приглушить (1 = не приглушать)
    if (level >= 1) { if (ducked) { ducked = 0; setVol(userVol); } return; }
    if (ducked !== level) { ducked = level; setVol(userVol * level); }
  }
  function hideOv() { if (ovEl) { ovEl.style.display = "none"; if (ovEl.pause) ovEl.pause(); } duck(1); }
  function bannerGeom(s, b) {
    const aspect = b.width / b.height;
    let w = GW * s.width_percent / 100;
    if (b.kind === "video") w = Math.max(w, Math.sqrt((cfg.min_area_percent / 100) * GW * GH * aspect));
    w = Math.min(w, GW, GH * aspect);
    return {w, h: w / aspect};
  }
  function bannerDur(s, b) {
    return b.kind === "video" ? (s.duration > 0 ? Math.min(s.duration, b.duration) : b.duration) : (s.duration > 0 ? s.duration : cfg.banner_defaults.image.duration || 6);
  }
  function ensureOv(b) {
    if (ovEl && ovName === b.name) return;
    ov.replaceChildren(); ovName = b.name; frozen = null;
    const src = "/api/banners/file?name=" + encodeURIComponent(b.name);
    ovEl = b.kind === "video" ? h("video", {src, playsinline: true, preload: "auto"}) : h("img", {src, alt: ""});
    ovEl.style.cssText = "position:absolute;display:none;height:auto";
    ov.append(ovEl);
  }
  video.addEventListener("seeked", () => { frozen = null; frozenDone = !!(bannerSel && F.start && video.currentTime > +F.start.value + 0.3); });
  video.addEventListener("play", () => { if (frozen) { frozen = null; frozenDone = true; } });
  function tick() {
    if (!document.contains(video)) return;   // редактор перерисован — цикл останавливается
    requestAnimationFrame(tick);
    const b = bannerSel && S.banners.find(x => x.name === bannerSel.name);
    if (!live || !b || !F.start || !F.start.isConnected) { hideOv(); return; }
    ensureOv(b);
    const s = bannerSpecFrom(), dur = bannerDur(s, b), t0 = s.start, t = video.currentTime;
    const now = performance.now();
    let show = false, bt = 0;
    if (s.pause) {
      if (!frozen && !video.paused && !frozenDone && t >= t0 && t < t0 + 0.6) { frozen = {at: now, auto: true}; video.pause(); }
      if (frozen) {
        bt = (now - frozen.at) / 1000; show = true;
        if (bt >= dur) { frozen = null; frozenDone = true; show = false; video.play().catch(() => {}); }
      } else if (video.paused && t >= t0 && t < t0 + 0.05) { show = true; bt = 0; }
      duck(1);
    } else {
      bt = t - t0; show = t >= t0 && t < t0 + Math.min(dur, c.duration - t0);
      duck(show && b.has_audio ? Math.max(s.duck_percent / 100, 0.001) : 1);
    }
    if (!show) { hideOv(); return; }
    const g = bannerGeom(s, b), Hh = GH;
    const y = s.position === "top" ? s.margin : s.position === "bottom" ? Hh - g.h - s.margin : (Hh - g.h) / 2;
    ovEl.style.display = "block";
    ovEl.style.left = ((GW - g.w) / 2 / GW * 100) + "%"; ovEl.style.width = (g.w / GW * 100) + "%"; ovEl.style.top = (y / Hh * 100) + "%";
    if (b.kind === "video") {
      const playing = frozen || !video.paused;
      if (ovEl.volume !== userVol) ovEl.volume = userVol;
      ovEl.muted = video.muted;
      if (Math.abs(ovEl.currentTime - bt) > 0.35) ovEl.currentTime = Math.max(0, bt);
      if (playing && ovEl.paused) ovEl.play().catch(() => {});
      if (!playing && !ovEl.paused) ovEl.pause();
    }
  }
  requestAnimationFrame(tick);
  function bannerSpecFrom() {
    return {name: bannerSel.name, start: +F.start.value, duration: +F.duration.value, width_percent: +F.width.value,
      position: F.pos.value, margin: +F.margin.value, duck_percent: +F.duck.value, pause: !!(F.pause && F.pause.checked)};
  }
  const F = {};
  function bannerPresetRow() {
    const list = S.presets.filter(p => p.banner);
    const sel = h("select", {style: {flex: "1", minWidth: "160px"}}, [h("option", {value: ""}, list.length ? "— пресет баннера —" : "— пресетов баннера пока нет —"),
      ...list.map(p => h("option", {value: p.id}, p.name))]);
    if (S.bpresetId && list.some(p => p.id === S.bpresetId)) sel.value = S.bpresetId;
    sel.onchange = () => {
      S.bpresetId = Number(sel.value) || null;
      const p = list.find(x => x.id === S.bpresetId); if (!p) return;
      if (!S.banners.some(b => b.name === p.banner.name)) return toast(`Файла баннера «${p.banner.name}» из пресета больше нет — загрузи его заново`, "err");
      bannerSel = {...p.banner}; drawBanner(); toast(`Пресет «${p.name}» применён к баннеру (не забудь «Сохранить»)`);
    };
    return h("div", {class: "row", style: {marginBottom: "12px"}}, sel,
      h("button", {class: "btn sm", disabled: !bannerSel, onclick: () => {
        const name = h("input", {type: "text", placeholder: "Например: «Реклама казино — внизу»"});
        const cur = list.find(x => x.id === S.bpresetId); if (cur) name.value = cur.name;
        dialog(h("div", {}, h("h3", {}, "Сохранить пресет баннера"),
          h("div", {class: "muted small", style: {marginBottom: "10px"}}, "Сохранятся все поля баннера: файл, начало, длительность, ширина, положение, отступ, громкость, пауза. Пресет с тем же названием будет перезаписан."), name),
          [{text: "Отмена", cls: "ghost"}, {text: "Сохранить", cls: "primary", fn: async () => {
            try {
              const old = S.presets.find(x => x.name === name.value.trim());
              const r = await api("POST", "/api/presets", {name: name.value, banner: bannerSpecFrom(), tags: old ? old.tags : "", footer: old ? old.footer : ""});
              S.bpresetId = r.id; await save(true); toast("Пресет баннера сохранён"); render();
            } catch (e) { fail(e); } }}]);
        setTimeout(() => name.focus(), 50); }}, "Сохранить как пресет"),
      h("button", {class: "btn sm danger", disabled: !S.bpresetId || !list.some(p => p.id === S.bpresetId), onclick: () => {
        const p = list.find(x => x.id === S.bpresetId); if (!p) return;
        confirmBox("Удалить пресет?", `«${p.name}» будет удалён (баннеры клипов, к которым он применён, не изменятся).`, "Удалить", async () => {
          try { await api("DELETE", `/api/presets/${p.id}`); S.bpresetId = null; await save(true); render(); } catch (e) { fail(e); } }); }}, "Удалить пресет"));
  }
  function drawBanner() {
    bnBox.replaceChildren(bannerPresetRow());
    const lib = h("div", {class: "lib"}, S.banners.map(b => h("div", {class: "bn" + (bannerSel && bannerSel.name === b.name ? " on" : ""), title: b.name, onclick: () => pick(b)},
      h("div", {class: "pv"}, b.kind === "image" ? h("img", {src: "/api/banners/file?name=" + encodeURIComponent(b.name), alt: ""}) : "▶"),
      h("span", {}, b.name), h("span", {}, b.kind === "video" ? `${b.duration} с${b.has_audio ? " ♪" : ""}` : "картинка"))),
      h("label", {class: "bn add"}, "+ файл", h("input", {type: "file", hidden: true, accept: "image/*,video/*", onchange: upload})),
      bannerSel ? h("div", {class: "bn add", style: {color: "var(--danger)"}, onclick: () => { bannerSel = null; drawBanner(); }}, "без баннера") : null);
    bnBox.append(lib);
    if (!bannerSel) { bnBox.append(h("div", {class: "note"}, "Баннер для этого клипа не выбран.")); return; }
    const b = S.banners.find(x => x.name === bannerSel.name);
    const isVideo = b && b.kind === "video";
    const mk = (id, label, val, min, max, step, hint) => { F[id] = h("input", {type: "number", min, max, step, value: val, oninput: info}); return h("div", {class: "field"}, h("label", {}, label), F[id], hint ? h("div", {class: "hint"}, hint) : null); };
    F.pos = h("select", {onchange: info}, [["center", "По центру"], ["top", "Сверху"], ["bottom", "Снизу"]].map(([v, t]) => h("option", {value: v}, t)));
    F.pos.value = bannerSel.position || "center";
    F.pause = h("input", {type: "checkbox", onchange: info}); F.pause.checked = !!bannerSel.pause;
    bnBox.append(h("div", {class: "grid3"},
      mk("start", "Начало, с", bannerSel.start ?? 3, 0, 3600, 0.5),
      isVideo
        ? mk("duration", "Длительность, с", Math.round(((bannerSel.duration > 0 ? Math.min(bannerSel.duration, b.duration) : b.duration)) * 10) / 10, 0.5, b.duration, 0.1, `длина видео-баннера: ${b.duration} с (можно показать только начало)`)
        : mk("duration", "Длительность, с", bannerSel.duration || 6, 0.5, 600, 0.5, "сколько показывать"),
      mk("width", "Ширина, % экрана", bannerSel.width_percent ?? 90, 10, 100, 1)),
      h("div", {class: "grid3"}, h("div", {class: "field"}, h("label", {}, "Положение"), F.pos),
        mk("margin", "Отступ, px", bannerSel.margin ?? 160, 0, 800, 10, "для «сверху/снизу»"),
        mk("duck", "Громкость игры, %", bannerSel.duck_percent ?? 15, 0, 100, 1, isVideo && b.has_audio ? "звук основного видео на время баннера" : "актуально для баннера со звуком")),
      h("label", {class: "check", style: {margin: "0 0 12px"}}, F.pause, h("span", {}, "Поставить основное видео на паузу, пока играет баннер (клип станет длиннее на время баннера)")),
      pvTitle);
    info();
  }
  let infoT;
  function info() {
    clearTimeout(infoT);
    infoT = setTimeout(async () => {
      if (!bannerSel || !F.start || !document.contains(pvTitle)) return;
      const s = bannerSpecFrom();
      try {
        const q = new URLSearchParams({name: s.name, start: s.start, duration: s.duration, width_percent: s.width_percent, position: s.position, duck_percent: s.duck_percent, margin: s.margin});
        const d = await api("GET", "/api/banners/describe?" + q);
        pvTitle.className = "note " + (d.ok ? "ok" : "warn"); pvTitle.textContent = d.text;
      } catch (e) { pvTitle.className = "note warn"; pvTitle.textContent = e.message; }
    }, 250);
  }
  function pick(b) {
    const d = (cfg.banner_defaults || {})[b.kind] || {};
    bannerSel = {name: b.name, ...d, position: "center", ...(bannerSel && bannerSel.name === b.name ? bannerSel : {})};   // новый баннер — по центру
    drawBanner();
  }
  async function upload(e) {
    const f = e.target.files[0]; if (!f) return;
    toast("Загружаю баннер…");
    try {
      const r = await api("PUT", "/api/banners?name=" + encodeURIComponent(f.name), undefined, f);
      await loadBanners(); pick(S.banners.find(x => x.name === r.name) || r);
    } catch (er) { fail(er); }
  }
  async function save(silent) {
    const body = {title: title.value, description: desc.value, tags: tags.value, publish_at: inputToIso(when.value)};
    body.banner = bannerSel ? bannerSpecFrom() : null;
    const r = await api("PATCH", `/api/clips/${c.id}`, body);
    Object.assign(c, r); const i = S.clips.findIndex(x => x.id === c.id); if (i >= 0) S.clips[i] = r;
    tags.value = r.tags;
    if (!silent) toast("Сохранено");
    return r;
  }
  const presetSel = h("select", {style: {width: "100%"}}, S.presets.length ? S.presets.map(p => h("option", {value: p.id}, p.name)) : [h("option", {value: ""}, "— пресетов пока нет —")]);
  if (S.presetId && S.presets.some(p => p.id === S.presetId)) presetSel.value = S.presetId;
  presetSel.onchange = () => { S.presetId = Number(presetSel.value) || null; showPreset(); };
  const presetInfo = h("div", {class: "note"});
  function showPreset() {
    const p = S.presets.find(x => x.id === Number(presetSel.value));
    presetInfo.style.display = p ? "" : "none";
    if (!p) return;
    presetInfo.textContent = [p.banner ? `баннер: ${p.banner.name}` : "", p.tags ? `теги: ${p.tags}` : "", p.footer ? `в конце описания: «${p.footer}»` : ""].filter(Boolean).join(" · ");
  }
  async function applyPreset(ids, text) {
    const p = S.presets.find(x => x.id === Number(presetSel.value)); if (!p) return toast("Сначала выбери пресет", "err");
    try {
      await save(true);
      await api("POST", `/api/presets/${p.id}/apply`, {clip_ids: ids});
      await loadClips(); toast(`Пресет «${p.name}» применён: ${text}`); render();
    } catch (e) { fail(e); }
  }
  function savePresetDialog() {
    const name = h("input", {type: "text", placeholder: "Например: «Реклама казино»"});
    const useBn = h("input", {type: "checkbox"}); useBn.checked = !!bannerSel; useBn.disabled = !bannerSel;
    const ptags = h("input", {type: "text", placeholder: "#теги, которые добавятся ко всем"}); ptags.value = "";
    const foot = h("textarea", {placeholder: "Текст, который допишется в конец описания (ссылка, промокод…)", style: {minHeight: "70px"}});
    dialog(h("div", {}, h("h3", {}, "Сохранить пресет"),
      h("div", {class: "field"}, h("label", {}, "Название"), name),
      h("label", {class: "check"}, useBn, h("span", {}, bannerSel ? `Баннер ${bannerSel.name} с текущими настройками` : "Баннер (сейчас не выбран)")),
      h("div", {class: "field", style: {marginTop: "12px"}}, h("label", {}, "Теги"), ptags),
      h("div", {class: "field"}, h("label", {}, "Текст в конце описания"), foot)),
      [{text: "Отмена", cls: "ghost"}, {text: "Сохранить", cls: "primary", fn: async () => {
        try {
          const body = {name: name.value, tags: ptags.value, footer: foot.value, banner: useBn.checked && bannerSel ? bannerSpecFrom() : null};
          const r = await api("POST", "/api/presets", body); S.presetId = r.id; await save(true); toast("Пресет сохранён"); render();
        } catch (e) { fail(e); } }}]);
    setTimeout(() => name.focus(), 50);
  }
  const saveBtn = h("button", {class: "btn", onclick: async () => { try { await save(); render(); } catch (e) { fail(e); } }}, "Сохранить");
  // куда публиковать: все аккаунты группы или выбранные аккаунты (любых групп)
  S.pubAccounts = (S.pubAccounts || []).filter(i => S.groups.some(g => g.accounts.some(a => a.id === i)));
  const targetBox = h("div", {});
  function drawTargets() {
    const sel = S.pubMode === "pick";
    const seg = h("div", {class: "seg"}, [["group", "Все аккаунты группы"], ["pick", "Выбрать аккаунты"]].map(([k, t]) =>
      h("button", {class: (sel ? "pick" : "group") === k ? "on" : "", onclick: () => { S.pubMode = k; drawTargets(); }}, t)));
    const gsel = h("select", {style: {width: "100%"}}, S.groups.map(g => h("option", {value: g.id}, g.name)));
    gsel.value = c.group_id;
    gsel.onchange = async () => {
      try { await save(true); const r = await api("PATCH", `/api/clips/${c.id}`, {group_id: Number(gsel.value)}); Object.assign(c, r);
        const i = S.clips.findIndex(x => x.id === c.id); if (i >= 0) S.clips[i] = r; toast(`Клип перенесён в группу «${r.group}»`); render(); } catch (e) { fail(e); gsel.value = c.group_id; }
    };
    targetBox.replaceChildren(sel ? "" : h("div", {class: "field"}, h("label", {}, "Группа"), gsel), h("div", {class: "field"}, h("label", {}, "Куда публиковать"), seg,
      sel ? h("div", {style: {marginTop: "10px"}}, S.groups.map(g => g.accounts.length ? h("div", {style: {marginBottom: "8px"}},
        h("div", {class: "muted small", style: {marginBottom: "4px"}}, `Группа «${g.name}»`),
        g.accounts.map(a => { const cb = h("input", {type: "checkbox", disabled: !a.supported}); cb.checked = S.pubAccounts.includes(a.id);
          cb.onchange = () => { S.pubAccounts = cb.checked ? [...S.pubAccounts, a.id] : S.pubAccounts.filter(i => i !== a.id); };
          return h("label", {class: "check", style: {margin: "2px 0", color: a.supported ? "var(--text)" : "var(--muted)"}}, cb,
            h("span", {}, `${platName(a.platform)} · ${a.label}` + (a.supported ? (a.authorized ? "" : " (нужен вход)") : " — публикация позже"))); })) : null),
        h("div", {class: "hint muted small"}, "Клип можно выложить на аккаунт любой группы; он уйдёт только на отмеченные и пропадёт из очереди."))
        : h("div", {class: "hint muted small"}, `Сейчас: ${((S.groups.find(x => x.id === c.group_id) || {accounts: []}).accounts.map(a => platName(a.platform) + " · " + a.label).join(", ")) || "в группе нет аккаунтов"}`)));
  }
  drawTargets();
  function targetIds() {
    if (S.pubMode !== "pick") return null;
    if (!S.pubAccounts.length) { toast("Отметь хотя бы один аккаунт", "err"); return undefined; }
    return S.pubAccounts;
  }
  const nowBtn = h("button", {class: "btn primary", disabled: c.test, onclick: async () => { const tg = targetIds(); if (tg === undefined) return; try { await save(true); when.value = ""; await api("PATCH", `/api/clips/${c.id}`, {publish_at: ""}); publish([c.id], "now", tg, c.group_id); } catch (e) { fail(e); } }}, "Опубликовать сейчас");
  const schBtn = h("button", {class: "btn primary", disabled: c.test, onclick: async () => {
    if (!when.value) return toast("Укажи время публикации", "err");
    const tg = targetIds(); if (tg === undefined) return;
    try { await save(true); publish([c.id], "keep", tg, c.group_id); } catch (e) { fail(e); } }}, "Опубликовать по времени");
  const delBtn = h("button", {class: "btn danger", onclick: () => confirmBox("Удалить клип?", "Файл клипа будет удалён с диска.", "Удалить", async () => {
    try { await api("DELETE", `/api/clips/${c.id}`); await loadClips(); render(); toast("Клип удалён"); } catch (e) { fail(e); } })}, "Удалить");
  root.replaceChildren(h("div", {class: "ed-grid"},
    h("div", {class: "phone"}, frame, pvBtn, h("div", {class: "muted small", style: {marginTop: "8px"}}, "Баннер на плеере показан приблизительно. «Точный предпросмотр» собирает настоящий видеофайл.")),
    h("div", {class: "ed-main"}, h("div", {class: "card"}, h("h2", {}, "Описание"),
      h("div", {class: "field"}, h("label", {}, "Название"), title),
      h("div", {class: "field"}, h("label", {}, "Описание"), desc),
      h("div", {class: "field"}, h("label", {}, "Теги"), tags, h("div", {class: "hint"}, "Через пробел, с # или без — программа приведёт к одному виду.")),
      h("div", {class: "muted small"}, `Источник: ${c.source || "—"} · ${Math.round(c.duration)} с`)),
      h("div", {class: "card"}, h("h2", {}, "Баннер", h("small", {}, `минимум ${cfg.min_area_percent}% экрана`)), bnBox),
      h("div", {class: "card"}, h("h2", {}, "Пресеты", h("small", {}, "баннер, теги и подпись одним нажатием")),
        h("div", {class: "field"}, presetSel), presetInfo,
        h("div", {class: "sticky-actions"},
          h("button", {class: "btn", disabled: !S.presets.length, onclick: () => applyPreset([c.id], "этот клип")}, "Применить к клипу"),
          h("button", {class: "btn", disabled: !S.presets.length, onclick: () => applyPreset(S.clips.filter(x => x.group_id === c.group_id).map(x => x.id), `все клипы группы «${c.group}»`)}, "Ко всем клипам группы"),
          h("button", {class: "btn", onclick: savePresetDialog}, "Сохранить как пресет…"),
          h("button", {class: "btn danger", disabled: !S.presets.length, onclick: () => {
            const p = S.presets.find(x => x.id === Number(presetSel.value)); if (!p) return;
            confirmBox("Удалить пресет?", `«${p.name}» — клипы, к которым он уже применён, не изменятся.`, "Удалить", async () => {
              try { await api("DELETE", `/api/presets/${p.id}`); S.presetId = null; render(); } catch (e) { fail(e); } }); }}, "Удалить пресет"))),
      h("div", {class: "card"}, h("h2", {}, "Публикация"),
        h("div", {class: "field"}, h("label", {}, "Время выхода (по часам этого компьютера)"), when,
          h("div", {class: "hint"}, `Пусто — выйдет сразу (или по задержке группы). Не раньше чем через ${cfg.min_ahead_minutes} мин.`)),
        c.test ? h("div", {class: "note warn", style: {marginBottom: "12px"}}, "Тестовый клип: публикация отключена.") : null,
        targetBox,
        h("div", {class: "sticky-actions"}, saveBtn, nowBtn, schBtn, delBtn)))));
  drawBanner(); showPreset();
}

/* ---------- аккаунты ---------- */
function pageAccounts(page) {
  const sel = S.groups.find(g => g.id === S.accGid);
  const left = h("div", {}, h("div", {class: "list"}, S.groups.map(g => h("button", {class: "item" + (g.id === S.accGid ? " on" : ""), onclick: () => { S.accGid = g.id; S.gid = g.id; try { localStorage.setItem("gid", g.id); } catch (e) {} render(); }},
      h("b", {}, g.name), h("span", {class: "badge"}, g.accounts.length)))),
    h("button", {class: "btn", style: {marginTop: "10px", width: "100%"}, onclick: () => ask("Новая группа", "Например: «Собаки» или «Игры»", "", "Создать", async v => {
      try { const r = await api("POST", "/api/groups", {name: v}); S.gid = S.accGid = r.id; await loadGroups(); await loadClips(); render(); } catch (e) { fail(e); } })}, "+ Новая группа"));
  const right = h("div", {});
  if (!sel) right.append(h("div", {class: "card empty"}, h("b", {}, "Групп пока нет"), "Создай первую группу слева."));
  else {
    const have = new Set(sel.accounts.map(a => a.platform));
    right.append(h("div", {class: "card"}, h("h2", {}, `Группа «${sel.name}»`, h("small", {}, "по одному аккаунту на платформу")),
      sel.accounts.length ? sel.accounts.map(a => h("div", {class: "acc"},
        h("div", {class: "plat " + a.platform}, (PLAT[a.platform] || [a.platform[0]])[0]),
        h("div", {class: "grow"}, h("b", {}, a.label), h("span", {class: "muted small"}, (PLAT[a.platform] || ["", a.platform])[1])),
        !a.supported ? h("span", {class: "badge"}, "публикация позже")
          : a.authorized ? h("span", {class: "badge ok"}, "вход выполнен") : h("span", {class: "badge warn"}, "нужен вход"),
        !a.supported ? null : S.tasks.some(t => t.kind === "login" && t.status === "running")
          ? h("button", {class: "btn sm danger", onclick: cancelLogin}, "Отменить вход")
          : h("button", {class: "btn sm" + (a.authorized ? "" : " primary"), onclick: () => login(a)}, a.authorized ? "Войти заново" : "Войти"),
        h("button", {class: "btn sm danger", onclick: () => confirmBox("Удалить аккаунт?", `«${a.label}» будет убран из группы (вход сбросится).`, "Удалить", async () => {
          try { await api("DELETE", `/api/accounts/${a.id}`); await loadGroups(); render(); } catch (e) { fail(e); } })}, "Убрать")))
        : h("div", {class: "muted"}, "Аккаунтов нет — добавь ниже.")));
    const free = S.cfg.platforms.filter(p => !have.has(p));
    if (free.length) {
      const plat = h("select", {}, free.map(p => h("option", {value: p}, PLAT[p] ? PLAT[p][1] : p)));
      const label = h("input", {type: "text", placeholder: "Название (например, «Основной канал»)"});
      right.append(h("div", {class: "card"}, h("h2", {}, "Добавить аккаунт"), h("div", {class: "row"}, plat, h("div", {class: "grow"}, label),
        h("button", {class: "btn primary", onclick: async () => {
          try { await api("POST", `/api/groups/${sel.id}/accounts`, {platform: plat.value, label: label.value}); await loadGroups(); render(); } catch (e) { fail(e); } }}, "Добавить")),
        h("div", {class: "hint muted small", style: {marginTop: "10px"}}, "Публикация работает на YouTube Shorts, Instagram Reels и TikTok. Для Instagram и TikTok нужны ключи приложения в файле .env (см. README). У них нет отложенной публикации, поэтому по времени клип выкладывает сама программа — она должна быть запущена.")));
    }
  }
  page.append(h("div", {class: "two"}, left, right));
}
async function login(a) {
  try { const t = await api("POST", `/api/accounts/${a.id}/login`); startTracking(t); toast("Открою браузер для входа — выбери аккаунт и канал"); render(); }
  catch (e) { fail(e); }
}
async function cancelLogin() {
  try { await api("POST", "/api/login/cancel"); toast("Вход отменён"); } catch (e) { fail(e); }
}

/* ---------- история ---------- */
function pageHistory(page) {
  const bar = h("div", {class: "row", style: {marginBottom: "16px"}},
    h("button", {class: "btn primary", onclick: async () => { try { startTracking(await api("POST", "/api/history/refresh")); toast("Обновляю статистику…"); } catch (e) { fail(e); } }}, "Обновить статистику"),
    h("button", {class: "btn", onclick: () => ask("Добавить в историю", "Ссылки на ранее опубликованные видео YouTube — по одной в строке", "", "Добавить", async v => {
      try { startTracking(await api("POST", "/api/history/import", {urls: v, group_id: S.gid})); } catch (e) { fail(e); } }, true)}, "Добавить старые видео"));
  page.append(bar);
  const sched = h("div", {}); page.append(sched);
  const drawSched = async () => {
    let L = []; try { L = await api("GET", "/api/scheduled"); } catch (e) {}
    if (!L.length) { sched.replaceChildren(); return; }
    const st = r => r.status === "error" ? h("span", {class: "badge err", title: r.error}, "ошибка") : r.status === "run" ? h("span", {class: "badge warn"}, "выкладываю…") : h("span", {class: "badge"}, "ждёт");
    sched.replaceChildren(h("div", {class: "card", style: {marginBottom: "16px"}}, h("h2", {}, "Очередь Instagram и TikTok", h("small", {}, "выкладывает программа в назначенное время")),
      L.map(r => h("div", {class: "acc"}, h("div", {class: "grow"}, h("b", {}, r.clip || "Клип"), h("span", {class: "muted small"}, `${r.account || r.platform} · ${fmtLocal(r.run_at)}${r.error ? " · " + r.error : ""}`)), st(r),
        r.status === "error" ? h("button", {class: "btn sm", onclick: async () => { try { await api("POST", `/api/scheduled/${r.id}/retry`); drawSched(); } catch (e) { fail(e); } }}, "Повторить") : null,
        h("button", {class: "btn sm danger", onclick: async () => { try { await api("DELETE", `/api/scheduled/${r.id}`); drawSched(); } catch (e) { fail(e); } }}, "Убрать")))));
  };
  drawSched();
  const box = h("div", {}); page.append(box);
  const draw = () => {
    const H = S.history || [];
    if (!H.length) { box.replaceChildren(h("div", {class: "card empty"}, h("b", {}, "История пуста"), "Опубликованные видео появятся здесь.")); return; }
    const open = p => window.open(p.url, "_blank", "noopener");
    const thumb = p => h("div", {class: "thumb", style: p.has_thumb ? {backgroundImage: `url(/api/history/${p.id}/thumb)`} : {}}, p.has_thumb ? "" : "нет");
    const st = p => h("span", {class: "badge " + (p.status === "Опубликовано" ? "ok" : p.status === "Удалено" ? "err" : p.status.startsWith("Запл") ? "warn" : "")}, p.status);
    const when = p => fmtLocal(p.uploaded_at);
    const table = h("table", {class: "htable"}, h("thead", {}, h("tr", {}, ["Видео", "Аккаунт", "Статус", "Просмотры", "Лайки", "Коммент.", "Загружено"].map((t, i) => h("th", {class: i > 2 && i < 6 ? "num" : ""}, t)))),
      h("tbody", {}, H.map(p => h("tr", {class: "link", onclick: () => open(p)},
        h("td", {}, h("div", {class: "hcell"}, thumb(p), h("div", {}, h("b", {}, p.title || "Без названия"), h("span", {class: "muted small"}, p.group)))),
        h("td", {}, p.account, h("div", {class: "muted small"}, platName(p.platform))), h("td", {}, st(p)), h("td", {class: "num"}, fmtNum(p.views)), h("td", {class: "num"}, fmtNum(p.likes)),
        h("td", {class: "num"}, fmtNum(p.comments)), h("td", {class: "muted small"}, when(p))))));
    const cards = h("div", {class: "cards"}, H.map(p => h("div", {class: "card", style: {cursor: "pointer"}, onclick: () => open(p)},
      h("div", {class: "hcell"}, thumb(p), h("div", {}, h("b", {}, p.title || "Без названия"), h("div", {class: "row", style: {gap: "8px", marginTop: "4px"}}, st(p), h("span", {class: "muted small"}, `${platName(p.platform)} · ${p.account}`)),
        h("div", {class: "muted small", style: {marginTop: "6px"}}, `👁 ${fmtNum(p.views)} · 👍 ${fmtNum(p.likes)} · 💬 ${fmtNum(p.comments)}`))))));
    box.replaceChildren(h("div", {class: "card tcard", style: {padding: "8px 10px", overflowX: "auto"}}, table), cards);
  };
  if (S.history) draw();
  loadHistory().then(draw).catch(fail);
}

/* ---------- прокси ---------- */
const flag = code => code && code.length === 2 ? String.fromCodePoint(...[...code.toUpperCase()].map(c => 127397 + c.charCodeAt(0))) : "";
function pageProxies(page) {
  const ta = h("textarea", {placeholder: "По одному в строке, любой формат:\n1.2.3.4:8080\nuser:pass@1.2.3.4:8080\n1.2.3.4:8080:user:pass\nsocks5://user:pass@1.2.3.4:1080", style: {minHeight: "130px", fontFamily: "var(--mono)", fontSize: "13px"}, spellcheck: "false"});
  const scheme = h("select", {}, [["http", "HTTP"], ["socks5", "SOCKS5"]].map(([v, t]) => h("option", {value: v}, t)));
  const add = h("button", {class: "btn primary", onclick: async () => {
    try {
      const r = await api("POST", "/api/proxies", {text: ta.value, scheme: scheme.value});
      ta.value = "";
      let msg = `Добавлено: ${r.added}` + (r.duplicates ? `, повторов: ${r.duplicates}` : "");
      if (r.bad.length) msg += `. Не понял строк: ${r.bad.length} (${r.bad.slice(0, 2).join("; ")})`;
      toast(msg, r.bad.length ? "err" : "ok");
      if (r.task) startTracking(r.task);
      draw();
    } catch (e) { fail(e); } }}, "Добавить и проверить");
  page.append(h("div", {class: "card"}, h("h2", {}, "Добавить прокси", h("small", {}, "только для скачивания видео")), ta,
    h("div", {class: "row", style: {marginTop: "12px"}}, h("label", {class: "muted small"}, "Если схема не указана: "), scheme, h("div", {class: "grow"}), add)));
  const box = h("div", {style: {display: "flex", flexDirection: "column", gap: "18px"}}); page.append(box);
  async function draw() {
    let list; try { list = await api("GET", "/api/proxies"); } catch (e) { return fail(e); }
    if (!list.length) { box.replaceChildren(h("div", {class: "card empty"}, h("b", {}, "Прокси пока нет"), "Вставь список выше — программа сама проверит каждый и покажет IP и страну.")); return; }
    const row = p => h("div", {class: "acc"},
      h("span", {class: "chip " + (p.status === "ok" ? "ok" : p.status === "bad" ? "" : "warn"), style: {padding: "6px"}}, h("i")),
      h("div", {class: "grow", style: {minWidth: 0}}, h("b", {style: {wordBreak: "break-all"}}, p.url),
        h("span", {class: "muted small"}, p.status === "ok" ? `${flag(p.code)} ${[p.country, p.city].filter(Boolean).join(", ") || "страна не определена"} · IP ${p.ip} · ${p.ms} мс${p.isp ? " · " + p.isp : ""}`
          : p.status === "bad" ? p.error || "не работает" : "ещё не проверен")),
      p.active ? h("span", {class: "badge acc"}, "для скачивания") : null,
      p.status === "ok" ? h("button", {class: "btn sm" + (p.active ? "" : " primary"), onclick: async () => { try { await api("POST", `/api/proxies/${p.id}/use`, {on: !p.active}); draw(); } catch (e) { fail(e); } }}, p.active ? "Не использовать" : "Использовать") : null,
      h("button", {class: "btn sm", onclick: async () => { try { startTracking(await api("POST", "/api/proxies/check", [p.id])); } catch (e) { fail(e); } }}, "Проверить"),
      h("button", {class: "btn sm danger", onclick: async () => { try { await api("DELETE", `/api/proxies/${p.id}`); draw(); } catch (e) { fail(e); } }}, "Убрать"));
    const bad = list.filter(p => p.status === "bad").length;
    box.replaceChildren(h("div", {class: "card"}, h("div", {class: "row", style: {marginBottom: "6px"}}, h("h2", {style: {margin: 0}, class: "grow"}, `Список (${list.length})`),
      h("button", {class: "btn sm", onclick: async () => { try { startTracking(await api("POST", "/api/proxies/check")); toast("Проверяю…"); } catch (e) { fail(e); } }}, "Проверить все"),
      bad ? h("button", {class: "btn sm danger", onclick: async () => { try { await api("DELETE", "/api/proxies?bad=true"); draw(); } catch (e) { fail(e); } }}, `Убрать нерабочие (${bad})`) : null),
      list.map(row)), h("div", {class: "note"}, "Выбранный прокси используется только при скачивании видео по ссылке — для обхода региональных ограничений и блокировок сайта-источника. Загрузка на YouTube идёт напрямую."));
  }
  draw();
  S.proxyRedraw = draw;
}

/* ---------- старт ---------- */
function applyTheme(t) {
  if (t) document.documentElement.setAttribute("data-theme", t); else document.documentElement.removeAttribute("data-theme");
}
async function init() {
  try { applyTheme(localStorage.getItem("theme")); } catch (e) {}
  $("#theme").onclick = () => {
    const dark = (document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
    const n = dark ? "light" : "dark"; applyTheme(n); try { localStorage.setItem("theme", n); } catch (e) {}
  };
  $("#group").onchange = async e => { S.gid = Number(e.target.value); try { localStorage.setItem("gid", S.gid); } catch (er) {} S.cid = null; await loadClips().catch(fail); render(); };
  addEventListener("hashchange", render);
  try {
    [S.cfg, S.health] = await Promise.all([api("GET", "/api/config"), api("GET", "/api/health")]);
    await loadGroups(); await loadClips();
  } catch (e) { fail(e); }
  renderHealth(); render(); pollTasks();
  setInterval(async () => { try { S.health = await api("GET", "/api/health"); renderHealth(); } catch (e) {} }, 15000);
}
init();
