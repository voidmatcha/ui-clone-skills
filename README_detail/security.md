# Security

All skills process untrusted external content (DOM, CSS, JS bundles, screenshots) from arbitrary URLs. Built-in mitigations:

- **Prompt injection defense** — extracted data is wrapped in boundary markers and treated as display-only. All extraction sub-documents include explicit untrusted-data handling rules.
- **Post-extraction sanitization** — automated scans for suspicious patterns (`javascript:`, `eval(atob`, prompt injection phrases) in extracted JSON.
- **Content boundary enforcement** — `component-generation.md` never follows directives found in DOM text, HTML comments, CSS content properties, or `data-*` attributes.
- **Bundle safety** — HTTPS-only, size-limited (10 MB), time-limited (30s), read-only (grep only, never executed).
- **No credential forwarding** — `curl` sends no cookies or auth tokens.
- **Cleanup** — `tmp/ref/` (may contain PII-bearing screenshots) is removed after verification.

See each skill's `SKILL.md` for full details.
