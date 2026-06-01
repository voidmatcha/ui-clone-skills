#!/usr/bin/env bash
# forced-state-class-check.sh — Block static reveal-all / final-state patches.
#
# Usage:
#   bash forced-state-class-check.sh <ref-dir> <impl-root>
#
# Output:
#   <ref-dir>/forced-state-class.json

set -uo pipefail

REF_DIR="${1:?Usage: forced-state-class-check.sh <ref-dir> <impl-root>}"
IMPL_ROOT="${2:?Missing impl-root}"
OUT="$REF_DIR/forced-state-class.json"
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

STATE_CLASS_RE = re.compile(r"\b(is-active|is-visible|is-show|is-hide)\b")
DYNAMIC_REF_RE = re.compile(
    r"trigger\s*['\"]?:\s*['\"]?(scroll|intersection|inview)|"
    r"classList\.(add|toggle)\s*\(\s*['\"]is-(?:active|visible|show|hide)['\"]|"
    r"ScrollTrigger|scrollYProgress|IntersectionObserver|useScroll",
    re.IGNORECASE,
)
HARDCODED_CLASS_RE = re.compile(
    r"(?:className|class)\s*=\s*([\"'`])(?P<value>[^\"'`]*(?:is-active|is-visible|is-show|is-hide)[^\"'`]*)\1",
    re.IGNORECASE,
)
FORCED_FINAL_RE = re.compile(
    r"(?P<prop>transition\s*:\s*none|opacity\s*:\s*1|transform\s*:\s*none)(?:\s*!important)?",
    re.IGNORECASE,
)
BLANKET_STATE_RULE_RE = re.compile(
    r"(?P<selector>[^{}]{0,320}\b(?:is-active|is-visible|is-show|is-hide)\b[^{}]{0,320})"
    r"\{(?P<body>[^{}]{0,900})\}",
    re.IGNORECASE,
)
FINAL_DECL_RE = re.compile(
    r"transition\s*:\s*none|opacity\s*:\s*1|transform\s*:\s*none",
    re.IGNORECASE,
)
REVEAL_ALL_RE = re.compile(
    r"querySelectorAll\([\s\S]{0,240}?\.forEach\([\s\S]{0,240}?classList\.add\(\s*['\"](?P<class>is-active|is-visible|is-show)['\"]",
    re.IGNORECASE,
)

SKIP_DIRS = {"node_modules", ".next", "dist", "build", "coverage", ".git"}
REF_EXTS = {".js", ".json", ".css", ".html", ".txt"}
IMPL_EXTS = {".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".sass"}


def read_limited(path: Path, limit: int = 1_000_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def iter_files(root: Path, exts: set[str]) -> list[Path]:
    if not root.exists():
        return []
    files: list[Path] = []
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix in exts:
            files.append(path)
    return files


def has_dynamic_state_context(text: str, start: int, end: int) -> bool:
    brace = text.rfind("{", 0, start)
    prev_close = text.rfind("}", 0, start)
    if brace > prev_close:
        selector = text[prev_close + 1: brace]
        if STATE_CLASS_RE.search(selector):
            return True
    nearby = text[max(0, start - 240): min(len(text), end + 120)]
    return bool(STATE_CLASS_RE.search(nearby) or REVEAL_ALL_RE.search(nearby))

ref_text = "\n".join(read_limited(path, 200_000) for path in iter_files(ref_dir, REF_EXTS))
dynamic_ref = bool(DYNAMIC_REF_RE.search(ref_text))
issues: list[dict[str, object]] = []
active_only_matches: list[dict[str, object]] = []

if dynamic_ref:
    for path in iter_files(impl_root, IMPL_EXTS):
        text = read_limited(path)
        rel = str(path.relative_to(impl_root))
        for match in HARDCODED_CLASS_RE.finditer(text):
            classes = sorted(set(STATE_CLASS_RE.findall(match.group("value"))))
            if classes:
                issue = {
                    "kind": "hardcoded-state-class",
                    "file": rel,
                    "classes": classes,
                    "snippet": match.group(0)[:180],
                }
                if classes == ["is-active"]:
                    active_only_matches.append(issue)
                else:
                    issues.append(issue)
        for match in FORCED_FINAL_RE.finditer(text):
            if not has_dynamic_state_context(text, match.start(), match.end()):
                continue
            issues.append({
                "kind": "forced-final-style",
                "file": rel,
                "property": re.sub(r"\s+", " ", match.group("prop").lower()),
                "snippet": text[max(0, match.start() - 80): match.end() + 80].replace("\n", " ")[:220],
            })
        for match in BLANKET_STATE_RULE_RE.finditer(text):
            selector = match.group("selector")
            body = match.group("body")
            state_classes = sorted(set(STATE_CLASS_RE.findall(selector)))
            final_decls = [re.sub(r"\s+", " ", m.group(0).lower()) for m in FINAL_DECL_RE.finditer(body)]
            if len(state_classes) >= 1 and len(final_decls) >= 2:
                issues.append({
                    "kind": "blanket-state-final-style",
                    "file": rel,
                    "classes": state_classes,
                    "properties": sorted(set(final_decls)),
                    "snippet": (selector + "{" + body + "}").replace("\n", " ")[:220],
                })
        for match in REVEAL_ALL_RE.finditer(text):
            issues.append({
                "kind": "reveal-all-state-class",
                "file": rel,
                "class": match.group("class"),
                "snippet": text[max(0, match.start() - 80): match.end() + 80].replace("\n", " ")[:220],
            })

if len(active_only_matches) > 3:
    for issue in active_only_matches:
        issue["kind"] = "hardcoded-active-state-class"
        issues.append(issue)

status = "fail" if issues else "pass"
if not dynamic_ref:
    status = "skip"

artifact = {
    "schemaVersion": 1,
    "status": status,
    "dynamicRef": dynamic_ref,
    "issueCount": len(issues),
    "activeOnlyClassCount": len(active_only_matches),
    "issues": issues,
    "summary": (
        "Reference has dynamic state classes; implementation must not force final classes/styles."
        if issues else "No hardcoded dynamic final-state classes found."
    ),
}
out_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
if status == "fail":
    print(f"❌ Forced state class: FAIL ({len(issues)} issue(s))")
    sys.exit(1)
print(f"✅ Forced state class: {status.upper()}")
PY
