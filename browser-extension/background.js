const LOGIN_URL = "https://www.tiktok.com/login/phone-or-email/email";
const HOME_URL = "https://www.tiktok.com/";
const HOST_DOMAIN = "tiktok.com";
const ORIGINS = ["https://www.tiktok.com", "https://tiktok.com", "https://m.tiktok.com", "https://business.tiktok.com"];
const SESSION_COOKIE = "sessionid";
const KEEP_COOKIES = new Set(["cookie-consent"]);   // ответ на баннер про куки не стираем и не подменяем при смене аккаунта

// Сессии аккаунтов (куки) хранятся в хранилище расширения: при переключении аккаунта куки уходящего сохраняются,
// куки нужного возвращаются — TikTok не видит «нового устройства» и не просит код с почты каждый раз.
const store = {
  get: async (k, d) => ((await chrome.storage.local.get(k))[k] ?? d),
  set: (k, v) => chrome.storage.local.set({ [k]: v }),
};

async function tiktokCookies() { return chrome.cookies.getAll({ domain: HOST_DOMAIN }); }

async function clearTikTok() {
  // стираем только куки сессии: данные страницы с идентификатором устройства и ответ на баннер про куки остаются
  const all = (await tiktokCookies()).filter((c) => !KEEP_COOKIES.has(c.name));
  await Promise.all(all.map((c) => chrome.cookies.remove({
    url: `http${c.secure ? "s" : ""}://${c.domain.replace(/^\./, "")}${c.path}`, name: c.name, storeId: c.storeId })));
}

async function saveSession(accId) {
  const all = await tiktokCookies();
  if (!all.some((c) => c.name === SESSION_COOKIE)) return false;     // не вошли — нечего сохранять
  const snaps = await store.get("snaps", {});
  snaps[accId] = all.filter((c) => !KEEP_COOKIES.has(c.name)).map(({ name, value, domain, path, secure, httpOnly, sameSite, expirationDate, hostOnly }) =>
    ({ name, value, domain, path, secure, httpOnly, sameSite, expirationDate, hostOnly }));
  await store.set("snaps", snaps);
  return true;
}

async function restoreSession(accId) {
  const snap = (await store.get("snaps", {}))[accId];
  if (!snap || !snap.some((c) => c.name === SESSION_COOKIE)) return false;
  const now = Date.now() / 1000;
  for (const c of snap) {
    if (c.expirationDate && c.expirationDate < now) continue;
    const host = c.domain.replace(/^\./, "");
    try {
      await chrome.cookies.set({ url: `http${c.secure ? "s" : ""}://${host}${c.path}`, name: c.name, value: c.value, path: c.path,
        secure: c.secure, httpOnly: c.httpOnly, ...(c.sameSite && c.sameSite !== "unspecified" ? { sameSite: c.sameSite } : {}),
        ...(c.hostOnly ? {} : { domain: c.domain }), ...(c.expirationDate ? { expirationDate: c.expirationDate } : {}) });
    } catch (e) { /* отдельная кука не встала — остальные всё равно полезны */ }
  }
  return true;
}

// выполняется внутри страницы входа TikTok
function fillLogin(login, password) {
  const setVal = (el, v) => {
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    el.focus(); set.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true }));
  };
  return new Promise((resolve) => {
    const t0 = Date.now();
    const tick = () => {
      const u = document.querySelector('input[name="username"], input[type="text"][placeholder*="mail"], input[type="text"]');
      const p = document.querySelector('input[type="password"]');
      if (u && p) {
        setVal(u, login); setVal(p, password);
        setTimeout(() => {
          const b = document.querySelector('button[data-e2e="login-button"], button[type="submit"]');
          if (b && !b.disabled) { b.click(); resolve({ ok: true, clicked: true }); }
          else resolve({ ok: true, clicked: false });
        }, 500);
      } else if (Date.now() - t0 > 25000) resolve({ ok: false, error: "Форма входа не появилась (возможно, TikTok показал капчу или изменил страницу)" });
      else setTimeout(tick, 400);
    };
    tick();
  });
}

// выполняется внутри страницы TikTok: закрывает всплывающие окна («подтвердите аккаунт», «войти» и т. п.)
function closePopups() {
  return new Promise((resolve) => {
    const t0 = Date.now(); let closed = 0, quiet = 0;
    const SEL = '[data-e2e="modal-close-inner-button"], [data-e2e="modal-close-button"], [aria-label="Close"], [aria-label="Закрыть"], [aria-label="close"]';
    const tick = () => {
      const dlg = document.querySelector('[role="dialog"], [class*="Modal"], [class*="modal"], [class*="DivModal"]');
      let did = false;
      if (dlg) {
        const btn = dlg.querySelector(SEL) || document.querySelector(SEL) ||
          [...dlg.querySelectorAll("button, div[role=button]")].find((b) => /^(закрыть|не сейчас|позже|close|not now|later|cancel|отмена)$/i.test((b.innerText || "").trim()));
        if (btn) { btn.click(); closed++; did = true; }
      }
      quiet = did ? 0 : quiet + 1;
      if (Date.now() - t0 > 12000 || (closed > 0 && quiet > 4)) resolve({ closed });
      else setTimeout(tick, 500);
    };
    setTimeout(tick, 1200);
  });
}

// выполняется внутри страницы TikTok: нажимает «Отклонить необязательные» (или «Разрешить все») в баннере про куки
function dismissCookies() {
  return new Promise((resolve) => {
    const t0 = Date.now();
    const walk = (root, out) => { for (const e of root.querySelectorAll("*")) { if (e.shadowRoot) { out.push(e.shadowRoot); walk(e.shadowRoot, out); } } return out; };
    const tick = () => {
      const roots = [document, ...walk(document, [])];
      for (const r of roots) {
        const btns = [...r.querySelectorAll("button, [role=button], a")];
        const b = btns.find((x) => /decline optional|отклонить необязательные|decline all/i.test(x.innerText || x.textContent || "")) ||
                  btns.find((x) => /allow all|разрешить все|accept all|принять все/i.test(x.innerText || x.textContent || ""));
        if (b) { b.click(); return resolve({ ok: true }); }
      }
      if (Date.now() - t0 > 5000) resolve({ ok: false }); else setTimeout(tick, 400);
    };
    tick();
  });
}

function openAndWait(url) {
  return chrome.tabs.create({ url, active: true }).then((tab) => new Promise((resolve) => {
    const on = (id, info) => { if (id === tab.id && info.status === "complete") { chrome.tabs.onUpdated.removeListener(on); resolve(tab); } };
    chrome.tabs.onUpdated.addListener(on);
    setTimeout(() => resolve(tab), 20000);
  }));
}

async function doLogin(origin, force) {
  const r = await fetch(`${origin}/api/tiktok/pending`, { headers: { "X-Narez-Helper": "1" }, cache: "no-store" });
  if (!r.ok) throw new Error("Нет ожидающего входа: нажми «Войти» в narez ещё раз");
  const acc = await r.json();
  const active = await store.get("active", null);
  if (active != null) await saveSession(active);                 // запоминаем аккаунт, из которого выходим
  await clearTikTok();
  await store.set("active", acc.id);
  if (!force && await restoreSession(acc.id)) {
    const tab = await openAndWait(HOME_URL);
    try { await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: dismissCookies }); } catch (e) {}
    let closed = 0;
    try { const [r2] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: closePopups }); closed = (r2 && r2.result && r2.result.closed) || 0; } catch (e) { /* вкладку закрыли — не страшно */ }
    return { ok: true, restored: true, closed };
  }
  const tab = await openAndWait(LOGIN_URL);
  try { await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: dismissCookies }); } catch (e) {}
  const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: fillLogin, args: [acc.login, acc.password] });
  return res && res.result ? res.result : { ok: false, error: "Не удалось заполнить форму" };
}

chrome.runtime.onMessage.addListener((msg, _sender, send) => {
  if (msg && msg.op === "login") {
    doLogin(msg.origin, !!msg.force).then(send, (e) => send({ ok: false, error: String(e.message || e) }));
    return true;
  }
});
