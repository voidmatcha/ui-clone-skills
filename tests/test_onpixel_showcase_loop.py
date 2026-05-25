from __future__ import annotations

import json
from pathlib import Path
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
