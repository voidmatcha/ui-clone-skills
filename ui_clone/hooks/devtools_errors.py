"""
Chrome DevTools error auto-detection hook for ui-reverse-engineering.

Fires on PostToolUse(Bash) when a browser session appears to be active.
Runs `agent-browser --session <name> eval <script>` to collect console errors
and prints actionable fix hints to stdout.

Advisory only — always exits 0.

Usage: python -m ui_clone.hooks.devtools_errors
Reads PostToolUse JSON from stdin.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

from ui_clone.hooks._common import extract_tool_command as _extract_tool_command
from ui_clone.hooks._common import find_project_root as _find_project_root
from ui_clone.hooks._common import find_ref_dir as _find_ref_dir

# Single-call snippet: idempotently install error listeners (first call) and
# collect accumulated errors. Folds the previous inject + collect pair into one
# eval so each PostToolUse(Bash) costs at most one subprocess round-trip.
_COLLECT_ERRORS_JS = """
(function() {
  if (!window.__uiSkillsErrors) {
    window.__uiSkillsErrors = [];
    window.addEventListener('error', function(e) {
      window.__uiSkillsErrors.push({
        type: 'uncaught', message: e.message || String(e),
        source: e.filename || '', line: e.lineno || 0
      });
    });
    window.addEventListener('unhandledrejection', function(e) {
      window.__uiSkillsErrors.push({
        type: 'promise', message: String(e.reason || e)
      });
    });
    var _orig = console.error;
    console.error = function() {
      var msg = Array.prototype.slice.call(arguments).join(' ');
      window.__uiSkillsErrors.push({ type: 'console.error', message: msg });
      _orig.apply(console, arguments);
    };
  }
  var errs = window.__uiSkillsErrors.slice(0, 20); // collect 20, display 10 in Python
  return JSON.stringify({ errors: errs, count: errs.length });
})()
""".strip()

# Known noisy patterns to suppress (library internals, HMR noise, etc.)
_IGNORE_PATTERNS = [
    re.compile(r"ResizeObserver loop", re.IGNORECASE),
    re.compile(r"favicon\.ico", re.IGNORECASE),
    re.compile(r"net::ERR_BLOCKED_BY_CLIENT"),  # ad blocker noise
    re.compile(r"\[HMR\]"),  # Next.js HMR
    re.compile(r"webpack-internal://"),
]

# Error → fix mapping (ordered: first match wins)
_FIX_HINTS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"is not defined", re.IGNORECASE),
        "Undefined variable/function — check for missing import or wrong scope",
    ),
    (
        re.compile(r"Cannot read prop", re.IGNORECASE),
        "null/undefined access — add optional chaining (?.) or an early-return guard",
    ),
    (
        re.compile(r"Hydration", re.IGNORECASE),
        "SSR/CSR mismatch — server-rendered HTML differs from client output. Use suppressHydrationWarning or move to useEffect",
    ),
    (
        re.compile(r"Failed to fetch|NetworkError|net::ERR", re.IGNORECASE),
        "Network request failed — check URL, CORS policy, and auth headers",
    ),
    (
        re.compile(r"chunk.*failed|loading.*chunk|ChunkLoadError", re.IGNORECASE),
        "JS chunk load failed — check build output paths and the public/ directory",
    ),
    (
        re.compile(r"Invalid hook call", re.IGNORECASE),
        "React hook rules violation — hooks must be called at the top level of a component",
    ),
    (
        re.compile(r"Warning.*Each child.*unique.*key", re.IGNORECASE),
        "Missing React key prop — add a unique key to each item in map()",
    ),
    (
        re.compile(r"Maximum update depth", re.IGNORECASE),
        "Infinite render loop — check useEffect dependency array or setState condition",
    ),
    (
        re.compile(r"CORS", re.IGNORECASE),
        "CORS policy blocked — add Access-Control-Allow-Origin header on the API server or use Next.js rewrites",
    ),
    (
        re.compile(r"404|not found", re.IGNORECASE),
        "Resource 404 — check the path, public/ files, and API route existence",
    ),
]


def _extract_session_name(bash_cmd: str) -> str | None:
    """Extract --session NAME from a bash command string.

    Supports unquoted, single-quoted, and double-quoted session names.
    """
    # Double-quoted: --session "my session"
    m = re.search(r'--session\s+"([^"]+)"', bash_cmd)
    if m:
        return m.group(1)
    # Single-quoted: --session 'my session'
    m = re.search(r"--session\s+'([^']+)'", bash_cmd)
    if m:
        return m.group(1)
    # Unquoted: --session my-sess
    m = re.search(r"--session\s+(\S+)", bash_cmd)
    return m.group(1) if m else None


def _run_agent_browser(session: str, js: str) -> str | None:
    """Run agent-browser eval and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["agent-browser", "--session", session, "eval", js],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _is_suppressed(msg: str) -> bool:
    return any(p.search(msg) for p in _IGNORE_PATTERNS)


def _fix_hint(msg: str) -> str:
    for pattern, hint in _FIX_HINTS:
        if pattern.search(msg):
            return hint
    return "Check the browser DevTools console for details"


def _collect_errors(session: str) -> list[dict[str, str]]:
    """Inject capture (idempotent) and collect errors in one eval round-trip."""
    raw = _run_agent_browser(session, _COLLECT_ERRORS_JS)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return []
        return [
            e
            for e in data.get("errors", [])
            if isinstance(e, dict) and not _is_suppressed(e.get("message", ""))
        ]
    except (json.JSONDecodeError, ValueError):
        return []


def main() -> None:
    raw_input = sys.stdin.read() if not sys.stdin.isatty() else ""

    project_root = _find_project_root()
    search_root = project_root / "tmp" / "ref"
    ref_dir = _find_ref_dir(search_root)

    # Only run if inside a ui-re project with an active WIP marker
    if ref_dir is None:
        sys.exit(0)
    if not (ref_dir / ".ui-re-active").is_file():
        sys.exit(0)

    # Parse bash command from stdin
    bash_cmd = ""
    if raw_input.strip():
        try:
            data = json.loads(raw_input)
            bash_cmd = _extract_tool_command(data) if isinstance(data, dict) else ""
        except json.JSONDecodeError:
            pass

    # Only run when an agent-browser session is mentioned in the command
    session = _extract_session_name(bash_cmd)
    if not session:
        sys.exit(0)

    errors = _collect_errors(session)
    if not errors:
        sys.exit(0)

    print()
    print(f"⚠️  UI-RE DevTools: {len(errors)} console error(s) detected in session '{session}'")
    print()
    for i, err in enumerate(errors[:10], 1):
        msg = err.get("message", "")
        err_type = err.get("type", "error")
        source = err.get("source", "")
        line = err.get("line", 0)
        location = f" ({source}:{line})" if source and line else ""
        print(f"  [{i}] [{err_type}]{location}")
        print(f"      {msg[:200]}")
        print(f"      → {_fix_hint(msg)}")
        print()

    if len(errors) > 10:
        print(f"  ... and {len(errors) - 10} more errors (showing first 10)")
        print()

    # Always advisory — exit 0
    sys.exit(0)


if __name__ == "__main__":
    main()
