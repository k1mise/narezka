const LOGIN_URL = "https://www.tiktok.com/login/phone-or-email/email";

async function clearTikTok() {
  const origins = ["https://www.tiktok.com", "https://tiktok.com", "https://m.tiktok.com", "https://business.tiktok.com"];
  await chrome.browsingData.remove({ origins }, { cookies: true, localStorage: true, indexedDB: true, cacheStorage: true, serviceWorkers: true });
  const all = await chrome.cookies.getAll({ domain: "tiktok.com" });
  await Promise.all(all.map((c) => chrome.cookies.remove({
    url: `http${c.secure ? "s" : ""}://${c.domain.replace(/^\./, "")}${c.path}`, name: c.name, storeId: c.storeId })));
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

async function doLogin(origin) {
  const r = await fetch(`${origin}/api/tiktok/pending`, { headers: { "X-Narez-Helper": "1" }, cache: "no-store" });
  if (!r.ok) throw new Error("Нет ожидающего входа: нажми «Войти» в narez ещё раз");
  const acc = await r.json();
  await clearTikTok();
  const tab = await chrome.tabs.create({ url: LOGIN_URL, active: true });
  await new Promise((resolve) => {
    const on = (id, info) => { if (id === tab.id && info.status === "complete") { chrome.tabs.onUpdated.removeListener(on); resolve(); } };
    chrome.tabs.onUpdated.addListener(on);
    setTimeout(resolve, 20000);
  });
  const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: fillLogin, args: [acc.login, acc.password] });
  return res && res.result ? res.result : { ok: false, error: "Не удалось заполнить форму" };
}

chrome.runtime.onMessage.addListener((msg, _sender, send) => {
  if (msg && msg.op === "login") {
    doLogin(msg.origin).then(send, (e) => send({ ok: false, error: String(e.message || e) }));
    return true;
  }
});
