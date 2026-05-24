// Step B scaffold — wires popup buttons to background service worker.
// The background worker will forward to the native-messaging host that
// talks to mcp_server. Native-messaging wiring is the next commit's work.

(() => {
  const buttons = document.querySelectorAll("button[data-job]");
  const conn = document.getElementById("conn");

  conn.textContent = "(native-messaging host not wired yet — see chrome-extension/README.md)";

  buttons.forEach((btn) => {
    btn.addEventListener("click", async () => {
      const job = btn.dataset.job;
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      const url = tab?.url || "";
      // Background worker will route this to native-messaging host
      // once that's wired. For now, dispatch a no-op message so the
      // event flow is testable.
      chrome.runtime.sendMessage({ type: "ui-clone-skills:job", job, url });
      btn.textContent = `${btn.textContent.split("—")[0].trim()} — dispatched (scaffold)`;
      btn.disabled = true;
    });
  });
})();
