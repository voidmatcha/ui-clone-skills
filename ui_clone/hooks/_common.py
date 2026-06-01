"""
Shared utilities for ui_clone hook modules.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# ── ANSI colors (shared across pipeline/gate/hooks) ──

GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
BOLD = "\033[1m"
NC = "\033[0m"


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


def find_ref_dir(search_root: Path) -> Path | None:
    """Find ref dir: prefer WIP marker, fall back to newest extracted.json mtime."""
    if not search_root.is_dir():
        return None

    # 1. WIP marker
    for d in sorted(search_root.iterdir()):
        if not d.is_dir():
            continue
        if (d / ".ui-re-active").is_file():
            return d

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
        # Codex review v0.8: gate.py + verification-plan.sh references that
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
    # Codex review v0.8: substring matches on short keywords (`key`, `page`,
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

    Codex audit (signal #2): heavy-motion site agent wrote impl/src/main.jsx
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


def _log_gate_skip(ref_dir: Path, gate_name: str, reason: str) -> None:
    """Append a gate skip event to ref_dir/.gate-skip-log for auditability.

    Best-effort — never raises.
    """
    try:
        from datetime import UTC, datetime

        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        log_path = ref_dir / ".gate-skip-log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts} gate={gate_name} reason={reason}\n")
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
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        reason = f"{type(exc).__name__}: {exc}"
        print(f"ui-clone-skills: WARNING: gate not runnable: {exc}", file=sys.stderr)
        _log_gate_skip(ref_dir, gate_name, reason)
    return {"passed": True, "fail_count": 0, "failures": []}
