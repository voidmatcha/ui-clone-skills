"""Codex-driven onpixel showcase loop automation.

This is maintainer automation for running the ui-clone-skills pipeline against
the local onpixel showcase catalogue. It deliberately separates clone work from
plugin repair work:

1. Clone pass writes only under tmp/onpixel-codex-loop/<slug>/.
2. Skill-fix pass may edit this plugin repo, using the clone failure evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ui_clone.clone_experiment_score import score_clone_attempt
from ui_clone.local_source_reuse import detect_local_source_reuse


@dataclass(frozen=True)
class ShowcaseItem:
    slug: str
    title: str
    description: str
    original_url: str
    disabled: bool = False


@dataclass(frozen=True)
class SiteWorkspace:
    item: ShowcaseItem
    site_dir: Path
    ref_dir: Path
    impl_dir: Path
    handover_path: Path


def _field(obj: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*:\s*(['\"])(.*?)\1", obj, flags=re.S)
    return match.group(2) if match else ""


def parse_showcase_items(text: str) -> list[ShowcaseItem]:
    """Parse the simple object-literal catalogue in ShowcaseClient.tsx."""
    items: list[ShowcaseItem] = []
    for match in re.finditer(r"\{[^{}]*\bslug\s*:\s*['\"][^{}]+?\}", text, flags=re.S):
        obj = match.group(0)
        slug = _field(obj, "slug").strip()
        if not slug:
            continue
        items.append(
            ShowcaseItem(
                slug=slug,
                title=_field(obj, "title").strip() or slug,
                description=_field(obj, "description").strip(),
                original_url=_field(obj, "originalUrl").strip(),
                disabled=bool(re.search(r"\bdisabled\s*:\s*true\b", obj)),
            )
        )
    return items


def discover_showcase_items(
    showcase_root: Path,
    *,
    include_disabled: bool = False,
    include_url_less: bool = False,
) -> list[ShowcaseItem]:
    catalogue = showcase_root / "src" / "app" / "ShowcaseClient.tsx"
    text = catalogue.read_text(encoding="utf-8")
    items = parse_showcase_items(text)
    selected: list[ShowcaseItem] = []
    for item in items:
        if item.disabled and not include_disabled:
            continue
        if not item.original_url and not include_url_less:
            continue
        if not (showcase_root / "src" / "app" / item.slug).is_dir():
            continue
        selected.append(item)
    return selected


def select_items(items: Sequence[ShowcaseItem], slugs: str | None, limit: int | None) -> list[ShowcaseItem]:
    selected = list(items)
    if slugs:
        wanted = [s.strip() for s in slugs.split(",") if s.strip()]
        by_slug = {item.slug: item for item in selected}
        missing = [slug for slug in wanted if slug not in by_slug]
        if missing:
            raise SystemExit(f"unknown showcase slug(s): {', '.join(missing)}")
        selected = [by_slug[slug] for slug in wanted]
    if limit is not None:
        selected = selected[:limit]
    return selected


def _copy_ref_if_present(showcase_root: Path, site: SiteWorkspace) -> bool:
    """Copy a prebuilt reference tree into the site ref dir if one exists.

    Returns True when an external prebuilt reference was supplied (copied in),
    so the caller can mark it acquisition-satisfied-by-supply (LAND item A).
    """
    src = showcase_root / "tmp" / "ref" / site.item.slug
    if not src.is_dir() or site.ref_dir.exists():
        site.ref_dir.mkdir(parents=True, exist_ok=True)
        return False
    shutil.copytree(src, site.ref_dir)
    return True


def _candidate_context_paths(showcase_root: Path, slug: str) -> list[Path]:
    candidates = [
        showcase_root / "src" / "app" / slug / "page.tsx",
        showcase_root / "src" / "app" / slug / "layout.tsx",
        showcase_root / "src" / "projects" / slug,
        showcase_root / "src" / "stories" / f"{slug}.stories.tsx",
        showcase_root / "tmp" / "ref" / slug,
        showcase_root / "public" / "projects" / slug,
        showcase_root / "public" / "images" / slug,
        showcase_root / "public" / "videos" / slug,
        showcase_root / "public" / "fonts" / slug,
    ]
    return [path for path in candidates if path.exists()]


def _handover_sources(showcase_root: Path, slug: str) -> list[Path]:
    roots = [
        showcase_root / "tmp" / "ref" / slug,
        showcase_root / "src" / "projects" / slug,
        showcase_root / "src" / "app" / slug,
    ]
    hits: list[Path] = []
    names = re.compile(r"(handover|handoff|brief|prompt)", re.I)
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if names.search(path.name) or path.suffix.lower() in {".jsonl", ".md"}:
                hits.append(path)
            if len(hits) >= 20:
                return hits
    return hits


def write_handover(showcase_root: Path, site: SiteWorkspace) -> None:
    item = site.item
    context_paths = _candidate_context_paths(showcase_root, item.slug)
    handover_sources = _handover_sources(showcase_root, item.slug)

    def rel(path: Path) -> str:
        try:
            return str(path.relative_to(showcase_root))
        except ValueError:
            return str(path)

    lines = [
        f"# {item.title} Codex Handover",
        "",
        f"- Slug: `{item.slug}`",
        f"- Original URL: {item.original_url or '(none)'}",
        f"- Description: {item.description or '(none)'}",
        f"- Showcase root: `{showcase_root}`",
        f"- Work ref dir: `{site.ref_dir}`",
        f"- Work impl dir: `{site.impl_dir}`",
        "",
        "## Context Paths",
    ]
    if context_paths:
        lines.extend(f"- `{rel(path)}`" for path in context_paths)
    else:
        lines.append("- No local context paths found.")

    lines.extend(["", "## Handover Sources"])
    if handover_sources:
        lines.extend(f"- `{rel(path)}`" for path in handover_sources)
    else:
        lines.append("- No explicit handover/brief/jsonl file found; use the context paths above.")

    lines.extend([
        "",
        "## Clone Pass Contract",
        "",
        "- Implement under the work impl dir only.",
        "- Use the work ref dir as the active `tmp/ref/<slug>` equivalent.",
        "- Do not copy, rsync, cp -R, or port source files/assets from the",
        "  showcase tree into the impl. Local showcase paths are orientation",
        "  only; clone fidelity must come from the original URL and ref artifacts.",
        "- Do not edit plugin code during clone work.",
        "- If a gate failure looks like a ui-clone-skills bug, write a concise",
        "  `skill-issue.md` in the site work dir with reproduction commands and",
        "  evidence paths; leave plugin edits for the separate skill-fix pass.",
        "",
    ])
    site.handover_path.write_text("\n".join(lines), encoding="utf-8")


def write_impl_agents(site: SiteWorkspace, plugin_root: Path) -> None:
    lines = [
        "# AGENTS.md - Natural Clone Workspace",
        "",
        f"This directory is the implementation workspace for `{site.item.original_url}`.",
        "The prompt sent to Codex is intentionally terse and user-like; this",
        "workspace file carries runner constraints without leaking them into the",
        "user prompt.",
        "",
        "## Workspace",
        "",
        "- Create and edit the clone inside this directory only.",
        "- Serve previews with `--host 0.0.0.0` when possible.",
        f"- Active reference artifacts: `{site.ref_dir}`.",
        f"- ui-clone-skills root: `{plugin_root}`.",
        "",
        "## Source Rules",
        "",
        "- Build from the live URL, downloaded assets, and generated reference",
        "  artifacts.",
        "- Do not copy, rsync, cp -R, or port source files or public assets from",
        "  local showcase/source directories into this implementation.",
        "- Do not edit plugin code, scripts, hooks, manifests, or tests during the",
        "  clone pass.",
        "- Do not inspect, patch, or debug ui-clone-skills gate internals from this",
        "  workspace. If `completion-report.sh` or `ui_clone.goal --check-done`",
        "  fails, treat the output as clone evidence to fix in `impl/` or report",
        "  as `INCOMPLETE`; do not chase gate implementation code.",
        "",
        "## Research And Scoring",
        "",
        f"- Read `{site.site_dir / 'clone-research.md'}` before editing; it summarizes",
        "  the reference evidence available for this run.",
        f"- Treat `{site.site_dir / 'clone-experiments.tsv'}` as runner-owned score",
        "  history. Do not edit it from clone work.",
        "",
        "## Browser Inspection",
        "",
        "- Do not run `npx playwright node`; Playwright CLI has no `node` subcommand.",
        "- Use `node` for inline Playwright scripts, or use real Playwright CLI",
        "  subcommands such as `npx playwright screenshot`.",
        "",
        "## Layout Stability",
        "",
        "- Before adding broad CSS overrides, check 1440px, 1280px, and 375px",
        "  viewport geometry.",
        "- `document.documentElement.scrollWidth` and `document.body.scrollWidth`",
        "  must not exceed `window.innerWidth` unless the reference does by the",
        "  same amount.",
        "- Clip offscreen rails, marquee strips, mosaics, and parallax layers inside",
        "  section-local wrappers instead of letting children widen the page.",
        "- Do not keep piling `position:absolute`, `!important`, `zoom`, or fixed",
        "  `1440px` overrides. Replace stale overrides with structured responsive",
        "  layout code.",
        "- If horizontal overflow or distorted fixed-width layout remains, answer",
        "  `INCOMPLETE` with the offending selectors and viewport.",
        "",
        "## Unattended Loop",
        "",
        "- Do not ask the user to choose between approaches, approve a retry,",
        "  or pick a blocker. The runner already authorized safe reversible",
        "  clone work.",
        "- When several safe fixes are viable, pick the next reversible fix yourself",
        "  using the current artifacts, gate output, and score history, then verify.",
        "- Do not ask the user to stash or revert unrelated parent-repo WIP.",
        "  `impl-scope` ignores unchanged baseline-dirty files; if it still fails,",
        "  report `INCOMPLETE` with the changed paths and fix only clone-caused",
        "  edits from this workspace.",
        "- Stop only for destructive/external actions (credentials, paid licenses,",
        "  deleting unrelated user work) or a documented unclonable condition.",
        "",
        "## Closeout",
        "",
        "Before claiming done, run both checks from this directory:",
        "",
        f"- `bash \"{plugin_root / 'scripts' / 'verify' / 'completion-report.sh'}\" --check \"{site.ref_dir}\" \"$(pwd)\"`",
        f"- `uv run --project \"{plugin_root}\" python -m ui_clone.goal \"{site.ref_dir}\" --check-done`",
        "",
        "If either check reports missing artifacts, failed rows, or a non-zero exit,",
        "the first line of your final answer must be exactly `INCOMPLETE`, followed",
        "by current_gate, failed artifacts/gates, and the next command to run. Only",
        "start with `DONE` when both checks exit 0. Build success, HTTP 200, page",
        "title checks, manual screenshots, implementation-only smoke tests, CLI",
        "`task_complete`, elapsed-cost lines, or closed tabs are not completion",
        "evidence.",
        "",
    ]
    site.impl_dir.joinpath("AGENTS.md").write_text("\n".join(lines), encoding="utf-8")


def _read_json_artifact(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _artifact_list(data: Any, key: str) -> list[dict[str, Any]]:
    raw = data.get(key) if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _entry_index(entry: dict[str, Any]) -> int | None:
    for key in ("index", "i", "sectionIndex"):
        value = entry.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _entry_y(entry: dict[str, Any]) -> float | None:
    for key in ("top", "y"):
        value = entry.get(key)
        if isinstance(value, int | float):
            return float(value)
    rect = entry.get("rect")
    if isinstance(rect, dict):
        for key in ("top", "y"):
            value = rect.get(key)
            if isinstance(value, int | float):
                return float(value)
    return None


def _entry_height(entry: dict[str, Any]) -> float | None:
    value = entry.get("height")
    if isinstance(value, int | float):
        return float(value)
    rect = entry.get("rect")
    if isinstance(rect, dict):
        value = rect.get("height")
        if isinstance(value, int | float):
            return float(value)
    return None


def _component_label(entry: dict[str, Any]) -> str | None:
    for key in ("file", "componentFile", "componentName", "name", "id", "sectionId"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _section_label(entry: dict[str, Any]) -> str:
    for key in ("id", "name", "className", "class", "cls"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    index = _entry_index(entry)
    return f"section-{index}" if index is not None else "unnamed"


def _component_map_by_index(ref_dir: Path) -> dict[int, str]:
    data = _read_json_artifact(ref_dir / "component-map.json")
    mapped: dict[int, str] = {}
    for entry in _artifact_list(data, "sections"):
        index = _entry_index(entry)
        label = _component_label(entry)
        if index is not None and label:
            mapped[index] = label
    return mapped


def _section_for_y(sections: Sequence[dict[str, Any]], y: float | None) -> int | None:
    if y is None:
        return None
    fallback: int | None = None
    for section in sections:
        index = _entry_index(section)
        top = _entry_y(section)
        height = _entry_height(section)
        if index is None or top is None:
            continue
        if top <= y and (height is None or y < top + height):
            return index
        if y >= top:
            fallback = index
    return fallback


def _compact_signal(entry: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("selector", "trigger", "event", "property", "type", "kind", "name", "id"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(f"{key}={value.strip()}")
    return ", ".join(parts) if parts else json.dumps(entry, ensure_ascii=False, default=str)[:180]


def write_clone_research(site: SiteWorkspace) -> None:
    ref_dir = site.ref_dir
    section_entries = _artifact_list(_read_json_artifact(ref_dir / "section-map.json"), "sections")
    component_by_index = _component_map_by_index(ref_dir)
    visible_images = _artifact_list(_read_json_artifact(ref_dir / "visible-images.json"), "images")
    transitions = _artifact_list(_read_json_artifact(ref_dir / "transition-spec.json"), "transitions")
    runtime = _read_json_artifact(ref_dir / "runtime-spec.json")
    runtime_signals = _artifact_list(runtime, "signals")
    if not runtime_signals:
        runtime_signals = _artifact_list(runtime, "interactions")
    placement = _read_json_artifact(ref_dir / "asset-placement.json")
    missing_assets = _artifact_list(placement, "missingPlacements")

    lines = [
        "# Clone Research",
        "",
        f"Original URL: {site.item.original_url or '(none)'}",
        "",
        "## Section Map",
    ]
    if section_entries:
        for section in section_entries[:30]:
            index = _entry_index(section)
            top = _entry_y(section)
            height = _entry_height(section)
            component = component_by_index.get(index) if index is not None else None
            detail = [
                f"Section {index}" if index is not None else "Section ?",
                _section_label(section),
            ]
            if top is not None:
                detail.append(f"top={int(top)}")
            if height is not None:
                detail.append(f"height={int(height)}")
            if component:
                detail.append(f"component={component}")
            lines.append(f"- {', '.join(detail)}")
    else:
        lines.append("- No section-map.json sections available.")

    lines.extend(["", "## Visible Assets By Section"])
    if visible_images:
        grouped: dict[int | None, list[str]] = {}
        for image in visible_images:
            src = image.get("src") or image.get("currentSrc") or image.get("url")
            if not isinstance(src, str) or not src.strip():
                continue
            section_index = _section_for_y(section_entries, _entry_y(image))
            grouped.setdefault(section_index, []).append(src.strip())
        if grouped:
            for section_index, assets in sorted(grouped.items(), key=lambda item: -1 if item[0] is None else item[0]):
                label = "unmapped" if section_index is None else f"section {section_index}"
                component = component_by_index.get(section_index) if section_index is not None else None
                suffix = f" ({component})" if component else ""
                lines.append(f"- {label}{suffix}")
                lines.extend(f"  - {asset}" for asset in assets[:12])
        else:
            lines.append("- visible-images.json had no usable src values.")
    else:
        lines.append("- No visible-images.json assets available.")

    lines.extend(["", "## Video Lottie Canvas Evidence"])
    evidence_files = [
        "video-detection.json",
        "lottie-detection.json",
        "canvas-webgl-detection.json",
        "animation-runtime-dump.json",
    ]
    evidence_lines: list[str] = []
    for name in evidence_files:
        data = _read_json_artifact(ref_dir / name)
        if isinstance(data, dict):
            status = data.get("status") or data.get("primaryRenderType") or "present"
            evidence_lines.append(f"- {name}: {status}")
    lines.extend(evidence_lines or ["- No video/lottie/canvas evidence artifact available."])

    lines.extend(["", "## Known Hover Scroll Transition Signals"])
    signal_lines: list[str] = []
    signal_lines.extend(f"- transition: {_compact_signal(entry)}" for entry in transitions[:20])
    signal_lines.extend(f"- runtime: {_compact_signal(entry)}" for entry in runtime_signals[:20])
    lines.extend(signal_lines or ["- No transition-spec.json or runtime-spec.json signals available."])

    lines.extend(["", "## Current missing assets"])
    if missing_assets:
        for asset in missing_assets[:30]:
            src = asset.get("src") or asset.get("url") or asset.get("asset")
            section_value = asset.get("sectionIndex")
            component = asset.get("componentFile")
            bits = []
            if isinstance(src, str):
                bits.append(src)
            if section_value is not None:
                bits.append(f"section={section_value}")
            if isinstance(component, str):
                bits.append(f"component={component}")
            lines.append(f"- {', '.join(bits) if bits else json.dumps(asset, ensure_ascii=False, default=str)[:180]}")
    else:
        lines.append("- No current missing assets from asset-placement.json.")

    site.site_dir.joinpath("clone-research.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare_site_workspace(
    showcase_root: Path,
    work_root: Path,
    item: ShowcaseItem,
    *,
    reset: bool,
) -> SiteWorkspace:
    site_dir = work_root / item.slug
    if reset and site_dir.exists():
        shutil.rmtree(site_dir)
    ref_dir = site_dir / "ref"
    impl_dir = site_dir / "impl"
    handover_path = site_dir / "handover.md"
    site_dir.mkdir(parents=True, exist_ok=True)
    impl_dir.mkdir(parents=True, exist_ok=True)
    site = SiteWorkspace(item, site_dir, ref_dir, impl_dir, handover_path)
    supplied_prebuilt = _copy_ref_if_present(showcase_root, site)
    if supplied_prebuilt:
        # LAND item A: an externally-supplied prebuilt reference is acquisition-
        # satisfied-by-supply. Advisory only — inspect_site() still gates "done"
        # via check_strict_done + `goal --check-done`, so the self-pass /
        # localized-defect / ref-variance guards and the pinned-scorer verdict
        # remain authoritative.
        from ui_clone.pipeline import reference_evidence_satisfied

        satisfied, missing = reference_evidence_satisfied(
            site.ref_dir, external_prebuilt=True
        )
        (site.site_dir / "reference-evidence-supply.json").write_text(
            json.dumps(
                {
                    "external_prebuilt": True,
                    "ref_dir": str(site.ref_dir),
                    "satisfied": satisfied,
                    "missing_acquisition_artifacts": missing,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    write_handover(showcase_root, site)
    write_impl_agents(site, Path(__file__).resolve().parents[1])
    write_clone_research(site)
    return site


def build_clone_prompt(site: SiteWorkspace, _showcase_root: Path, _plugin_root: Path) -> str:
    target = site.item.original_url or site.item.title or site.item.slug
    return (
        f"Copy {target} as closely as possible, including transitions. "
        "Make it runnable locally."
    )


def build_skill_fix_prompt(site: SiteWorkspace, plugin_root: Path) -> str:
    return f"""You are the ui-clone-skills maintainer pass after a Codex clone run.

Source site: `{site.item.slug}` ({site.item.original_url})
Work dir: `{site.site_dir}`
Ref dir: `{site.ref_dir}`
Impl dir: `{site.impl_dir}`
Clone transcript: `{site.site_dir / "codex-clone.jsonl"}`
Clone final message: `{site.site_dir / "codex-clone-last.md"}`
Optional issue report: `{site.site_dir / "skill-issue.md"}`
Plugin repo: `{plugin_root}`

Task:
1. Inspect the clone evidence and decide whether the failure is a legitimate
   ui-clone-skills bug or just an incomplete impl.
2. If it is a skill bug, use TDD: add the failing test, verify RED, implement
   the minimal fix, verify GREEN, update CHANGELOG/version if skill code or
   docs changed, run required repo gates, and commit.
3. If it is not a skill bug, do not edit plugin code. Write
   `{site.site_dir / "no-skill-change.md"}` explaining the evidence.
4. Do not edit the showcase source tree in this pass.
"""


def build_codex_command(
    *,
    codex_bin: str,
    cwd: Path,
    output_last_message: Path,
    add_dirs: Sequence[Path],
    model: str | None,
    extra_args: Sequence[str],
) -> list[str]:
    command = [
        codex_bin,
        "exec",
        "--json",
        "-C",
        str(cwd),
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-bypass-hook-trust",
        "--enable",
        "plugin_hooks",
        "-o",
        str(output_last_message),
    ]
    for directory in add_dirs:
        command.extend(["--add-dir", str(directory)])
    if model:
        command.extend(["-m", model])
    command.extend(extra_args)
    command.append("-")
    return command


def invoke_codex(
    prompt: str,
    *,
    codex_bin: str,
    cwd: Path,
    log_path: Path,
    output_last_message: Path,
    status_path: Path,
    add_dirs: Sequence[Path],
    model: str | None,
    extra_args: Sequence[str],
    timeout_s: int,
    poll_s: int,
) -> dict[str, Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output_last_message.parent.mkdir(parents=True, exist_ok=True)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_codex_command(
        codex_bin=codex_bin,
        cwd=cwd,
        output_last_message=output_last_message,
        add_dirs=add_dirs,
        model=model,
        extra_args=extra_args,
    )

    def write_status(status: str, poll_count: int, exit_code: int | None = None) -> None:
        _write_json(
            status_path,
            {
                "status": status,
                "exitCode": exit_code,
                "pollCount": poll_count,
                "elapsedS": round(time.time() - started, 1),
                "log": str(log_path),
                "stderr": str(log_path.with_suffix(log_path.suffix + ".stderr")),
                "lastMessage": str(output_last_message),
            },
        )

    started = time.time()
    stderr_path = log_path.with_suffix(log_path.suffix + ".stderr")
    poll_count = 0
    with log_path.open("w", encoding="utf-8") as stdout_fh, stderr_path.open("w", encoding="utf-8") as stderr_fh:
        env = os.environ.copy()
        plugin_root = Path(__file__).resolve().parents[1]
        env.setdefault("PLUGIN_ROOT", str(plugin_root))
        env.setdefault("CODEX_PLUGIN_ROOT", str(plugin_root))
        env.setdefault("UI_CLONE_ROOT", str(plugin_root))
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=stdout_fh,
            stderr=stderr_fh,
            text=True,
            cwd=str(cwd),
            env=env,
        )
        if proc.stdin is not None:
            proc.stdin.write(prompt)
            proc.stdin.close()
        write_status("running", poll_count)
        while True:
            ret = proc.poll()
            if ret is not None:
                write_status("ok" if ret == 0 else "error", poll_count, ret)
                break
            if time.time() - started >= timeout_s:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
                write_status("timeout", poll_count, 124)
                return {
                    "status": "timeout",
                    "exit_code": 124,
                    "elapsed_s": round(time.time() - started, 1),
                    "command": command,
                    "status_path": str(status_path),
                }
            poll_count += 1
            write_status("running", poll_count)
            time.sleep(max(1, poll_s))

    exit_code = proc.returncode if proc.returncode is not None else 1
    return {
        "status": "ok" if exit_code == 0 else "error",
        "exit_code": exit_code,
        "elapsed_s": round(time.time() - started, 1),
        "command": command,
        "status_path": str(status_path),
    }


def inspect_site(site: SiteWorkspace) -> dict[str, Any]:
    try:
        from ui_clone.benchmark_harness import check_strict_done

        done, unmet = check_strict_done(site.ref_dir, site.impl_dir)
    except Exception as exc:  # pragma: no cover - defensive summary path
        done = False
        unmet = [f"strict inspection failed: {exc}"]

    goal_status: dict[str, Any] = {"exit_code": None}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ui_clone.goal", str(site.ref_dir), "--check-done"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        goal_status = {
            "exit_code": proc.returncode,
            "stdout": proc.stdout.strip()[-1000:],
            "stderr": proc.stderr.strip()[-1000:],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        goal_status = {"exit_code": None, "error": str(exc)}
    return {"done": done, "unmet": unmet, "goal": goal_status}


def _protected_showcase_roots(showcase_root: Path, slug: str) -> list[Path]:
    return [
        showcase_root / "src" / "projects" / slug,
        showcase_root / "src" / "app" / slug,
        showcase_root / "public" / "projects" / slug,
        showcase_root / "public" / "images" / slug,
        showcase_root / "public" / "videos" / slug,
        showcase_root / "public" / "fonts" / slug,
    ]


def detect_showcase_reuse(
    site: SiteWorkspace,
    showcase_root: Path,
    *,
    log_paths: Sequence[Path] | None = None,
) -> list[str]:
    """Return contamination findings when clone work copied from showcase.

    Reading local showcase files is allowed for orientation. Copy-style commands
    from showcase source/public paths into impl are not, because they measure
    the pre-existing onpixel implementation rather than the clone pipeline.
    """
    logs = list(log_paths) if log_paths is not None else sorted(site.site_dir.glob("codex-clone*.jsonl"))
    return detect_local_source_reuse(
        impl_dir=site.impl_dir,
        protected_roots=_protected_showcase_roots(showcase_root, site.item.slug),
        log_paths=logs,
        source_label="showcase",
    )


_GATE_SOURCE_PATH_RE = re.compile(r"\b(?:[\w./-]*/)?ui_clone/(?:goal|gate)\.py\b")
_GATE_MODULE_SOURCE_RE = re.compile(r"\b(?:[\w./-]*/)?ui_clone/gates/[\w./-]+\.py\b")
_GATE_READ_RE = re.compile(r"\b(?:Read|Edit|Open)\s+(?:goal|gate)\.py\b")
_GATE_SYMBOL_SEARCH_RE = re.compile(r"\b(?:Search|grep|rg)\b[^\n]*(?:current_gate|VALID_GATES|gate_[a-z_]+)")


def _read_text_for_detection(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def detect_clone_scope_leak(
    *,
    log_paths: Sequence[Path],
    text_paths: Sequence[Path] | None = None,
) -> list[str]:
    """Detect clone-worker drift into ui-clone-skills implementation internals.

    Normal closeout commands such as `python -m ui_clone.goal --check-done` are
    allowed. This flags only source/plumbing investigation that belongs in a
    maintainer pass, not in a natural clone attempt.
    """
    findings: list[str] = []
    seen: set[str] = set()

    def add(message: str) -> None:
        if message not in seen:
            seen.add(message)
            findings.append(message)

    text = "\n".join(_read_text_for_detection(path) for path in [*log_paths, *(text_paths or [])])

    for pattern in (_GATE_SOURCE_PATH_RE, _GATE_MODULE_SOURCE_RE):
        for match in pattern.finditer(text):
            add(f"gate source investigation: {match.group(0)}")

    for match in _GATE_READ_RE.finditer(text):
        add(f"gate source read: {match.group(0)}")

    for match in _GATE_SYMBOL_SEARCH_RE.finditer(text):
        add(f"gate symbol search: {match.group(0).strip()}")

    return findings


def _write_scope_leak_issue(
    site: SiteWorkspace,
    findings: Sequence[str],
    *,
    count: int,
    threshold: int,
) -> None:
    lines = [
        "# Clone Scope Leak",
        "",
        "The clone worker repeatedly investigated ui-clone-skills gate internals",
        "instead of treating gate output as clone evidence.",
        "",
        f"Observed scope-leak count: {count}/{threshold}",
        "",
        "## Findings",
        *(f"- {finding}" for finding in findings),
        "",
        "## Required maintainer action",
        "",
        "Review whether the generated clone workspace instructions or runner",
        "closeout wording need adjustment. Do not continue natural clone retries",
        "until this scope boundary is addressed.",
        "",
    ]
    site.site_dir.joinpath("skill-issue.md").write_text("\n".join(lines), encoding="utf-8")


def _apply_scope_leak_findings(
    site: SiteWorkspace,
    inspection: dict[str, Any],
    findings: Sequence[str],
    *,
    count: int,
    threshold: int,
) -> dict[str, Any]:
    updated = dict(inspection)
    unmet = list(updated.get("unmet") or [])
    action = "meta-fix" if count >= threshold else "recorded"
    updated["done"] = False
    updated["unmet"] = [
        f"clone scope leak detected: {findings[0]}",
        *unmet,
    ]
    updated["scopeLeak"] = {
        "status": "fail",
        "findings": list(findings),
        "count": count,
        "threshold": threshold,
        "action": action,
    }
    if action == "meta-fix":
        _write_scope_leak_issue(site, findings, count=count, threshold=threshold)
    return updated


def _write_source_reuse_report(site: SiteWorkspace, findings: Sequence[str]) -> None:
    lines = [
        "# Showcase Source Reuse",
        "",
        "This clone attempt is contaminated: it copied or embedded local showcase",
        "source/assets instead of deriving the implementation from the live",
        "reference URL and ref artifacts.",
        "",
        "## Findings",
        *(f"- {finding}" for finding in findings),
        "",
        "## Required Next Action",
        "",
        "Discard the contaminated impl or overwrite it with a clean implementation",
        "built from the original URL, downloaded assets, and generated ref artifacts.",
        "Do not use the local showcase implementation as source material.",
        "",
    ]
    site.site_dir.joinpath("source-reuse.md").write_text("\n".join(lines), encoding="utf-8")


def _apply_source_reuse_findings(
    site: SiteWorkspace,
    inspection: dict[str, Any],
    findings: Sequence[str],
) -> dict[str, Any]:
    if not findings:
        return inspection
    _write_source_reuse_report(site, findings)
    updated = dict(inspection)
    unmet = list(updated.get("unmet") or [])
    unmet.insert(0, f"showcase source reuse detected: {findings[0]}")
    updated["done"] = False
    updated["unmet"] = unmet
    updated["sourceReuse"] = {"status": "fail", "findings": list(findings)}
    return updated


def _completion_status(inspection: dict[str, Any]) -> str:
    source_reuse = inspection.get("sourceReuse")
    if isinstance(source_reuse, dict) and source_reuse.get("status") == "fail":
        return "contaminated"
    return "done" if inspection.get("done") else "wip"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _attempt_path(site: SiteWorkspace, stem: str, suffix: str, attempt: int) -> Path:
    if attempt == 1:
        return site.site_dir / f"{stem}{suffix}"
    return site.site_dir / f"{stem}-{attempt}{suffix}"


def _archive_skill_issue(site: SiteWorkspace, attempt: int) -> None:
    issue_path = site.site_dir / "skill-issue.md"
    if not issue_path.exists():
        return
    archive_path = site.site_dir / f"skill-issue-attempt-{attempt}.md"
    counter = 2
    while archive_path.exists():
        archive_path = site.site_dir / f"skill-issue-attempt-{attempt}-{counter}.md"
        counter += 1
    issue_path.rename(archive_path)


EXPERIMENT_COLUMNS = [
    "attempt",
    "score",
    "status",
    "asset_missing",
    "section_pass",
    "section_fail",
    "runtime_proof",
    "transition_proof",
    "commit_or_snapshot",
    "description",
]


def _tsv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.replace("\t", " ").split())


def _impl_snapshot(impl_dir: Path) -> str:
    try:
        file_count = sum(1 for path in impl_dir.rglob("*") if path.is_file())
    except OSError:
        file_count = 0
    return f"files:{file_count}"


def _attempt_description(clone_result: dict[str, Any], last_message_path: Path) -> str:
    if clone_result.get("status") == "dry-run":
        return "dry-run"
    if last_message_path.is_file():
        try:
            text = last_message_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        line = " ".join(text.split())
        if line:
            return line[:200]
    return str(clone_result.get("status") or "unknown")


def _append_experiment_row(
    site: SiteWorkspace,
    score: dict[str, Any],
    *,
    commit_or_snapshot: str,
    description: str,
) -> None:
    path = site.site_dir / "clone-experiments.tsv"
    needs_header = not path.exists() or path.stat().st_size == 0
    row = {
        "attempt": score.get("attempt"),
        "score": score.get("score"),
        "status": score.get("completionStatus"),
        "asset_missing": score.get("assetMissing"),
        "section_pass": score.get("sectionPass"),
        "section_fail": score.get("sectionFail"),
        "runtime_proof": score.get("runtimeProof"),
        "transition_proof": score.get("transitionProof"),
        "commit_or_snapshot": commit_or_snapshot,
        "description": description,
    }
    lines: list[str] = []
    if needs_header:
        lines.append("\t".join(EXPERIMENT_COLUMNS))
    lines.append("\t".join(_tsv_cell(row[column]) for column in EXPERIMENT_COLUMNS))
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _should_run_skill_fix(
    *,
    args: argparse.Namespace,
    site: SiteWorkspace,
    clone_result: dict[str, Any],
    inspection: dict[str, Any],
) -> bool:
    if args.dry_run or args.skip_skill_fix:
        return False
    source_reuse = inspection.get("sourceReuse")
    if isinstance(source_reuse, dict) and source_reuse.get("status") == "fail":
        return False
    if args.skill_fix_policy == "issue-only":
        return (site.site_dir / "skill-issue.md").exists()
    return clone_result.get("exit_code") != 0 or not inspection.get("done")


def run_loop(args: argparse.Namespace) -> str:
    plugin_root = Path(__file__).resolve().parents[1]
    showcase_root = Path(args.showcase_root).expanduser().resolve()
    work_root = Path(args.work_root).expanduser().resolve()
    work_root.mkdir(parents=True, exist_ok=True)

    items = discover_showcase_items(
        showcase_root,
        include_disabled=args.include_disabled,
        include_url_less=args.include_url_less,
    )
    selected = select_items(items, args.slugs, args.limit)
    if not selected:
        raise SystemExit("no showcase items selected")

    summary: dict[str, Any] = {
        "showcaseRoot": str(showcase_root),
        "workRoot": str(work_root),
        "dryRun": args.dry_run,
        "experimentLog": args.experiment_log,
        "scoreCloneAttempts": args.score_clone_attempts,
        "discardWorse": args.discard_worse,
        "scopeLeakThreshold": args.scope_leak_threshold,
        "sites": [],
    }
    log_path = work_root / "onpixel-codex-loop.jsonl"
    log_fh = log_path.open("a", encoding="utf-8")

    def log(record: dict[str, Any]) -> None:
        log_fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        log_fh.flush()

    outcome = "DRY_RUN" if args.dry_run else "RAN"
    try:
        for item in selected:
            site = prepare_site_workspace(showcase_root, work_root, item, reset=args.reset)
            site_record: dict[str, Any] = {
                "slug": item.slug,
                "item": asdict(item),
                "cloneAttempts": [],
                "skillFixAttempts": [],
                "done": False,
                "completionStatus": "wip",
                "previewEligible": False,
            }
            summary["sites"].append(site_record)
            log({"event": "site_start", "slug": item.slug, "siteDir": str(site.site_dir)})

            clone_result: dict[str, Any] = {"status": "not-run"}
            inspection: dict[str, Any] = {"done": False, "unmet": ["not inspected"], "goal": {}}
            max_attempts = max(1, args.clone_attempts)
            scope_leak_count = 0

            for attempt in range(1, max_attempts + 1):
                previous_score: dict[str, Any] | None = None
                if args.score_clone_attempts:
                    previous_score = score_clone_attempt(
                        site.ref_dir,
                        site.impl_dir,
                        completion_status=site_record["completionStatus"],
                    )
                clone_prompt = build_clone_prompt(site, showcase_root, plugin_root)
                clone_prompt_path = _attempt_path(site, "codex-clone-prompt", ".md", attempt)
                clone_log_path = _attempt_path(site, "codex-clone", ".jsonl", attempt)
                clone_last_path = _attempt_path(site, "codex-clone-last", ".md", attempt)
                clone_prompt_path.write_text(clone_prompt, encoding="utf-8")

                if args.dry_run:
                    clone_result = {"status": "dry-run", "prompt": str(clone_prompt_path)}
                else:
                    clone_result = invoke_codex(
                        clone_prompt,
                        codex_bin=args.codex_bin,
                        cwd=site.impl_dir,
                        log_path=clone_log_path,
                        output_last_message=clone_last_path,
                        status_path=_attempt_path(site, "codex-clone-status", ".json", attempt),
                        add_dirs=[plugin_root, site.ref_dir],
                        model=args.model,
                        extra_args=args.codex_arg,
                        timeout_s=args.timeout_s,
                        poll_s=args.poll_s,
                    )

                inspection = inspect_site(site)
                reuse_findings = detect_showcase_reuse(site, showcase_root, log_paths=[clone_log_path])
                inspection = _apply_source_reuse_findings(site, inspection, reuse_findings)
                scope_leak_findings = detect_clone_scope_leak(
                    log_paths=[clone_log_path],
                    text_paths=[
                        clone_last_path,
                        clone_log_path.with_suffix(clone_log_path.suffix + ".stderr"),
                    ],
                )
                scope_leak_threshold = max(1, args.scope_leak_threshold)
                scope_leak_threshold_reached = False
                if scope_leak_findings:
                    scope_leak_count += 1
                    scope_leak_threshold_reached = scope_leak_count >= scope_leak_threshold
                    inspection = _apply_scope_leak_findings(
                        site,
                        inspection,
                        scope_leak_findings,
                        count=scope_leak_count,
                        threshold=scope_leak_threshold,
                    )
                completion_status = _completion_status(inspection)
                attempt_score: dict[str, Any] | None = None
                if args.score_clone_attempts:
                    attempt_score = score_clone_attempt(
                        site.ref_dir,
                        site.impl_dir,
                        attempt=attempt,
                        completion_status=completion_status,
                    )
                    if previous_score is not None:
                        previous_value = int(previous_score.get("score") or 0)
                        delta = int(attempt_score["score"]) - previous_value
                        attempt_score["previousScore"] = previous_value
                        attempt_score["delta"] = delta
                        attempt_score["worseThanPrevious"] = delta < 0
                    attempt_score["discardWorse"] = "enabled" if args.discard_worse else "disabled"
                    if args.experiment_log:
                        _append_experiment_row(
                            site,
                            attempt_score,
                            commit_or_snapshot=_impl_snapshot(site.impl_dir),
                            description=_attempt_description(clone_result, clone_last_path),
                        )
                attempt_record = {
                    "attempt": attempt,
                    "clone": clone_result,
                    "inspection": inspection,
                    "completionStatus": completion_status,
                    "previewEligible": completion_status == "done",
                    "skillIssue": str(site.site_dir / "skill-issue.md")
                    if (site.site_dir / "skill-issue.md").exists()
                    else None,
                }
                if attempt_score is not None:
                    attempt_record["score"] = attempt_score
                site_record["cloneAttempts"].append(attempt_record)
                site_record["clone"] = clone_result
                site_record["inspection"] = inspection
                if attempt_score is not None:
                    site_record["score"] = attempt_score
                site_record["done"] = bool(inspection.get("done"))
                site_record["completionStatus"] = completion_status
                site_record["previewEligible"] = completion_status == "done"
                _write_json(work_root / "onpixel-codex-loop-summary.json", summary)

                if inspection.get("done"):
                    break

                if _should_run_skill_fix(
                    args=args,
                    site=site,
                    clone_result=clone_result,
                    inspection=inspection,
                ):
                    skill_prompt = build_skill_fix_prompt(site, plugin_root)
                    skill_prompt_path = _attempt_path(site, "codex-skill-fix-prompt", ".md", attempt)
                    skill_prompt_path.write_text(skill_prompt, encoding="utf-8")
                    if args.dry_run:
                        skill_result = {"status": "dry-run", "prompt": str(skill_prompt_path)}
                    else:
                        skill_result = invoke_codex(
                            skill_prompt,
                            codex_bin=args.codex_bin,
                            cwd=plugin_root,
                            log_path=_attempt_path(site, "codex-skill-fix", ".jsonl", attempt),
                            output_last_message=_attempt_path(site, "codex-skill-fix-last", ".md", attempt),
                            status_path=_attempt_path(site, "codex-skill-fix-status", ".json", attempt),
                            add_dirs=[site.site_dir, showcase_root],
                            model=args.model,
                            extra_args=args.codex_arg,
                            timeout_s=args.timeout_s,
                            poll_s=args.poll_s,
                        )
                    site_record["skillFixAttempts"].append({"attempt": attempt, "skillFix": skill_result})
                    site_record["skillFix"] = skill_result
                    if args.skill_fix_policy == "issue-only":
                        _archive_skill_issue(site, attempt)
                    if scope_leak_threshold_reached:
                        site_record["stopReason"] = "repeated-scope-leak"
                        break
                    if args.skill_fix_policy != "issue-only":
                        break
                elif args.dry_run and not args.skip_skill_fix and args.skill_fix_policy != "issue-only":
                    skill_prompt = build_skill_fix_prompt(site, plugin_root)
                    skill_prompt_path = _attempt_path(site, "codex-skill-fix-prompt", ".md", attempt)
                    skill_prompt_path.write_text(skill_prompt, encoding="utf-8")
                    site_record["skillFix"] = {"status": "dry-run", "prompt": str(skill_prompt_path)}
                    break
                elif scope_leak_threshold_reached:
                    site_record["stopReason"] = "repeated-scope-leak"
                    break

                if args.stop_on_error and clone_result.get("status") in {"error", "timeout"}:
                    outcome = "STOPPED_ON_ERROR"
                    break

                if args.dry_run:
                    break

            if "skillFix" not in site_record:
                site_record["skillFix"] = {"status": "skipped"}

            log({"event": "site_end", "slug": item.slug, "record": site_record})
            _write_json(work_root / "onpixel-codex-loop-summary.json", summary)

            if args.stop_on_error and clone_result.get("status") in {"error", "timeout"}:
                outcome = "STOPPED_ON_ERROR"
                break
    finally:
        log_fh.close()

    _write_json(work_root / "onpixel-codex-loop-summary.json", summary)
    print(json.dumps({"outcome": outcome, "sites": len(summary["sites"]), "summary": str(work_root / "onpixel-codex-loop-summary.json")}, ensure_ascii=False))
    return outcome


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ui_clone.onpixel_showcase_loop",
        description="Run Codex over onpixel showcase clones, then launch skill-fix passes from evidence.",
    )
    parser.add_argument("--showcase-root", default="~/Documents/onpixel/apps/showcase")
    parser.add_argument("--work-root", default="tmp/onpixel-codex-loop")
    parser.add_argument("--slugs", default=None, help="Comma-separated slug allowlist")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--include-url-less", action="store_true")
    parser.add_argument("--reset", action="store_true", help="Delete selected site work dirs first")
    parser.add_argument("--dry-run", action="store_true", help="Write prompts/summary without invoking Codex")
    parser.add_argument("--skip-skill-fix", action="store_true")
    parser.add_argument(
        "--experiment-log",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Append clone attempt rows to clone-experiments.tsv",
    )
    parser.add_argument(
        "--score-clone-attempts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Score each clone attempt from ref artifacts",
    )
    parser.add_argument(
        "--discard-worse",
        action="store_true",
        help="Reserve destructive worse-score handling for explicit opt-in runs",
    )
    parser.add_argument(
        "--clone-attempts",
        type=int,
        default=1,
        help="Maximum clone passes per site before moving to the next site",
    )
    parser.add_argument(
        "--scope-leak-threshold",
        type=int,
        default=2,
        help="Repeated clone-worker gate-internal investigations before switching to a maintainer pass",
    )
    parser.add_argument(
        "--skill-fix-policy",
        choices=("incomplete", "issue-only"),
        default="incomplete",
        help=(
            "When to launch the maintainer pass: existing incomplete behavior, "
            "or only when the clone pass writes skill-issue.md"
        ),
    )
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--model", default=None)
    parser.add_argument("--codex-arg", action="append", default=[])
    parser.add_argument("--timeout-s", type=int, default=14400)
    parser.add_argument("--poll-s", type=int, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    outcome = run_loop(build_parser().parse_args(argv))
    return 0 if outcome in {"RAN", "DRY_RUN"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
