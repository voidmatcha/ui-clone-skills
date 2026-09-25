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
import hashlib
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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


sanitized_ref_css_hashes: dict[str, str] = {}
sanitize_report = ref_dir / "ref-css-sanitize-report.json"
if sanitize_report.is_file():
    try:
        report = json.loads(sanitize_report.read_text(encoding="utf-8"))
        for item in report.get("files") or []:
            if not isinstance(item, dict):
                continue
            destination = item.get("destination")
            digest = item.get("destinationSha256")
            if isinstance(destination, str) and isinstance(digest, str):
                rel = destination.replace("\\", "/").lstrip("./")
                if rel and re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                    sanitized_ref_css_hashes[rel] = digest.lower()
    except (json.JSONDecodeError, OSError):
        sanitized_ref_css_hashes = {}


def is_sanitized_ref_css_file(path: Path) -> bool:
    try:
        rel = str(path.relative_to(impl_root)).replace("\\", "/")
    except ValueError:
        return False
    expected = sanitized_ref_css_hashes.get(rel)
    if not expected:
        return False
    try:
        return sha256_file(path) == expected
    except OSError:
        return False


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


def normalize_css_fragment(text: str, *, selector: bool = False) -> str:
    """Normalize insignificant CSS whitespace without changing quoted text."""
    out: list[str] = []
    quote = ""
    escaped = False
    pending_space = False
    i = 0
    while i < len(text):
        if not quote and text.startswith("/*", i):
            close = text.find("*/", i + 2)
            i = len(text) if close == -1 else close + 2
            pending_space = True
            continue
        char = text[i]
        if quote:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            i += 1
            continue
        if char in {'"', "'"}:
            if pending_space and out and out[-1] not in "{(:,>+~":
                out.append(" ")
            pending_space = False
            quote = char
            out.append(char)
            i += 1
            continue
        if char.isspace():
            pending_space = True
            i += 1
            continue
        tight = ",>+~" if selector else ":;,()"
        if char in tight:
            while out and out[-1] == " ":
                out.pop()
            out.append(char)
            pending_space = False
        else:
            if pending_space and out and out[-1] not in tight + "(":
                out.append(" ")
            out.append(char)
            pending_space = False
        i += 1
    return "".join(out).strip()


def css_rules(text: str) -> list[dict[str, object]]:
    """Return ordinary CSS rules, including rules nested in at-rule blocks."""
    rules: list[dict[str, object]] = []

    def skip_string_or_comment(pos: int, limit: int) -> int:
        if text.startswith("/*", pos):
            close = text.find("*/", pos + 2, limit)
            return limit if close == -1 else close + 2
        quote = text[pos]
        pos += 1
        while pos < limit:
            if text[pos] == "\\":
                pos += 2
                continue
            if text[pos] == quote:
                return pos + 1
            pos += 1
        return limit

    def matching_brace(open_pos: int, limit: int) -> int:
        depth = 1
        pos = open_pos + 1
        while pos < limit:
            if text.startswith("/*", pos) or text[pos] in {'"', "'"}:
                pos = skip_string_or_comment(pos, limit)
                continue
            if text[pos] == "{":
                depth += 1
            elif text[pos] == "}":
                depth -= 1
                if depth == 0:
                    return pos
            pos += 1
        return limit

    def scan(start: int, limit: int, context: tuple[str, ...] = ()) -> None:
        prelude_start = start
        pos = start
        while pos < limit:
            if text.startswith("/*", pos) or text[pos] in {'"', "'"}:
                pos = skip_string_or_comment(pos, limit)
                continue
            char = text[pos]
            if char == ";":
                prelude_start = pos + 1
            elif char == "{":
                close = matching_brace(pos, limit)
                prelude = text[prelude_start:pos].strip()
                if prelude.startswith("@"):
                    scan(pos + 1, close, (*context, normalize_css_fragment(prelude)))
                elif prelude:
                    selector_key = normalize_css_fragment(prelude, selector=True)
                    body = text[pos + 1:close]
                    rules.append(
                        {
                            "selector": prelude,
                            "selectorKey": selector_key,
                            "contextKey": json.dumps(context),
                            "bodyKey": normalize_css_fragment(body),
                            "start": prelude_start,
                            "bodyStart": pos + 1,
                            "bodyEnd": close,
                            "end": close + 1,
                        }
                    )
                pos = close
                prelude_start = close + 1
            elif char == "}":
                return
            pos += 1

    scan(0, len(text))
    return rules


source_rule_index: dict[tuple[str, str, str], list[dict[str, object]]] = {}
source_css_dir = ref_dir / "css"
if source_css_dir.is_dir():
    for source_path in sorted(source_css_dir.glob("*.css")):
        if not source_path.is_file():
            continue
        source_text = read_limited(source_path)
        source_sha256 = sha256_file(source_path)
        for rule in css_rules(source_text):
            key = (str(rule["selectorKey"]), str(rule["bodyKey"]), str(rule["contextKey"]))
            start = int(rule["start"])
            source_rule_index.setdefault(key, []).append(
                {
                    "source": str(source_path.relative_to(ref_dir)),
                    "sourceSha256": source_sha256,
                    "sourceCharStart": start,
                    "sourceLine": source_text.count("\n", 0, start) + 1,
                }
            )


def source_provenance_for_rule(rule: dict[str, object]) -> dict[str, object] | None:
    key = (str(rule["selectorKey"]), str(rule["bodyKey"]), str(rule["contextKey"]))
    origins = source_rule_index.get(key)
    if not origins:
        return None
    canonical = json.dumps(key)
    return {
        **origins[0],
        "selector": key[0],
        "atRuleContext": json.loads(key[2]),
        "ruleSha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def containing_rule(
    rules: list[dict[str, object]], start: int, end: int
) -> dict[str, object] | None:
    matches = [
        rule
        for rule in rules
        if int(rule["bodyStart"]) <= start and end <= int(rule["bodyEnd"])
    ]
    if not matches:
        return None
    return min(matches, key=lambda rule: int(rule["end"]) - int(rule["start"]))


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
sanitized_ref_css_skipped: list[str] = []
source_authored_state_rules: list[dict[str, object]] = []
source_authored_rule_keys: set[tuple[str, str]] = set()

if dynamic_ref:
    for path in iter_files(impl_root, IMPL_EXTS):
        if path.suffix.lower() in {".css", ".scss", ".sass"} and is_sanitized_ref_css_file(path):
            sanitized_ref_css_skipped.append(str(path.relative_to(impl_root)))
            continue
        text = read_limited(path)
        rel = str(path.relative_to(impl_root))
        parsed_rules = (
            css_rules(text)
            if path.suffix.lower() in {".css", ".scss", ".sass"}
            else []
        )
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
            rule = containing_rule(parsed_rules, match.start(), match.end())
            provenance = source_provenance_for_rule(rule) if rule else None
            if provenance is not None:
                provenance_key = (rel, str(provenance["ruleSha256"]))
                if provenance_key not in source_authored_rule_keys:
                    source_authored_rule_keys.add(provenance_key)
                    source_authored_state_rules.append(
                        {"file": rel, **provenance}
                    )
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
                rule = containing_rule(parsed_rules, match.start("body"), match.end("body"))
                if rule is not None and source_provenance_for_rule(rule) is not None:
                    continue
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
    "sanitizedRefCssSkipped": sorted(sanitized_ref_css_skipped),
    "sourceAuthoredStateRuleCount": len(source_authored_state_rules),
    "sourceAuthoredStateRules": source_authored_state_rules,
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
