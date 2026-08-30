"""
Shared utilities for ui_clone hook modules.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── ANSI colors (shared across pipeline/gate/hooks) ──

GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
BOLD = "\033[1m"
NC = "\033[0m"

UTC = timezone.utc  # noqa: UP017 - macOS /usr/bin/python3 is still 3.9.


def _plugin_root() -> Path:
    """Return the ui-clone-skills plugin root (the directory containing pyproject.toml).

    Priority:
    1. Plugin-root env vars set by agent runtimes (generic host, Codex, Claude Code)
    2. Walk up from this file's location looking for pyproject.toml
    """
    for env_name in ("PLUGIN_ROOT", "CODEX_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"):
        env_root = os.environ.get(env_name, "")
        if env_root and (Path(env_root) / "pyproject.toml").is_file():
            return Path(env_root)
    cur = Path(__file__).resolve()
    while cur != cur.parent:
        if (cur / "pyproject.toml").is_file():
            return cur
        cur = cur.parent
    raise FileNotFoundError(
        "Cannot find ui-clone-skills plugin root. "
        "Set PLUGIN_ROOT, CODEX_PLUGIN_ROOT, or CLAUDE_PLUGIN_ROOT, or run from within the plugin directory."
    )


_cached_project_root: Path | None = None


def find_project_root() -> Path:
    """Discover project root (where pipeline state's tmp/ref/ lives).

    Priority — designed so nested sub-workspaces (any directory below the
    Claude-Code-launched project that holds its own tmp/ref/) resolve to
    the SUB-workspace's state instead of the parent repo's:

    1. If $CLAUDE_PROJECT_DIR is set AND cwd is INSIDE that env root,
       walk UP from cwd toward env_root looking for tmp/ref/ — the
       CLOSEST ancestor wins. When a sub-tool runs from a nested impl
       workspace (e.g. <env_root>/<subdir>/impl/), the walk finds
       <env_root>/<subdir>/tmp/ref/ BEFORE <env_root>/tmp/ref/ and
       returns the sub-workspace, so hooks see the actual current
       state, not the parent's stale copy.
       If no ancestor in that chain has tmp/ref/, fall back to env_root.
    2. If $CLAUDE_PROJECT_DIR is set AND cwd is OUTSIDE env_root,
       return env_root directly — preserves test fixtures that point
       env at a temp path while the test runs from the project repo.
    3. git rev-parse --show-toplevel — only when it has tmp/ref/.
    4. Free cwd walk — any ancestor with tmp/ref/.
    5. cwd fallback.

    Without this ordering, env_root would preempt the cwd walk and
    Stop-hook decisions would read parent-repo state while a sub-
    workspace's pipeline-state.json holds the actual current state.
    """
    global _cached_project_root

    cwd = Path.cwd().resolve()
    env_root = os.environ.get("CLAUDE_PROJECT_DIR", "")

    if env_root:
        try:
            env_path = Path(env_root).resolve()
        except (OSError, RuntimeError):
            env_path = None
        if env_path is not None and env_path.is_dir():
            # Determine whether cwd is inside env_root.
            try:
                cwd.relative_to(env_path)
                cwd_inside_env = True
            except ValueError:
                cwd_inside_env = False
            if cwd_inside_env:
                # Walk cwd UP to (and including) env_path looking for tmp/ref/.
                # Closest ancestor with tmp/ref/ wins — sub-workspace beats parent repo.
                cur = cwd
                while True:
                    if (cur / "tmp" / "ref").is_dir():
                        _cached_project_root = cur
                        return cur
                    if cur == env_path:
                        break
                    parent = cur.parent
                    if parent == cur:
                        break
                    cur = parent
                # No tmp/ref/ found in the chain — return env_root.
                _cached_project_root = env_path
                return env_path
            # cwd is OUTSIDE env_root — env_root is the explicit project pointer.
            _cached_project_root = env_path
            return env_path

    if _cached_project_root is not None:
        return _cached_project_root

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            git_root = Path(result.stdout.strip())
            if (git_root / "tmp" / "ref").is_dir():
                _cached_project_root = git_root
                return git_root
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Free cwd walk (no env, no git tmp/ref).
    cur = cwd
    while cur != cur.parent:
        if (cur / "tmp" / "ref").is_dir():
            _cached_project_root = cur
            return cur
        cur = cur.parent

    _cached_project_root = cwd
    return cwd


_DEFAULT_STALE_DAYS = 3


def stale_seconds() -> float:
    """WIP-marker stale threshold in seconds (UI_RE_STALE_DAYS env, default 3d).

    An active run re-touches its `.ui-re-active` marker on every gated component
    write, so a marker not updated within this window belongs to an abandoned /
    dead run. Shared so EVERY hook (via find_ref_dir) ignores stale markers the
    same way the Stop-hook reaper already does — not just the reaper.
    """
    try:
        days = float(os.environ.get("UI_RE_STALE_DAYS", _DEFAULT_STALE_DAYS))
    except (ValueError, TypeError):
        days = _DEFAULT_STALE_DAYS
    return days * 24 * 3600


def find_ref_dir(search_root: Path) -> Path | None:
    """Find ref dir: prefer newest FRESH WIP marker, fall back to newest extracted.json mtime."""
    if not search_root.is_dir():
        return None

    # 1. WIP marker — newest FRESH marker wins. A marker older than the stale
    # threshold is an abandoned/dead run (its owner stopped re-touching it) and
    # must NOT be treated as an active task: that was the cause of hooks firing
    # "out of context" off weeks-old markers left in sibling/scratch dirs.
    cutoff = time.time() - stale_seconds()
    newest_marker_time = 0.0
    newest_marker_dir: Path | None = None
    for d in sorted(search_root.iterdir()):
        if not d.is_dir():
            continue
        marker = d / ".ui-re-active"
        if not marker.is_file():
            continue
        mtime = marker.stat().st_mtime
        if mtime < cutoff:
            continue  # stale: abandoned/dead run, ignore
        if mtime > newest_marker_time:
            newest_marker_time = mtime
            newest_marker_dir = d
    if newest_marker_dir is not None:
        return newest_marker_dir

    # 2. mtime fallback — only refs with extracted.json
    newest_time = 0.0
    newest_dir: Path | None = None
    for d in sorted(search_root.iterdir()):
        if not d.is_dir():
            continue
        extracted = d / "extracted.json"
        if not extracted.is_file():
            continue
        mtime = extracted.stat().st_mtime
        if mtime > newest_time:
            newest_time = mtime
            newest_dir = d

    return newest_dir


def load_json_safe(path: Path) -> dict[str, Any] | None:
    """Load a JSON file and return it as a dict. Returns None if missing, malformed, or not an object."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def extract_tool_command(data: dict[str, Any]) -> str:
    """Return a shell command from Claude- or Codex-shaped hook payloads."""
    candidates: list[Any] = []
    tool_input = data.get("tool_input")
    if isinstance(tool_input, dict):
        candidates.extend([tool_input.get("command"), tool_input.get("cmd")])
    candidates.extend([data.get("command"), data.get("cmd")])
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return ""


_UI_RE_MODULES = {"ui_clone.pipeline", "ui_clone.gate", "ui_clone.goal"}
_UI_RE_SCRIPT_RE = re.compile(
    r"(?:^|[;&|]\s*)"
    r"(?:(?:bash|sh|zsh)\s+(?:-[A-Za-z]+\s+)*)?"
    r"['\"]?(?:\./)?(?:skills/visual-debug/scripts|scripts/(?:verify|extract))/"
    r"[^'\"\s;|&]+\.sh\b"
)


def _shell_tokens(cmd: str) -> list[str]:
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


_ASSIGNMENT_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _maybe_ref_dir_from_token(token: str, base: Path) -> Path | None:
    cleaned = token.strip().strip("'\"")
    # `VAR=value` assignment prefix: the ref dir is the value, not the token.
    cleaned = _ASSIGNMENT_PREFIX_RE.sub("", cleaned, count=1)
    # This hook reads the command string BEFORE the shell expands it, so a token
    # carrying `$VAR`, `$(...)` or backticks can never be resolved here. Guessing
    # materialises the literal string as a directory (mark_ref_session mkdirs the
    # parent), which both litters the tree and de-scopes the session marker that
    # should_enforce_ref_for_session reads. Fail closed instead.
    if "$" in cleaned or "`" in cleaned:
        return None
    if "tmp/ref/" not in cleaned and not cleaned.startswith("tmp/ref"):
        return None
    # Trim common shell punctuation while preserving paths with dots/dashes.
    # `;` is included: `tmp/ref/foo;` is the same clone as `tmp/ref/foo`, and
    # treating them as distinct forks session bookkeeping across two marker dirs.
    cleaned = cleaned.rstrip(";,:)]}")
    path = Path(cleaned)
    if not path.is_absolute():
        path = base / path
    for candidate in (path, *path.parents):
        if (
            candidate.name
            and candidate.parent.name == "ref"
            and candidate.parent.parent.name == "tmp"
        ):
            return candidate
    return None


def _ref_dir_from_command_tokens(tokens: list[str], base: Path) -> Path | None:
    for token in tokens:
        ref_dir = _maybe_ref_dir_from_token(token, base)
        if ref_dir is not None:
            return ref_dir
    return None


def _module_args(tokens: list[str], module_name: str) -> list[str] | None:
    for index, token in enumerate(tokens[:-1]):
        if token == "-m" and tokens[index + 1] == module_name:
            return tokens[index + 2 :]
    return None


_CLI_WRAPPER_VERBS = {
    "pipeline": "ui_clone.pipeline",
    "gate": "ui_clone.gate",
    "goal": "ui_clone.goal",
    "state": "ui_clone.state",
}

_WRAPPER_PRECEDING_OK = {"npx", "node", "command", "exec", "time", "env", "uv", "run"}


def _cli_wrapper_module_args(tokens: list[str]) -> tuple[str, list[str]] | None:
    """Map CLI-wrapper invocations onto (module, args).

    `ui-clone ...`, `npx ui-clone-cli ...`, and `node <path>/bin/ui-clone ...`
    are documented as equivalent to `python -m ui_clone.<module> ...`, but the
    hooks layer only recognized the python-module form — an agent driving the
    pipeline through the wrapper never got session-ownership markers, silently
    scoping away Stop-hook enforcement. Normalize the wrapper here so both
    command surfaces behave identically.
    """
    idx: int | None = None
    for i, tok in enumerate(tokens):
        base = tok.rsplit("/", 1)[-1]
        if base in {"ui-clone", "ui-clone-cli"}:
            # Only when everything before it is an env assignment, a known
            # launcher, or a flag — excludes mentions like `cat ui-clone`.
            prefix_ok = all(
                "=" in prev or prev.startswith("-")
                or prev.rsplit("/", 1)[-1] in _WRAPPER_PRECEDING_OK
                for prev in tokens[:i]
            )
            if prefix_ok:
                idx = i + 1
                break
    if idx is None or idx >= len(tokens):
        return None
    verb = tokens[idx]
    module = _CLI_WRAPPER_VERBS.get(verb)
    if module is not None:
        return module, tokens[idx + 1:]
    # Bare pipeline shorthand: ui-clone <url> <component> <session> <action>
    return "ui_clone.pipeline", tokens[idx:]


def _pipeline_ref_dir(args: list[str], project_root: Path) -> Path | None:
    """Resolve tmp/ref/<component> from pipeline-form args
    (<url> <component> <session> <action> ...)."""
    if len(args) < 4:
        return None
    component = args[1]
    action = args[3]
    if action not in {"run", "verify"}:
        return None
    if not component or component.startswith("-") or "/" in component:
        return None
    return project_root / "tmp" / "ref" / component


def _subshell_command(tokens: list[str]) -> str | None:
    for index, token in enumerate(tokens[:-1]):
        if token in {"-c", "-lc"}:
            return tokens[index + 1]
        if token.startswith("-") and "c" in token and index + 1 < len(tokens):
            # Handles compact shell options such as `bash -lc '<cmd>'`.
            return tokens[index + 1]
    return None


def target_ref_dir_for_ui_re_command(
    cmd: str, project_root: Path, cwd: Path | None = None
) -> Path | None:
    """Return the ref dir targeted by an executing UI-RE command.

    This deliberately excludes read-only development commands that merely
    mention repo script paths, such as `ruff check scripts/extract/foo.py`,
    `git diff -- scripts/extract/foo.sh`, or `sed -n ... scripts/extract/foo.sh`.
    Session ownership markers are meaningful only when a command actually runs
    the UI-RE pipeline/gate/scripts against a concrete ref.
    """
    if not cmd.strip():
        return None
    tokens = _shell_tokens(cmd)
    if not tokens:
        return None

    nested = _subshell_command(tokens)
    if nested and nested != cmd:
        nested_ref = target_ref_dir_for_ui_re_command(nested, project_root, cwd)
        if nested_ref is not None:
            return nested_ref

    base = cwd or project_root

    pipeline_args = _module_args(tokens, "ui_clone.pipeline")
    if pipeline_args is not None:
        return _pipeline_ref_dir(pipeline_args, project_root)

    for module in ("ui_clone.gate", "ui_clone.goal"):
        args = _module_args(tokens, module)
        if args:
            ref_dir = _ref_dir_from_command_tokens(args[:2], base)
            if ref_dir is not None:
                return ref_dir

    wrapper = _cli_wrapper_module_args(tokens)
    if wrapper is not None:
        module, args = wrapper
        if module == "ui_clone.pipeline":
            ref_dir = _pipeline_ref_dir(args, project_root)
            if ref_dir is not None:
                return ref_dir
        else:
            # gate <ref-dir> <gate>, goal <ref-dir> ..., state terminal <ref-dir> ...
            ref_dir = _ref_dir_from_command_tokens(args[:3], base)
            if ref_dir is not None:
                return ref_dir

    if not _UI_RE_SCRIPT_RE.search(cmd):
        return None
    return _ref_dir_from_command_tokens(tokens, base)


def is_ui_re_execution_command(cmd: str) -> bool:
    """True when `cmd` executes a UI-RE command, not just references files."""
    return target_ref_dir_for_ui_re_command(cmd, find_project_root()) is not None


def session_id_from_payload(data: dict[str, Any] | None = None) -> str:
    """Return the current agent-session id from hook payload/env.

    Stop hooks should not force one Codex/Claude session to finish a UI-RE
    clone that another session owns. Both hosts expose the active session id
    differently across hook events, so centralize the tolerant extraction here.
    """
    if isinstance(data, dict):
        for key in ("session_id", "sessionId"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    for env_name in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID"):
        value = os.environ.get(env_name, "")
        if value.strip():
            return value.strip()
    return ""


_REF_SESSION_DIR = ".ui-re-sessions"


def _session_marker_path(ref_dir: Path, session_id: str) -> Path:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return ref_dir / _REF_SESSION_DIR / f"{digest}.json"


def mark_ref_session(ref_dir: Path, session_id: str, *, source: str) -> None:
    """Record that `session_id` interacted with `ref_dir`.

    The marker is intentionally per-ref and local-only. It lets the Stop hook
    distinguish "my unfinished clone" from "a sibling session's unfinished
    clone" without weakening legacy fail-closed behavior when no session id is
    available.
    """
    sid = session_id.strip()
    if not sid:
        return
    marker = _session_marker_path(ref_dir, sid)
    payload = {
        "session_id": sid,
        "source": source,
        "updatedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def ref_has_session_markers(ref_dir: Path) -> bool:
    marker_dir = ref_dir / _REF_SESSION_DIR
    if not marker_dir.is_dir():
        return False
    try:
        return any(p.is_file() for p in marker_dir.iterdir())
    except OSError:
        return False


def ref_touched_by_session(ref_dir: Path, session_id: str) -> bool:
    sid = session_id.strip()
    if not sid:
        return False
    return _session_marker_path(ref_dir, sid).is_file()


def should_enforce_ref_for_session(ref_dir: Path, session_id: str) -> bool:
    """Return True when this hook session should enforce `ref_dir`.

    With a known session id, Stop/declare-done gates are scoped to refs this
    session touched. That prevents an unrelated Codex/Claude tab from being
    forced to complete a clone already owned by another tab. When the runtime
    provides no session id we keep legacy fail-closed behavior.
    """
    sid = session_id.strip()
    force_unowned = os.environ.get("UI_RE_ENFORCE_UNOWNED_ACTIVE", "").strip() == "1"
    if not sid:
        # No session id in the payload. Keep the legacy fail-closed behavior for
        # refs with NO session owner (could be this session's own pre-session-id
        # work — a genuine in-progress clone). But SKIP a ref whose session
        # markers all belong to other, identifiable sessions: an unrelated tab
        # must not be forced to finish another tab's clone (the "fires in
        # unrelated work" recurrence). The override restores enforce-everything.
        if force_unowned:
            return True
        return not ref_has_session_markers(ref_dir)
    if ref_touched_by_session(ref_dir, sid):
        return True
    return force_unowned


def _parse_patch_paths(patch: str) -> list[str]:
    """Extract touched paths from Codex/Claude apply_patch or unified diff text."""
    paths: list[str] = []
    for line in patch.splitlines():
        if line.startswith(("*** Add File: ", "*** Update File: ", "*** Delete File: ")):
            paths.append(line.split(": ", 1)[1].strip())
            continue
        if line.startswith("*** Move to: "):
            paths.append(line.removeprefix("*** Move to: ").strip())
            continue
        match = re.match(r"diff --git a/(.+?) b/(.+)$", line)
        if match:
            paths.extend([match.group(1), match.group(2)])
            continue
        if line.startswith(("+++ b/", "--- a/")):
            paths.append(line[6:].strip())
    return paths


def extract_tool_file_paths(data: dict[str, Any]) -> list[str]:
    """Return all file paths from Claude- or Codex-shaped write hook payloads.

    Claude Write/Edit/MultiEdit payloads normally provide `file_path`. Codex
    apply_patch payloads may provide only a patch body, so parse patch headers
    too. Unknown payload shapes safely return an empty list.
    """
    paths: list[str] = []
    containers: list[dict[str, Any]] = [data]
    tool_input = data.get("tool_input")
    if isinstance(tool_input, dict):
        containers.insert(0, tool_input)

    for container in containers:
        for key in ("file_path", "filepath", "path", "filename"):
            value = container.get(key)
            if isinstance(value, str) and value:
                paths.append(value)
        for key in ("file_paths", "paths", "files"):
            value = container.get(key)
            if isinstance(value, list):
                paths.extend(item for item in value if isinstance(item, str) and item)
        edits = container.get("edits")
        if isinstance(edits, list):
            for edit in edits:
                if isinstance(edit, dict):
                    value = edit.get("file_path") or edit.get("path")
                    if isinstance(value, str) and value:
                        paths.append(value)
        for key in ("patch", "input", "content", "diff"):
            value = container.get(key)
            if isinstance(value, str) and value:
                paths.extend(_parse_patch_paths(value))

    # Preserve order while de-duplicating.
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


_DEFAULT_COMPONENT_SUBSTRINGS = ("/src/components/", "/src/projects/")
_DEFAULT_APP_PREFIX = "/src/app/"

# ── Canonical ref-dir artifact allowlist ──
#
# Names of JSON artifacts the canonical pipeline writes to `tmp/ref/<component>/`.
# Sourced from grep of ui_clone/ and skills/visual-debug/scripts/. Includes the
# parsed shadow shapes (`*-parsed.json`, `*-raw.json`) some scripts emit
# alongside the primary artifact.
#
# Top-level *.json writes to a ref dir that don't match this set are flagged
# as ad-hoc (LLM-invented) names — typical failure mode where the agent dumps
# `sections.json` / `content-detail.json` / `key-sections.json` /
# `styles-core.json` instead of running the script that produces the canonical
# `section-map.json` / `structure.json` / `styles.json`. Hook denies the Write
# and points the agent at the right script.
#
# Sub-directories (`bundles/`, `css/`, `fonts/`, `images/`, `scroll-video/`,
# `sections/`, `static/`, `transitions/`, `clip/`, `diff/`, `impl/`, `ref/`)
# are unrestricted — anything goes under them.
CANONICAL_REF_ARTIFACTS: frozenset[str] = frozenset(
    {
        # Phase 0A / 2 — DOM and scaffold
        "canvas-webgl-detection.json",
        "structure.json",
        "section-map.json",
        "portal-candidates.json",
        "sticky-elements.json",
        "hidden-elements.json",
        "dom-scaffold.json",
        "content.json",
        "content-raw.json",
        # Phase 2.5 — Assets
        "head.json",
        "head-parsed.json",
        "page-dims.json",
        "page-dims-parsed.json",
        "assets.json",
        "inline-svgs.json",
        "fonts.json",
        "visible-images.json",
        "asset-substitution.json",
        "asset-substitutions.json",
        # Phase 2.6 — Animation seeds
        "animation-init-styles.json",
        "animation-runtime-dump.json",
        "state-coupling.json",
        # Phase 3 — Styles
        "styles.json",
        "styles-raw.json",
        "advanced-styles.json",
        "body-state.json",
        "decorative-svgs.json",
        "design-bundles.json",
        "em-conversion.json",
        "typography.json",
        "sizing-expressions.json",
        "detected-breakpoints.json",
        "ref-viewport-visibility.json",
        "dynamic-regions.json",
        "dynamic-styles.json",
        "element-groups.json",
        "element-roles.json",
        "elements.json",
        "regions.json",
        "svg-text-elements.json",
        # Phase 5 — Interactions
        "interactions-detected.json",
        "scroll-transitions.json",
        "hover-deltas.json",
        "hover-timing.json",
        "hover-css-rules.json",
        "hover-states.json",
        # Phase 5c — Bundle
        "scroll-engine.json",
        "scroll-library.json",
        "bundle-analysis.json",
        "bundle-extraction.json",
        "bundle-map.json",
        "paid-features.json",
        "paid-fonts.json",
        # Phase 5d — Spec + verification plan
        "transition-spec.json",
        "external-sdks.json",
        "verification-plan.json",
        "component-map.json",
        # Phase 6 — Aggregated + final
        "extracted.json",
        "generation-plan.json",
        "animations-detected.json",
        "pipeline-state.json",
        "artifact-provenance.json",
        # Webflow specifics
        "webflow-detection.json",
        "webflow-hide-rule.json",
        "webflow-ix2.json",
        # Misc generated by verification scripts at ref-dir top-level
        "args.json",
        "log.json",
        "report.json",
        "summary.json",
        "known-artifacts.json",
        "tailwind-conflict.json",
        "ref-css-sanitize-report.json",
        # Review follow-up: gate.py + verification-plan.sh references that
        # were missing from the initial allowlist — would otherwise
        # false-positive deny on a standard pipeline run.
        "regions.json",
        "dom-state-diff.json",
        "dom-mirror-check.json",
        "tree-diff-status.json",
        # Photo of the gate result (visual-debug scripts deposit some at top)
        "hydration-check.json",
        "boundary-collisions.json",
        "font-parity.json",
        "reveal-trigger.json",
        "scroll-completion.json",
        "scroll-coverage.json",
        "spec-implementation-coverage.json",
        "transition-coverage.json",
        "transition-spec-coverage.json",
        "runtime-spec-coverage.json",
        "text-fidelity-check.json",
        "lottie-runtime.json",
        "asset-transfer.json",
        "asset-utilization.json",
        "bundle-impl-coverage.json",
        "image-fidelity.json",
        "layout-decisions.json",
        "layout-health.json",
        "samples.json",
        "ref-elements.json",
        "ref-samples.json",
        "impl-elements.json",
        "impl-layout.json",
        "impl-samples.json",
        # v0.7.0 closeout-policy stamps + attestation (canvas-replay).
        # Attestation is operator-written (the whole point — opt-in
        # license proof). Stamps are written by the canonical writer
        # scripts (`scripts/verify/check-converged.sh` for structural,
        # `scripts/verify/check-canvas-replay.sh` for canvas-replay,
        # `pipeline.execute_verify` for canonical). Listed here so the
        # ad-hoc-artifact hook doesn't false-positive on legitimate
        # operator opt-in workflows.
        "canvas-replay-attestation.json",
        "canvas-replay-stamp.json",
        "structural-convergence-stamp.json",
        "verify-stamp.json",
    }
)

# Subdirectories under `tmp/ref/<c>/` that are unrestricted (any *.json inside
# is allowed). Matches by suffix on the parent directory name.
_REF_SUBDIR_UNRESTRICTED: frozenset[str] = frozenset(
    {"bundles", "css", "fonts", "images", "scroll-video", "sections", "static",
     "transitions", "clip", "diff", "impl", "ref"}
)


def is_ad_hoc_ref_artifact(file_path: str) -> tuple[bool, str]:
    """Detect Write attempts at `<ANY>/tmp/ref/<component>/<adhoc>.json`.

    Returns (is_adhoc, canonical_suggestion):
        is_adhoc — True when the path is a top-level *.json inside a ref dir
                   AND the basename isn't on CANONICAL_REF_ARTIFACTS.
        canonical_suggestion — closest canonical name to hint at, or empty.

    Sub-directory writes (`sections/foo.json`, `css/foo.json`, etc.) are
    always allowed — the allowlist only enforces the top-level ref-dir
    namespace where canonical pipeline artifacts live.

    The allowlist intentionally matches every parent path that ends in
    `tmp/ref/<dirname>/` so it catches both project-local
    (`./tmp/ref/<component>/sections.json`) and home-directory
    (`~/tmp/ref/<component>/sections.json`) shortcut paths — both have
    been observed in nested fresh-prompt runs.
    """
    if not file_path or not file_path.endswith(".json"):
        return False, ""
    parts = Path(file_path).parts
    try:
        idx = parts.index("ref")
    except ValueError:
        return False, ""
    # Pattern: .../tmp/ref/<component>/<file>  → idx-1 must be 'tmp'
    if idx == 0 or parts[idx - 1] != "tmp":
        return False, ""
    # File must be directly under <component>, not in a subdirectory.
    # parts after 'ref' should be: [<component>, <file>] → length 2
    tail = parts[idx + 1 :]
    if len(tail) != 2:
        # Could be `tmp/ref/<c>/<subdir>/file.json` — allowed unrestrictedly
        # when the subdir is in the unrestricted set.
        if len(tail) >= 3 and tail[1] in _REF_SUBDIR_UNRESTRICTED:
            return False, ""
        # Other deep paths also pass (e.g., `bundles/chunk-3/index.json`).
        return False, ""
    basename = tail[1]
    if basename in CANONICAL_REF_ARTIFACTS:
        return False, ""
    # Heuristic suggestion — only return a suggestion when we're confident.
    # Review follow-up: substring matches on short keywords (`key`, `page`,
    # `stat`) over-fire on legitimate diagnostic names like
    # `section-counts.json` → suggests `section-map.json` confidently and
    # misleads the agent. Restrict to prefix matches on the file stem.
    # Ambiguous short tokens are intentionally dropped from the table
    # rather than rephrased — better to deny with no suggestion than to
    # point at the wrong canonical.
    prefix_to_canonical = (
        # Listed longest-prefix-first so `sections-map` (length 8) matches
        # before the more general `section-` (length 8 — tie, longer key wins
        # via iteration order).
        ("sections-map", "section-map.json"),
        ("sections", "section-map.json"),
        ("section-", "section-map.json"),
        ("structure", "structure.json"),
        ("style", "styles.json"),
        ("animations-", "animations-detected.json"),
        ("transition-", "transition-spec.json"),
        ("bundle-", "bundle-map.json"),
        ("font", "fonts.json"),
        ("hover-", "hover-css-rules.json"),
        ("interactions-", "interactions-detected.json"),
        ("paid-", "paid-features.json"),
        ("scroll-engine", "scroll-engine.json"),
    )
    stem = basename.lower()
    suggestion = ""
    for prefix, name in prefix_to_canonical:
        if stem.startswith(prefix) and stem != name:
            suggestion = name
            break
    return True, suggestion


def is_component_file(file_path: str) -> bool:
    """Return True for component/page files that pre-generate / pre-bash should enforce.

    Default enforced paths:
    - /src/components/**       — all component files
    - /src/projects/**         — project-scoped component trees (monorepo layouts)
    - /src/app/**/page.*       — Next.js App Router page files only
                                 (layout.tsx, route.ts etc. are excluded)
    - /src/main.{jsx,tsx,js,ts}    — Vite / CRA / generic React entry point
    - /src/App.{jsx,tsx,js,ts}     — top-level App component (when hand-authored)
    - /src/app/layout.{tsx,jsx}    — Next.js App Router root layout
    - /src/pages/**            — Next.js Pages Router

    Audit signal: heavy-motion site agent wrote impl/src/main.jsx
    by hand after scaffold-to-jsx failed; the old substring set
    (components/, projects/, app/**/page.*) did NOT cover main.jsx, so
    pre_generate / pre_bash silently allowed the handcrafted entry to
    ship without going through the canonical pipeline. Entry-point
    filenames are explicitly enforced now.

    Override via UI_RE_COMPONENT_PATHS env var (colon-separated substrings):
        UI_RE_COMPONENT_PATHS=/src/components/:/app/components/
    """
    if not file_path:
        return False
    custom = os.environ.get("UI_RE_COMPONENT_PATHS", "").strip()
    if custom:
        return any(p in file_path for p in custom.split(":") if p)
    if any(sub in file_path for sub in _DEFAULT_COMPONENT_SUBSTRINGS):
        return True
    if "/src/pages/" in file_path:
        return True
    if _DEFAULT_APP_PREFIX in file_path:
        return any(seg.startswith("page.") for seg in file_path.split("/"))
    # Top-level React entry-point filenames under /src/.
    entry_filenames = {
        "main.jsx", "main.tsx", "main.js", "main.ts",
        "App.jsx", "App.tsx", "App.js", "App.ts",
        "index.jsx", "index.tsx",  # plain index entries (CRA / Vite)
    }
    for entry in entry_filenames:
        if file_path.endswith(f"/src/{entry}") or file_path.endswith(f"/{entry}"):
            # Restrict to paths that include /src/ to avoid catching
            # arbitrary main.jsx outside a project tree.
            if "/src/" in file_path:
                return True
    return False


def _log_gate_skip(ref_dir: Path, gate_name: str, reason: str) -> bool:
    """Append a gate skip event to ref_dir/.gate-skip-log for auditability.

    Returns True only if the skip was durably recorded (written, flushed,
    fsync'd, and read back). Returns False on any failure; never raises.
    """
    try:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        log_path = ref_dir / ".gate-skip-log"
        marker = f"gate={gate_name} reason={reason}"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts} {marker}\n")
            f.flush()
            os.fsync(f.fileno())
        return any(marker in line for line in log_path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return False


def _gate_skip_acknowledged(ref_dir: Path) -> bool:
    """Return True when verification-plan.json explicitly accepts skipped gates."""
    plan_path = Path(ref_dir) / "verification-plan.json"
    if not plan_path.is_file():
        return False
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return bool(str(plan.get("gateSkipAck") or "").strip())


def _clear_gate_skip(ref_dir: Path, gate_name: str) -> None:
    """Drop any prior skip entries for `gate_name` from .gate-skip-log.

    A gate that actually RAN (pass or fail) is no longer "not enforced", so its
    earlier fail-open skip must stop blocking closeout. This self-heals the
    run-scoping: the entries that remain are exactly the gates skipped and never
    since recovered — which is what gate_skip_blocker reports. Best-effort.
    """
    try:
        log_path = ref_dir / ".gate-skip-log"
        if not log_path.is_file():
            return
        lines = log_path.read_text(encoding="utf-8").splitlines()
        kept = [ln for ln in lines if f"gate={gate_name} reason=" not in ln]
        if len(kept) == len(lines):
            return
        if kept:
            log_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        else:
            log_path.unlink()
    except OSError:
        pass


def run_gate(ref_dir: Path, gate_name: str) -> dict[str, object]:
    """Run `uv run python -m ui_clone.gate <ref_dir> <gate_name> --json` as a subprocess.

    Uses `uv run` to guarantee execution inside the ui-clone-skills virtual environment
    (with scikit-image, Pillow installed). Falls back to sys.executable if uv is
    not available, which will fail-open with a warning if dependencies are missing.

    Returns parsed JSON dict from gate output.
    Falls back to {"passed": True} if gate script not found (fail-open).
    Gate skips are logged to ref_dir/.gate-skip-log for auditability.
    """
    uv = shutil.which("uv")
    if uv:
        cmd = [
            uv,
            "run",
            "--project",
            str(_plugin_root()),
            "python",
            "-m",
            "ui_clone.gate",
            str(ref_dir),
            gate_name,
            "--json",
        ]
    else:
        print(
            "ui-clone-skills: WARNING: uv not found, falling back to sys.executable",
            file=sys.stderr,
        )
        cmd = [sys.executable, "-m", "ui_clone.gate", str(ref_dir), gate_name, "--json"]

    skip_reason = ""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        raw = result.stdout.strip()
        if raw:
            data: dict[str, object] = json.loads(raw)
            # The gate actually ran (pass OR fail) — clear any earlier fail-open
            # skip so it stops blocking closeout (self-healing run-scope).
            _clear_gate_skip(ref_dir, gate_name)
            return data
        if result.returncode != 0:
            return {
                "passed": False,
                "fail_count": 1,
                "failures": [
                    {
                        "label": gate_name,
                        "reason": result.stderr.strip() or "gate failed",
                        "fix": "",
                    }
                ],
            }
        skip_reason = "gate produced no output (exit 0, empty stdout)"
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        skip_reason = f"{type(exc).__name__}: {exc}"
    # Fail-LOUD, not silent fail-open. We keep returning passed=True so a host
    # missing uv/Pillow is not bricked, but the gate was NOT enforced — mark the
    # result `skipped`, warn loudly, and record it in .gate-skip-log so
    # gate_skip_blocker refuses a terminal `done`/push until a capable host
    # re-runs the gate (which clears the entry) or the user sets gateSkipAck.
    print(
        f"ui-clone-skills: ⚠ ENFORCEMENT SKIPPED: gate '{gate_name}' could "
        f"not run ({skip_reason}). It is NOT enforced this run.",
        file=sys.stderr,
    )
    recorded = _log_gate_skip(ref_dir, gate_name, skip_reason)
    if not recorded and not _gate_skip_acknowledged(ref_dir):
        print(
            f"ui-clone-skills: ⛔ FAIL-CLOSED: gate '{gate_name}' was skipped "
            f"({skip_reason}) but the skip could NOT be durably recorded to "
            f"{ref_dir}/.gate-skip-log. Blocking.",
            file=sys.stderr,
        )
        return {
            "passed": False,
            "fail_count": 1,
            "failures": [
                {
                    "label": gate_name,
                    "reason": (
                        f"gate '{gate_name}' could not run ({skip_reason}) and the "
                        "skip could not be durably recorded to .gate-skip-log, so it "
                        "cannot be enforced at closeout."
                    ),
                    "fix": (
                        "Make .gate-skip-log writable, run on a host with the "
                        "ui-clone-skills environment so the gate executes, or set "
                        "`gateSkipAck` in verification-plan.json to explicitly accept "
                        "un-enforced runs."
                    ),
                }
            ],
            "skipped": True,
            "skip_reason": skip_reason,
            "skip_record_failed": True,
        }
    return {
        "passed": True,
        "fail_count": 0,
        "failures": [],
        "skipped": True,
        "skip_reason": skip_reason,
    }


def quick_tier_blocker(ref_dir: Path) -> str | None:
    """Return a closeout blocker when verification-plan.json is tier=quick.

    Shared by BOTH closeout paths — canonical (pipeline verify ->
    verify-stamp.json) and structural (check-converged.sh ->
    structural-convergence-stamp.json) — so an agent cannot dodge the
    blocker by routing through the structural stamp (the benchmark-077d8c3
    tier=quick gaming vector). Missing/unreadable plans are left to the
    normal spec gate.
    """
    plan_path = Path(ref_dir) / "verification-plan.json"
    if not plan_path.is_file():
        return None
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    tier = str(plan.get("tier") or "").lower()
    if tier != "quick":
        return None
    return (
        "verification-plan.json is tier=quick. Quick plans are for inner "
        "iteration only — closeout requires tier=standard or "
        "tier=comprehensive so browser scroll/live parity checks can run. "
        "Regenerate the plan with: UI_CLONE_VERIFY_TIER=standard bash "
        "skills/visual-debug/scripts/verification-plan.sh <ref-dir>"
    )


# ── Off-pipeline clone detection (omx postmortem) ─────────────────────────
# A session that browses an EXTERNAL site via agent-browser and then writes
# component files without any owned ref dir is doing clone-shaped work
# outside the pipeline — the failure mode where a "static approximation +
# smoke check" ships with font/layout drift unmeasured (no gates ever ran).

_EXTERNAL_BROWSE_DIR = "tmp/.ui-re-external-browse"
# Command-position anchor shared by every pre_bash deny matcher (82b0a4e class).
# A tool is INVOKED only at command position: string start, or right after a
# shell connector (newline ; & | && || or a subshell paren), optionally via
# xargs. A tool NAME elsewhere — a quoted pgrep pattern, a `command -v` / grep
# argument — is DATA, not an invocation, and must not trigger a deny.
# An invocation may be preceded by leading environment assignments and/or `env`
# (batch-4 review MAJOR 2): `FOO=1 agent-browser open`, `HTTPS_PROXY=x curl ...`,
# `PORT=5173 npm run dev` are real command-position invocations and must not
# bypass the deny. The trailing `(?:(?:\w+=\S*|env)\s+)*` consumes those leading
# KEY=VAL / env tokens; it cannot match a bare tool name (no `=`, not `env`), so
# the quoted/heredoc/command-v/pgrep-argument exemptions are unaffected.
CMD_POSITION_PREFIX = r"(?:^|[\n;&|(]\s*|&&\s*|\|\|\s*|\bxargs\s+)(?:(?:\w+=\S*|env)\s+)*"
# Command-position anchor (orchestrator live-fire false positive 2026-06-12):
# the previous bare `search()` matched the literal trigger string INSIDE a
# heredoc body (a commission doc written via `cat <<EOF`) and crumbed the
# orchestrator session with the placeholder url `https://<external>``. The
# invocation must start a command: string start or right after a shell
# connector (&& ; | & newline or subshell paren).
_AB_OPEN_RE = re.compile(
    rf"{CMD_POSITION_PREFIX}agent-browser\b[^\n;|&]*\bopen\s+['\"]?(https?://[^'\"\s;|&]+)"
)
_LOCAL_HOST_RE = re.compile(
    r"^https?://(localhost|127\.|0\.0\.0\.0|192\.168\.|10\.|\[::1\])"
)
_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?\w")
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")


def _sanitize_for_command_match(cmd: str) -> str:
    """Drop heredoc bodies and quoted text so DATA can never look like a
    command. Everything after the first heredoc operator is document body
    until its terminator — for crumb detection, simply stop scanning there."""
    m = _HEREDOC_RE.search(cmd)
    if m:
        cmd = cmd[: m.start()]
    return _QUOTED_RE.sub("''", cmd)


# Heredoc BODY remover: drops the document body between `<<DELIM` and its
# terminator line while KEEPING any command after the terminator (unlike
# _sanitize_for_command_match, which truncates at the first heredoc — fine for
# crumb detection but it would hide a real extraction placed after a heredoc).
_HEREDOC_BODY_RE = re.compile(
    r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n[ \t]*\2(?=\s|$)",
    re.DOTALL,
)


def strip_heredoc_bodies(cmd: str) -> str:
    """Remove heredoc document bodies (between `<<DELIM` and its terminator)
    while KEEPING any command after the terminator and ALL quoted text. Use
    this for matchers that must still read quoted arguments (a mirror's quoted
    impl/public target, a writeFileSync('x.html') path) but must not be fooled
    by a heredoc body — e.g. a commit message describing `npm run dev`."""
    if not cmd:
        return cmd
    out = cmd
    prev = ""
    while prev != out:  # collapse stacked/nested heredocs
        prev = out
        out = _HEREDOC_BODY_RE.sub(" ", out, count=1)
    return out


def sanitize_command_for_deny(cmd: str) -> str:
    """Strip heredoc bodies AND quoted strings so deny matchers see only command
    structure — tool/script names appearing as DATA (a quoted pgrep pattern, a
    heredoc document body, a grep/command-v argument) can never trigger a deny.
    Real commands after a heredoc terminator are preserved, so an extraction
    hidden after a doc body still matches. Do NOT use for matchers that need to
    read a quoted argument (paths) — use strip_heredoc_bodies there."""
    if not cmd:
        return cmd
    return _QUOTED_RE.sub("''", strip_heredoc_bodies(cmd))


def _is_plausible_external_url(url: str) -> bool:
    """Placeholder rejection: `https://<external>` (angle brackets, backticks)
    is doc text, not a navigable URL; require a parseable hostname with at
    least one dot or a plausible bare host."""
    if any(ch in url for ch in "<>`"):
        return False
    try:
        from urllib.parse import urlparse

        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    if not host:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9.-]+", host))


def mark_external_browse(cmd: str, base: Path, session_id: str) -> None:
    """Record that `session_id` opened an external URL via agent-browser."""
    if not session_id:
        return
    m = _AB_OPEN_RE.search(_sanitize_for_command_match(cmd or ""))
    if not m:
        return
    url = m.group(1)
    if _LOCAL_HOST_RE.match(url):
        return
    if not _is_plausible_external_url(url):
        return
    try:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        crumb_dir = base / _EXTERNAL_BROWSE_DIR
        crumb_dir.mkdir(parents=True, exist_ok=True)
        (crumb_dir / f"{digest}.json").write_text(
            json.dumps({"url": url, "source": "pre_bash"}), encoding="utf-8"
        )
    except OSError:
        pass


def has_external_browse(base: Path, session_id: str) -> bool:
    if not session_id:
        return False
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return (base / _EXTERNAL_BROWSE_DIR / f"{digest}.json").is_file()


def mark_clone_write(base: Path, session_id: str, paths: list[str]) -> None:
    """Record that a crumbed session attempted clone-shaped file writes.

    The declaration cascade consults this: external browse + clone-shaped
    writes + no active ref dir means a completion commit would ship an
    unverified scratch clone (omx postmortem).
    """
    if not session_id or not paths:
        return
    try:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        crumb_dir = base / _EXTERNAL_BROWSE_DIR
        crumb_dir.mkdir(parents=True, exist_ok=True)
        marker = crumb_dir / f"{digest}-writes.json"
        existing: list[str] = []
        if marker.is_file():
            try:
                prev = json.loads(marker.read_text(encoding="utf-8"))
                existing = [str(p) for p in prev.get("paths", [])]
            except (json.JSONDecodeError, OSError):
                existing = []
        merged = list(dict.fromkeys(existing + [str(p) for p in paths]))[:50]
        marker.write_text(json.dumps({"paths": merged}), encoding="utf-8")
    except OSError:
        pass


def has_clone_writes(base: Path, session_id: str) -> bool:
    if not session_id:
        return False
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return (base / _EXTERNAL_BROWSE_DIR / f"{digest}-writes.json").is_file()


def deferred_checks_blocker(ref_dir: Path) -> str | None:
    """Closeout blocker when the plan deferred checks at a sub-comprehensive
    tier without an explicit user acknowledgment.

    verification-plan.sh records tier-dropped rows in deferredChecks[] —
    emitting them is only half the contract; without a consumer an agent can
    run standard tier, defer every motion-arc compare, and close out green.
    The plan may carry `deferredAck: "<who/why>"` (set when the USER chose
    the lower tier) to release this block.
    """
    plan_path = Path(ref_dir) / "verification-plan.json"
    if not plan_path.is_file():
        return None
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    deferred = plan.get("deferredChecks")
    if not isinstance(deferred, list) or not deferred:
        return None
    if str(plan.get("deferredAck") or "").strip():
        return None
    ids = ", ".join(str(d.get("id", "?")) for d in deferred[:8] if isinstance(d, dict))
    return (
        f"verification-plan.json defers {len(deferred)} check(s) at tier="
        f"{plan.get('tier')} ({ids}). Deferred checks are tracked debt — "
        "closeout requires either re-generating the plan at "
        "tier=comprehensive (UI_CLONE_VERIFY_TIER=comprehensive bash "
        "skills/visual-debug/scripts/verification-plan.sh <ref-dir>) and "
        "running the deferred checks, or an explicit user decision recorded "
        "as `deferredAck` in the plan."
    )


def gate_skip_blocker(ref_dir: Path) -> str | None:
    """Closeout blocker when one or more gates could not be ENFORCED this run.

    run_gate is fail-OPEN (missing uv/Pillow or a subprocess error returns
    passed=True so a dependency-poor host is not bricked) but records every such
    skip in .gate-skip-log. A gate that later actually runs clears its own entry
    (see _clear_gate_skip), so the entries that remain are exactly the gates
    skipped and never recovered — i.e. checks whose green status is unverified.
    Closing out / pushing on those is the silent-fail-open hole this closes.

    Releasable by `gateSkipAck` in verification-plan.json (the user explicitly
    accepting an un-enforced run), mirroring deferredAck. Best-effort: never
    raises.
    """
    log_path = Path(ref_dir) / ".gate-skip-log"
    if not log_path.is_file():
        return None
    try:
        lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return None
    if not lines:
        return None
    if _gate_skip_acknowledged(ref_dir):
        return None
    gates: list[str] = []
    for ln in lines:
        m = re.search(r"gate=(\S+)", ln)
        if m and m.group(1) not in gates:
            gates.append(m.group(1))
    listed = ", ".join(gates[:8])
    return (
        f"{len(gates)} gate(s) were NOT enforced this run (fail-open: {listed}). "
        "run_gate could not execute them (missing uv / Pillow / scikit-image, or "
        "the gate subprocess errored or timed out), so a green result here is "
        "unverified. Closeout requires re-running on a host with the "
        "ui-clone-skills environment (uv + deps) so the gates actually execute "
        "(which clears .gate-skip-log), or an explicit user decision recorded as "
        "`gateSkipAck` in verification-plan.json."
    )
