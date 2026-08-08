from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import cast

ref_dir = Path(sys.argv[1]).resolve()
impl_root = Path(sys.argv[2]).resolve()
copy_to = sys.argv[3].strip("/") or "src/ref-css"
src_dir = ref_dir / "css"
dst_dir = impl_root / copy_to
dst_dir.mkdir(parents=True, exist_ok=True)

URLISH_VAR_RE = re.compile(
    r"(?P<prefix>(?:^|[;{]\s*)"
    r"(?:background(?:-image)?|border-image(?:-source)?|list-style-image|"
    r"mask(?:-image)?|-webkit-mask(?:-image)?)\s*:\s*)"
    r"var\(\s*(?P<quote>['\"])(?P<url>[^'\"]+\."
    r"(?:png|jpe?g|gif|webp|svg|ico|avif)(?:\?[^'\"]*)?)"
    r"(?P=quote)\s*\)",
    re.IGNORECASE,
)
STATIC_URL_RE = re.compile(
    r"""url\(\s*(?P<quote>['"]?)(?P<url>[^'")]+?\.(?:woff2|woff|ttf|otf|eot|png|jpe?g|gif|webp|svg|ico|avif)(?:\?[^'")]*)?)(?P=quote)\s*\)""",
    re.IGNORECASE,
)
VAR_ONE_DASH_CUSTOM_PROPERTY_RE = re.compile(
    r"var\(\s*-(?!--)(?P<name>[A-Za-z_][\w-]*)(?P<fallback>\s*(?:,[^)]*)?)\)",
)
INVALID_PSEUDO_DESCENDANT_RULE_RE = re.compile(
    r"(?P<prefix>^|[{}])"
    r"(?P<selector>[^{}]*::?(?:before|after)\s+\.[A-Za-z_][\w-]*)"
    r"\{(?P<body>[^{}]*)\}",
    re.IGNORECASE,
)
MISSPELLED_BEFORE_RE = re.compile(r"(?P<prefix>::?)beofre\b", re.IGNORECASE)
SWIPER_DISABLED_PSEUDO_RE = re.compile(
    r":swiper-button-disabled\b", re.IGNORECASE
)
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


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sanitize_css(text: str) -> tuple[str, list[dict[str, str]]]:
    replacements: list[dict[str, str]] = []

    def replace(match: re.Match[str]) -> str:
        original = match.group(0)
        url = match.group("url")
        replacement = f"{match.group('prefix')}url(\"{url}\")"
        replacements.append(
            {
                "kind": "urlish-var-to-url",
                "original": original.strip(),
                "replacement": replacement.strip(),
            }
        )
        return replacement

    def replace_relative_static_url(match: re.Match[str]) -> str:
        original = match.group(0)
        url = match.group("url").strip()
        low = url.lower()
        if low.startswith(("/", "http://", "https://", "//", "data:")):
            return original
        path, sep, query = url.partition("?")
        normalized_path = path.replace("\\", "/")
        lowered_path = normalized_path.lower()
        marker_idx = -1
        marker_name = ""
        for marker in ("font", "img"):
            marker_with_slashes = f"/{marker}/"
            if marker_with_slashes in lowered_path:
                marker_idx = lowered_path.rfind(marker_with_slashes) + 1
                marker_name = marker
                break
            if lowered_path.startswith(f"{marker}/"):
                marker_idx = 0
                marker_name = marker
                break
        if marker_idx == -1:
            return original
        normalized_url = "/" + normalized_path[marker_idx:].lstrip("/")
        if sep:
            normalized_url = f"{normalized_url}?{query}"
        replacement = f'url("{normalized_url}")'
        replacements.append(
            {
                "kind": "relative-static-url-to-public-root",
                "root": marker_name,
                "original": original.strip(),
                "replacement": replacement,
            }
        )
        return replacement

    def replace_one_dash_var(match: re.Match[str]) -> str:
        original = match.group(0)
        name = match.group("name")
        fallback = match.group("fallback") or ""
        replacement = f"var(--{name}{fallback})"
        replacements.append(
            {
                "kind": "one-dash-var-custom-property-to-css-var",
                "original": original.strip(),
                "replacement": replacement,
            }
        )
        return replacement

    def remove_invalid_pseudo_descendant_rule(match: re.Match[str]) -> str:
        original = f"{match.group('selector')}{{{match.group('body')}}}"
        replacements.append(
            {
                "kind": "invalid-pseudo-descendant-rule-removed",
                "original": original.strip(),
                "replacement": "",
            }
        )
        return match.group("prefix")

    def repair_misspelled_before(match: re.Match[str]) -> str:
        original = match.group(0)
        replacement = f"{match.group('prefix')}before"
        replacements.append(
            {
                "kind": "misspelled-before-selector-repaired",
                "original": original,
                "replacement": replacement,
            }
        )
        return replacement

    def repair_swiper_disabled_pseudo(match: re.Match[str]) -> str:
        original = match.group(0)
        replacement = ".swiper-button-disabled"
        replacements.append(
            {
                "kind": "swiper-disabled-pseudo-to-class",
                "original": original,
                "replacement": replacement,
            }
        )
        return replacement

    sanitized = URLISH_VAR_RE.sub(replace, text)
    sanitized = STATIC_URL_RE.sub(replace_relative_static_url, sanitized)
    sanitized = VAR_ONE_DASH_CUSTOM_PROPERTY_RE.sub(replace_one_dash_var, sanitized)
    sanitized = MISSPELLED_BEFORE_RE.sub(repair_misspelled_before, sanitized)
    sanitized = SWIPER_DISABLED_PSEUDO_RE.sub(
        repair_swiper_disabled_pseudo, sanitized
    )
    sanitized = INVALID_PSEUDO_DESCENDANT_RULE_RE.sub(
        remove_invalid_pseudo_descendant_rule, sanitized
    )
    return sanitized, replacements


# Production CSS can be multi-megabyte and minified. The previous whole-file
# rule regex ([^{}]+\{[^{}]*\}) backtracks O(n^2) over long brace-free tails
# (e.g. sourcemap data URIs) and spun for minutes on a 2.6MB bundle. Mirror
# generation-plan.sh: inspect only the prefix where first-paint root locks
# normally live, and walk braces directly instead of regex-matching rules.
RUNTIME_UNLOCK_SCAN_CHARS = 256 * 1024


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
        selector_start = max(
            window.rfind("}", 0, open_idx), window.rfind("{", 0, open_idx)
        ) + 1
        selectors = window[selector_start:open_idx].strip()
        body = window[open_idx + 1 : close_idx]
        search_from = open_idx + 1
        if not HIDDEN_ROOT_SELECTOR_RE.search(selectors + "{"):
            continue
        decl = HIDDEN_DECL_RE.search(body)
        if not decl:
            continue
        hints.append(
            {
                "source": rel_source,
                "selector": selectors[:160],
                "declaration": decl.group(0)[:120],
            }
        )
    return hints


files = []
all_runtime_unlock_hints: list[dict[str, str]] = []
for src in sorted(src_dir.glob("*.css")):
    if not src.is_file():
        continue
    raw = src.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    sanitized, replacements = sanitize_css(text)
    rel_source = str(src.relative_to(ref_dir))
    hints = runtime_unlock_hints(sanitized, rel_source)
    all_runtime_unlock_hints.extend(hints)
    dst = dst_dir / src.name
    data = sanitized.encode("utf-8")
    dst.write_bytes(data)
    files.append(
        {
            "source": str(src.relative_to(ref_dir)),
            "destination": str(dst.relative_to(impl_root)),
            "sourceSha256": sha256(raw),
            "destinationSha256": sha256(data),
            "bytes": len(data),
            "changed": bool(replacements),
            "replacementCount": len(replacements),
            "replacements": replacements[:20],
            "requiresRuntimeUnlock": bool(hints),
            "runtimeUnlockHints": hints[:20],
        }
    )

report = {
    "schemaVersion": 1,
    "tool": "scripts/extract/sanitize-ref-css.sh",
    "copyTo": copy_to,
    "fileCount": len(files),
    "changedFileCount": sum(1 for f in files if f["changed"]),
    "replacementCount": sum(cast(int, f["replacementCount"]) for f in files),
    "requiresRuntimeUnlock": bool(all_runtime_unlock_hints),
    "runtimeUnlockHints": all_runtime_unlock_hints[:50],
    "files": files,
}
(ref_dir / "ref-css-sanitize-report.json").write_text(
    json.dumps(report, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
print(json.dumps(report, ensure_ascii=False))
