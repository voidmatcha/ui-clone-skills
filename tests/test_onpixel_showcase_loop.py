from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from ui_clone import onpixel_showcase_loop as loop

SHOWCASE_TSX = """
const showcases: ShowcaseItem[] = [
  { slug: 'alpha', title: 'Alpha', description: 'First clone', originalUrl: 'https://alpha.example', isNew: true },
  { slug: 'beta', title: 'Beta', description: 'Second clone', originalUrl: 'https://beta.example', disabled: true },
]
"""


def _write_showcase(root: Path) -> None:
    app = root / "src" / "app"
    app.mkdir(parents=True)
    (app / "ShowcaseClient.tsx").write_text(SHOWCASE_TSX, encoding="utf-8")
    for slug in ("alpha", "beta"):
        (app / slug).mkdir()
        (app / slug / "page.tsx").write_text("export default function Page() { return null }\n")
        (app / slug / "layout.tsx").write_text("export default function Layout() { return null }\n")
        (root / "src" / "projects" / slug).mkdir(parents=True)
        (root / "src" / "projects" / slug / "Site.tsx").write_text(
            "export default function Site() { return null }\n",
            encoding="utf-8",
        )


def test_parse_showcase_items_keeps_disabled_flag() -> None:
    items = loop.parse_showcase_items(SHOWCASE_TSX)

    assert [item.slug for item in items] == ["alpha", "beta"]
    assert items[0].original_url == "https://alpha.example"
    assert items[1].disabled is True


def test_discover_showcase_items_skips_disabled_by_default(tmp_path: Path) -> None:
    _write_showcase(tmp_path)

    items = loop.discover_showcase_items(tmp_path, include_disabled=False)

    assert [item.slug for item in items] == ["alpha"]


def test_prepare_site_workspace_copies_ref_and_writes_handover(tmp_path: Path) -> None:
    showcase = tmp_path / "showcase"
    work = tmp_path / "work"
    _write_showcase(showcase)
    ref_src = showcase / "tmp" / "ref" / "alpha"
    ref_src.mkdir(parents=True)
    (ref_src / "transition-spec.json").write_text('{"transitions":[]}\n')

    item = loop.discover_showcase_items(showcase)[0]
    site = loop.prepare_site_workspace(showcase, work, item, reset=False)

    assert (site.ref_dir / "transition-spec.json").is_file()
    assert site.impl_dir.is_dir()
    assert (site.impl_dir / "AGENTS.md").is_file()
    handover = site.handover_path.read_text(encoding="utf-8")
    assert "https://alpha.example" in handover
    assert "src/projects/alpha" in handover
    assert "tmp/ref/alpha" in handover


def test_build_codex_command_is_unattended_and_writable(tmp_path: Path) -> None:
    command = loop.build_codex_command(
        codex_bin="codex",
        cwd=tmp_path / "repo",
        output_last_message=tmp_path / "last.md",
        add_dirs=[tmp_path / "work", tmp_path / "showcase"],
        model="gpt-5",
        extra_args=["--profile", "nightly"],
    )

    assert command[:2] == ["codex", "exec"]
    assert "--json" in command
    assert "--skip-git-repo-check" in command
    assert "--dangerously-bypass-approvals-and-sandbox" in command
    assert "--dangerously-bypass-hook-trust" in command
    assert command[-1] == "-"
    assert str(tmp_path / "work") in command
    assert str(tmp_path / "showcase") in command
    assert "gpt-5" in command


def test_run_loop_dry_run_writes_prompts_without_invoking_codex(tmp_path: Path) -> None:
    showcase = tmp_path / "showcase"
    work = tmp_path / "work"
    _write_showcase(showcase)

    args = loop.build_parser().parse_args([
        "--showcase-root", str(showcase),
        "--work-root", str(work),
        "--slugs", "alpha",
        "--dry-run",
    ])
    with mock.patch.object(loop, "invoke_codex") as invoke:
        outcome = loop.run_loop(args)

    assert outcome == "DRY_RUN"
    invoke.assert_not_called()
    assert (work / "alpha" / "codex-clone-prompt.md").is_file()
    summary = json.loads((work / "onpixel-codex-loop-summary.json").read_text())
    assert summary["sites"][0]["slug"] == "alpha"
    assert summary["sites"][0]["clone"]["status"] == "dry-run"
    ledger = (work / "alpha" / "clone-experiments.tsv").read_text(encoding="utf-8").splitlines()
    assert ledger[0].split("\t") == [
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
    row = dict(zip(ledger[0].split("\t"), ledger[1].split("\t"), strict=True))
    assert row["attempt"] == "1"
    assert row["status"] == "wip"
    assert row["description"] == "dry-run"
    assert summary["sites"][0]["cloneAttempts"][0]["score"]["completionStatus"] == "wip"


def test_build_clone_prompt_is_natural_user_request_without_internal_handover(tmp_path: Path) -> None:
    showcase = tmp_path / "showcase"
    work = tmp_path / "work"
    _write_showcase(showcase)

    item = loop.discover_showcase_items(showcase)[0]
    site = loop.prepare_site_workspace(showcase, work, item, reset=False)

    prompt = loop.build_clone_prompt(site, showcase, tmp_path / "plugin")

    assert prompt == (
        "Copy https://alpha.example as closely as possible, including transitions. "
        "Make it runnable locally."
    )
    lower = prompt.lower()
    for forbidden in (
        "handover",
        "ref dir",
        "active ref",
        "plugin",
        "gate",
        "showcase",
        "tmp/ref",
        "skill-issue",
        "ui-clone",
        "clone-experiments",
        "clone-research",
        "score",
    ):
        assert forbidden not in lower


def test_impl_workspace_agents_carries_research_and_scoring_contract_without_prompt_leak(tmp_path: Path) -> None:
    showcase = tmp_path / "showcase"
    work = tmp_path / "work"
    _write_showcase(showcase)

    item = loop.discover_showcase_items(showcase)[0]
    site = loop.prepare_site_workspace(showcase, work, item, reset=False)

    agents = (site.impl_dir / "AGENTS.md").read_text(encoding="utf-8")

    assert "Do not copy, rsync, cp -R, or port source files" in agents
    assert "completion-report.sh" in agents
    assert "python -m ui_clone.goal" in agents
    assert "INCOMPLETE" in agents
    assert "Do not run `npx playwright node`" in agents
    assert "Use `node` for inline Playwright scripts" in agents
    assert "Do not inspect, patch, or debug ui-clone-skills gate internals" in agents
    assert "## Layout Stability" in agents
    assert "scrollWidth" in agents
    assert "offscreen rails" in agents
    assert "clone-research.md" in agents
    assert "clone-experiments.tsv" in agents
    assert "handover" not in agents.lower()


def test_prepare_site_workspace_writes_clone_research_from_ref_artifacts(tmp_path: Path) -> None:
    showcase = tmp_path / "showcase"
    work = tmp_path / "work"
    _write_showcase(showcase)
    ref_src = showcase / "tmp" / "ref" / "alpha"
    ref_src.mkdir(parents=True)
    (ref_src / "section-map.json").write_text(json.dumps({
        "sections": [
            {"index": 0, "id": "hero", "y": 0, "height": 700},
            {"index": 1, "id": "gallery", "y": 700, "height": 700},
        ]
    }))
    (ref_src / "component-map.json").write_text(json.dumps({
        "sections": [
            {"index": 0, "componentName": "HeroSection"},
            {"index": 1, "file": "src/components/Gallery.tsx"},
        ]
    }))
    (ref_src / "visible-images.json").write_text(json.dumps({
        "images": [
            {"src": "https://cdn.example.com/hero.png", "rect": {"y": 120}},
            {"src": "https://cdn.example.com/gallery.png", "rect": {"y": 820}},
        ]
    }))
    (ref_src / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"selector": ".gallery", "trigger": "scroll", "property": "transform"},
        ]
    }))
    (ref_src / "runtime-spec.json").write_text(json.dumps({
        "signals": [{"selector": ".gallery", "event": "hover"}]
    }))
    (ref_src / "asset-placement.json").write_text(json.dumps({
        "missingPlacements": [{"src": "https://cdn.example.com/gallery.png", "sectionIndex": 1}]
    }))

    item = loop.discover_showcase_items(showcase)[0]
    site = loop.prepare_site_workspace(showcase, work, item, reset=False)

    research = (site.site_dir / "clone-research.md").read_text(encoding="utf-8")
    assert "Original URL: https://alpha.example" in research
    assert "HeroSection" in research
    assert "src/components/Gallery.tsx" in research
    assert "https://cdn.example.com/gallery.png" in research
    assert "scroll" in research
    assert "hover" in research
    assert "Current missing assets" in research


def test_detect_showcase_reuse_flags_copy_commands(tmp_path: Path) -> None:
    showcase = tmp_path / "showcase"
    work = tmp_path / "work"
    _write_showcase(showcase)

    item = loop.discover_showcase_items(showcase)[0]
    site = loop.prepare_site_workspace(showcase, work, item, reset=False)
    (site.site_dir / "codex-clone.jsonl").write_text(
        json.dumps({
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": (
                    f"rsync -a {showcase}/src/projects/alpha "
                    f"{site.impl_dir}/src/projects/"
                ),
            },
        })
        + "\n",
        encoding="utf-8",
    )

    findings = loop.detect_showcase_reuse(site, showcase)

    assert findings
    assert "src/projects/alpha" in findings[0]


def test_detect_clone_scope_leak_ignores_normal_closeout(tmp_path: Path) -> None:
    log = tmp_path / "codex-clone.jsonl"
    log.write_text(
        json.dumps({
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": (
                    "uv run --project /repo python -m ui_clone.goal "
                    "/tmp/ref --check-done"
                ),
            },
        })
        + "\n",
        encoding="utf-8",
    )
    last = tmp_path / "codex-clone-last.md"
    last.write_text(
        "INCOMPLETE: ui_clone.goal --check-done failed: "
        "not satisfied: current_gate is 'reference', not 'done'\n",
        encoding="utf-8",
    )

    assert loop.detect_clone_scope_leak(log_paths=[log], text_paths=[last]) == []


def test_detect_clone_scope_leak_flags_gate_source_investigation(tmp_path: Path) -> None:
    log = tmp_path / "codex-clone.jsonl"
    log.write_text(
        "\n".join([
            json.dumps({
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "sed -n '1,120p' /repo/ui_clone/goal.py",
                },
            }),
            json.dumps({
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "rg current_gate /repo/ui_clone",
                },
            }),
        ])
        + "\n",
        encoding="utf-8",
    )

    findings = loop.detect_clone_scope_leak(log_paths=[log])

    assert findings
    assert any("ui_clone/goal.py" in finding for finding in findings)
    assert any("current_gate" in finding for finding in findings)


def test_run_loop_marks_showcase_reuse_attempt_contaminated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    showcase = tmp_path / "showcase"
    work = tmp_path / "work"
    _write_showcase(showcase)

    def fake_invoke(*args: object, **kwargs: Any) -> dict[str, object]:
        log_path = kwargs["log_path"]
        log_path.write_text(
            json.dumps({
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": (
                        f"cp -R {showcase}/src/projects/alpha "
                        f"{work}/alpha/impl/src/projects/"
                    ),
                },
            })
            + "\n",
            encoding="utf-8",
        )
        return {"status": "ok", "exit_code": 0, "elapsed_s": 0}

    monkeypatch.setattr(loop, "invoke_codex", fake_invoke)
    monkeypatch.setattr(loop, "inspect_site", lambda site: {"done": True, "unmet": [], "goal": {}})

    args = loop.build_parser().parse_args([
        "--showcase-root", str(showcase),
        "--work-root", str(work),
        "--slugs", "alpha",
        "--clone-attempts", "1",
        "--skill-fix-policy", "issue-only",
    ])

    loop.run_loop(args)

    summary = json.loads((work / "onpixel-codex-loop-summary.json").read_text())
    site = summary["sites"][0]
    assert site["done"] is False
    assert site["completionStatus"] == "contaminated"
    assert site["previewEligible"] is False
    assert "showcase source reuse detected" in site["inspection"]["unmet"][0]
    assert (work / "alpha" / "source-reuse.md").is_file()


def test_run_loop_stops_natural_retries_after_repeated_scope_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    showcase = tmp_path / "showcase"
    work = tmp_path / "work"
    _write_showcase(showcase)

    invoke_calls: list[Path] = []

    def fake_invoke(*args: object, **kwargs: Any) -> dict[str, object]:
        log_path = kwargs["log_path"]
        invoke_calls.append(log_path)
        if log_path.name.startswith("codex-clone"):
            log_path.write_text(
                json.dumps({
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": "sed -n '1,120p' /repo/ui_clone/goal.py",
                    },
                })
                + "\n",
                encoding="utf-8",
            )
        return {"status": "ok", "exit_code": 0, "elapsed_s": 0}

    monkeypatch.setattr(loop, "invoke_codex", fake_invoke)
    monkeypatch.setattr(
        loop,
        "inspect_site",
        lambda site: {"done": False, "unmet": ["section-compare failed"], "goal": {}},
    )

    args = loop.build_parser().parse_args([
        "--showcase-root", str(showcase),
        "--work-root", str(work),
        "--slugs", "alpha",
        "--clone-attempts", "3",
        "--skill-fix-policy", "issue-only",
    ])

    loop.run_loop(args)

    assert [path.name for path in invoke_calls] == [
        "codex-clone.jsonl",
        "codex-clone-2.jsonl",
        "codex-skill-fix-2.jsonl",
    ]
    assert (work / "alpha" / "skill-issue-attempt-2.md").is_file()
    summary = json.loads((work / "onpixel-codex-loop-summary.json").read_text())
    site = summary["sites"][0]
    assert site["stopReason"] == "repeated-scope-leak"
    assert len(site["cloneAttempts"]) == 2
    assert site["cloneAttempts"][0]["inspection"]["scopeLeak"]["count"] == 1
    assert site["cloneAttempts"][1]["inspection"]["scopeLeak"]["action"] == "meta-fix"


def test_invoke_codex_polls_status_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStdin:
        def __init__(self) -> None:
            self.text = ""

        def write(self, text: str) -> int:
            self.text += text
            return len(text)

        def close(self) -> None:
            return None

    class FakeProc:
        def __init__(self) -> None:
            self.stdin = FakeStdin()
            self.returncode: int | None = None
            self.poll_count = 0

        def poll(self) -> int | None:
            self.poll_count += 1
            if self.poll_count >= 2:
                self.returncode = 0
                return 0
            return None

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

        def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
            self.returncode = 0 if self.returncode is None else self.returncode
            return self.returncode

    fake = FakeProc()

    def fake_popen(*args: object, **kwargs: object) -> FakeProc:  # noqa: ARG001
        return fake

    monkeypatch.setattr(loop.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(loop.time, "sleep", lambda _: None)

    result = loop.invoke_codex(
        "hello codex",
        codex_bin="codex",
        cwd=tmp_path,
        log_path=tmp_path / "codex.jsonl",
        output_last_message=tmp_path / "last.md",
        status_path=tmp_path / "status.json",
        add_dirs=[tmp_path],
        model=None,
        extra_args=[],
        timeout_s=60,
        poll_s=1,
    )

    assert result["status"] == "ok"
    assert fake.stdin.text == "hello codex"
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["status"] == "ok"
    assert status["pollCount"] >= 1


def test_run_loop_retries_clone_until_done_without_skill_fix_for_incomplete_clone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    showcase = tmp_path / "showcase"
    work = tmp_path / "work"
    _write_showcase(showcase)

    invoke_calls: list[Path] = []
    invoke_cwds: list[Path] = []
    invoke_add_dirs: list[list[Path]] = []

    def fake_invoke(*args: object, **kwargs: Any) -> dict[str, object]:
        invoke_calls.append(kwargs["log_path"])
        invoke_cwds.append(kwargs["cwd"])
        invoke_add_dirs.append(list(kwargs["add_dirs"]))
        return {"status": "ok", "exit_code": 0, "elapsed_s": 0}

    inspections = iter([
        {"done": False, "unmet": ["post-implement missing"], "goal": {}},
        {"done": True, "unmet": [], "goal": {}},
    ])

    monkeypatch.setattr(loop, "invoke_codex", fake_invoke)
    monkeypatch.setattr(loop, "inspect_site", lambda site: next(inspections))

    args = loop.build_parser().parse_args([
        "--showcase-root", str(showcase),
        "--work-root", str(work),
        "--slugs", "alpha",
        "--clone-attempts", "3",
        "--skill-fix-policy", "issue-only",
    ])

    outcome = loop.run_loop(args)

    assert outcome == "RAN"
    assert [path.name for path in invoke_calls] == [
        "codex-clone.jsonl",
        "codex-clone-2.jsonl",
    ]
    assert invoke_cwds == [work / "alpha" / "impl", work / "alpha" / "impl"]
    assert all(showcase not in add_dirs for add_dirs in invoke_add_dirs)
    summary = json.loads((work / "onpixel-codex-loop-summary.json").read_text())
    site = summary["sites"][0]
    assert site["done"] is True
    assert len(site["cloneAttempts"]) == 2
    assert site["skillFix"]["status"] == "skipped"


def test_issue_only_skill_fix_runs_only_when_issue_file_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    showcase = tmp_path / "showcase"
    work = tmp_path / "work"
    _write_showcase(showcase)

    invoke_calls: list[Path] = []

    def fake_invoke(*args: object, **kwargs: Any) -> dict[str, object]:
        log_path = kwargs["log_path"]
        invoke_calls.append(log_path)
        if log_path.name.startswith("codex-clone"):
            (work / "alpha" / "skill-issue.md").write_text("# issue\n", encoding="utf-8")
        return {"status": "ok", "exit_code": 0, "elapsed_s": 0}

    monkeypatch.setattr(loop, "invoke_codex", fake_invoke)
    monkeypatch.setattr(
        loop,
        "inspect_site",
        lambda site: {"done": False, "unmet": ["tool broke"], "goal": {}},
    )

    args = loop.build_parser().parse_args([
        "--showcase-root", str(showcase),
        "--work-root", str(work),
        "--slugs", "alpha",
        "--clone-attempts", "1",
        "--skill-fix-policy", "issue-only",
    ])

    loop.run_loop(args)

    assert [path.name for path in invoke_calls] == [
        "codex-clone.jsonl",
        "codex-skill-fix.jsonl",
    ]
    summary = json.loads((work / "onpixel-codex-loop-summary.json").read_text())
    assert summary["sites"][0]["skillFix"]["status"] == "ok"
