#!/usr/bin/env bash
# body-opacity-unlock-check.sh — enforce the runtime unlock that
# ref-css-sanitize-report.json flags as required.
#
# Production sites commonly ship first-paint locks (`body{opacity:0}`,
# `html.is-loading{visibility:hidden}`) that their JS releases after boot.
# sanitize-ref-css.sh DETECTS those locks (requiresRuntimeUnlock + hints) but
# nothing enforced that the impl actually releases them — an impl importing the
# preserved ref CSS without an unlock renders an invisible page, which shows up
# only as uniformly catastrophic AE with no usable gradient (loop A-06 unit 1).
#
# Usage:
#   bash body-opacity-unlock-check.sh <ref-dir> <impl-root>
#
# Output:
#   <ref-dir>/body-opacity-unlock.json

set -uo pipefail

REF_DIR="${1:?Usage: body-opacity-unlock-check.sh <ref-dir> <impl-root>}"
IMPL_ROOT="${2:?Missing impl-root}"
OUT="$REF_DIR/body-opacity-unlock.json"
mkdir -p "$REF_DIR"

python3 - "$REF_DIR" "$IMPL_ROOT" "$OUT" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
impl_root = Path(sys.argv[2])
out_path = Path(sys.argv[3])

SKIP_DIRS = {"node_modules", ".next", "dist", "build", "coverage", ".git"}
IMPL_EXTS = {".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".sass", ".html"}

JS_UNLOCK_RE = re.compile(
    r"(?:document\s*\.\s*)?body\s*\.\s*style\s*\.\s*(?:opacity|visibility|display)\s*=",
    re.IGNORECASE,
)
CSS_BODY_UNLOCK_RE = re.compile(
    r"(?:^|[,}\s])(?:html\s+)?body[^{}]*\{[^{}]*(?:"
    r"opacity\s*:\s*(?:1|0?\.[1-9]\d*)|"
    r"visibility\s*:\s*visible|"
    r"display\s*:\s*(?:block|flex|grid|initial|revert|unset)"
    r")",
    re.IGNORECASE,
)
CLASS_TOKEN_RE = re.compile(r"\.([A-Za-z_][\w-]*)")
HIDDEN_ROOT_SELECTOR_RE = re.compile(
    r"(^|[,\s])(?P<selector>(?:html|body)(?:[.#:[\]\w=\"'-]+)?|#(?:root|__next|app))\s*(?:,|\{)",
    re.IGNORECASE,
)
HIDDEN_DECL_RE = re.compile(
    r"(?:opacity\s*:\s*(?:0(?:\.0+)?)(?:\s*!important)?\b|"
    r"visibility\s*:\s*hidden(?:\s*!important)?\b|"
    r"display\s*:\s*none(?:\s*!important)?\b)",
    re.IGNORECASE,
)
RUNTIME_UNLOCK_SCAN_CHARS = 256 * 1024


def write(payload: dict) -> None:
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def runtime_unlock_hints(text: str, rel_source: str) -> list[dict[str, str]]:
    hints: list[dict[str, str]] = []
    window = text[:RUNTIME_UNLOCK_SCAN_CHARS]
    search_from = 0
    while len(hints) < 20:
        open_idx = window.find("{", search_from)
        if open_idx == -1:
            break
        close_idx = window.find("}", open_idx + 1)
        if close_idx == -1:
            break
        selector_start = max(window.rfind("}", 0, open_idx), window.rfind("{", 0, open_idx)) + 1
        selectors = window[selector_start:open_idx].strip()
        body = window[open_idx + 1 : close_idx]
        search_from = open_idx + 1
        if not HIDDEN_ROOT_SELECTOR_RE.search(selectors + "{"):
            continue
        decl = HIDDEN_DECL_RE.search(body)
        if not decl:
            continue
        hints.append({
            "source": rel_source,
            "selector": selectors[:160],
            "declaration": decl.group(0)[:120],
        })
    return hints


def scan_impl_ref_css_report() -> dict | None:
    ref_css_dir = impl_root / "src" / "ref-css"
    if not ref_css_dir.is_dir():
        return None
    hints: list[dict[str, str]] = []
    destinations: list[str] = []
    for css in sorted(ref_css_dir.glob("*.css")):
        if not css.is_file():
            continue
        rel = str(css.relative_to(impl_root)).replace("\\", "/")
        destinations.append(rel)
        try:
            hints.extend(runtime_unlock_hints(css.read_text(encoding="utf-8", errors="ignore"), rel))
        except OSError:
            continue
    if not hints:
        return None
    return {
        "requiresRuntimeUnlock": True,
        "runtimeUnlockHints": hints[:50],
        "files": [{"destination": rel} for rel in destinations],
        "inferredFromImplRefCss": True,
    }


report_path = ref_dir / "ref-css-sanitize-report.json"
if not report_path.is_file():
    report = scan_impl_ref_css_report()
    if report is None:
        write({
            "schemaVersion": 1,
            "status": "skip",
            "reasons": ["no ref-css-sanitize-report.json and no root lock found under impl/src/ref-css"],
        })
        print("✅ Body opacity unlock: SKIP (no sanitize report/ref-css root lock)")
        sys.exit(0)
else:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        write({"schemaVersion": 1, "status": "fail", "reasons": [f"unreadable sanitize report: {exc}"]})
        print("❌ Body opacity unlock: FAIL (unreadable sanitize report)")
        sys.exit(1)

requires = bool(report.get("requiresRuntimeUnlock"))
hints = [h for h in (report.get("runtimeUnlockHints") or []) if isinstance(h, dict)]
if not requires:
    write({
        "schemaVersion": 1,
        "status": "pass",
        "requiresRuntimeUnlock": False,
        "reasons": ["sanitize report flags no first-paint root lock"],
    })
    print("✅ Body opacity unlock: PASS (no lock to release)")
    sys.exit(0)

# Files copied by the sanitizer ARE the lock source — they cannot also be the
# unlock evidence.
sanitized_destinations = {
    str(item.get("destination", "")).replace("\\", "/").lstrip("./")
    for item in (report.get("files") or [])
    if isinstance(item, dict)
}

hint_class_tokens: set[str] = set()
for hint in hints:
    hint_class_tokens.update(CLASS_TOKEN_RE.findall(str(hint.get("selector", ""))))
class_unlock_re = (
    re.compile(
        r"classList\s*\.\s*(?:remove|toggle|add)\s*\(\s*['\"](?:"
        + "|".join(re.escape(token) for token in sorted(hint_class_tokens))
        + r")['\"]",
    )
    if hint_class_tokens
    else None
)

evidence: list[dict[str, str]] = []
for path in sorted(impl_root.rglob("*")):
    if any(part in SKIP_DIRS for part in path.parts):
        continue
    if not path.is_file() or path.suffix.lower() not in IMPL_EXTS:
        continue
    rel = str(path.relative_to(impl_root)).replace("\\", "/")
    if rel in sanitized_destinations:
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:1_000_000]
    except OSError:
        continue
    for kind, regex in (
        ("js-body-style-unlock", JS_UNLOCK_RE),
        ("css-body-unlock-rule", CSS_BODY_UNLOCK_RE),
        ("hint-class-toggle", class_unlock_re),
    ):
        if regex is None:
            continue
        match = regex.search(text)
        if match:
            evidence.append({
                "kind": kind,
                "file": rel,
                "snippet": text[max(0, match.start() - 60): match.end() + 60].replace("\n", " ")[:200],
            })
            break

status = "pass" if evidence else "fail"
write({
    "schemaVersion": 1,
    "status": status,
    "requiresRuntimeUnlock": True,
    "hints": hints[:20],
    "evidence": evidence[:20],
    "summary": (
        "Ref CSS ships a first-paint root lock and the impl releases it at runtime."
        if evidence else
        "Ref CSS ships a first-paint root lock (e.g. body{opacity:0}) but no impl "
        "source releases it — the page will render invisible. Unlock body "
        "opacity/visibility in a mount effect or a local override stylesheet."
    ),
})
if status == "fail":
    print("❌ Body opacity unlock: FAIL (lock required but never released)")
    sys.exit(1)
print(f"✅ Body opacity unlock: PASS ({len(evidence)} evidence file(s))")
PY
