"use strict";
/* narez — веб-интерфейс. Без сборки: один файл, чистый JS. */
const $ = (s, r = document) => r.querySelector(s);
const ICONS = {
  panel: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>',
  editor: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.1 15.9M14.5 14.5 20 20M8.1 8.1 12 12"/></svg>',
  accounts: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="4"/><path d="M2 21c0-4 3-6 7-6s7 2 7 6M17 4a4 4 0 0 1 0 8M22 21c0-3-2-5-4-5.6"/></svg>',
  proxies: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18"/></svg>',
  templates: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="6" rx="1"/><rect x="3" y="13" width="8" height="8" rx="1"/><path d="M15 13h6M15 17h6M15 21h6"/></svg>',
  presets: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>',
  tiktok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3v11.5a3.5 3.5 0 1 1-3.5-3.5"/><path d="M14 3c.4 2.6 2 4.2 5 4.5"/></svg>',
  history: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></svg>',
};
const PAGES = [["panel", "Панель"], ["editor", "Редактор"], ["accounts", "Аккаунты"], ["tiktok", "TikTok"], ["presets", "Пресеты"], ["templates", "Шаблоны"], ["fillers", "Вставки"], ["history", "История"], ["proxies", "Прокси"]];
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
    const n = id === "editor" ? S.clips.length : id === "tiktok" ? (S.tt ? S.tt.clips.length : 0) : 0;
    const cnt = n ? h("span", {class: "cnt"}, n) : null;
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
  $("#group-pick").hidden = !S.groups.length || ["accounts", "history", "proxies", "editor", "templates", "presets", "fillers", "tiktok"].includes(S.page);
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
  // каждая отрисовка получает новый контейнер: «асинхронные» страницы (ждущие данные) от прошлой отрисовки дорисовывают
  // в старый, уже отсоединённый контейнер и не попадают на другую вкладку или в дубль
  const old = $("#page"), page = old.cloneNode(false);
  old.replaceWith(page);
  ({panel: pagePanel, editor: pageEditor, accounts: pageAccounts, history: pageHistory, proxies: pageProxies, templates: pageTemplates, presets: pagePresets, fillers: pageFillers, tiktok: pageTikTok})[S.page](page);
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
async function loadTikTok() {
  try { S.tt = {clips: await api("GET", "/api/tiktok/clips"), accounts: await api("GET", "/api/tiktok/accounts")}; }
  catch (e) { S.tt = S.tt || {clips: [], accounts: []}; }
  renderNav();
}
async function loadStickers() {
  try { S.stickerLib = await api("GET", "/api/stickers"); } catch (e) { S.stickerLib = S.stickerLib || {dir: "", screen_mm: 67, defaults: {}, files: []}; }
  try { S.stickerPresets = await api("GET", "/api/sticker-presets"); } catch (e) { S.stickerPresets = S.stickerPresets || []; }
}
async function loadBanners() { S.banners = await api("GET", "/api/banners"); }
async function loadFillers() { try { S.fillers = await api("GET", "/api/fillers"); } catch (e) { S.fillers = S.fillers || {dir: "", files: []}; } }
const FILLER_POS = {"": "Без вставки", bottom: "Вставка снизу", top: "Вставка сверху"};
async function loadTemplates() { try { S.templates = await api("GET", "/api/templates"); } catch (e) { S.templates = S.templates || []; } if (S.tplId && !S.templates.some(t => t.id === S.tplId)) S.tplId = null; }
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
    if (!S.tasksLoaded) { S.tasksLoaded = true; if (S.page === "panel" && list.length && !document.activeElement?.matches("input,textarea")) setTimeout(render, 0); }   // журнал прошлых запусков появляется после первой загрузки
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
  else if (t.kind === "publish") {
    const r = t.result || {}, bad = (r.errors || 0) > 0 || r.limit || (r.left || 0) > 0;
    const parts = [`загружено: ${r.published ?? 0}`];
    if (r.scheduled) parts.push(`ждут своего времени (IG/TikTok): ${r.scheduled}`);
    if (r.errors) parts.push(`ошибок: ${r.errors}`);
    if (r.left) parts.push(`осталось в очереди: ${r.left}`);
    if (r.limit) parts.push("упёрлись в дневной лимит канала");
    toast("Публикация завершена — " + parts.join(", ") + (bad ? ". Подробности — в журнале на «Панели»" : ""), bad ? "err" : undefined);
  }
  else if (t.kind === "login") toast("Вход выполнен");
  else if (t.kind === "stats") toast("Статистика обновлена");
  else if (t.kind === "import") toast(`Добавлено видео: ${t.result.added ?? 0}`);
  else if (t.kind === "proxycheck") toast(`Прокси проверены: рабочих ${t.result.ok ?? 0} из ${t.result.checked ?? 0}`);
  try { await loadGroups(); await loadClips(); await loadTikTok(); if (t.kind === "stats" || t.kind === "import" || t.kind === "publish") await loadHistory(); } catch (e) {}
  if (t.kind === "preview" && S.page === "editor") S.previewTick = Date.now();
  render();
}
function startTracking(task) { S.tracked.add(task.id); S.trackId = task.id; S.tasks = [task, ...S.tasks.filter(t => t.id !== task.id)]; renderPill(); }

/* ---------- режим нарезки ---------- */
const cutDefaults = {mode: "moments", length: 45, overlap: 2.5, audio: "ru", adSb: null, adText: null, subs: "youtube", reddit: false, trim: 15};
function cutState() {
  if (!S.cut) { try { S.cut = {...cutDefaults, ...JSON.parse(localStorage.getItem("cut") || "{}")}; } catch (e) { S.cut = {...cutDefaults}; } }
  return S.cut;
}
const cutSave = () => { try { localStorage.setItem("cut", JSON.stringify(S.cut)); } catch (e) {} };
const cutPayload = () => { const c = cutState(); return {mode: c.mode, length: +c.length || 45, overlap: c.overlap === "" ? 2.5 : +c.overlap, audio: c.audio === "original" ? "original" : "ru",
  subs_fallback: c.subs === "whisper" ? "whisper" : "youtube", reddit: c.mode === "moments" && !!c.reddit, trim_end: c.trim === "" || c.trim == null ? 15 : Math.max(0, +c.trim || 0), adcut_sb: c.adSb == null ? !!(S.cfg && S.cfg.adcut_sb) : !!c.adSb, adcut_text: c.adText == null ? !!(S.cfg && S.cfg.adcut_text) : !!c.adText}; };
function cutBox() {
  const c = cutState();
  const num = (key, step, min, max) => { const i = h("input", {type: "number", step: String(step), min: String(min), max: String(max), value: String(c[key]), style: {width: "90px"}});
    i.oninput = () => { c[key] = i.value; cutSave(); }; return i; };
  const params = h("div", {class: "row", style: {marginTop: "10px", gap: "16px", alignItems: "end"}},
    h("label", {class: "muted small"}, "Длина клипа, с", h("div", {}, num("length", 1, 5, 180))),
    h("label", {class: "muted small"}, "Нахлёст, с", h("div", {}, num("overlap", 0.5, 0, 30))),
    h("div", {class: "grow muted small"}, "Каждый следующий клип начинается с последних секунд предыдущего. Видео режется подряд, целиком, без выбора моментов — заголовки «Часть N/M» можно поменять в редакторе."));
  const redditBox = h("label", {class: "check", title: "Для видео, где диктор читает истории с Reddit (и иногда комментарии к ним). Клип собирается из 2–3 историй вместе с комментариями, длиной 60–180 секунд."},
    h("input", {type: "checkbox", onchange: e => { c.reddit = e.target.checked; cutSave(); }}), h("span", {}, "Реддит: в клипе 2–3 истории с комментариями (60–180 с)"));
  redditBox.querySelector("input").checked = !!c.reddit;
  const draw = () => { params.style.display = c.mode === "full" ? "" : "none"; redditBox.style.display = c.mode === "full" ? "none" : ""; seg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.k === c.mode)); };
  const seg = h("div", {class: "seg"}, [["moments", "Интересные моменты"], ["full", "Нарезать целиком"]].map(([k, t]) =>
    h("button", {class: "", "data-k": k, onclick: () => { c.mode = k; cutSave(); draw(); }}, t)));
  draw();
  const aud = h("select", {style: {width: "auto"}}, [["ru", "Звук: русский (если есть дубляж)"], ["original", "Звук: оригинал"]].map(([k, t]) => h("option", {value: k}, t)));
  aud.value = c.audio === "original" ? "original" : "ru"; aud.onchange = () => { c.audio = aud.value; cutSave(); };
  const subsSel = h("select", {style: {width: "auto"}, title: "Ручные субтитры YouTube (если есть) берутся всегда. Здесь — что делать, если их нет."},
    [["youtube", "Нет ручных субтитров → автоматические YouTube (быстро)"], ["whisper", "Нет ручных субтитров → распознать речь Whisper (точнее, дольше)"]].map(([k, t]) => h("option", {value: k}, t)));
  subsSel.value = c.subs === "whisper" ? "whisper" : "youtube"; subsSel.onchange = () => { c.subs = subsSel.value; cutSave(); };
  const pay = cutPayload();
  const chk = (key, label, hint, val) => { const i = h("input", {type: "checkbox"}); i.checked = val; i.onchange = () => { c[key] = i.checked; cutSave(); };
    return h("label", {class: "check", title: hint}, i, h("span", {}, label)); };
  const ads = h("div", {class: "row", style: {gap: "18px", marginTop: "10px"}},
    chk("adText", "Вырезать рекламу по тексту (локальная модель)", "Ищет в речи «промокод», «ссылка в описании», «скидка» и т. п., модель проверяет границы вставки, вырезается до нарезки", pay.adcut_text),
    chk("adSb", "Вырезать рекламу по SponsorBlock (только YouTube)", "Готовая разметка спонсорских вставок от зрителей; для свежих и русскоязычных видео разметки может не быть", pay.adcut_sb));
  const trim = h("input", {type: "number", step: "1", min: "0", max: "120", value: String(c.trim ?? 15), style: {width: "80px"}});
  trim.oninput = () => { c.trim = trim.value; cutSave(); };
  const trimRow = h("label", {class: "muted small", title: "Конец исходника часто занимают титры, музыка и прощание. 0 — ничего не отрезать."}, "Отрезать конец исходника, с ", trim);
  return h("div", {style: {marginTop: "12px"}}, h("div", {class: "row", style: {gap: "12px", flexWrap: "wrap"}}, seg, aud), h("div", {class: "row", style: {marginTop: "10px", gap: "16px", flexWrap: "wrap"}}, subsSel, trimRow), params, h("div", {style: {marginTop: "10px"}}, redditBox), ads);
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
  const ttBox = h("input", {type: "checkbox", id: "ttmode"});
  ttBox.checked = !!S.ttMode; ttBox.onchange = () => { S.ttMode = ttBox.checked; render(); };
  url.addEventListener("keydown", e => { if (e.key === "Enter") start(); });
  const startBtn = h("button", {class: "btn primary", id: "start", onclick: start}, "Нарезать");
  const tplSel = h("select", {style: {minWidth: "220px"}}, [h("option", {value: ""}, `Без шаблона — только группа «${g.name}»`),
    ...(S.templates || []).map(t => h("option", {value: t.id}, `Шаблон: ${t.name} (${t.items.map(i => i.group).join(", ")})`))]);
  if (S.tplId) tplSel.value = S.tplId;
  tplSel.onchange = () => { S.tplId = Number(tplSel.value) || null; render(); };
  const tpl = (S.templates || []).find(t => t.id === S.tplId);
  async function start() {
    try {
      const t = await api("POST", "/api/process", {url: url.value, group_id: S.gid, template_id: S.tplId || undefined, test: testBox.checked && !ttBox.checked, tiktok: ttBox.checked, ...cutPayload()});
      startTracking(t); url.value = ""; toast(ttBox.checked ? "Запустил нарезку для TikTok — клипы появятся во вкладке «TikTok»" : "Запустил нарезку"); updateRunBox();
    } catch (e) { fail(e); }
  }
  page.append(h("div", {class: "card"}, h("h2", {}, "Новое видео", h("small", {}, tpl ? `шаблон «${tpl.name}»` : `группа «${g.name}»`)),
    h("div", {class: "hero-input"}, url, startBtn),
    h("div", {class: "row", style: {margin: "10px 0", opacity: ttBox.checked ? .4 : 1, pointerEvents: ttBox.checked ? "none" : ""}}, h("span", {class: "muted small"}, "Куда:"), tplSel,
      tpl ? h("span", {class: "muted small"}, "клипы появятся в каждой группе шаблона, со своим пресетом баннеров") : null),
    cutBox(),
    h("label", {class: "check", title: "Клипы без баннера попадут во вкладку «TikTok» для ручной выкладки; группа и шаблон не используются"}, ttBox, h("span", {}, "Для ручной выкладки в TikTok (без баннера, клипы появятся во вкладке «TikTok»)")),
    h("label", {class: "check"}, testBox, h("span", {}, "Тестовый прогон — клипы сохранятся в папку тестов и не будут публиковаться"))));
  page.append(h("div", {id: "runbox"}));
  page.append(filesCard());
  const mine = S.clips.filter(c => c.group_id === S.gid), n = mine.length;
  S.sel = new Set([...(S.sel || [])].filter(id => S.clips.some(c => c.id === id && !c.test)));
  const selBtn = h("button", {class: "btn primary"}, "");
  const selCbs = [];
  const updSel = () => { selBtn.textContent = S.sel.size ? `Опубликовать выбранные (${S.sel.size})` : "Выбери клипы для публикации"; selBtn.disabled = !S.sel.size; };
  selBtn.onclick = () => publishWindow([...S.sel]);
  const pubable = mine.filter(c => !c.test);
  const setAll = on => { pubable.forEach(c => on ? S.sel.add(c.id) : S.sel.delete(c.id)); selCbs.forEach(([cb, c]) => { cb.checked = S.sel.has(c.id); }); updSel(); };
  page.append(h("div", {class: "card"}, h("h2", {}, "Готово к публикации", h("small", {}, `группа «${g.name}»`)),
    n ? [h("div", {class: "row"}, h("div", {class: "grow"}, h("b", {}, `${n} ${plural(n, "клип", "клипа", "клипов")} в очереди`),
        h("div", {class: "muted small"}, `Аккаунтов в группе: ${g.accounts.length}`)),
      h("button", {class: "btn", onclick: () => go("editor")}, "Открыть редактор"),
      h("button", {class: "btn", onclick: () => { if (!pubable.length) return toast("Тестовые клипы не публикуются", "err"); publishWindow(pubable.map(c => c.id)); }}, "Опубликовать все")),
      h("div", {class: "row", style: {marginTop: "12px", gap: "8px", flexWrap: "wrap"}},
        h("button", {class: "btn sm", onclick: () => setAll(S.sel.size < pubable.length)}, "Выбрать / снять все"),
        h("button", {class: "btn sm", title: "Отметить клипы всех групп", onclick: () => { S.clips.filter(c => !c.test).forEach(c => S.sel.add(c.id)); render(); }}, "Выбрать клипы всех групп"),
        h("div", {class: "grow"}), selBtn),
      h("div", {style: {marginTop: "12px"}}, nameToggle(() => render())),
      h("div", {style: {marginTop: "10px"}}, mine.map(c => h("div", {class: "task"},
        (() => { const cb = h("input", {type: "checkbox", disabled: c.test, title: c.test ? "Тестовые клипы не публикуются" : "Выбрать для публикации", style: {flex: "none"}});
          cb.checked = S.sel.has(c.id); cb.onchange = () => { cb.checked ? S.sel.add(c.id) : S.sel.delete(c.id); updSel(); }; selCbs.push([cb, c]); return cb; })(),
        h("div", {class: "thumb", style: Object.assign({width: "34px", height: "60px"}, c.has_thumb ? {backgroundImage: `url(/api/clips/${c.id}/thumb)`} : {})}, c.has_thumb ? "" : "нет"),
        h("div", {class: "t"}, h("b", {}, clipLabel(c)), h("span", {class: "muted small"}, `${Math.round(c.duration)} с${c.score ? ` · ★ ${c.score}/10` : ""}${c.test ? " · тест" : ""}${c.banner ? " · баннер" : ""}`)),
        h("button", {class: "btn sm", onclick: () => ask("Переименовать клип", "Название, с которым клип будет опубликован", c.title, "Сохранить", async v => {
          try { await api("PATCH", `/api/clips/${c.id}`, {title: v}); await loadClips(); render(); } catch (e) { fail(e); } })}, "Переименовать"),
        h("button", {class: "btn sm", onclick: () => { S.cid = c.id; go("editor"); }}, "Править"),
        h("button", {class: "btn sm danger", onclick: () => confirmBox("Удалить клип?", `«${c.title}» будет убран из очереди, файл удалён с диска.`, "Удалить", async () => {
          try { await api("DELETE", `/api/clips/${c.id}`); await loadClips(); toast("Клип удалён"); render(); } catch (e) { fail(e); } })}, "Удалить"))))]
      : h("div", {class: "muted"}, "Пока пусто — вставь ссылку выше.")));
  updSel();
  const fin = S.tasks.filter(t => t.status !== "running"), dayAgo = Date.now() / 1000 - 86400;
  const fresh = fin.filter(t => (t.finished || t.started) >= dayAgo), older = fin.filter(t => (t.finished || t.started) < dayAgo);
  const hist = S.tasksMore ? fin : fresh;
  if (fin.length) page.append(h("div", {class: "card"}, h("h2", {}, "Журнал задач", h("small", {}, "за последние 24 часа · нажми на задачу — откроется полный лог · записи старше 7 дней удаляются")),
    hist.length ? null : h("div", {class: "muted small"}, "За последние 24 часа задач не было."),
    hist.map(t => {
    const r = t.result || {}, warn = t.status === "done" && (r.errors || r.limit || r.left);
    const when = t.started ? new Date(t.started * 1000).toLocaleString("ru-RU", {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"}) : "";
    const box = h("pre", {class: "log", style: {display: "none", marginTop: "8px", maxHeight: "320px", overflow: "auto", whiteSpace: "pre-wrap"}});
    const sum = t.kind === "publish" && t.status === "done" ? ` · загружено ${r.published ?? 0}${r.errors ? `, ошибок ${r.errors}` : ""}${r.left ? `, осталось ${r.left}` : ""}${r.limit ? ", лимит канала" : ""}` : "";
    const row = h("div", {class: "task", style: {cursor: "pointer", flexWrap: "wrap"}, onclick: async () => {
      if (box.style.display === "none") {
        box.style.display = "block"; box.textContent = "Загружаю…";
        try { const d = await api("GET", `/api/tasks/${t.id}?after=0`); box.textContent = d.log.join("\n") || "(лог пуст)"; box.scrollTop = box.scrollHeight; } catch (e) { box.textContent = e.message; }
      } else box.style.display = "none";
    }}, h("div", {class: "t"}, h("b", {}, t.title), h("span", {class: "muted small"}, `${when}${sum}${t.status === "error" ? " · " + t.error : ""}`)),
      h("span", {class: "badge " + (t.status === "done" ? (warn ? "warn" : "ok") : "err")}, t.status === "done" ? (warn ? "с замечаниями" : "готово") : "ошибка"));
    return h("div", {}, row, box);
  }), !S.tasksMore && older.length ? h("button", {class: "btn sm", style: {marginTop: "10px"}, onclick: () => { S.tasksMore = true; render(); }}, `Увидеть больше (${older.length})`)
    : S.tasksMore && older.length ? h("button", {class: "btn sm", style: {marginTop: "10px"}, onclick: () => { S.tasksMore = false; render(); }}, "Свернуть") : null));
  updateRunBox();
}
function updateRunBox() {
  const box = $("#runbox"); if (!box) return;
  const t = S.tasks.find(x => x.id === S.trackId) || S.tasks.find(x => x.status === "running");
  const problem = t && t.status !== "running" && (t.status === "error" || (t.result && (t.result.errors || t.result.limit || t.result.left)));
  S.hidden = S.hidden || new Set();
  if (!t || S.hidden.has(t.id) || (t.status !== "running" && !problem && Date.now() / 1000 - t.finished > 60 && t.kind !== "process")) { box.replaceChildren(); return; }
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
  box.replaceChildren(h("div", {class: "card"}, h("h2", {}, t.title, h("small", {}, t.status === "running" ? "выполняется…" : t.status === "done" ? (problem ? "завершено с замечаниями" : "готово") : "ошибка"),
    t.status !== "running" ? h("button", {class: "btn sm", style: {marginLeft: "auto"}, onclick: () => { S.hidden.add(t.id); updateRunBox(); }}, "Скрыть") : null), body));
  if (stick) lg.scrollTop = lg.scrollHeight;
}
const platName = p => (PLAT[p] || ["", p])[1];
const fmtDur = sec => { sec = Math.round(sec); return sec >= 60 ? `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}` : `${sec} с`; };
/* окно публикации выбранных клипов: порядок выхода, промежуток, время начала */
function publishWindow(ids) {
  const cfg0 = S.cfg || {};
  let order = ids.filter(id => S.clips.some(c => c.id === id));
  if (!order.length) return toast("Нет клипов для публикации", "err");
  const d = $("#dlg");
  const unit = h("select", {style: {width: "110px"}}, [["60", "часов"], ["1", "минут"]].map(([v, t]) => h("option", {value: v}, t)));
  const amount = h("input", {type: "number", min: "0", step: "any", value: "1", style: {width: "90px"}});
  const startMode = h("select", {}, [["now", "Первый — сразу"], ["at", "Первый — в указанное время"]].map(([v, t]) => h("option", {value: v}, t)));
  const startAt = h("input", {type: "datetime-local", value: isoToInput(new Date(Date.now() + 3600e3).toISOString())});
  const chips = h("div", {class: "row", style: {gap: "6px", flexWrap: "wrap"}}, [[0, "Все сразу"], [15, "15 мин"], [30, "30 мин"], [60, "1 ч"], [120, "2 ч"], [180, "3 ч"], [360, "6 ч"]].map(([m, t]) =>
    h("button", {class: "btn sm", type: "button", onclick: () => { if (m >= 60) { unit.value = "60"; amount.value = m / 60; } else { unit.value = "1"; amount.value = m; } draw(); }}, t)));
  const list = h("div", {style: {maxHeight: "46vh", overflow: "auto", margin: "12px 0", border: "1px solid var(--border)", borderRadius: "12px"}});
  const hint = h("div", {class: "note"}), err = h("div", {class: "note warn", style: {display: "none"}});
  const PRIV = {public: "Публично", unlisted: "Доступно по ссылке", private: "Закрытое"};
  const privSel = h("select", {}, Object.entries(PRIV).map(([v, t]) => h("option", {value: v}, t + (v === cfg0.youtube_privacy ? " (как в config.toml)" : ""))));
  let savedPriv = ""; try { savedPriv = localStorage.getItem("pubPrivacy") || ""; } catch (e) {}
  privSel.value = savedPriv in PRIV ? savedPriv : "public";   // клипы с временем YouTube всё равно сам откроет публично — первый должен быть таким же
  privSel.addEventListener("change", () => { try { localStorage.setItem("pubPrivacy", privSel.value); } catch (e) {} });
  const FSEL = [["keep", "Как в редакторе (у каждого клипа своё)"], ["", "Без вставки"], ["bottom", "Вставка снизу (клип сверху)"], ["top", "Вставка сверху (клип снизу)"]];
  const fillSel = h("select", {}, FSEL.map(([v, t]) => h("option", {value: v}, t)));
  let savedFill = "keep"; try { savedFill = localStorage.getItem("pubFiller") || "keep"; } catch (e) {}
  fillSel.value = FSEL.some(x => x[0] === savedFill) ? savedFill : "keep";
  const fillNote = h("div", {class: "hint"});
  const fillUpd = () => { try { localStorage.setItem("pubFiller", fillSel.value); } catch (e) {}
    const n = ((S.fillers && S.fillers.files) || []).length;
    fillNote.textContent = fillSel.value === "keep" || fillSel.value === "" ? "" : n ? `Для каждого клипа случайно выберется одно из ${n} видео (вкладка «Вставки»). Название клипа напишется в полоске.` : "В папке вставок нет видео — публикация с вставкой не получится. Загрузи ролики во вкладке «Вставки»."; };
  fillSel.addEventListener("change", fillUpd);
  if (!S.fillers) loadFillers().then(fillUpd); else fillUpd();
  const intervalMin = () => Math.max(0, Number(amount.value) || 0) * Number(unit.value);
  const startDate = () => startMode.value === "at" && startAt.value ? new Date(startAt.value) : null;
  const whenOf = i => { const iv = intervalMin(), st = startDate();
    if (st) return new Date(st.getTime() + iv * i * 60e3);
    return iv && i > 0 ? new Date(Date.now() + iv * i * 60e3) : null; };
  function draw() {
    startAt.style.display = startMode.value === "at" ? "" : "none";
    const iv = intervalMin();
    list.replaceChildren(...order.map((id, i) => {
      const c = S.clips.find(x => x.id === id), g = S.groups.find(x => x.id === c.group_id);
      const w = whenOf(i), nb = (c.banners || []).length;
      const mv = (txt, dis, fn) => h("button", {class: "btn sm", type: "button", disabled: dis, style: {padding: "2px 8px"}, onclick: fn}, txt);
      return h("div", {class: "task", style: {padding: "8px 10px", gap: "10px", flexWrap: "wrap"}},
        h("div", {class: "muted", style: {width: "22px", textAlign: "right", flex: "none"}}, i + 1),
        h("div", {class: "thumb", style: Object.assign({width: "34px", height: "60px", flex: "none"}, c.has_thumb ? {backgroundImage: `url(/api/clips/${c.id}/thumb)`} : {})}, c.has_thumb ? "" : "нет"),
        h("div", {class: "t", style: {minWidth: "160px", flex: "1"}}, h("b", {title: clipLabel(c)}, clipLabel(c)),
          h("span", {class: "muted small"}, `${fmtDur(c.duration)} · ` + (g ? `${g.name}: ${g.accounts.map(a => platName(a.platform)).join(", ") || "нет аккаунтов"}` : "без группы"))),
        h("span", {class: "badge " + (nb ? "acc" : "")}, nb ? (nb > 1 ? `баннеры ×${nb}` : "баннер") : "без баннера"),
        c.filler && c.filler.position ? h("span", {class: "badge acc"}, c.filler.position === "top" ? "вставка ↑" : "вставка ↓") : null,
        h("div", {class: "small", style: {width: "128px", flex: "none", textAlign: "right", color: w ? "var(--text)" : "var(--ok)"}}, w ? fmtLocal(w.toISOString()) : "сразу"),
        mv("▲", i === 0, () => { [order[i - 1], order[i]] = [order[i], order[i - 1]]; draw(); }),
        mv("▼", i === order.length - 1, () => { [order[i + 1], order[i]] = [order[i], order[i + 1]]; draw(); }),
        mv("✕", false, () => { order.splice(i, 1); if (!order.length) { d.close(); return; } draw(); }));
    }));
    const total = order.length, last = whenOf(total - 1);
    hint.className = "note";
    hint.textContent = iv === 0 && !startDate() ? "Все клипы уйдут сразу, один за другим."
      : `Интервал ${iv ? (iv % 60 === 0 ? iv / 60 + " ч" : iv + " мин") : "0"}${last ? `; последний клип выйдет ${fmtLocal(last.toISOString())}` : ""}.`;
    err.style.display = "none";
  }
  [unit, amount, startMode, startAt].forEach(el => el.addEventListener("input", draw));
  const go1 = h("button", {class: "btn primary", onclick: async () => {
    const iv = intervalMin(), st = startDate();
    const fail1 = m => { err.textContent = m; err.style.display = ""; };
    if (iv > 0 && iv < 5) return fail1("Промежуток между выходами: 0 (все сразу) или не меньше 5 минут.");
    if (startMode.value === "at" && (!st || st.getTime() < Date.now() + 5 * 60e3)) return fail1("Время начала должно быть минимум на 5 минут позже текущего.");
    go1.disabled = true;
    try {
      const t = await api("POST", "/api/publish/batch", {clip_ids: order, start: st ? st.toISOString() : "", interval_minutes: iv, privacy: privSel.value, ...(fillSel.value === "keep" ? {} : {filler: {position: fillSel.value, file: ""}})});
      d.close(); S.sel = new Set(); startTracking(t); toast("Публикую…"); go("panel"); if (S.page === "panel") render();
    } catch (e) { go1.disabled = false; fail1(e.message || String(e)); }
  }}, "Опубликовать");
  d.replaceChildren(h("div", {}, h("h3", {}, `Публикация: ${order.length} ${plural(order.length, "клип", "клипа", "клипов")}`),
    h("div", {class: "muted small"}, "Порядок выхода сверху вниз. Каждый клип уйдёт на все аккаунты своей группы."),
    list,
    h("div", {class: "row", style: {gap: "10px", flexWrap: "wrap", alignItems: "center"}}, h("b", {}, "Промежуток между выходами:"), amount, unit),
    h("div", {style: {margin: "8px 0"}}, chips),
    h("div", {class: "row", style: {gap: "10px", flexWrap: "wrap", alignItems: "center", marginBottom: "10px"}}, startMode, startAt),
    h("div", {class: "row", style: {gap: "10px", flexWrap: "wrap", alignItems: "center", marginBottom: "10px"}}, h("b", {}, "YouTube, клипы без времени (выходят сразу):"), privSel,
      h("span", {class: "muted small"}, "Клипы с временем YouTube держит закрытыми и сам открывает публично в назначенный момент.")),
    h("div", {class: "row", style: {gap: "10px", flexWrap: "wrap", alignItems: "center", marginBottom: "4px"}}, h("b", {}, "Видео-вставка:"), fillSel),
    fillNote, hint, err),
    h("div", {class: "acts"}, h("button", {class: "btn ghost", onclick: () => d.close()}, "Отмена"), go1));
  d.style.width = "min(820px, calc(100vw - 32px))";
  d.addEventListener("close", () => { d.style.width = ""; }, {once: true});
  draw();
  d.showModal();
}
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
      const payload = {folder: tab, name: x.name, group_id: S.gid, template_id: kind === "slice" ? (S.tplId || undefined) : undefined, test: !!S.testMode, ...cutPayload()};
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
function fillerCard(c) {
  const files = (S.fillers && S.fillers.files) || [];
  const cur = c.filler || {position: "", file: ""};
  const pos = h("select", {}, Object.entries(FILLER_POS).map(([v, t]) => h("option", {value: v}, t)));
  const file = h("select", {}, [h("option", {value: ""}, "Случайное видео"), ...files.map(f => h("option", {value: f.name}, f.name))]);
  pos.value = cur.position || ""; file.value = cur.file || "";
  const hint = h("div", {class: "hint"});
  const upd = () => { file.disabled = !pos.value;
    hint.textContent = !files.length ? "В папке вставок пока нет видео — открой вкладку «Вставки»."
      : pos.value === "bottom" ? "Клип сверху, под ним полоска с названием, ниже — видео-вставка."
      : pos.value === "top" ? "Сверху полоска с названием, под ней видео-вставка, клип внизу."
      : "Клип остаётся на весь экран."; };
  const send = async () => { upd(); try { await api("PATCH", `/api/clips/${c.id}`, {filler: {position: pos.value, file: file.value}}); await loadClips(); toast("Вставка сохранена"); } catch (e) { fail(e); } };
  pos.onchange = send; file.onchange = send; upd();
  return h("div", {class: "card"}, h("h2", {}, "Видео-вставка", h("small", {}, "собирается при публикации; «Точный предпросмотр» покажет результат")),
    h("div", {class: "field"}, h("label", {}, "Расположение"), pos), h("div", {class: "field"}, h("label", {}, "Какое видео"), file), hint);
}
function stickerCard(c) {
  const L = S.stickerPresets || [];
  const sel = h("select", {style: {width: "100%"}}, L.length ? L.map(p => h("option", {value: p.id}, p.name)) : [h("option", {value: ""}, "— пресетов стикеров пока нет —")]);
  if (S.stickerPresetId && L.some(p => p.id === S.stickerPresetId)) sel.value = S.stickerPresetId;
  sel.onchange = () => { S.stickerPresetId = Number(sel.value) || null; };
  const go_ = async (ids, text) => {
    const p = L.find(x => x.id === Number(sel.value)); if (!p) return toast("Сначала выбери пресет стикеров", "err");
    try { await api("POST", `/api/sticker-presets/${p.id}/apply`, {clip_ids: ids}); await loadClips(); toast(`Стикеры «${p.name}» добавлены: ${text}`); render(); } catch (e) { fail(e); }
  };
  const cur = c.stickers;
  return h("div", {class: "card"}, h("h2", {}, "Стикеры", h("small", {}, "мелкие картинки поверх клипа; собираются при публикации")),
    cur ? h("div", {class: "note", style: {marginBottom: "10px"}}, `Сейчас: «${cur.preset || "стикеры"}», штук: ${cur.items.length}, размер ≈ ${cur.size_mm} мм. Повторное применение расставит их заново.`) : null,
    h("div", {class: "field"}, sel),
    h("div", {class: "sticky-actions"},
      h("button", {class: "btn", disabled: !L.length, onclick: () => go_([c.id], "этот клип")}, "Применить к клипу"),
      h("button", {class: "btn", disabled: !L.length, onclick: () => go_(S.clips.filter(x => x.group_id === c.group_id).map(x => x.id), `все клипы группы «${c.group}»`)}, "Ко всем клипам группы"),
      h("button", {class: "btn", disabled: !cur, onclick: async () => { try { await api("PATCH", `/api/clips/${c.id}`, {stickers: {}}); await loadClips(); toast("Стикеры убраны"); render(); } catch (e) { fail(e); } }}, "Убрать стикеры"),
      h("button", {class: "btn", onclick: () => { S.presetTab = "stickers"; try { localStorage.setItem("presetTab", "stickers"); } catch (e) {} go("presets"); }}, "Пресеты стикеров…")));
}
function pageEditor(page) {
  if (!S.fillers) loadFillers().then(() => { if (S.page === "editor") render(); });
  if (!S.groups.length || !S.clips.length) {
    page.append(h("div", {class: "card empty"}, h("b", {}, "Нет клипов для редактирования"), "Нарежь видео на вкладке «Панель»."));
    return;
  }
  const list = h("div", {class: "clips"}, S.clips.map(c => h("button", {class: "clip" + (c.id === S.cid ? " on" : ""), onclick: () => { S.cid = c.id; render(); }},
    h("div", {class: "thumb", style: c.has_thumb ? {backgroundImage: `url(/api/clips/${c.id}/thumb)`} : {}}, c.has_thumb ? "" : "нет"),
    h("div", {class: "info"}, h("b", {}, clipLabel(c)), h("div", {class: "tags"},
      S.groups.length > 1 ? h("span", {class: "badge acc"}, c.group) : null,
      h("span", {class: "badge"}, Math.round(c.duration) + " с"), c.score ? h("span", {class: "badge ok", title: "Оценка модели: насколько клип цепляет (1–10)"}, "★ " + c.score) : null, c.banner ? h("span", {class: "badge acc"}, "баннер") : null, c.test ? h("span", {class: "badge warn"}, "тест") : null,
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
  let cards = [];
  const title = h("input", {type: "text", maxlength: 200, value: c.title});
  const desc = h("textarea", {}); desc.value = c.description;
  const tags = h("input", {type: "text", placeholder: "#тег1 #тег2"}); tags.value = c.tags;
  const when = h("input", {type: "datetime-local", value: isoToInput(c.publish_at)});
  const bnBox = h("div", {});
  const video = h("video", {controls: true, playsinline: true, preload: "auto", poster: `/api/clips/${c.id}/thumb`, src: `/api/clips/${c.id}/video?t=${S.previewTick || 0}`});
  video.addEventListener("loadedmetadata", () => { if (video.currentTime === 0) video.currentTime = 0.1; });
  video.addEventListener("error", () => toast("Браузер не смог загрузить видео клипа (превью-кадр при этом виден).", "err"));
  const frame = h("div", {class: "frame"}, video);
  const ov = h("div", {class: "bn-ov"}); frame.append(ov);
  const stkOv = h("div", {class: "stk-ov"}, ((c.stickers && c.stickers.items) || []).map(it => h("img", {class: "stk", src: "/api/stickers/file?name=" + encodeURIComponent(it.file), alt: "",
    style: {left: (it.x * 100) + "%", top: (100 - it.y) + "%", width: ((c.stickers.size_mm || 4) / ((S.stickerLib && S.stickerLib.screen_mm) || 67) * 100) + "%", transform: `translate(-50%,-50%) rotate(${it.angle || 0}deg)`}})));
  frame.append(stkOv);
  if (!document.getElementById("nz-key-svg")) {   // фильтр «зелёный → прозрачный» для живого предпросмотра (точный результат — в рендере)
    const wrap = document.createElement("div"); wrap.id = "nz-key-svg"; wrap.style.cssText = "position:absolute;width:0;height:0;overflow:hidden";
    wrap.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0"><filter id="nz-key" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">'
      + '<feColorMatrix in="SourceGraphic" type="matrix" values="-1 1 0 0 0  -1 1 0 0 0  -1 1 0 0 0  0 0 0 0 1" result="gr"/>'      // насколько зелёного больше, чем красного
      + '<feColorMatrix in="SourceGraphic" type="matrix" values="0 1 -1 0 0  0 1 -1 0 0  0 1 -1 0 0  0 0 0 0 1" result="gb"/>'      // ... чем синего
      + '<feComposite in="gr" in2="gb" operator="arithmetic" k1="1" k2="0" k3="0" k4="0" result="prod"/>'
      + '<feColorMatrix in="prod" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  -4 0 0 0 1" result="mask"/>'                // зелёное → прозрачное
      + '<feComposite in="SourceGraphic" in2="mask" operator="in"/></filter></svg>';
    document.body.append(wrap);
  }
  let live = true;   // живой предпросмотр: баннер рисуется поверх плеера (приблизительно); «точный» — настоящий рендер ffmpeg
  const pvBtn = h("button", {class: "btn", onclick: preview}, "Точный предпросмотр (рендер)");
  async function preview() {
    if (!live) {   // вернуться к быстрому предпросмотру
      live = true; stkOv.style.display = ""; video.src = `/api/clips/${c.id}/video?t=${Date.now()}`; pvBtn.textContent = "Точный предпросмотр (рендер)"; return;
    }
    try {
      await save(true);
      const curClip = S.clips.find(x => x.id === c.id) || c;
      if (!specsNow().length && !(curClip.filler && curClip.filler.position) && !curClip.stickers) return toast("Ни баннер, ни видео-вставка, ни стикеры не выбраны", "err");
      pvBtn.disabled = true; pvBtn.textContent = "Собираю предпросмотр…";
      const t = await api("POST", `/api/clips/${c.id}/preview`); startTracking(t);
      for (;;) {
        await new Promise(r => setTimeout(r, 1000));
        const s = await api("GET", `/api/tasks/${t.id}`);
        if (s.status === "running") continue;
        if (s.status === "error") throw new Error(s.error);
        break;
      }
      live = false; hideOv(); stkOv.style.display = "none";
      video.src = `/api/clips/${c.id}/video?variant=final&t=${Date.now()}`; video.play().catch(() => {});
      pvBtn.disabled = false; pvBtn.textContent = "Вернуться к быстрому предпросмотру"; return;
    } catch (e) { fail(e); }
    pvBtn.disabled = false; pvBtn.textContent = "Точный предпросмотр (рендер)";
  }

  /* ---- баннеры клипа: каждый — отдельная карточка со своими настройками; живой предпросмотр поверх плеера ---- */
  const GW = 1080, GH = 1920, MAXB = cfg.max_banners || 5;
  function newCard(sel, choosing = false) { return {sel, F: {}, ovEl: null, ovName: "", frozen: null, frozenDone: false, info: h("div", {class: "note"}), infoT: 0, choosing}; }
  cards = (c.banners || []).map(sp => newCard({...sp}));
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
  function hideCard(card) { if (card.ovEl) { card.ovEl.style.display = "none"; if (card.ovEl.pause) card.ovEl.pause(); } }
  function hideOv() { cards.forEach(hideCard); duck(1); }
  // видимая часть видео-баннера: при хромакее с тем же ключевым цветом, что нашёл сервер, пустой фон по краям обрезается
  function visBox(s, b) {
    return b.kind === "video" && s.chroma && b.box && b.key && String(s.chroma_color).toLowerCase() === b.key.toLowerCase() ? b.box : null;
  }
  function bannerGeom(s, b) {
    const bx = visBox(s, b);
    const aspect = bx ? (b.width * (bx[2] - bx[0])) / (b.height * (bx[3] - bx[1])) : b.width / b.height;
    let w = GW * s.width_percent / 100;
    if (b.kind === "video") w = Math.max(w, Math.sqrt((cfg.min_area_percent / 100) * GW * GH * aspect));
    w = Math.min(w, GW, GH * aspect);
    let h = w / aspect;
    // меньше минимальной площади, а по ширине уже упёрлись в кадр — растягиваем по высоте непропорционально (как на сервере)
    if (b.kind === "video" && w * h < (cfg.min_area_percent / 100) * GW * GH) h = Math.min(GH, Math.max(h, (cfg.min_area_percent / 100) * GW * GH / w));
    return {w, h};
  }
  function bannerDur(s, b) {
    return b.kind === "video" ? (s.duration > 0 ? Math.min(s.duration, b.duration) : b.duration) : (s.duration > 0 ? s.duration : cfg.banner_defaults.image.duration || 6);
  }
  const startOf = s => s.start_mode === "percent" ? c.duration * Math.min(Math.max(s.start_percent || 0, 0), 100) / 100 : s.start;
  function ensureOv(card, b) {
    if (card.ovEl && card.ovName === b.name) return;
    if (card.ovEl) card.ovEl.remove();
    card.ovName = b.name; card.frozen = null;
    const src = "/api/banners/file?name=" + encodeURIComponent(b.name);
    card.ovEl = b.kind === "video" ? h("video", {src, playsinline: true, preload: "auto"}) : h("img", {src, alt: ""});
    card.ovEl.style.cssText = "position:absolute;display:none;height:auto";
    ov.append(card.ovEl);
  }
  video.addEventListener("seeked", () => cards.forEach(k => { k.frozen = null; k.frozenDone = !!(k.sel && video.currentTime > startOf(specOf(k)) + 0.3); }));
  video.addEventListener("play", () => cards.forEach(k => { if (k.frozen) { k.frozen = null; k.frozenDone = true; } }));
  function tickCard(card) {
    const b = card.sel && !card.choosing && S.banners.find(x => x.name === card.sel.name);
    if (!live || !b) { hideCard(card); return 1; }
    ensureOv(card, b);
    const s = specOf(card), dur = bannerDur(s, b), t0 = startOf(s), t = video.currentTime;
    const now = performance.now();
    let show = false, bt = 0, level = 1;
    if (s.pause) {
      if (!card.frozen && !video.paused && !card.frozenDone && t >= t0 && t < t0 + 0.6) { card.frozen = {at: now}; video.pause(); }
      if (card.frozen) {
        bt = (now - card.frozen.at) / 1000; show = true;
        if (bt >= dur) { card.frozen = null; card.frozenDone = true; show = false; video.play().catch(() => {}); }
      } else if (video.paused && t >= t0 && t < t0 + 0.05) { show = true; bt = 0; }
    } else {
      bt = t - t0; show = t >= t0 && t < t0 + Math.min(dur, c.duration - t0);
      if (show && b.has_audio) level = Math.max(s.duck_percent / 100, 0.001);
    }
    if (!show) { hideCard(card); return 1; }
    const g = bannerGeom(s, b);
    const y = s.position === "top" ? s.margin : s.position === "bottom" ? GH - g.h - s.margin : (GH - g.h) / 2;
    const e = card.ovEl;
    e.style.display = "block";
    e.style.left = ((GW - g.w) / 2 / GW * 100) + "%"; e.style.width = (g.w / GW * 100) + "%"; e.style.height = (g.h / GH * 100) + "%"; e.style.objectFit = "fill"; e.style.top = (y / GH * 100) + "%";
    if (b.kind === "video") {
      const bx = visBox(s, b);
      e.style.objectViewBox = bx ? `inset(${bx[1] * 100}% ${(1 - bx[2]) * 100}% ${(1 - bx[3]) * 100}% ${bx[0] * 100}%)` : "none";
      e.style.filter = s.chroma ? "url(#nz-key)" : "none";   // приблизительное вырезание зелёного в живом предпросмотре
    }
    if (b.kind === "video") {
      const playing = card.frozen || !video.paused;
      if (e.volume !== userVol) e.volume = userVol;
      e.muted = video.muted;
      if (Math.abs(e.currentTime - bt) > 0.35) e.currentTime = Math.max(0, bt);
      if (playing && e.paused) e.play().catch(() => {});
      if (!playing && !e.paused) e.pause();
    }
    return level;
  }
  function tick() {
    if (!document.contains(video)) return;   // редактор перерисован — цикл останавливается
    requestAnimationFrame(tick);
    let level = 1;
    for (const card of cards) level = Math.min(level, tickCard(card));
    duck(level);
  }
  requestAnimationFrame(tick);
  // параметры карточки: из полей формы, а пока форма не построена — как сохранены
  function specOf(card) {
    const F = card.F, s = card.sel;
    if (!F.start || !F.start.isConnected) return {...s};
    return {name: s.name, start: +F.start.value || 0, start_mode: F.mode.value, start_percent: +F.pct.value || 0, duration: +F.duration.value,
      width_percent: +F.width.value, position: F.pos.value, margin: +F.margin.value, duck_percent: +F.duck.value, pause: !!F.pause.checked,
      ...(F.chroma ? {chroma: !!F.chroma.checked, chroma_color: F.chromaColor.value} : {})};
  }
  const specsNow = () => cards.filter(k => k.sel && !k.choosing).map(specOf);
  const syncCards = () => cards.forEach(k => { if (k.sel && !k.choosing) k.sel = specOf(k); });
  function bannerPresetRow() {
    const list = S.presets.filter(p => (p.banners || []).length);
    const sel = h("select", {style: {flex: "1", minWidth: "160px"}}, [h("option", {value: ""}, list.length ? "— пресет баннеров —" : "— пресетов баннера пока нет —"),
      ...list.map(p => h("option", {value: p.id}, p.name + (p.banners.length > 1 ? ` (${p.banners.length} баннера)` : "")))]);
    if (S.bpresetId && list.some(p => p.id === S.bpresetId)) sel.value = S.bpresetId;
    sel.onchange = () => {
      S.bpresetId = Number(sel.value) || null;
      const p = list.find(x => x.id === S.bpresetId); if (!p) return;
      const gone = p.banners.find(sp => !S.banners.some(b => b.name === sp.name));
      if (gone) return toast(`Файла баннера «${gone.name}» из пресета больше нет — загрузи его заново`, "err");
      cards = p.banners.map(sp => newCard({...sp})); drawBanner(); toast(`Пресет «${p.name}» применён к баннерам (не забудь «Сохранить»)`);
    };
    return h("div", {class: "row", style: {marginBottom: "12px"}}, sel,
      h("button", {class: "btn sm", disabled: !specsNow().length, onclick: () => {
        const name = h("input", {type: "text", placeholder: "Например: «Реклама казино — внизу»"});
        const cur = list.find(x => x.id === S.bpresetId); if (cur) name.value = cur.name;
        dialog(h("div", {}, h("h3", {}, "Сохранить пресет баннеров"),
          h("div", {class: "muted small", style: {marginBottom: "10px"}}, "Сохранятся все баннеры клипа и все их поля: файл, начало (секунды или %), длительность, ширина, положение, отступ, громкость, пауза. Пресет с тем же названием будет перезаписан."), name),
          [{text: "Отмена", cls: "ghost"}, {text: "Сохранить", cls: "primary", fn: async () => {
            try {
              const old = S.presets.find(x => x.name === name.value.trim());
              const r = await api("POST", "/api/presets", {name: name.value, banners: specsNow(), tags: old ? old.tags : "", footer: old ? old.footer : ""});
              S.bpresetId = r.id; await save(true); toast("Пресет баннеров сохранён"); render();
            } catch (e) { fail(e); } }}]);
        setTimeout(() => name.focus(), 50); }}, "Сохранить как пресет"),
      h("button", {class: "btn sm danger", disabled: !S.bpresetId || !list.some(p => p.id === S.bpresetId), onclick: () => {
        const p = list.find(x => x.id === S.bpresetId); if (!p) return;
        confirmBox("Удалить пресет?", `«${p.name}» будет удалён (баннеры клипов, к которым он применён, не изменятся).`, "Удалить", async () => {
          try { await api("DELETE", `/api/presets/${p.id}`); S.bpresetId = null; await save(true); render(); } catch (e) { fail(e); } }); }}, "Удалить пресет"));
  }
  function libFor(card) {
    return h("div", {class: "lib"}, S.banners.map(b => h("div", {class: "bn" + (card.sel && card.sel.name === b.name ? " on" : ""), title: b.name, onclick: () => pick(card, b)},
      h("div", {class: "pv"}, b.kind === "image" ? h("img", {src: "/api/banners/file?name=" + encodeURIComponent(b.name), alt: ""}) : "▶"),
      h("span", {}, b.name), h("span", {}, b.kind === "video" ? `${b.duration} с${b.has_audio ? " ♪" : ""}` : "картинка"))),
      h("label", {class: "bn add"}, "+ файл", h("input", {type: "file", hidden: true, accept: "image/*,video/*", onchange: e => upload(e, card)})));
  }
  function cardEl(card, idx) {
    const head = (extra) => h("div", {class: "row", style: {justifyContent: "space-between", marginBottom: "10px"}},
      h("b", {}, `Баннер ${idx + 1}`, card.sel ? h("span", {class: "muted small", style: {marginLeft: "8px", fontWeight: "400"}}, card.sel.name) : null),
      h("div", {class: "row", style: {gap: "6px"}}, extra,
        h("button", {class: "btn sm danger", onclick: () => { syncCards(); cards.splice(idx, 1); hideOv(); drawBanner(); }}, "Убрать")));
    const box = h("div", {class: "bncard"});
    if (!card.sel || card.choosing) {
      box.append(head(card.sel ? h("button", {class: "btn sm", onclick: () => { card.choosing = false; drawBanner(); }}, "Отмена") : null),
        h("div", {class: "muted small", style: {marginBottom: "8px"}}, "Выбери файл баннера:"), libFor(card));
      return box;
    }
    const b = S.banners.find(x => x.name === card.sel.name), s = card.sel, F = card.F;
    const isVideo = b && b.kind === "video";
    const info = () => cardInfo(card);
    const mk = (id, label, val, min, max, step, hint) => { F[id] = h("input", {type: "number", min, max, step, value: val, oninput: info}); return h("div", {class: "field"}, h("label", {}, label), F[id], hint ? h("div", {class: "hint"}, hint) : null); };
    F.pos = h("select", {onchange: info}, [["center", "По центру"], ["top", "Сверху"], ["bottom", "Снизу"]].map(([v, t]) => h("option", {value: v}, t)));
    F.pos.value = s.position || "center";
    F.pause = h("input", {type: "checkbox", onchange: info}); F.pause.checked = !!s.pause;
    if (isVideo) {
      F.chroma = h("input", {type: "checkbox", onchange: () => { drawKeyHint(); info(); }}); F.chroma.checked = !!s.chroma;
      F.chromaColor = h("input", {type: "color", value: s.chroma_color || b.key || "#00ff00", style: {width: "40px", height: "28px", padding: "0", border: "none", background: "none"}, onchange: info});
    }
    // начало: в секундах от начала клипа или в процентах длины клипа
    F.mode = h("select", {onchange: () => { showMode(); info(); }}, [["seconds", "В секундах"], ["percent", "В % видео"]].map(([v, t]) => h("option", {value: v}, t)));
    F.mode.value = s.start_mode === "percent" ? "percent" : "seconds";
    const startField = mk("start", "Начало, с", s.start ?? 3, 0, 3600, 0.5);
    const pctField = mk("pct", "Начало, % видео", s.start_percent ?? 10, 0, 99, 1, `клип ${Math.round(c.duration)} с: 10% ≈ ${(c.duration * 0.1).toFixed(1)} с`);
    const drawKeyHint = () => { if (F.chromaColor) F.chromaColor.disabled = !F.chroma.checked; };
    setTimeout(drawKeyHint, 0);
    const showMode = () => { startField.style.display = F.mode.value === "seconds" ? "" : "none"; pctField.style.display = F.mode.value === "percent" ? "" : "none"; };
    showMode();
    box.append(head(h("button", {class: "btn sm", onclick: () => { syncCards(); card.choosing = true; drawBanner(); }}, "Сменить файл")),
      h("div", {class: "grid3"},
        h("div", {class: "field"}, h("label", {}, "Начало"), F.mode), startField, pctField),
      h("div", {class: "grid3"},
        isVideo
          ? mk("duration", "Длительность, с", Math.round(((s.duration > 0 ? Math.min(s.duration, b.duration) : b.duration)) * 10) / 10, 0.5, b.duration, 0.1, `длина видео-баннера: ${b.duration} с (можно показать только начало)`)
          : mk("duration", "Длительность, с", s.duration || 6, 0.5, 600, 0.5, "сколько показывать"),
        mk("width", "Ширина, % экрана", s.width_percent ?? 90, 10, 100, 1),
        h("div", {class: "field"}, h("label", {}, "Положение"), F.pos)),
      h("div", {class: "grid3"},
        mk("margin", "Отступ, px", s.margin ?? 160, 0, 800, 10, "для «сверху/снизу»"),
        mk("duck", "Громкость игры, %", s.duck_percent ?? 15, 0, 100, 1, isVideo && b.has_audio ? "звук основного видео на время баннера" : "актуально для баннера со звуком")),
      h("label", {class: "check", style: {margin: "0 0 12px"}}, F.pause, h("span", {}, "Поставить основное видео на паузу, пока играет баннер (клип станет длиннее на время баннера)")),
      isVideo ? h("div", {class: "row", style: {margin: "0 0 12px", gap: "8px", alignItems: "center"}},
        h("label", {class: "check", style: {margin: "0"}}, F.chroma, h("span", {}, "Убрать однотонный фон (хромакей)")), F.chromaColor,
        h("span", {class: "muted small"}, b.key ? "цвет фона найден автоматически" : "фон не найден — выбери цвет вручную")) : null,
      card.info);
    setTimeout(info, 0);
    return box;
  }
  function drawBanner() {
    syncCards();
    bnBox.replaceChildren(bannerPresetRow());
    if (!cards.length) {
      cards.push(newCard(null, true));
    }
    cards.forEach((k, i) => bnBox.append(cardEl(k, i)));
    const ready = cards.every(k => k.sel && !k.choosing);
    if (ready && cards.length < MAXB) bnBox.append(h("button", {class: "btn", style: {marginTop: "4px"}, onclick: () => { cards.push(newCard(null, true)); drawBanner(); }}, "+ Добавить ещё один баннер"));
    else if (ready) bnBox.append(h("div", {class: "muted small"}, `Достигнут лимит: ${MAXB} баннеров на клип.`));
    if (cards.length === 1 && !cards[0].sel) bnBox.append(h("div", {class: "note"}, "Баннер для этого клипа не выбран — клип выйдет без рекламы."));
  }
  function cardInfo(card) {
    clearTimeout(card.infoT);
    card.infoT = setTimeout(async () => {
      if (!card.sel || !card.F.start || !card.F.start.isConnected) return;
      const s = specOf(card);
      try {
        const q = new URLSearchParams({name: s.name, start: s.start, duration: s.duration, width_percent: s.width_percent, position: s.position, duck_percent: s.duck_percent, margin: s.margin});
        if (s.chroma !== undefined) { q.set("chroma", s.chroma ? "true" : "false"); q.set("chroma_color", s.chroma_color || ""); }
        const d = await api("GET", "/api/banners/describe?" + q);
        card.info.className = "note " + (d.ok ? "ok" : "warn"); card.info.textContent = d.text;
      } catch (e) { card.info.className = "note warn"; card.info.textContent = e.message; }
    }, 250);
  }
  function pick(card, b) {
    const d = (cfg.banner_defaults || {})[b.kind] || {};
    const same = card.sel && card.sel.name === b.name;
    const ck = b.kind === "video" ? {chroma: !!b.key, chroma_color: b.key || "#00ff00"} : {};
    card.sel = {name: b.name, ...d, ...ck, position: "center", ...(same ? card.sel : {})};   // новый баннер — по центру
    card.choosing = false; card.ovName = "";
    drawBanner();
  }
  async function upload(e, card) {
    const f = e.target.files[0]; if (!f) return;
    toast("Загружаю баннер…");
    try {
      const r = await api("PUT", "/api/banners?name=" + encodeURIComponent(f.name), undefined, f);
      await loadBanners(); pick(card, S.banners.find(x => x.name === r.name) || r);
    } catch (er) { fail(er); }
  }
  async function save(silent) {
    const body = {title: title.value, description: desc.value, tags: tags.value, publish_at: inputToIso(when.value)};
    body.banners = specsNow();
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
    presetInfo.textContent = [(p.banners || []).length ? `баннеры: ${p.banners.map(x => x.name).join(", ")}` : "", p.tags ? `теги: ${p.tags}` : "", p.footer ? `в конце описания: «${p.footer}»` : ""].filter(Boolean).join(" · ");
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
    const useBn = h("input", {type: "checkbox"}); useBn.checked = specsNow().length > 0; useBn.disabled = !specsNow().length;
    const ptags = h("input", {type: "text", placeholder: "#теги, которые добавятся ко всем"}); ptags.value = "";
    const foot = h("textarea", {placeholder: "Текст, который допишется в конец описания (ссылка, промокод…)", style: {minHeight: "70px"}});
    dialog(h("div", {}, h("h3", {}, "Сохранить пресет"),
      h("div", {class: "field"}, h("label", {}, "Название"), name),
      h("label", {class: "check"}, useBn, h("span", {}, specsNow().length ? `Баннеры клипа (${specsNow().map(x => x.name).join(", ")}) с текущими настройками` : "Баннер (сейчас не выбран)")),
      h("div", {class: "field", style: {marginTop: "12px"}}, h("label", {}, "Теги"), ptags),
      h("div", {class: "field"}, h("label", {}, "Текст в конце описания"), foot)),
      [{text: "Отмена", cls: "ghost"}, {text: "Сохранить", cls: "primary", fn: async () => {
        try {
          const body = {name: name.value, tags: ptags.value, footer: foot.value, banners: useBn.checked ? specsNow() : []};
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
      const to = Number(gsel.value), from = c.group_id;
      if (to === from) return;
      const nameOf = id => (S.groups.find(x => x.id === id) || {name: "?"}).name;
      const others = S.clips.filter(x => x.group_id === from && x.id !== c.id && !x.test);
      const doMove = async ids => {
        try { await save(true); const r = await api("POST", "/api/clips/move", {clip_ids: ids, group_id: to});
          await loadClips(); toast(ids.length > 1 ? `Перенесено клипов: ${r.moved} → «${nameOf(to)}»` : `Клип перенесён в группу «${nameOf(to)}»`); render(); }
        catch (e) { fail(e); gsel.value = from; }
      };
      if (!others.length) return doMove([c.id]);
      $("#dlg").addEventListener("close", () => { if (gsel.isConnected && Number(gsel.value) !== c.group_id) gsel.value = c.group_id; }, {once: true});
      dialog(h("div", {}, h("h3", {}, "Сменить группу?"),
        h("div", {class: "muted"}, `Клип пойдёт в группу «${nameOf(to)}». В группе «${nameOf(from)}» есть ещё ${others.length} ${plural(others.length, "клип", "клипа", "клипов")}. Применить это изменение ко всем клипам группы «${nameOf(from)}» (всего ${others.length + 1})?`)),
        [{text: "Отмена", cls: "ghost"}, {text: "Только этот клип", fn: () => doMove([c.id])},
         {text: `Все клипы группы (${others.length + 1})`, cls: "primary", fn: () => doMove([c.id, ...others.map(x => x.id)])}]);
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
      fillerCard(c),
      stickerCard(c),
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
        a.platform === "youtube" ? h("button", {class: "btn sm", title: a.has_keys ? "Свои ключи заданы: " + a.keys_id + "…" : "Использовать общие ключи (client_secret.json или .env)", onclick: () => keysDialog(a)}, a.has_keys ? "Ключи ✓" : "Ключи") : null,
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
        h("div", {class: "hint muted small", style: {marginTop: "10px"}}, "Публикация работает на YouTube Shorts, Instagram Reels и TikTok. Для Instagram и TikTok нужны ключи приложения в файле .env (см. README). У них нет отложенной публикации, поэтому по времени клип выкладывает сама программа — она должна быть запущена. В TikTok клип попадает в черновики приложения: подпись, обложку и время (TikTok Studio) ты задаёшь сам.")));
    }
  }
  page.append(h("div", {class: "two"}, left, right));
  page.append(ttAccountsBlock());
}
function keysDialog(a) {
  const id = h("input", {type: "text", placeholder: "123456789-abc….apps.googleusercontent.com", autocomplete: "off", spellcheck: "false"});
  const sec = h("input", {type: "password", placeholder: a.has_keys ? "уже задан — впиши новый, чтобы заменить" : "GOCSPX-…", autocomplete: "off"});
  dialog(h("div", {}, h("h3", {}, `Ключи Google для «${a.label}»`),
      h("div", {class: "muted small", style: {marginBottom: "10px"}}, "Client ID и Client secret OAuth-приложения из Google Cloud (Credentials → OAuth client ID, тип строго «Desktop app», не «Web application»). Можно вставить в первое поле содержимое скачанного JSON целиком. У каждого канала могут быть свои — тогда у каждого свой дневной лимит загрузок. Пусто = общие ключи."),
      h("label", {class: "muted small"}, "Client ID"), id, h("div", {style: {height: "8px"}}),
      h("label", {class: "muted small"}, "Client secret"), sec),
    [{text: "Отмена", cls: "ghost"}, ...(a.has_keys ? [{text: "Сбросить", cls: "ghost", fn: async () => { try { await api("PUT", `/api/accounts/${a.id}/keys`, {}); toast("Свои ключи убраны"); await loadGroups(); render(); } catch (e) { fail(e); } }}] : []),
      {text: "Сохранить", cls: "primary", fn: async () => {
        if (a.has_keys && !id.value.trim() && !sec.value.trim()) return;
        try { await api("PUT", `/api/accounts/${a.id}/keys`, {client_id: id.value, client_secret: sec.value}); toast("Ключи сохранены — нажми «Войти»"); await loadGroups(); render(); } catch (e) { fail(e); } }}]);
  setTimeout(() => id.focus(), 50);
}
async function login(a) {
  try { const t = await api("POST", `/api/accounts/${a.id}/login`); startTracking(t); toast("Открою браузер для входа — выбери аккаунт и канал"); render(); }
  catch (e) { fail(e); }
}
async function cancelLogin() {
  try { await api("POST", "/api/login/cancel"); toast("Вход отменён"); } catch (e) { fail(e); }
}

/* ---------- пресеты (без клипа) ---------- */
function pageFillers(page) {
  const box = h("div", {});
  page.append(h("div", {class: "note", style: {marginBottom: "16px"}},
    "Видео-вставки — ролики, которые идут под или над клипом (чистка ковров, песок, чернила…). Перед публикацией программа берёт случайную из этой папки; расположение (сверху или снизу) выбирается в «Редакторе» или в окне публикации. Клип сжимается до ~половины экрана, вставка занимает ~85% оставшегося места, в узкой полоске пишется название клипа."),
    h("div", {class: "row", style: {marginBottom: "16px"}},
      h("label", {class: "btn primary"}, "+ Загрузить видео", h("input", {type: "file", hidden: true, multiple: true, accept: "video/*", onchange: async e => {
        const files = [...e.target.files]; e.target.value = ""; if (!files.length) return;
        toast(`Загружаю: ${files.length}…`);
        for (const f of files) { try { await api("PUT", "/api/fillers?name=" + encodeURIComponent(f.name), undefined, f); } catch (er) { fail(er); } }
        await loadFillers(); draw(); }}))), box);
  const draw = () => {
    const F = S.fillers || {dir: "", files: []};
    box.replaceChildren(h("div", {class: "muted small", style: {marginBottom: "10px"}}, `Папка: ${F.dir} · роликов: ${F.files.length}. Пусто? Запусти get_fillers.py (скачает бесплатные ролики) или загрузи свои.`),
      h("div", {class: "cards"}, F.files.map(f => h("div", {class: "card"},
        h("video", {src: "/api/fillers/file?name=" + encodeURIComponent(f.name), muted: true, controls: true, preload: "metadata", style: {width: "100%", maxHeight: "220px", borderRadius: "10px", background: "#000"}}),
        h("div", {class: "row", style: {marginTop: "8px"}}, h("div", {class: "grow"}, h("b", {}, f.name), h("div", {class: "muted small"}, `${f.width}×${f.height} · ${fmtDur(f.duration)} · ${(f.size / 1048576).toFixed(1)} МБ`)),
          h("button", {class: "btn sm danger", onclick: () => confirmBox("Удалить видео?", `«${f.name}» будет удалено из папки вставок.`, "Удалить", async () => {
            try { await api("DELETE", "/api/fillers?name=" + encodeURIComponent(f.name)); await loadFillers(); draw(); } catch (er) { fail(er); } })}, "Удалить"))))));
  };
  draw();
  loadFillers().then(draw);
}
async function pagePresets(page) {
  let tab = S.presetTab; if (!tab) { try { tab = localStorage.getItem("presetTab"); } catch (e) {} }
  tab = tab === "stickers" ? "stickers" : "banners";
  const body = h("div", {});
  const seg = h("div", {class: "seg", style: {marginBottom: "16px"}}, [["banners", "Пресеты баннеров"], ["stickers", "Пресеты стикеров"]].map(([k, t]) =>
    h("button", {class: tab === k ? "on" : "", onclick: () => { S.presetTab = k; try { localStorage.setItem("presetTab", k); } catch (e) {} render(); }}, t)));
  page.append(seg, body);
  if (tab === "stickers") return pageStickerPresets(body);
  return pageBannerPresets(body);
}
async function pageBannerPresets(page) {
  try { S.presets = await api("GET", "/api/presets"); } catch (e) { S.presets = S.presets || []; }
  if (!S.banners.length) { try { await loadBanners(); } catch (e) {} }
  const box = h("div", {style: {display: "flex", flexDirection: "column", gap: "16px"}});
  page.append(h("div", {class: "note", style: {marginBottom: "16px"}}, "Пресет — набор баннеров с настройками, хэштеги и текст в конце описания. Его можно создать здесь, без клипа, и использовать в шаблонах или применять к клипам в «Редакторе». Предпросмотр баннера на видео — в «Редакторе»: применить пресет к клипу и посмотреть."), box);
  const dflt = kind => ({...(S.cfg.banner_defaults[kind] || S.cfg.banner_defaults.image)});
  const fresh = b => ({name: b.name, ...dflt(b.kind)});
  const draw = () => {
    const L = S.presets || [];
    box.replaceChildren(h("div", {class: "row"}, h("button", {class: "btn primary", onclick: () => edit({id: null, name: "", tags: "", footer: "", banners: []})}, "+ Новый пресет")),
      ...(L.length ? L.map(p => h("div", {class: "card"}, h("div", {class: "row", style: {marginBottom: "6px"}}, h("h2", {style: {margin: 0}, class: "grow"}, p.name),
        h("button", {class: "btn sm", onclick: () => edit({id: p.id, name: p.name, tags: p.tags || "", footer: p.footer || "", banners: (p.banners || []).map(x => ({...x}))})}, "Изменить"),
        h("button", {class: "btn sm danger", onclick: () => confirmBox("Удалить пресет?", `«${p.name}» будет удалён (клипы и шаблоны не изменятся, но шаблон без пресета станет «без пресета»).`, "Удалить", async () => {
          try { await api("DELETE", `/api/presets/${p.id}`); await refresh(); } catch (e) { fail(e); } })}, "Удалить")),
        h("div", {class: "muted small"}, [(p.banners || []).length ? `баннеры: ${p.banners.map(x => x.name).join(", ")}` : "", p.tags ? `теги: ${p.tags}` : "", p.footer ? `в конце описания: «${p.footer.slice(0, 80)}${p.footer.length > 80 ? "…" : ""}»` : ""].filter(Boolean).join(" · "))))
        : [h("div", {class: "card empty"}, h("b", {}, "Пресетов пока нет"), "Создай первый: например «Реклама казино — внизу» с баннером и тегами.")]));
  };
  const refresh = async () => { S.presets = await api("GET", "/api/presets"); draw(); };
  function edit(p) {
    const name = h("input", {type: "text", placeholder: "Например: «Реклама казино — внизу»", value: p.name});
    const tags = h("input", {type: "text", placeholder: "#теги, которые добавятся ко всем клипам", value: p.tags});
    const foot = h("textarea", {placeholder: "Текст, который допишется в конец описания (ссылка, промокод…)", style: {minHeight: "70px"}}); foot.value = p.footer;
    const list = h("div", {});
    const max = S.cfg.max_banners || 5;
    const num = (sp, key, label, step, min, max_) => { const i = h("input", {type: "number", step: String(step), min: String(min), max: String(max_), value: sp[key] ?? 0, oninput: () => { sp[key] = i.value === "" ? 0 : +i.value; }});
      return h("div", {class: "field"}, h("label", {}, label), i); };
    const drawBanners = () => {
      list.replaceChildren(...[...p.banners.map((sp, idx) => {
        const b = S.banners.find(x => x.name === sp.name), isVideo = b && b.kind === "video";
        const file = h("select", {style: {flex: "1", minWidth: "160px"}}, [...(b ? [] : [h("option", {value: sp.name}, `⚠ ${sp.name} (файла нет)`)]),
          ...S.banners.map(x => h("option", {value: x.name}, `${x.name} · ${x.kind === "video" ? x.duration + " с" : "картинка"}`))]);
        file.value = sp.name;
        file.onchange = () => { const nb = S.banners.find(x => x.name === file.value); if (nb) { p.banners[idx] = fresh(nb); drawBanners(); } };
        const up = h("label", {class: "btn sm", title: "Заменить этот баннер другим файлом с компьютера"}, "Другой файл…", h("input", {type: "file", hidden: true, accept: "image/*,video/*", onchange: async e => {
          const f = e.target.files[0]; if (!f) return; toast("Загружаю баннер…");
          try { const r = await api("PUT", "/api/banners?name=" + encodeURIComponent(f.name), undefined, f); await loadBanners();
            const nb = S.banners.find(x => x.name === r.name) || r; p.banners[idx] = fresh(nb); drawBanners(); } catch (er) { fail(er); } }}));
        const mode = h("select", {onchange: () => { sp.start_mode = mode.value; drawBanners(); }}, [["seconds", "В секундах"], ["percent", "В % видео"]].map(([v, t]) => h("option", {value: v}, t)));
        mode.value = sp.start_mode === "percent" ? "percent" : "seconds";
        const pos = h("select", {onchange: () => { sp.position = pos.value; }}, [["center", "По центру"], ["top", "Сверху"], ["bottom", "Снизу"]].map(([v, t]) => h("option", {value: v}, t)));
        pos.value = sp.position || "center";
        const pause = h("input", {type: "checkbox", onchange: () => { sp.pause = pause.checked; }}); pause.checked = !!sp.pause;
        let chroma = null;
        if (isVideo) {
          const cc = h("input", {type: "checkbox", onchange: () => { sp.chroma = cc.checked; col.disabled = !cc.checked; }}); cc.checked = !!sp.chroma;
          const col = h("input", {type: "color", value: sp.chroma_color || b.key || "#00ff00", disabled: !sp.chroma, style: {width: "40px", height: "28px", padding: "0", border: "none", background: "none"}, onchange: () => { sp.chroma_color = col.value; }});
          chroma = h("div", {class: "row", style: {gap: "8px"}}, h("label", {class: "check"}, cc, h("span", {}, "Убрать однотонный фон")), col);
        }
        return h("div", {class: "bncard"}, h("div", {class: "row", style: {marginBottom: "10px"}}, h("b", {}, `Баннер ${idx + 1}`), file, up,
          h("button", {class: "btn sm danger", onclick: () => { p.banners.splice(idx, 1); drawBanners(); }}, "Убрать")),
          h("div", {class: "row", style: {alignItems: "end", gap: "12px"}},
            h("div", {class: "field"}, h("label", {}, "Начало"), mode),
            sp.start_mode === "percent" ? num(sp, "start_percent", "Начало, % видео", 1, 0, 99) : num(sp, "start", "Начало, с", 0.5, 0, 3600),
            num(sp, "duration", isVideo ? "Длительность, с (0 = целиком)" : "Длительность, с", 0.5, 0, 600),
            num(sp, "width_percent", "Ширина, %", 5, 10, 100),
            h("div", {class: "field"}, h("label", {}, "Положение"), pos),
            num(sp, "margin", "Отступ, px", 10, 0, 800), num(sp, "duck_percent", "Громкость основного, %", 5, 0, 100),
            h("label", {class: "check"}, pause, h("span", {}, "Пауза на баннере"))), chroma);
      }), h("div", {class: "row", style: {gap: "8px", marginTop: "4px"}},
        h("label", {class: "btn sm primary" + (p.banners.length >= max ? " disabled" : ""), title: "Картинка (png, jpg, webp) или видео (mp4, mov, mkv, webm) с компьютера — попадёт в библиотеку баннеров и сразу добавится в пресет"},
          "+ Загрузить с компьютера", h("input", {type: "file", hidden: true, multiple: true, accept: "image/*,video/*", disabled: p.banners.length >= max, onchange: async e => {
            const files = [...e.target.files].slice(0, max - p.banners.length); e.target.value = "";
            if (!files.length) return;
            toast(files.length > 1 ? `Загружаю баннеры: ${files.length}…` : "Загружаю баннер…");
            for (const f of files) {
              try { const r = await api("PUT", "/api/banners?name=" + encodeURIComponent(f.name), undefined, f); await loadBanners();
                p.banners.push(fresh(S.banners.find(x => x.name === r.name) || r)); } catch (er) { fail(er); }
            }
            drawBanners(); }})),
        S.banners.length ? h("button", {class: "btn sm", disabled: p.banners.length >= max, onclick: () => { p.banners.push(fresh(S.banners[0])); drawBanners(); }}, "+ Из библиотеки") : null,
        h("span", {class: "muted small"}, `${p.banners.length} из ${max}`))].filter(Boolean));
    };
    drawBanners();
    box.replaceChildren(h("div", {class: "card"}, h("h2", {}, p.id ? "Изменить пресет" : "Новый пресет"),
      h("div", {class: "field"}, h("label", {}, "Название"), name),
      h("div", {class: "muted small", style: {margin: "14px 0 8px"}}, `Баннеры (до ${max}) — настройки такие же, как в «Редакторе»`), list,
      h("div", {class: "field", style: {marginTop: "14px"}}, h("label", {}, "Теги"), tags),
      h("div", {class: "field"}, h("label", {}, "Текст в конце описания"), foot),
      h("div", {class: "row", style: {marginTop: "14px", justifyContent: "flex-end"}},
        h("button", {class: "btn ghost", onclick: draw}, "Отмена"),
        h("button", {class: "btn primary", onclick: async () => {
          try {
            await api("POST", "/api/presets", {id: p.id || undefined, name: name.value, tags: tags.value, footer: foot.value, banners: p.banners});
            toast("Пресет сохранён"); await refresh();
          } catch (e) { fail(e); } }}, "Сохранить"))));
    setTimeout(() => name.focus(), 50);
  }
  draw();
}

/* ---------- шаблоны публикации ---------- */
async function pageTemplates(page) {
  try { S.presets = await api("GET", "/api/presets"); } catch (e) { S.presets = S.presets || []; }
  await loadTemplates();
  const box = h("div", {style: {display: "flex", flexDirection: "column", gap: "16px"}});
  page.append(h("div", {class: "note", style: {marginBottom: "16px"}}, "Шаблон — это набор групп, для каждой свой пресет баннеров, плюс хэштеги (общие для всего шаблона и отдельные для группы). Выбери шаблон на «Панели» перед нарезкой: видео нарежется один раз, а клипы появятся во всех группах шаблона — каждый со своим баннером. Пресеты баннеров создаются в «Редакторе» (кнопка «Сохранить как пресет»)."));
  const draw = () => {
    const L = S.templates || [];
    box.replaceChildren(h("div", {class: "row"}, h("button", {class: "btn primary", onclick: () => edit(null)}, "+ Новый шаблон")),
      ...(L.length ? L.map(t => h("div", {class: "card"}, h("div", {class: "row", style: {marginBottom: "6px"}}, h("h2", {style: {margin: 0}, class: "grow"}, t.name, t.tags ? h("small", {}, t.tags) : null, t.broken ? h("small", {style: {color: "var(--danger)"}}, "есть удалённая группа или пресет") : null),
        h("button", {class: "btn sm", onclick: () => edit(t)}, "Изменить"),
        h("button", {class: "btn sm danger", onclick: () => confirmBox("Удалить шаблон?", `«${t.name}» будет удалён (группы и пресеты не изменятся).`, "Удалить", async () => {
          try { await api("DELETE", `/api/templates/${t.id}`); await loadTemplates(); draw(); } catch (e) { fail(e); } })}, "Удалить")),
        t.items.map(i => h("div", {class: "acc"}, h("div", {class: "grow"}, h("b", {}, i.group || "группа удалена"),
          h("span", {class: "muted small"}, (i.preset_id ? `пресет баннеров: ${i.preset || "удалён"}` : "без пресета (баннер по умолчанию)") + (i.sticker_preset_id ? ` · стикеры: ${i.sticker_preset || "удалён"}` : "") + (i.tags ? ` · теги группы: ${i.tags}` : ""))),
          i.broken ? h("span", {class: "badge err"}, "исправь") : null))))
        : [h("div", {class: "card empty"}, h("b", {}, "Шаблонов пока нет"), "Создай шаблон, например «Собаки»: группы A и B, у каждой свой баннер.")]));
  };
  function edit(t) {
    const name = h("input", {type: "text", placeholder: "Например: «Собаки — все каналы»", value: t ? t.name : ""});
    const tags = h("input", {type: "text", placeholder: "#собаки #щенки — для клипов всех групп шаблона", value: t ? t.tags || "" : ""});
    let rows = t ? t.items.map(i => ({group_id: i.group_id, preset_id: i.preset_id || "", sticker_preset_id: i.sticker_preset_id || "", tags: i.tags || ""})) : [{group_id: (S.groups[0] || {}).id, preset_id: "", sticker_preset_id: "", tags: ""}];
    const list = h("div", {});
    const drawRows = () => {
      const used = new Set(rows.map(r => r.group_id));
      list.replaceChildren(...rows.map((r, idx) => {
        const gs = h("select", {style: {flex: "1", minWidth: "140px"}}, S.groups.filter(g => g.id === r.group_id || !used.has(g.id)).map(g => h("option", {value: g.id}, g.name)));
        gs.value = r.group_id; gs.onchange = () => { r.group_id = Number(gs.value); drawRows(); };
        const ps = h("select", {style: {flex: "1", minWidth: "140px"}}, [h("option", {value: ""}, "— без пресета баннеров —"), ...(S.presets || []).map(p => h("option", {value: p.id}, p.name))]);
        ps.value = r.preset_id || ""; ps.onchange = () => { r.preset_id = Number(ps.value) || ""; };
        const ss = h("select", {style: {flex: "1", minWidth: "140px"}}, [h("option", {value: ""}, "— без стикеров —"), ...(S.stickerPresets || []).map(p => h("option", {value: p.id}, p.name))]);
        ss.value = r.sticker_preset_id || ""; ss.onchange = () => { r.sticker_preset_id = Number(ss.value) || ""; };
        const tg = h("input", {type: "text", placeholder: "теги этой группы", value: r.tags || "", style: {flex: "1", minWidth: "140px"}, oninput: () => { r.tags = tg.value; }});
        return h("div", {class: "row", style: {marginBottom: "8px"}}, gs, ps, ss, tg, h("button", {class: "btn sm danger", disabled: rows.length < 2, onclick: () => { rows.splice(idx, 1); drawRows(); }}, "×"));
      }), h("button", {class: "btn sm", disabled: rows.length >= S.groups.length, onclick: () => { const free = S.groups.find(g => !used.has(g.id)); if (free) { rows.push({group_id: free.id, preset_id: "", sticker_preset_id: "", tags: ""}); drawRows(); } }}, "+ Добавить группу"));
    };
    drawRows();
    dialog(h("div", {}, h("h3", {}, t ? "Изменить шаблон" : "Новый шаблон"), h("div", {class: "muted small", style: {marginBottom: "6px"}}, "Название"), name,
      h("div", {class: "muted small", style: {margin: "12px 0 6px"}}, "Хэштеги для всех групп шаблона"), tags,
      h("div", {class: "muted small", style: {margin: "12px 0 6px"}}, "Группа → пресет баннеров → пресет стикеров → свои хэштеги группы"), list),
      [{text: "Отмена", cls: "ghost"}, {text: "Сохранить", cls: "primary", fn: async () => {
        try {
          await api("POST", "/api/templates", {id: t ? t.id : undefined, name: name.value, tags: tags.value, items: rows.map(r => ({group_id: r.group_id, preset_id: r.preset_id || null, sticker_preset_id: r.sticker_preset_id || null, tags: r.tags || ""}))});
          await loadTemplates(); toast("Шаблон сохранён"); draw();
        } catch (e) { fail(e); } }}]);
    setTimeout(() => name.focus(), 50);
  }
  page.append(box);
  draw();
}

/* ---------- история ---------- */
function pageHistory(page) {
  const bar = h("div", {class: "row", style: {marginBottom: "16px"}},
    h("button", {class: "btn primary", onclick: async () => { try { startTracking(await api("POST", "/api/history/refresh")); toast("Обновляю статистику…"); } catch (e) { fail(e); } }}, "Обновить статистику"),
    h("button", {class: "btn", onclick: () => ask("Добавить в историю", "Ссылки на ранее опубликованные видео YouTube — по одной в строке", "", "Добавить", async v => {
      try { startTracking(await api("POST", "/api/history/import", {urls: v, group_id: S.gid})); } catch (e) { fail(e); } }, true)}, "Добавить старые видео"));
  page.append(bar);
  // при открытии истории статистика обновляется сама (не чаще раза в 3 минуты), без уведомлений
  if (Date.now() - (S.statsAuto || 0) > 3 * 60e3 && (S.history || []).some(p => p.platform === "youtube")) {
    S.statsAuto = Date.now();
    api("POST", "/api/history/refresh").then(() => setTimeout(async () => { try { await loadHistory(); if (S.page === "history") render(); } catch (e) {} }, 4000)).catch(() => {});
  }
  S.histSort = S.histSort || "date";
  const sortSeg = h("div", {class: "seg"}, [["date", "По дате загрузки"], ["views", "По просмотрам"], ["ret", "По удержанию"]].map(([k, t]) =>
    h("button", {class: S.histSort === k ? "on" : "", onclick: () => { S.histSort = k; sortSeg.querySelectorAll("button").forEach((b, i) => b.classList.toggle("on", i === ["date", "views", "ret"].indexOf(k))); draw(); }}, t)));
  bar.append(sortSeg);
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
    const st = p => h("span", {class: "badge " + (p.status === "Опубликовано" ? "ok" : p.status === "Удалено" ? "err" : p.status.startsWith("Запл") || p.status.startsWith("В черн") ? "warn" : "")}, p.status);
    const when = p => fmtLocal(p.uploaded_at);
    const ret = p => p.avg_pct == null ? "—" : `${Math.round(p.avg_pct)}%`;
    const retFull = p => p.avg_pct == null ? "" : ` · ⏱ ${Math.round(p.avg_pct)}% (${Math.round(p.avg_sec || 0)} с)`;
    const rowsOf = L => h("table", {class: "htable"}, h("thead", {}, h("tr", {}, ["Видео", "Аккаунт", "Статус", "Просмотры", "Лайки", "Коммент.", "Удержание", "Дата на канале"].map((t, i) => h("th", {class: i > 2 && i < 7 ? "num" : "", title: i === 6 ? "Средний % ролика, который досматривают зрители, и среднее время просмотра. YouTube отдаёт эти данные с задержкой около суток." : ""}, t)))),
      h("tbody", {}, L.map(p => h("tr", {class: "link", onclick: () => open(p)},
        h("td", {}, h("div", {class: "hcell"}, thumb(p), h("div", {}, h("b", {}, p.title || "Без названия")))),
        h("td", {}, p.account, h("div", {class: "muted small"}, platName(p.platform))), h("td", {}, st(p)), h("td", {class: "num"}, fmtNum(p.views)), h("td", {class: "num"}, fmtNum(p.likes)),
        h("td", {class: "num"}, fmtNum(p.comments)), h("td", {class: "num"}, ret(p)), h("td", {class: "muted small"}, when(p))))));
    const cardsOf = L => h("div", {class: "cards"}, L.map(p => h("div", {class: "card", style: {cursor: "pointer"}, onclick: () => open(p)},
      h("div", {class: "hcell"}, thumb(p), h("div", {}, h("b", {}, p.title || "Без названия"), h("div", {class: "row", style: {gap: "8px", marginTop: "4px"}}, st(p), h("span", {class: "muted small"}, `${platName(p.platform)} · ${p.account}`)),
        h("div", {class: "muted small", style: {marginTop: "6px"}}, `👁 ${fmtNum(p.views)} · 👍 ${fmtNum(p.likes)} · 💬 ${fmtNum(p.comments)}${retFull(p)}`))))));
    // по группам: порядок как в «Аккаунтах», затем остальные (удалённые группы) в конце
    const byG = new Map();
    const ts = p => Date.parse(p.uploaded_at || "") || 0;
    const cmp = S.histSort === "ret" ? (a, b) => (b.avg_pct ?? -1) - (a.avg_pct ?? -1) || ts(b) - ts(a) : S.histSort === "views" ? (a, b) => (b.views || 0) - (a.views || 0) || ts(b) - ts(a) : (a, b) => ts(b) - ts(a) || b.id - a.id;
    for (const p of [...H].sort(cmp)) { const k = p.group_id || 0; if (!byG.has(k)) byG.set(k, []); byG.get(k).push(p); }
    const order = [...S.groups.map(g => g.id).filter(id => byG.has(id)), ...[...byG.keys()].filter(k => !S.groups.some(g => g.id === k))];
    S.histClosed = S.histClosed || new Set();
    const sum = (L, f) => L.reduce((a, p) => a + (p[f] || 0), 0);
    const total = {n: H.length, v: sum(H, "views"), l: sum(H, "likes"), c: sum(H, "comments")};
    const secs = order.map(k => {
      const L = byG.get(k), name = (S.groups.find(g => g.id === k) || {}).name || L[0].group || "Без группы";
      const closed = S.histClosed.has(k);
      const plats = ["youtube", "tiktok", "instagram"].map(pl => [pl, L.filter(p => p.platform === pl)]).filter(([_, x]) => x.length);
      const wrap = h("div", {class: "card hgroup", style: {padding: "8px 10px"}});
      const head = h("div", {class: "hgroup-head", onclick: () => { closed ? S.histClosed.delete(k) : S.histClosed.add(k); draw(); }},
        h("span", {class: "caret"}, closed ? "▸" : "▾"), h("b", {class: "grow"}, name),
        h("span", {class: "muted small"}, plats.map(([pl, x]) => `${platName(pl)}: ${x.length}`).join(" · ")),
        h("span", {class: "hstat"}, `${L.length} видео`), h("span", {class: "hstat"}, `👁 ${fmtNum(sum(L, "views"))}`), h("span", {class: "hstat"}, `👍 ${fmtNum(sum(L, "likes"))}`), h("span", {class: "hstat"}, `💬 ${fmtNum(sum(L, "comments"))}`));
      wrap.append(head);
      if (!closed) wrap.append(h("div", {class: "tcard", style: {overflowX: "auto"}}, rowsOf(L)), cardsOf(L));
      return wrap;
    });
    box.replaceChildren(h("div", {class: "muted small", style: {margin: "0 4px 10px"}}, `Всего: ${total.n} видео · 👁 ${fmtNum(total.v)} · 👍 ${fmtNum(total.l)} · 💬 ${fmtNum(total.c)}`),
      h("div", {style: {display: "flex", flexDirection: "column", gap: "14px"}}, secs));
  };
  if (S.history) draw();
  loadHistory().then(draw).catch(fail);
  // пока открыта история, список перечитывается из базы раз в минуту (статусы и просмотры обновляет сервер в фоне)
  clearInterval(S.histTimer);
  S.histTimer = setInterval(() => { if (S.page !== "history") { clearInterval(S.histTimer); return; } loadHistory().then(draw).catch(() => {}); }, 60e3);
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


/* ---------- пресеты стикеров ---------- */
function pageStickerPresets(page) {
  const box = h("div", {style: {display: "flex", flexDirection: "column", gap: "16px"}});
  page.append(h("div", {class: "note", style: {marginBottom: "16px"}},
    "Стикер — маленькая картинка без фона (насекомое), «приклеенная» поверх клипа. Положение задаётся в процентах высоты экрана: 100 % — самый верх, 50 % — середина. Размер — в миллиметрах на экране телефона: 4 мм ≈ живой муравей. Пресет хранит набор стикеров, их количество, диапазон высоты и размер; при применении к клипу программа случайно расставляет их в этом диапазоне."), box);
  const imgUrl = n => "/api/stickers/file?name=" + encodeURIComponent(n);
  const draw = () => {
    const F = S.stickerLib || {dir: "", files: [], screen_mm: 67}, L = S.stickerPresets || [];
    const lib = h("div", {class: "card"}, h("div", {class: "row", style: {marginBottom: "10px"}}, h("h2", {class: "grow", style: {margin: 0}}, "Библиотека стикеров", h("small", {}, `${F.files.length} шт.`)),
      h("label", {class: "btn sm primary"}, "+ Загрузить PNG/WebP", h("input", {type: "file", hidden: true, multiple: true, accept: "image/png,image/webp", onchange: async e => {
        const files = [...e.target.files]; e.target.value = ""; if (!files.length) return;
        for (const f of files) { try { await api("PUT", "/api/stickers?name=" + encodeURIComponent(f.name), undefined, f); } catch (er) { fail(er); } }
        await loadStickers(); draw(); toast("Загружено"); }}))),
      h("div", {class: "muted small", style: {marginBottom: "10px"}}, `Папка: ${F.dir}. Поставляются нарисованные насекомые; свои PNG/WebP с прозрачным фоном можно загрузить сюда же.`),
      h("div", {class: "stk-grid"}, F.files.map(f => h("div", {class: "stk-item"}, h("img", {src: imgUrl(f.name), alt: f.name}), h("div", {class: "muted small"}, f.name.replace(/\.\w+$/, "")),
        h("button", {class: "btn sm danger", onclick: () => confirmBox("Удалить стикер?", `«${f.name}» будет удалён из папки (пресеты, где он выбран, перестанут его использовать).`, "Удалить", async () => {
          try { await api("DELETE", "/api/stickers?name=" + encodeURIComponent(f.name)); await loadStickers(); draw(); } catch (er) { fail(er); } })}, "×")))));
    box.replaceChildren(lib, h("div", {class: "row"}, h("button", {class: "btn primary", onclick: () => edit(null)}, "+ Новый пресет стикеров")),
      ...(L.length ? L.map(p => h("div", {class: "card"}, h("div", {class: "row", style: {marginBottom: "6px"}}, h("h2", {style: {margin: 0}, class: "grow"}, p.name),
        h("button", {class: "btn sm", onclick: () => edit(p)}, "Изменить"),
        h("button", {class: "btn sm danger", onclick: () => confirmBox("Удалить пресет стикеров?", `«${p.name}» будет удалён (уже наложенные стикеры у клипов не изменятся, но шаблоны с ним нужно поправить).`, "Удалить", async () => {
          try { await api("DELETE", `/api/sticker-presets/${p.id}`); await loadStickers(); draw(); } catch (er) { fail(er); } })}, "Удалить")),
        h("div", {class: "muted small"}, `${p.files.length ? p.files.join(", ") : "случайные из всей библиотеки"} · штук: ${p.count} · высота ${p.y_min}–${p.y_max} % · размер ≈ ${p.size_mm} мм${p.rotate ? " · с поворотом" : ""}`)))
        : [h("div", {class: "card empty"}, h("b", {}, "Пресетов стикеров пока нет"), "Создай первый: например «Муравьи» — 3 муравья на высоте 50–95 %.")]));
  };
  function edit(p) {
    const D = (S.stickerLib && S.stickerLib.defaults) || {};
    const st = p ? {...p, files: [...p.files]} : {id: null, name: "", files: [], count: D.count || 3, y_min: D.y_min ?? 50, y_max: D.y_max ?? 95, size_mm: D.size_mm || 4, rotate: true};
    const name = h("input", {type: "text", placeholder: "Например: «Муравьи»", value: st.name});
    const F = (S.stickerLib && S.stickerLib.files) || [];
    const grid = h("div", {class: "stk-grid sel"});
    const prev = h("div", {class: "stk-prev"});
    const drawPrev = () => {
      const pool = st.files.length ? st.files : F.map(f => f.name), w = 150, mm = (S.stickerLib && S.stickerLib.screen_mm) || 67;
      const lo = Math.min(+st.y_min, +st.y_max), hi = Math.max(+st.y_min, +st.y_max);
      prev.replaceChildren(h("div", {class: "stk-scale"}, "100 %"), h("div", {class: "stk-scale", style: {top: "50%"}}, "50 %"),
        ...Array.from({length: Math.max(1, Math.min(12, +st.count || 1))}, () => pool.length ? h("img", {src: imgUrl(pool[Math.floor(Math.random() * pool.length)]), alt: "",
          style: {position: "absolute", left: (8 + Math.random() * 84) + "%", top: (100 - (lo + Math.random() * (hi - lo))) + "%", width: ((+st.size_mm || 4) / mm * w) + "px",
            transform: `translate(-50%,-50%) rotate(${st.rotate ? Math.round(Math.random() * 360) : 0}deg)`}}) : null));
    };
    const drawGrid = () => grid.replaceChildren(...F.map(f => { const on = st.files.includes(f.name);
      return h("button", {class: "stk-item" + (on ? " on" : ""), type: "button", onclick: () => { st.files = on ? st.files.filter(x => x !== f.name) : [...st.files, f.name]; drawGrid(); drawPrev(); }},
        h("img", {src: imgUrl(f.name), alt: f.name}), h("div", {class: "muted small"}, f.name.replace(/\.\w+$/, ""))); }));
    const num = (key, label, step, min, max) => { const i = h("input", {type: "number", step: String(step), min: String(min), max: String(max), value: st[key], oninput: () => { st[key] = i.value === "" ? 0 : +i.value; drawPrev(); }});
      return h("div", {class: "field"}, h("label", {}, label), i); };
    const rot = h("input", {type: "checkbox", onchange: () => { st.rotate = rot.checked; drawPrev(); }}); rot.checked = !!st.rotate;
    drawGrid(); drawPrev();
    dialog(h("div", {}, h("h3", {}, p ? "Изменить пресет стикеров" : "Новый пресет стикеров"), h("div", {class: "muted small", style: {marginBottom: "6px"}}, "Название"), name,
      h("div", {class: "muted small", style: {margin: "12px 0 6px"}}, "Какие стикеры (ничего не выбрано — случайные из всей библиотеки)"), grid,
      h("div", {class: "stk-edit"}, h("div", {}, num("count", "Сколько штук (1–12)", 1, 1, 12), num("y_max", "Выше всего, % высоты (100 — верх экрана)", 1, 0, 100), num("y_min", "Ниже всего, % высоты", 1, 0, 100),
        num("size_mm", "Размер, мм на экране телефона (4 ≈ муравей)", 0.5, 1, 40), h("label", {class: "check"}, rot, h("span", {}, "Случайный поворот"))),
        h("div", {}, prev, h("div", {class: "muted small", style: {margin: "6px 0"}}, "Макет экрана телефона в натуральном масштабе: стикеры такие же мелкие, как будут на экране."), h("button", {class: "btn sm", type: "button", onclick: drawPrev}, "Расставить по-другому")))),
      [{text: "Отмена", cls: "ghost"}, {text: "Сохранить", cls: "primary", fn: async () => {
        try { await api("POST", "/api/sticker-presets", {id: st.id || undefined, name: name.value, files: st.files, count: +st.count, y_min: +st.y_min, y_max: +st.y_max, size_mm: +st.size_mm, rotate: st.rotate});
          await loadStickers(); toast("Пресет стикеров сохранён"); draw(); } catch (e) { fail(e); } }}]);
    setTimeout(() => name.focus(), 50);
  }
  draw();
  loadStickers().then(draw);
}

/* ---------- TikTok: ручная выкладка ---------- */
const TT_EXT_HINT = "Расширение не найдено. Установи его один раз: chrome://extensions → «Режим разработчика» → «Загрузить распакованное расширение» → папка browser-extension из проекта. Потом обнови эту страницу (F5).";
function helperPing(timeout = 700) {
  return new Promise(res => {
    const on = ev => { if (ev.source === window && ev.data && ev.data.narez === "tiktok-reply" && ev.data.op === "pong") { removeEventListener("message", on); clearTimeout(tm); res(true); } };
    const tm = setTimeout(() => { removeEventListener("message", on); res(false); }, timeout);
    addEventListener("message", on);
    window.postMessage({narez: "tiktok", op: "ping"}, "*");
  });
}
function helperLogin(force, timeout = 70000) {
  return new Promise(res => {
    const on = ev => { if (ev.source === window && ev.data && ev.data.narez === "tiktok-reply" && ev.data.op === "login") { removeEventListener("message", on); clearTimeout(tm); res(ev.data); } };
    const tm = setTimeout(() => { removeEventListener("message", on); res({ok: false, error: "Расширение не ответило"}); }, timeout);
    addEventListener("message", on);
    window.postMessage({narez: "tiktok", op: "login", force: !!force}, "*");
  });
}
async function ttLogin(a, force) {
  if (!(await helperPing())) return dialog(h("div", {}, h("h3", {}, "Нужно расширение браузера"), h("div", {class: "muted"}, TT_EXT_HINT)), [{text: "Понятно", cls: "primary"}]);
  try {
    await api("POST", `/api/tiktok/accounts/${a.id}/arm`);
    toast(`Вхожу в «${a.label}»: сброшу вход TikTok в этом браузере и открою страницу входа…`);
    const r = await helperLogin(force);
    if (!r.ok) return toast(r.error || "Не удалось войти", "err");
    toast(r.restored ? "Сессия аккаунта восстановлена — код с почты не нужен. Если TikTok всё же просит войти, нажми «С паролем»" : r.clicked === false ? "Логин и пароль вписаны — нажми «Log in» сам" : "Данные введены. Если TikTok просит капчу или код — подтверди его сам");
  } catch (e) { fail(e); }
}
function ttAccountDialog(a) {
  const label = h("input", {type: "text", placeholder: "Например: «Мемы 1»", value: a ? a.label : ""});
  const login = h("input", {type: "text", placeholder: "Email, телефон или имя пользователя", autocomplete: "off", value: a ? a.login : ""});
  const pass = h("input", {type: "password", placeholder: a && a.has_password ? "уже сохранён — впиши новый, чтобы заменить" : "Пароль", autocomplete: "new-password"});
  const note = h("textarea", {rows: 2, placeholder: "Заметка для себя: на какую почту/телефон приходят коды, прокси и т. п. (в TikTok не отправляется)"}, a ? a.note : "");
  dialog(h("div", {}, h("h3", {}, a ? "TikTok-аккаунт" : "Новый TikTok-аккаунт"),
      h("div", {class: "muted small", style: {marginBottom: "10px"}}, "Данные хранятся в базе narez на этом компьютере в открытом виде и никуда не отправляются — только в форму входа TikTok по кнопке «Войти»."),
      h("label", {class: "muted small"}, "Название"), label, h("div", {style: {height: "8px"}}),
      h("label", {class: "muted small"}, "Логин"), login, h("div", {style: {height: "8px"}}),
      h("label", {class: "muted small"}, "Пароль"), pass, h("div", {style: {height: "8px"}}),
      h("label", {class: "muted small"}, "Заметка"), note),
    [{text: "Отмена", cls: "ghost"}, {text: "Сохранить", cls: "primary", fn: async () => {
      try {
        const body = {label: label.value, login: login.value, password: pass.value || undefined, note: note.value};
        if (a) await api("PUT", `/api/tiktok/accounts/${a.id}`, body); else await api("POST", "/api/tiktok/accounts", body);
        await loadTikTok(); render(); toast("Аккаунт сохранён");
      } catch (e) { fail(e); } }}]);
  setTimeout(() => label.focus(), 50);
}
function ttAccountsBlock() {
  const T = S.tt || {accounts: []};
  const badge = h("span", {class: "badge"}, "расширение: проверяю…");
  helperPing().then(ok => { badge.className = "badge " + (ok ? "ok" : "warn"); badge.textContent = ok ? "расширение подключено" : "расширение не найдено"; badge.title = ok ? "" : TT_EXT_HINT; });
  return h("div", {class: "card tt-block"}, h("h2", {}, "TikTok-аккаунты", h("small", {}, "для ручной выкладки, отдельно от групп")), 
    h("div", {class: "row", style: {marginBottom: "6px"}}, badge, h("div", {class: "grow"}),
      h("button", {class: "btn primary", onclick: () => ttAccountDialog(null)}, "+ Добавить аккаунт")),
    T.accounts.length ? T.accounts.map(a => h("div", {class: "acc"},
      h("div", {class: "plat tiktok"}, "TT"),
      h("div", {class: "grow"}, h("b", {}, a.label), h("span", {class: "muted small"}, a.login + (a.note ? " · " + a.note : ""))),
      h("button", {class: "btn sm primary", title: "Выйти из TikTok в этом браузере и войти в этот аккаунт", onclick: () => ttLogin(a)}, "Войти"),
      h("button", {class: "btn sm", title: "Не восстанавливать сохранённую сессию, а войти по логину и паролю (если сессия устарела)", onclick: () => ttLogin(a, true)}, "С паролем"),
      h("button", {class: "btn sm", onclick: () => ttAccountDialog(a)}, "Изменить"),
      h("button", {class: "btn sm danger", onclick: () => confirmBox("Удалить аккаунт?", `«${a.label}» и сохранённые данные входа будут удалены из narez.`, "Удалить", async () => {
        try { await api("DELETE", `/api/tiktok/accounts/${a.id}`); await loadTikTok(); render(); } catch (e) { fail(e); } })}, "Убрать")))
      : h("div", {class: "muted"}, "Аккаунтов нет. Добавь один раз логин и пароль — дальше вход в один клик."),
    h("div", {class: "hint muted small", style: {marginTop: "10px"}}, "Кнопка «Войти» стирает вход TikTok (куки) в этом браузере, открывает страницу входа и вписывает данные. Капчу и код подтверждения вводишь сам. Автоматический вход нескольких аккаунтов с одного IP TikTok может счесть подозрительным — для надёжности держи аккаунты в отдельных профилях браузера или за разными прокси."));
}
async function ttCopy(text, label) {
  try { await navigator.clipboard.writeText(text); toast(label ? `Скопировано: ${label}` : "Скопировано"); }
  catch (e) { const t = h("textarea", {style: {position: "fixed", opacity: 0}}, text); document.body.append(t); t.select(); try { document.execCommand("copy"); toast(label ? `Скопировано: ${label}` : "Скопировано"); } catch (er) { fail(er); } t.remove(); }
}
async function ttCopyAll(c, useP) {
  const path = useP ? c.path_preset : c.path_original;
  // в буфер по очереди: путь, описание, название — в истории буфера Windows (Win+V) лежат все три, последним — название
  try {
    for (const t of [path, c.text, c.title]) { await navigator.clipboard.writeText(t); await new Promise(r => setTimeout(r, 250)); }
    toast("Скопировано в буфер: путь к файлу, описание, название (Win+V — история буфера)");
  } catch (e) { fail(new Error("Браузер не дал записать в буфер обмена: " + e.message)); }
}
function pageTikTok(page) {
  const T = S.tt || (S.tt = {clips: [], accounts: []});
  const list = h("div", {class: "tt-grid"});
  const presetSel = h("select", {style: {minWidth: "240px"}});
  const stickerSel = h("select", {style: {minWidth: "240px"}});
  const draw = () => {
    presetSel.replaceChildren(h("option", {value: ""}, "— без пресета баннеров —"), ...(S.presets || []).map(p => h("option", {value: p.id}, p.name)));
    stickerSel.replaceChildren(h("option", {value: ""}, "— без стикеров —"), ...(S.stickerPresets || []).map(p => h("option", {value: p.id}, p.name)));
    if (S.ttPreset) presetSel.value = S.ttPreset;
    if (S.ttSticker) stickerSel.value = S.ttSticker;
    const used = [...new Set(T.clips.map(c => c.preset).filter(Boolean))];
    note.textContent = T.clips.length ? `Клипов: ${T.clips.length}. ` + (used.length ? `Сейчас применён пресет: ${used.join(", ")}.` : "Пресет не применён — у всех клипов только оригинал.") : "";
    list.replaceChildren(...T.clips.map(ttCard));
    del.hidden = !T.clips.length;
    empty.hidden = !!T.clips.length;
  };
  const note = h("span", {class: "muted small grow"});
  presetSel.onchange = () => { S.ttPreset = Number(presetSel.value) || null; };
  stickerSel.onchange = () => { S.ttSticker = Number(stickerSel.value) || null; };
  const run = async (fn, okMsg) => { try { await fn(); } catch (e) { fail(e); } };
  const apply = h("button", {class: "btn primary", onclick: () => run(async () => {
    if (!S.ttPreset && !S.ttSticker) return toast("Выбери пресет баннеров и/или пресет стикеров", "err");
    const t = await api("POST", "/api/tiktok/preset", {preset_id: S.ttPreset || undefined, sticker_preset_id: S.ttSticker || undefined}); startTracking(t); toast("Применяю пресет ко всем клипам… (прошлый вариант с пресетом будет удалён)"); }) }, "Применить к клипам");
  const reset = h("button", {class: "btn", title: "Убрать последний пресет: останутся только оригиналы", onclick: () => run(async () => {
    await api("POST", "/api/tiktok/reset"); await loadTikTok(); toast("Пресет убран, остались оригиналы"); render(); }) }, "Убрать пресет");
  const del = h("button", {class: "btn danger", onclick: () => confirmBox("Удалить все клипы вкладки?", "Будут удалены и оригиналы, и клипы с последним пресетом (и исходное видео, если оно больше не нужно). Это освободит место на диске, вернуть файлы нельзя.", "Удалить всё", () => run(async () => {
    const r = await api("DELETE", "/api/tiktok/clips"); await loadTikTok(); toast(`Удалено клипов: ${r.deleted}, освобождено ~${r.freed_mb} МБ`); render(); })) }, "Удалить все клипы и освободить место");
  const empty = h("div", {class: "card empty"}, h("b", {}, "Здесь пока пусто"), "На «Панели» вставь ссылку и отметь «Для ручной выкладки в TikTok» — нарезанные клипы без баннера появятся здесь.",
    h("div", {style: {marginTop: "14px"}}, h("button", {class: "btn primary", onclick: () => go("panel")}, "Перейти на «Панель»")));
  page.append(h("div", {class: "card tt-block"}, h("h2", {}, "Пресеты для всех клипов", h("small", {}, "баннеры и/или стикеры; остаются оригинал + последний вариант")),
      h("div", {class: "row", style: {gap: "10px", flexWrap: "wrap"}}, presetSel, stickerSel, apply, reset, note)),
    empty, list, h("div", {class: "row", style: {justifyContent: "flex-end"}}, del));
  draw();
  api("GET", "/api/presets").then(p => { S.presets = p; draw(); }).catch(() => {});
  loadStickers().then(draw);
  loadTikTok().then(() => { if (S.page === "tiktok" && page.isConnected) { T.clips = S.tt.clips; draw(); } });
}
function ttCard(c) {
  const hasP = c.has_preset_file;
  let useP = hasP;
  const video = h("video", {controls: true, playsinline: true, preload: "metadata", poster: `/api/clips/${c.id}/thumb`});
  const setSrc = () => { video.src = `/api/clips/${c.id}/video?${useP ? "variant=final&" : ""}t=${Date.now()}`; };
  setSrc();
  const seg = h("div", {class: "seg"}, [["o", "Оригинал"], ["p", c.preset ? `С пресетом «${c.preset}»` : "С пресетом"]].map(([k, t]) =>
    h("button", {"data-k": k, disabled: k === "p" && !hasP, class: (k === "p") === useP ? "on" : "", onclick: e => { useP = k === "p"; seg.querySelectorAll("button").forEach(b => b.classList.toggle("on", (b.dataset.k === "p") === useP)); setSrc(); }}, t)));
  const title = h("input", {type: "text", value: c.title});
  const desc = h("textarea", {rows: 5}, c.description);
  const tags = h("input", {type: "text", value: c.tags, placeholder: "#теги"});
  const save = async () => {
    try { const r = await api("PATCH", `/api/clips/${c.id}`, {title: title.value, description: desc.value, tags: tags.value});
      c.title = r.title; c.description = r.description; c.tags = r.tags; c.text = (r.description + (r.tags ? "\n\n" + r.tags : "")).trim(); } catch (e) { fail(e); }
  };
  [title, desc, tags].forEach(el => el.addEventListener("change", save));
  const curPath = () => useP ? c.path_preset : c.path_original;
  const full = () => (desc.value.trim() + (tags.value.trim() ? "\n\n" + tags.value.trim() : "")).trim();
  return h("div", {class: "card tt-card"},
    h("div", {class: "tt-frame"}, video),
    h("div", {class: "tt-body"},
      c.preset || hasP ? seg : h("div", {class: "muted small"}, "Только оригинал (пресет не применён)"),
      h("label", {class: "muted small"}, "Название"), title,
      h("label", {class: "muted small"}, "Описание"), desc,
      h("label", {class: "muted small"}, "Теги"), tags,
      h("div", {class: "muted small tt-path", title: "Путь к файлу"}, curPath()),
      h("div", {class: "row", style: {gap: "6px", flexWrap: "wrap"}},
        h("button", {class: "btn primary", onclick: async () => { await save(); ttCopyAll({...c, title: title.value, text: full(), path_original: c.path_original, path_preset: c.path_preset}, useP); }}, "Скопировать всё"),
        h("button", {class: "btn sm", onclick: () => ttCopy(curPath(), "путь к файлу")}, "Путь"),
        h("button", {class: "btn sm", onclick: () => ttCopy(full(), "описание")}, "Описание"),
        h("button", {class: "btn sm", onclick: () => ttCopy(title.value, "название")}, "Название"),
        h("div", {class: "grow"}),
        h("button", {class: "btn sm danger", onclick: () => confirmBox("Удалить клип?", `«${c.title}» (оригинал и вариант с пресетом) будет удалён.`, "Удалить", async () => {
          try { await api("DELETE", `/api/clips/${c.id}`); await loadTikTok(); render(); } catch (e) { fail(e); } })}, "Удалить"))));
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
    await loadGroups(); await loadClips(); await loadTemplates(); await loadTikTok(); await loadStickers();
  } catch (e) { fail(e); }
  renderHealth(); render(); pollTasks();
  setInterval(async () => { try { S.health = await api("GET", "/api/health"); renderHealth(); } catch (e) {} }, 15000);
}
init();
