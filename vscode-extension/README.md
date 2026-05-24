# vscode-extension — Motion forensics inside VS Code

Run ui-clone-skills jobs from the VS Code command palette. Highlight a URL in any file → Right-click → `Motion forensics: Decode` (or Clone / Verify / Extract).

## Status

**Scaffold — Step B of the positioning rollout.** `package.json` is in place with the four commands declared. Each command currently `exec`s the local mcp_server. Activation event: `onCommand:ui-clone-skills.<job>`.

## Install (dev)

```bash
cd vscode-extension
npm install
npm run compile
# F5 in VS Code to launch the Extension Development Host
```

## Roadmap

- [ ] Wire each command to mcp_server tools via local stdio
- [ ] Status-bar progress indicator
- [ ] Receipt webview panel that renders build-decode-receipt.sh output
