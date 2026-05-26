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
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

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


def _copy_ref_if_present(showcase_root: Path, site: SiteWorkspace) -> None:
    src = showcase_root / "tmp" / "ref" / site.item.slug
    if not src.is_dir() or site.ref_dir.exists():
        site.ref_dir.mkdir(parents=True, exist_ok=True)
        return
    shutil.copytree(src, site.ref_dir)


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
    _copy_ref_if_present(showcase_root, site)
    write_handover(showcase_root, site)
    return site


def build_clone_prompt(site: SiteWorkspace, showcase_root: Path, plugin_root: Path) -> str:
    return f"""You are the clone worker for onpixel showcase `{site.item.slug}`.

User is sleeping; run autonomously and stop only when this bounded pass is done.

Inputs:
- Handover: `{site.handover_path}`
- Original URL: {site.item.original_url}
- Existing showcase root for read-only context: `{showcase_root}`
- ui-clone-skills repo: `{plugin_root}`
- Active ref dir: `{site.ref_dir}`
- Target impl dir: `{site.impl_dir}`

Contract:
1. Implement or iterate the clone under `{site.impl_dir}` only.
2. Use the handover and ref artifacts first; recapture only when necessary.
   If `{site.ref_dir}` is empty, run the normal ui-clone-skills reference
   capture/extraction flow for `{site.item.original_url}` into that ref dir.
   If `{site.impl_dir}` has no app scaffold, create the smallest Next/React
   implementation scaffold needed for the verification commands to run.
3. Do not copy, rsync, cp -R, or port source files or public assets from
   `{showcase_root}` into `{site.impl_dir}`. The existing showcase tree is
   read-only orientation, not a source implementation. Download assets from the original URL
   or URLs recorded in `{site.ref_dir}` using the normal pipeline scripts.
   If you discover that you already copied from the showcase tree,
   write `{site.site_dir / "source-reuse.md"}` and mark the clone INCOMPLETE.
4. Do not edit `skills/`, `scripts/`, `ui_clone/`, `tests/`, hooks, plugin
   manifests, or any other plugin tooling in this clone pass.
5. Run the cheapest relevant verification after edits. Prefer `python -m
   ui_clone.goal {site.ref_dir}` for the next bounded action.
6. Do not report build, HTTP 200, source string presence, or copied local
   showcase behavior as completion. Completion needs strict inspection:
   `current_gate == "done"` plus runtime/media/transition proof artifacts.
   Dynamic primary renderers such as canvas/WebGL must have explicit runtime
   frame proof or canvas-replay closeout proof; static screenshots or
   approximated scroll effects are not completion.
7. If verification exposes a ui-clone-skills bug, do not patch it here. Write
   `{site.site_dir / "skill-issue.md"}` with reproduction command, expected
   behavior, actual behavior, and artifact paths.
8. Exit after one coherent pass. The outer runner will inspect and continue to
   the next site or launch a separate skill-fix pass.
"""


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
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=stdout_fh,
            stderr=stderr_fh,
            text=True,
            cwd=str(cwd),
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

            for attempt in range(1, max_attempts + 1):
                clone_prompt = build_clone_prompt(site, showcase_root, plugin_root)
                clone_prompt_path = _attempt_path(site, "codex-clone-prompt", ".md", attempt)
                clone_log_path = _attempt_path(site, "codex-clone", ".jsonl", attempt)
                clone_prompt_path.write_text(clone_prompt, encoding="utf-8")

                if args.dry_run:
                    clone_result = {"status": "dry-run", "prompt": str(clone_prompt_path)}
                else:
                    clone_result = invoke_codex(
                        clone_prompt,
                        codex_bin=args.codex_bin,
                        cwd=plugin_root,
                        log_path=clone_log_path,
                        output_last_message=_attempt_path(site, "codex-clone-last", ".md", attempt),
                        status_path=_attempt_path(site, "codex-clone-status", ".json", attempt),
                        add_dirs=[work_root, showcase_root],
                        model=args.model,
                        extra_args=args.codex_arg,
                        timeout_s=args.timeout_s,
                        poll_s=args.poll_s,
                    )

                inspection = inspect_site(site)
                reuse_findings = detect_showcase_reuse(site, showcase_root, log_paths=[clone_log_path])
                inspection = _apply_source_reuse_findings(site, inspection, reuse_findings)
                completion_status = _completion_status(inspection)
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
                site_record["cloneAttempts"].append(attempt_record)
                site_record["clone"] = clone_result
                site_record["inspection"] = inspection
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
                    if args.skill_fix_policy != "issue-only":
                        break
                elif args.dry_run and not args.skip_skill_fix and args.skill_fix_policy != "issue-only":
                    skill_prompt = build_skill_fix_prompt(site, plugin_root)
                    skill_prompt_path = _attempt_path(site, "codex-skill-fix-prompt", ".md", attempt)
                    skill_prompt_path.write_text(skill_prompt, encoding="utf-8")
                    site_record["skillFix"] = {"status": "dry-run", "prompt": str(skill_prompt_path)}
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
        "--clone-attempts",
        type=int,
        default=1,
        help="Maximum clone passes per site before moving to the next site",
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
