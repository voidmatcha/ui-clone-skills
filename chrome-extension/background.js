// Step B scaffold — service worker. Receives job dispatch from popup,
// will forward to native-messaging host (com.voidmatcha.ui_clone_skills)
// in the next commit.

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type !== "ui-clone-skills:job") return;
  // TODO: chrome.runtime.connectNative("com.voidmatcha.ui_clone_skills")
  //       and forward msg.{job, url} to the local mcp_server process.
  console.log("[ui-clone-skills] dispatched (scaffold):", msg);
  sendResponse({ ok: true, scaffold: true });
});
