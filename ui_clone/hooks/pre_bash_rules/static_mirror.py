"""Static-mirror / whole-document HTML snapshot detectors.

Per-section `outerHTML` probes are valid extraction evidence, but dumping
`document.documentElement.outerHTML` / `document.body.innerHTML` creates
a copied static page with no React/Tailwind component surface and
usually drops the original motion runtime. Block this before the file is
written; otherwise agents can self-verify HTTP 200 / title while
transitions are dead.
"""

from __future__ import annotations

import re
from pathlib import Path

_STATIC_MIRROR_DOWNLOAD_PATTERNS = re.compile(
    r"\bwget\b"
    r"(?=[^\n\r]*https?://)"
    r"(?=[^\n\r]*(?:\s-P\s+|--directory-prefix(?:=|\s+))[^\s|;&]*impl/public)"
    r"(?=[^\n\r]*(?:\s-p\b|--page-requisites|\s-r\b|--recursive|"
    r"--mirror|\s-E\b|--adjust-extension|\s-k\b|--convert-links))"
    r"|\bcurl\b"
    r"(?=[^\n\r]*https?://)"
    r"(?=[^\n\r]*(?:\s-o\s+|--output(?:=|\s+))[^\s|;&]*impl/public/index\.html)"
    r"|\bcurl\b[^\n\r]*https?://[^\n\r]*>\s*[^\s|;&]*impl/public/index\.html",
    re.IGNORECASE,
)

_STATIC_SERVER_PATTERNS = re.compile(
    r"\bnode\s+(?:\S+/)?server\.js\b"
    r"|python(?:3)?\s+-m\s+http\.server\b"
    r"|npx\s+(?:serve|vite|http-server)\b"
    r"|npm\s+run\s+dev\b",
    re.IGNORECASE,
)

_WHOLE_DOCUMENT_HTML_PATTERNS = re.compile(
    r"document\.(?:documentElement|body)\.(?:outerHTML|innerHTML)(?!\s*\.length)",
    re.IGNORECASE,
)

_STATIC_HTML_MIRROR_SOURCE_PATTERNS = re.compile(
    r"document\.(?:documentElement|body)\.(?:outerHTML|innerHTML)"
    r"|live-unwrapped\.html|live\.html|original\.html|snapshot\.html"
    r"|<!doctype\s+html|<html[\s>]|</html>|</body>",
    re.IGNORECASE,
)

_HTML_WRITE_PATTERNS = [
    re.compile(r">>?\s*(?![&(])\s*([^\s|;&<>()]+\.html)\b", re.IGNORECASE),
    re.compile(r"\btee\b\s+(?:-a\s+|--append\s+)?([^\s|;&<>()]+\.html)\b", re.IGNORECASE),
    re.compile(r"writeFileSync\s*\(\s*['\"]([^'\"]+\.html)['\"]", re.IGNORECASE),
    re.compile(r"open\s*\(\s*['\"]([^'\"]+\.html)['\"]\s*,\s*['\"]w(?:b|t)?['\"]", re.IGNORECASE),
    re.compile(r"\bcp\b\s+(?:-[a-zA-Z]+\s+)*\S+\s+([^\s|;&<>()]+\.html)\b", re.IGNORECASE),
    re.compile(r"\bmv\b\s+(?:-[a-zA-Z]+\s+)*\S+\s+([^\s|;&<>()]+\.html)\b", re.IGNORECASE),
]


def _static_mirror_download_violation(cmd: str) -> bool:
    return bool(cmd and _STATIC_MIRROR_DOWNLOAD_PATTERNS.search(cmd))


def _static_server_violation(cmd: str) -> bool:
    return bool(cmd and _STATIC_SERVER_PATTERNS.search(cmd))


def _is_impl_index_html_path(path: str) -> bool:
    stripped = path.strip("'\"")
    parts = Path(stripped).parts
    return "impl" in parts and Path(stripped).name.lower() == "index.html"


def _bash_html_write_targets(cmd: str) -> list[str]:
    targets: list[str] = []
    if not cmd:
        return targets
    for pat in _HTML_WRITE_PATTERNS:
        for m in pat.finditer(cmd):
            target = m.group(1).strip("\"'")
            if target and not target.startswith("&") and target != "/dev/null":
                targets.append(target)
    return targets


def _whole_document_html_snapshot_violation(cmd: str) -> bool:
    if not cmd or not _WHOLE_DOCUMENT_HTML_PATTERNS.search(cmd):
        return False
    # Site-detection probes may read outerHTML.length. Full document HTML
    # snapshots are different: they seed copied static mirrors.
    if re.search(r"document\.(?:documentElement|body)\.(?:outerHTML|innerHTML)\s*\.length", cmd, re.IGNORECASE):
        return False
    if "agent-browser" in cmd or _bash_html_write_targets(cmd):
        return True
    return False


def _static_html_mirror_write_target(cmd: str) -> str | None:
    """Return impl/index.html when it is being populated from copied page HTML.

    A minimal Vite/React index.html scaffold is legitimate. The blocked
    path is specifically whole-document/live snapshot HTML becoming the
    implementation.
    """
    if not cmd or not _STATIC_HTML_MIRROR_SOURCE_PATTERNS.search(cmd):
        return None
    for target in _bash_html_write_targets(cmd):
        if _is_impl_index_html_path(target):
            return target
    return None
