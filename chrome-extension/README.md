# chrome-extension — Motion forensics, one click from any page

A Chrome / Edge / Brave extension that runs ui-clone-skills on the current tab. Click the toolbar icon → pick decode / clone / verify / extract → output streams to the popup.

## Status

**Scaffold — Step B of the positioning rollout.** Manifest v3 is in place; the popup currently shows the four-job selector but the background handler that bridges to the local MCP server (`mcp-server/`) is the next commit's work.

## Architecture

```
[browser tab] --(active tab URL)--> [popup.html] --(message)--> [background.js]
                                                                       |
                                                                       v
                                                  [native messaging host] -> [uv run python -m mcp_server]
```

The extension does not bundle the engine — it talks to the local MCP server via Chrome's native-messaging API. Install the engine first:

```bash
git clone https://github.com/voidmatcha/ui-clone-skills && cd ui-clone-skills
uv sync --group mcp
```

Then load `chrome-extension/` as an unpacked extension via `chrome://extensions/` → Developer mode → Load unpacked.

## Roadmap

- [ ] Native-messaging host manifest (~/Library/Application Support/Google/Chrome/NativeMessagingHosts/...)
- [ ] Popup → background → native-host wiring
- [ ] Receipt HTML preview pane (reuses `build-decode-receipt.sh` output)
- [ ] Per-tab status indicator (decode running / verify in progress / done)
