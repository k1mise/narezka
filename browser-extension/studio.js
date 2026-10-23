// Страница загрузки TikTok Studio: по заданию из narez кладёт в форму файл, вписывает описание и (по желанию) время выхода.
// Кнопку «Опубликовать / Запланировать» не нажимает — это делает человек. Ход работы виден в панели справа вверху.
(() => {
  if (window.__narezStudio) return;
  window.__narezStudio = true;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const bg = (msg) => new Promise((res, rej) => chrome.runtime.sendMessage(msg, (r) => {
    const e = chrome.runtime.lastError; e ? rej(new Error(e.message)) : res(r);
  }));
  const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const waitFor = async (fn, ms = 30000, step = 300) => {
    const t0 = Date.now();
    for (;;) { const v = fn(); if (v) return v; if (Date.now() - t0 > ms) return null; await sleep(step); }
  };
  const txt = (el) => (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
  const MONTHS = [["january", "январ"], ["february", "феврал"], ["march", "март"], ["april", "апрел"], ["may", "ма"], ["june", "июн"],
                  ["july", "июл"], ["august", "август"], ["september", "сентябр"], ["october", "октябр"], ["november", "ноябр"], ["december", "декабр"]];

  // ---------- панель с ходом работы ----------
  let panel = null, logEl = null, job = null;
  function ui() {
    if (panel) return;
    panel = document.createElement("div");
    panel.style.cssText = "position:fixed;top:12px;right:12px;z-index:2147483647;width:340px;max-height:70vh;overflow:auto;background:#111;color:#eee;" +
      "font:13px/1.45 system-ui,sans-serif;border:1px solid #444;border-radius:10px;padding:10px 12px;box-shadow:0 6px 24px rgba(0,0,0,.5)";
    panel.innerHTML = '<div style="font-weight:600;margin-bottom:6px">narez → TikTok Studio</div><div data-log></div>' +
      '<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap"><button data-dump style="cursor:pointer">Скопировать структуру страницы</button>' +
      '<button data-close style="cursor:pointer">Скрыть</button></div>';
    logEl = panel.querySelector("[data-log]");
    panel.querySelector("[data-close]").onclick = () => panel.remove();
    panel.querySelector("[data-dump]").onclick = async () => {
      try { await navigator.clipboard.writeText(dumpPage()); say("Структура страницы скопирована в буфер — отправь её разработчику", "ok"); }
      catch (e) { say("Не удалось скопировать: " + e.message, "err"); }
    };
    document.documentElement.appendChild(panel);
  }
  function say(text, kind = "info") {
    ui();
    const row = document.createElement("div");
    row.textContent = (kind === "ok" ? "✓ " : kind === "warn" ? "⚠ " : kind === "err" ? "✗ " : "• ") + text;
    row.style.color = kind === "ok" ? "#7ee787" : kind === "warn" ? "#f0c36a" : kind === "err" ? "#ff8b8b" : "#ddd";
    logEl.appendChild(row);
    panel.scrollTop = panel.scrollHeight;
    if (job) bg({ op: "note", job, text: row.textContent }).catch(() => {});
  }

  // компактное описание интерактивных элементов страницы — для подгонки под изменившуюся вёрстку
  function dumpPage() {
    const sel = "input, textarea, button, [role=button], [role=radio], [role=textbox], [contenteditable], select, label, [data-e2e]";
    const out = [];
    for (const el of document.querySelectorAll(sel)) {
      if (!visible(el) && el.type !== "file") continue;
      out.push({ tag: el.tagName.toLowerCase(), type: el.type || "", text: txt(el).slice(0, 60), aria: el.getAttribute("aria-label") || "",
        e2e: el.getAttribute("data-e2e") || "", role: el.getAttribute("role") || "", ph: el.getAttribute("placeholder") || "",
        value: typeof el.value === "string" ? el.value.slice(0, 40) : "", cls: String(el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className).slice(0, 80),
        ce: el.getAttribute("contenteditable") || "", ro: !!el.readOnly });
      if (out.length >= 400) break;
    }
    return JSON.stringify({ url: location.href, frames: window.top === window ? "top" : "iframe", items: out }, null, 1);
  }

  // ---------- шаги ----------
  async function putFile(input) {
    const info = await bg({ op: "fileinfo", job });
    if (!info || info.error || !info.size) throw new Error((info && info.error) || "не удалось получить размер файла");
    const CH = 6_000_000, parts = [];
    for (let off = 0; off < info.size; off += CH) {
      const r = await bg({ op: "chunk", job, offset: off, size: CH });
      if (!r || r.error) throw new Error((r && r.error) || "ошибка чтения файла");
      const bin = atob(r.b64), u8 = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
      parts.push(u8);
      if (info.size > CH) say(`Читаю файл: ${Math.min(100, Math.round(((off + CH) / info.size) * 100))}%`);
    }
    const file = new File(parts, "narez-clip.mp4", { type: info.type || "video/mp4" });
    const dt = new DataTransfer(); dt.items.add(file);
    input.files = dt.files;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return file;
  }

  const editorEl = () => [...document.querySelectorAll('.public-DraftEditor-content, [contenteditable="true"], [role="textbox"]')].find(visible);

  // Способы ввода описания: TikTok на некоторых страницах роняет редактор от конкретного способа, поэтому пробуем по очереди
  async function setCaption(ed, text, mode) {
    const lines = String(text).replace(/\r/g, "").split("\n");
    const probe = lines.find((l) => l.trim()) || "";
    const has = () => probe && (ed.innerText || ed.textContent || "").includes(probe.replace(/^#/, "").slice(0, 10));
    ed.focus(); await sleep(400);
    if (mode === "type") {            // как будто печатает человек
      document.execCommand("selectAll", false); document.execCommand("delete", false);
      lines.forEach((l, i) => { if (i) document.execCommand("insertParagraph", false); if (l) document.execCommand("insertText", false, l); });
      await sleep(300);
      ed.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));   // закрыть подсказки хэштегов
    } else if (mode === "paste") {    // вставка из буфера целиком, без построчного набора и без служебных клавиш
      document.execCommand("selectAll", false);
      const dt = new DataTransfer(); dt.setData("text/plain", String(text));
      ed.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
      await sleep(500);
      if (!has() || /narez-clip/i.test(ed.innerText || "")) { document.execCommand("selectAll", false); document.execCommand("insertText", false, String(text)); await sleep(400); }   // редактор не принял вставку — одним куском
    } else {                          // plain: одна строка без # и переводов строк — самый простой ввод
      const one = lines.filter((l) => l.trim()).join(" ").replace(/#/g, "").replace(/\s+/g, " ").trim().slice(0, 150);
      document.execCommand("selectAll", false); document.execCommand("insertText", false, one);
      await sleep(400);
    }
    return has();
  }

  const SCHEDULE_RE = /^(schedule|запланировать|по расписанию|расписание|отложенная публикация|запланированная публикация)$/i;
  function findScheduleToggle() {
    for (const r of document.querySelectorAll('input[type="radio"], [role="radio"]')) {
      const host = r.closest("label") || r.parentElement;
      if (host && SCHEDULE_RE.test(txt(host)) && visible(host)) return host;
    }
    const all = [...document.querySelectorAll("label, span, div, button, [role=switch], [role=radio]")].filter((e) => visible(e) && SCHEDULE_RE.test(txt(e)));
    return all.sort((a, b) => txt(a).length - txt(b).length)[0] || null;
  }
  const DATE_RE = /^(\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4}|\d{2}\/\d{2}\/\d{4})$/;
  const TIME_RE = /^\d{1,2}:\d{2}(\s?[AP]M)?$/i;
  const fields = () => {
    const ins = [...document.querySelectorAll("input")].filter((e) => e.type !== "file" && e.type !== "radio" && e.type !== "checkbox" && visible(e));
    return { date: ins.find((e) => DATE_RE.test(e.value.trim())), time: ins.find((e) => TIME_RE.test(e.value.trim())) };
  };
  const pad = (n) => String(n).padStart(2, "0");
  const click = (el) => { el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })); el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true })); el.click(); };

  async function pickDate(input, y, m, d) {
    click(input); await sleep(500);
    const monthShown = () => { const t = (document.body.innerText || "").toLowerCase(); return MONTHS[m - 1].some((k) => t.includes(k)); };
    const dayCell = () => [...document.querySelectorAll("span, div, td, button, li")].filter((e) => visible(e) && txt(e) === String(d) && e.children.length === 0 &&
      !/disabled|invalid|other|outside|prev|next/i.test(String(e.className)) && e.getAttribute("aria-disabled") !== "true" && e !== input)
      .filter((e) => { const b = e.getBoundingClientRect(); const ib = input.getBoundingClientRect(); return b.top >= ib.bottom - 4 && b.top < ib.bottom + 420; })[0];
    // другой месяц — листаем вперёд (планировать можно максимум на 10 дней вперёд, поэтому хватит одного-двух кликов)
    for (let i = 0; i < 2 && !dayCell(); i++) {
      const next = [...document.querySelectorAll("span, div, button, svg, i")].filter(visible).find((e) => /arrow-?right|next|right-?arrow|chevron-?right/i.test(String(e.className && e.className.baseVal !== undefined ? e.className.baseVal : e.className)) || /next|след|вперёд/i.test(e.getAttribute("aria-label") || ""));
      if (!next) break; click(next); await sleep(400);
    }
    const cell = dayCell();
    if (!cell) return false;
    click(cell); await sleep(500);
    return DATE_RE.test(input.value.trim()) && input.value.replace(/\D/g, "").includes(pad(d)) ? true : DATE_RE.test(input.value.trim());
  }

  let pickerInfo = "";
  async function pickTime(input, hh, mm) {
    click(input); await sleep(700);
    const ib = input.getBoundingClientRect();
    // у прокручиваемых списков часть пунктов лежит выше видимой области — поэтому по вертикали не ограничиваем, только по горизонтали рядом с полем
    const below = (e) => { const r = e.getBoundingClientRect(); return e !== input && r.width > 0 && Math.abs(r.left - ib.left) < 1500; };
    const leaves = (re) => [...document.querySelectorAll("span, div, li, button, p, a")].filter((e) => e.children.length === 0 && re.test(txt(e)) && below(e));
    const press = async (el) => { el = el.closest('[role="option"], li') || el; try { el.scrollIntoView({ block: "center" }); } catch (e) {} await sleep(250); click(el); await sleep(400); };
    const cur = () => input.value.trim();
    // способ А: один список готовых значений «21:30»
    const full = leaves(/^\d{1,2}:\d{2}(\s?[AP]M)?$/i);
    if (full.length >= 6) {
      const val = (e) => { const m = /(\d{1,2}):(\d{2})\s?([AP]M)?/i.exec(txt(e)); let h = Number(m[1]); if (m[3]) h = (h % 12) + (/p/i.test(m[3]) ? 12 : 0); return h * 60 + Number(m[2]); };
      const want = hh * 60 + mm;
      const best = full.sort((x, y) => Math.abs(val(x) - want) - Math.abs(val(y) - want))[0];
      await press(best); document.body.click(); await sleep(200);
      return true;
    }
    // способ Б: два столбца — часы и минуты (столбцы находим по горизонтальному положению, обёртки вокруг чисел не важны)
    const columns = () => {
      const all = leaves(/^\d{1,2}$/);
      const group = (keyOf) => { const g = new Map(); for (const e of all) { const k = keyOf(e); (g.get(k) || g.set(k, []).get(k)).push(e); }
        return [...g.values()].filter((v) => v.length >= 6).sort((x, y) => x[0].getBoundingClientRect().left - y[0].getBoundingClientRect().left); };
      const byParent = group((e) => e.parentElement);                                         // числа лежат прямо в столбцах
      return byParent.length >= 2 ? byParent : group((e) => Math.round(e.getBoundingClientRect().left / 12));   // иначе (обёртки вокруг чисел) — по горизонтали
    };
    let cols = columns();
    pickerInfo = `чисел: ${leaves(/^\d{1,2}$/).length}; столбцов: ${cols.length}` + cols.map((c) => ` [${c.length}: ${c.slice(0, 4).map(txt).join(",")}…]`).join("") + `; значение: ${cur()}`;
    if (cols.length < 2) return false;
    const hourEl = cols[0].find((e) => Number(txt(e)) === hh) || cols[0].find((e) => Number(txt(e)) % 12 === hh % 12);
    if (!hourEl) return false;
    await press(hourEl);
    cols = columns();
    const mins = cols.length >= 2 ? cols[1] : [];
    const minEl = [...mins].sort((x, y) => Math.abs(Number(txt(x)) - mm) - Math.abs(Number(txt(y)) - mm))[0];
    if (!minEl) return false;
    await press(minEl);
    document.body.click(); await sleep(200);
    return true;
  }

  async function setSchedule(whenIso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(whenIso || "");
    if (!m) return say("Время выхода задано в неверном формате — выставь вручную", "warn");
    const [y, mo, d, hh, mm] = m.slice(1).map(Number);
    const tg = await waitFor(findScheduleToggle, 15000);
    if (!tg) return say("Не нашёл переключатель «Запланировать» — выставь время вручную (возможно, аккаунт не Creator/Business)", "warn");
    click(tg); await sleep(800);
    const f = await waitFor(() => { const x = fields(); return x.date && x.time ? x : null; }, 10000);
    if (!f) return say("После включения расписания не появились поля даты и времени — выставь вручную", "warn");
    const okD = await pickDate(f.date, y, mo, d);
    const okT = await pickTime(f.time, hh, mm);
    const now = fields();
    const dv = (now.date || f.date).value.trim(), tv = (now.time || f.time).value.trim();
    const dateOk = dv.replace(/\D/g, "").includes(pad(d)) && dv.replace(/\D/g, "").includes(String(y));
    const tm = /(\d{1,2}):(\d{2})/.exec(tv);
    const timeOk = !!tm && Number(tm[1]) % 12 === hh % 12 && Math.abs(Number(tm[2]) - mm) <= 5;   // минуты в TikTok идут с шагом 5 — ближайшее значение считаем успехом
    if (dateOk && timeOk) say(`Время выхода: ${dv} ${tv}`, "ok");
    else say((pickerInfo ? `[список времени: ${pickerInfo}] ` : "") + `Время выставилось не полностью (сейчас ${dv} ${tv}, нужно ${y}-${pad(mo)}-${pad(d)} ${pad(hh)}:${pad(mm)}) — поправь вручную`, "warn");
  }

  // ---------- основной сценарий ----------
  async function main() {
    // после падения страницы TikTok перезагружает её: продолжаем с того же места (состояние лежит в sessionStorage вкладки)
    let resume = null;
    try { resume = JSON.parse(sessionStorage.getItem("narezResume") || "null"); sessionStorage.removeItem("narezResume"); } catch (e) {}
    if (resume && Date.now() - resume.ts > 5 * 60 * 1000) resume = null;
    let input;
    if (resume) {
      input = await waitFor(() => document.querySelector('input[type="file"]'), 60000);
      if (!input) return;
      job = resume.job; say("Страница перезагрузилась — продолжаю, пробую другой способ ввода описания", "warn");
    } else {
      const peek = await bg({ op: "peekJob" }).catch(() => null);
      if (!peek || !peek.has) return;                                 // заданий нет — обычное открытие страницы
      input = await waitFor(() => document.querySelector('input[type="file"]'), 60000);
      if (!input) return;                                             // этот фрейм не форма загрузки
      const claimed = await bg({ op: "claimJob" }).catch(() => null);
      if (!claimed || !claimed.job) return;                           // задание забрал другой фрейм
      job = claimed.job;
    }
    const MODES = ["paste", "plain"];                                 // «type» ронял редактор TikTok у пользователя — не используем по умолчанию
    say("Начинаю: загружаю клип в форму…");
    // TikTok иногда роняет страницу («Что-то пошло не так»). Смотрим после каждого шага; при падении жмём «Повторить»
    // и повторяем уже без шага, после которого оно упало — тогда понятно, что именно её ломает, и остальное всё равно сделается
    const errShown = () => /что-то пошло не так|something went wrong/i.test((document.querySelector("#app, #root, main, body") || document.body).innerText || "");
    const settle = async (ms) => { for (let t = 0; t < ms; t += 500) { if (errShown()) return true; await sleep(500); } return errShown(); };
    const retry = async () => {
      try { sessionStorage.setItem("narezResume", JSON.stringify({ job, ts: Date.now(), skip: [...skip], mi })); } catch (e) {}
      const b = [...document.querySelectorAll("button, [role=button]")].find((x) => /^(повторить|retry|try again)$/i.test(txt(x)));
      if (b) click(b);
      else location.reload();
      await sleep(2500);
      return waitFor(() => document.querySelector('input[type="file"]'), 60000);
    };
    const skip = new Set(resume ? resume.skip : []);
    let mi = resume ? resume.mi : 0;
    let inp = input;
    for (let attempt = 1; attempt <= 3; attempt++) {
      if (!inp) return say("Страница загрузки не открылась после ошибки TikTok — добавь видео вручную", "err");
      let bad = null;
      try { await putFile(inp); say("Файл добавлен, жду форму описания…", "ok"); }
      catch (e) { return say("Не удалось добавить файл: " + e.message + ". Добавь видео вручную.", "err"); }
      if (await settle(6000)) bad = "file";
      let ed = null;
      if (!bad) { ed = await waitFor(editorEl, 180000); if (!ed) return say("Поле описания не появилось — впиши описание вручную (кнопка «Описание» в narez)", "warn"); if (errShown()) bad = "file"; }
      if (!bad && job.caption && !skip.has("caption")) {
        const ok = await setCaption(ed, job.caption, MODES[mi]);
        // TikTok сам дописывает в описание имя файла (иногда уже после нашей вставки) — ждём и вычищаем, повторяя вставку
        for (let k = 0; k < 3; k++) {
          await sleep(2500);
          if (!/narez-clip/i.test(ed.innerText || "")) break;
          if (errShown()) break;
          say("TikTok подставил имя файла в описание — заменяю", "warn");
          await setCaption(ed, job.caption, MODES[mi]);
        }
        const clean = !/narez-clip/i.test(ed.innerText || "");
        say(ok && clean ? `Описание вписано (способ: ${MODES[mi]})` : ok ? "В описании остался лишний текст (имя файла) — удали его вручную" : "Описание могло вписаться не полностью — проверь", ok && clean ? "ok" : "warn");
        if (await settle(4000)) bad = "caption";
      }
      if (!bad && job.schedule && !skip.has("schedule")) {
        await setSchedule(job.schedule).catch((e) => say("Время выхода: " + e.message, "warn"));
        if (await settle(3000)) bad = "schedule";
      }
      if (!bad && job.caption && !skip.has("caption") && ed) {      // имя файла TikTok может дописать позже всего остального — ещё немного наблюдаем
        for (let t = 0; t < 12; t++) {
          await sleep(1000);
          if (errShown()) { bad = "caption"; break; }
          if (/narez-clip/i.test(ed.innerText || "")) { say("TikTok подставил имя файла в описание — заменяю", "warn"); await setCaption(ed, job.caption, MODES[mi]); await sleep(1500); }
        }
        if (/narez-clip/i.test(ed.innerText || "")) say("В описании остался лишний текст (имя файла) — удали его вручную", "warn");
      }
      if (!bad) return say("Готово. Проверь видео, описание и время, затем нажми «Опубликовать» (или «Запланировать») сам." + (skip.size ? " Пропущено из-за ошибки TikTok: " + [...skip].map((k) => ({ caption: "описание", schedule: "время" }[k])).join(", ") + " — впиши вручную." : ""), skip.size ? "warn" : "ok");
      say(`TikTok показал страницу с ошибкой после шага «${{ file: "добавление файла", caption: "описание", schedule: "время" }[bad]}» — пробую ещё раз`, "warn");
      if (bad === "file" && attempt >= 2) return say("TikTok роняет страницу сразу после добавления файла. Добавь видео вручную (путь — кнопка «Путь» в narez)", "err");
      if (bad === "caption") { mi++; if (mi >= MODES.length) skip.add("caption"); }
      else if (bad) skip.add(bad);
      inp = await retry();
      try { sessionStorage.removeItem("narezResume"); } catch (e) {}   // страница не перезагружалась — продолжаем в этом же экземпляре
      if (!inp) return;      // при перезагрузке страницы сценарий продолжит новый экземпляр скрипта
    }
  }
  main().catch((e) => { try { say("Ошибка: " + e.message, "err"); } catch (_) { /* страница закрыта */ } });
})();
