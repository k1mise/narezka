// Мост между страницей narez и расширением. Работает только на localhost и только на сообщения самой страницы.
window.addEventListener("message", (ev) => {
  if (ev.source !== window || !ev.data || ev.data.narez !== "tiktok") return;
  if (ev.data.op === "ping") {
    window.postMessage({ narez: "tiktok-reply", op: "pong", version: chrome.runtime.getManifest().version }, "*");
  } else if (ev.data.op === "login") {
    chrome.runtime.sendMessage({ op: "login", origin: location.origin }, (res) => {
      const err = chrome.runtime.lastError;
      window.postMessage({ narez: "tiktok-reply", op: "login", ok: !err && !!(res && res.ok),
                           error: err ? err.message : (res && res.error) || "" }, "*");
    });
  }
});
