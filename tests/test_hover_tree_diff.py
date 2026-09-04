from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "hover-tree-diff.sh"


def test_hover_tree_diff_bounds_body_hover_reset_timeout(tmp_path: Path) -> None:
    """If agent-browser hangs while un-hovering via `hover body`, the
    per-element hover diff must time out that single command and still emit a
    report instead of blocking the whole verification loop."""
    bin_dir = tmp_path / "bin"
    out_dir = tmp_path / "out"
    bin_dir.mkdir()
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> '{tmp_path / 'calls.log'}'\n"
        "if [ \"$1\" = \"--session\" ]; then session=$2; shift 2; fi\n"
        "cmd=${1:-}; shift || true\n"
        "case \"$cmd\" in\n"
        "  open|set|wait|close) exit 0 ;;\n"
        "  mouse) exit 0 ;;\n"
        "  hover)\n"
        "    if [ \"${1:-}\" = \"body\" ]; then sleep 5; fi\n"
        "    exit 0 ;;\n"
        "  eval)\n"
        "    js=\"$*\"\n"
        "    if [[ \"$js\" == *\"htd-swiper-stabilize-v1\"* ]]; then\n"
        f"      echo \"SWIPER_STABILIZE $session\" >> '{tmp_path / 'calls.log'}'\n"
        "      echo '{\"marker\":\"htd-swiper-stabilize-v1\",\"ok\":true,\"count\":1,\"rows\":[{\"ok\":true}]}'\n"
        "    elif [[ \"$js\" == *\"htd-swiper-verify-v1\"* ]]; then\n"
        f"      echo \"SWIPER_VERIFY $session\" >> '{tmp_path / 'calls.log'}'\n"
        "      echo '{\"marker\":\"htd-swiper-verify-v1\",\"ok\":true,\"count\":1,\"orphaned\":0,\"rows\":[{\"ok\":true}]}'\n"
        "    elif [[ \"$js\" == *\"out.slice(0, maxN)\"* ]]; then\n"
        "      echo '[{\"tag\":\"A\",\"cls\":\"link\",\"txt\":\"Help\",\"x\":20,\"y\":20,\"w\":40,\"h\":20,\"area\":800,\"cursor\":\"pointer\",\"hasTrans\":false,\"idle\":{\"color\":\"rgb(0, 0, 0)\"},\"trans\":{}}]'\n"
        "    elif [[ \"$js\" == *\"const points =\"* ]]; then\n"
        "      echo '[{\"i\":0,\"tag\":\"path\",\"cls\":\"icon\",\"txt\":\"\",\"x\":20,\"y\":20,\"w\":40,\"h\":20,\"idle\":{\"color\":\"rgb(0, 0, 0)\"},\"trans\":{}}]'\n"
        "    elif [[ \"$js\" == *\"data-htd-target-\"* && \"$js\" == *\"Boolean\"* ]]; then\n"
        "      echo 'true'\n"
        "    elif [[ \"$js\" == *\"no-hittable-point\"* || \"$js\" == *\"stepX\"* ]]; then\n"
        "      echo '{\"found\":true,\"x\":20,\"y\":20}'\n"
        "    elif [[ \"$js\" == *\"getComputedStyle\"* ]]; then\n"
        "      echo '{\"color\":\"rgb(0, 0, 0)\"}'\n"
        "    elif [[ \"$js\" == *\"elementFromPoint\"* ]]; then\n"
        "      echo 'ok-A'\n"
        "    else\n"
        "      echo 'ok'\n"
        "    fi\n"
        "    exit 0 ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}:{env['PATH']}",
        "WAIT_MS": "0",
        "HOVER_WAIT": "0",
        "RESET_WAIT": "0",
        "SWIPER_SETTLE_MS": "0",
        "MAX_ELEMENTS": "1",
        # Leave enough budget for ordinary fake-agent process startup under CI
        # load while still proving that the five-second body hover is bounded.
        "HTD_AGENT_TIMEOUT": "0.5",
        "LC_ALL": "C",
        "LANG": "C",
    })
    proc = subprocess.run(
        ["bash", str(SCRIPT), "sess", "https://ref.test", "http://impl.test", str(out_dir)],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert (out_dir / "hover-tree-diff.json").is_file()
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "hover body" in calls
    call_lines = calls.splitlines()
    walk_index = next(
        index for index, line in enumerate(call_lines)
        if "out.slice(0, maxN)" in line
    )
    stabilize_lines = [
        (index, line) for index, line in enumerate(call_lines)
        if line.startswith("SWIPER_STABILIZE ")
    ]
    verify_lines = [
        (index, line) for index, line in enumerate(call_lines)
        if line.startswith("SWIPER_VERIFY ")
    ]
    assert len(stabilize_lines) == 2
    assert len(verify_lines) == 2
    assert {line.split()[1] for _, line in stabilize_lines} == {
        "sess-htd-ref",
        "sess-htd-impl",
    }
    assert {line.split()[1] for _, line in verify_lines} == {
        "sess-htd-ref",
        "sess-htd-impl",
    }
    assert all(index < walk_index for index, _ in stabilize_lines + verify_lines)
    report = (out_dir / "hover-tree-diff.md").read_text(encoding="utf-8")
    assert "FAIL hover-tree-diff" in report
    assert "UNPAIRED" in report
    raw = (out_dir / "hover-tree-diff.json").read_text(encoding="utf-8")
    assert "semantic pair mismatch" in raw


def test_hover_tree_diff_has_semantic_text_fallback() -> None:
    body = SCRIPT.read_text(encoding="utf-8")

    assert '"tag": e["tag"]' in body
    assert '"cls": e["cls"]' in body
    assert '"txt": e["txt"]' in body
    assert "match = 'semantic-text'" in body
    assert "compatibleClass(p.cls, direct.className)" in body
    assert "impl_classes.isdisjoint(ref_classes)" in body
    assert "strong_text_pair" in body
    assert "canonical_transition_value" in body
    assert "data-htd-target-" in body
    assert "no-hittable-point" in body
    assert "elementFromPoint(x, y)" in body
    assert "el.matches(':hover')" in body
    assert "hover activation unproven on " in body
    assert "swiper.autoplay.stop()" in body
    assert "swiper.slideToLoop(0, 0, false)" in body
    assert "swiper.slideTo(0, 0, false)" in body
    assert "swiper.setTransition(0)" in body
    assert body.index("htd-swiper-verify-v1") < body.index("out.slice(0, maxN)")


def test_hover_tree_diff_fails_closed_when_swiper_cannot_be_stabilized(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    out_dir = tmp_path / "out"
    bin_dir.mkdir()
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> '{tmp_path / 'calls.log'}'\n"
        "if [ \"$1\" = \"--session\" ]; then session=$2; shift 2; fi\n"
        "cmd=${1:-}; shift || true\n"
        "case \"$cmd\" in\n"
        "  open|set|wait|close) exit 0 ;;\n"
        "  eval)\n"
        "    js=\"$*\"\n"
        "    if [[ \"$js\" == *\"htd-swiper-stabilize-v1\"* ]]; then\n"
        "      if [[ \"$session\" == *\"-impl\" ]]; then\n"
        "        echo '{\"marker\":\"htd-swiper-stabilize-v1\",\"ok\":false,"
        "\"count\":1,\"rows\":[{\"ok\":false,\"error\":\"pin failed\"}]}'\n"
        "      else\n"
        "        echo '{\"marker\":\"htd-swiper-stabilize-v1\",\"ok\":true,"
        "\"count\":1,\"rows\":[{\"ok\":true}]}'\n"
        "      fi\n"
        "    elif [[ \"$js\" == *\"out.slice(0, maxN)\"* ]]; then\n"
        "      echo 'WALK-RAN'\n"
        "    fi\n"
        "    exit 0 ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}:{env['PATH']}",
        "WAIT_MS": "0",
        "SWIPER_SETTLE_MS": "0",
        "LC_ALL": "C",
        "LANG": "C",
    })

    proc = subprocess.run(
        ["bash", str(SCRIPT), "sess", "https://ref.test", "http://impl.test", str(out_dir)],
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )

    assert proc.returncode == 2
    assert "impl Swiper stabilization failed" in proc.stderr
    assert "WALK-RAN" not in (tmp_path / "calls.log").read_text(encoding="utf-8")


def test_hover_tree_diff_rejects_cross_role_child_pair_without_hiding_real_child_mismatch(
    tmp_path: Path,
) -> None:
    """Contained text must not pair different descendant roles, but a
    same-role descendant timing mismatch with real hover motion must remain
    blocking."""
    bin_dir = tmp_path / "bin"
    out_dir = tmp_path / "out"
    bin_dir.mkdir()
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$*\" >> '{tmp_path / 'calls.log'}'\n"
        "if [ \"$1\" = \"--session\" ]; then session=$2; shift 2; fi\n"
        "cmd=${1:-}; shift || true\n"
        "case \"$cmd\" in\n"
        "  open|set|wait|close|mouse|hover) exit 0 ;;\n"
        "  eval)\n"
        "    js=\"$*\"\n"
        "    if [[ \"$js\" == *\"htd-swiper-stabilize-v1\"* ]]; then\n"
        "      echo '{\"marker\":\"htd-swiper-stabilize-v1\",\"ok\":true,\"count\":1,\"rows\":[{\"ok\":true}]}'\n"
        "    elif [[ \"$js\" == *\"htd-swiper-verify-v1\"* ]]; then\n"
        "      echo '{\"marker\":\"htd-swiper-verify-v1\",\"ok\":true,\"count\":1,\"orphaned\":0,\"rows\":[{\"ok\":true}]}'\n"
        "    elif [[ \"$js\" == *\"out.slice(0, maxN)\"* ]]; then\n"
        "      echo '["
        "{\"tag\":\"A\",\"cls\":\"card-owner\",\"txt\":\"Card title\","
        "\"x\":20,\"y\":20,\"w\":40,\"h\":20,\"area\":800,"
        "\"cursor\":\"pointer\",\"hasTrans\":false,"
        "\"idle\":{\"opacity\":\"1\"},"
        "\"trans\":{\"transitionProperty\":\"none\",\"transitionDuration\":\"0s\","
        "\"transitionTimingFunction\":\"ease\",\"transitionDelay\":\"0s\"}},"
        "{\"tag\":\"DIV\",\"cls\":\"item-inner\",\"txt\":\"Section Details\","
        "\"x\":40,\"y\":40,\"w\":40,\"h\":20,\"area\":800,"
        "\"cursor\":\"auto\",\"hasTrans\":true,"
        "\"idle\":{\"opacity\":\"1\"},"
        "\"trans\":{\"transitionProperty\":\"transform\",\"transitionDuration\":\"0.2s\","
        "\"transitionTimingFunction\":\"ease-out\",\"transitionDelay\":\"0s\"}},"
        "{\"tag\":\"SPAN\",\"cls\":\"card-label\",\"txt\":\"Badge\","
        "\"x\":60,\"y\":60,\"w\":40,\"h\":20,\"area\":800,"
        "\"cursor\":\"auto\",\"hasTrans\":true,"
        "\"idle\":{\"opacity\":\"1\"},"
        "\"trans\":{\"transitionProperty\":\"opacity\",\"transitionDuration\":\"0.2s\","
        "\"transitionTimingFunction\":\"ease\",\"transitionDelay\":\"0s\"}}]'\n"
        "    elif [[ \"$js\" == *\"const points =\"* ]]; then\n"
        "      echo '["
        "{\"i\":0,\"tag\":\"A\",\"cls\":\"card-owner\",\"txt\":\"Card title\","
        "\"x\":20,\"y\":20,\"w\":40,\"h\":20,\"match\":\"coordinate\","
        "\"idle\":{\"opacity\":\"1\"},"
        "\"trans\":{\"transitionProperty\":\"none\",\"transitionDuration\":\"0s\","
        "\"transitionTimingFunction\":\"ease\",\"transitionDelay\":\"0s\"}},"
        "{\"i\":1,\"tag\":\"DIV\",\"cls\":\"item-subject\",\"txt\":\"Details\","
        "\"x\":40,\"y\":40,\"w\":40,\"h\":20,\"match\":\"coordinate\","
        "\"idle\":{\"opacity\":\"1\"},"
        "\"trans\":{\"transitionProperty\":\"all\",\"transitionDuration\":\"0s\","
        "\"transitionTimingFunction\":\"ease\",\"transitionDelay\":\"0s\"}},"
        "{\"i\":2,\"tag\":\"SPAN\",\"cls\":\"card-label active\",\"txt\":\"Badge\","
        "\"x\":60,\"y\":60,\"w\":40,\"h\":20,\"match\":\"semantic-text\","
        "\"idle\":{\"opacity\":\"1\"},"
        "\"trans\":{\"transitionProperty\":\"opacity\",\"transitionDuration\":\"0.1s\","
        "\"transitionTimingFunction\":\"ease\",\"transitionDelay\":\"0s\"}}]'\n"
        "    elif [[ \"$js\" == *\"data-htd-target-\"* && \"$js\" == *\"Boolean\"* ]]; then\n"
        "      echo 'true'\n"
        "    elif [[ \"$js\" == *\"stepX\"* ]]; then\n"
        "      echo '{\"found\":true,\"x\":20,\"y\":20}'\n"
        "    elif [[ \"$js\" == *\"getComputedStyle\"* ]]; then\n"
        "      if [[ \"$session\" == *\"-ref\" && \"$js\" == *\"const i = 0;\"* ]]; then\n"
        "        echo '{\"opacity\":\"1\",\"__hovered\":false}'\n"
        "      elif [[ \"$js\" == *\"const i = 2;\"* ]]; then\n"
        "        echo '{\"opacity\":\"0.5\",\"__hovered\":true}'\n"
        "      else\n"
        "        echo '{\"opacity\":\"1\",\"__hovered\":true}'\n"
        "      fi\n"
        "    else\n"
        "      echo 'ok'\n"
        "    fi\n"
        "    exit 0 ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}:{env['PATH']}",
        "WAIT_MS": "0",
        "HOVER_WAIT": "0",
        "RESET_WAIT": "0",
        "SWIPER_SETTLE_MS": "0",
        "MAX_ELEMENTS": "3",
        "LC_ALL": "C",
        "LANG": "C",
    })

    proc = subprocess.run(
        ["bash", str(SCRIPT), "sess", "https://ref.test", "http://impl.test", str(out_dir)],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    rows = {
        row["i"]: row
        for row in json.loads(
            (out_dir / "hover-tree-diff.json").read_text(encoding="utf-8")
        )
    }
    assert rows[0]["sev"] == "unpaired"
    assert any("hover activation unproven on ref" in issue for issue in rows[0]["issues"])
    assert rows[1]["sev"] == "unpaired"
    assert any("semantic pair mismatch" in issue for issue in rows[1]["issues"])
    assert rows[2]["sev"] == "critical"
    assert any(diff[0] == "transitionDuration" for diff in rows[2]["timing_diffs"])
    assert rows[2]["observed_hover_deltas"]


def test_hover_tree_diff_timing_only_metadata_without_hover_delta_is_advisory(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    out_dir = tmp_path / "out"
    bin_dir.mkdir()
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$*\" >> '{tmp_path / 'calls.log'}'\n"
        "if [ \"$1\" = \"--session\" ]; then session=$2; shift 2; fi\n"
        "cmd=${1:-}; shift || true\n"
        "case \"$cmd\" in\n"
        "  open|set|wait|close|mouse|hover) exit 0 ;;\n"
        "  eval)\n"
        "    js=\"$*\"\n"
        "    if [[ \"$js\" == *\"htd-swiper-stabilize-v1\"* ]]; then\n"
        "      echo '{\"marker\":\"htd-swiper-stabilize-v1\",\"ok\":true,\"count\":1,\"rows\":[{\"ok\":true}]}'\n"
        "    elif [[ \"$js\" == *\"htd-swiper-verify-v1\"* ]]; then\n"
        "      echo '{\"marker\":\"htd-swiper-verify-v1\",\"ok\":true,\"count\":1,\"orphaned\":0,\"rows\":[{\"ok\":true}]}'\n"
        "    elif [[ \"$js\" == *\"out.slice(0, maxN)\"* ]]; then\n"
        "      echo '[{\"tag\":\"DIV\",\"cls\":\"hero_intro\",\"txt\":\"Intro\","
        "\"x\":20,\"y\":20,\"w\":40,\"h\":20,\"area\":800,"
        "\"cursor\":\"auto\",\"hasTrans\":true,"
        "\"idle\":{\"opacity\":\"1\",\"transform\":\"none\"},"
        "\"trans\":{\"transitionProperty\":\"opacity, transform\","
        "\"transitionDuration\":\"0.8s, 0.8s\","
        "\"transitionTimingFunction\":\"cubic-bezier(0.25, 1, 0.5, 1)\","
        "\"transitionDelay\":\"0s\"}}]'\n"
        "    elif [[ \"$js\" == *\"const points =\"* ]]; then\n"
        "      echo '[{\"i\":0,\"tag\":\"DIV\",\"cls\":\"hero_intro\",\"txt\":\"Intro\","
        "\"x\":20,\"y\":20,\"w\":40,\"h\":20,\"match\":\"semantic-text\","
        "\"idle\":{\"opacity\":\"1\",\"transform\":\"none\"},"
        "\"trans\":{\"transitionProperty\":\"all\",\"transitionDuration\":\"0s\","
        "\"transitionTimingFunction\":\"ease\",\"transitionDelay\":\"0s\"}}]'\n"
        "    elif [[ \"$js\" == *\"data-htd-target-\"* && \"$js\" == *\"Boolean\"* ]]; then\n"
        "      echo 'true'\n"
        "    elif [[ \"$js\" == *\"stepX\"* ]]; then\n"
        "      echo '{\"found\":true,\"x\":20,\"y\":20}'\n"
        "    elif [[ \"$js\" == *\"getComputedStyle\"* ]]; then\n"
        "      echo '{\"opacity\":\"1\",\"transform\":\"none\",\"__hovered\":true}'\n"
        "    else\n"
        "      echo 'ok'\n"
        "    fi\n"
        "    exit 0 ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}:{env['PATH']}",
        "WAIT_MS": "0",
        "HOVER_WAIT": "0",
        "RESET_WAIT": "0",
        "SWIPER_SETTLE_MS": "0",
        "MAX_ELEMENTS": "1",
        "LC_ALL": "C",
        "LANG": "C",
    })

    proc = subprocess.run(
        ["bash", str(SCRIPT), "sess", "https://ref.test", "http://impl.test", str(out_dir)],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    rows = json.loads((out_dir / "hover-tree-diff.json").read_text(encoding="utf-8"))
    assert rows[0]["sev"] == "minor"
    assert rows[0]["timing_diffs"]
    assert rows[0]["delta_diffs"] == []
    assert rows[0]["observed_hover_deltas"] == []
    assert "without observed hover delta" in rows[0]["issues"][0]
    report = (out_dir / "hover-tree-diff.md").read_text(encoding="utf-8")
    assert "PASS hover-tree-diff" in report
    assert "WARN" in report


def test_hover_tree_diff_waits_through_declared_delay_before_downgrading(
    tmp_path: Path,
) -> None:
    """A ref effect delayed past the base sample must not look idle and let a
    missing impl hover rule pass as a metadata-only warning."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    assert "time.sleep(observation_wait)" in SCRIPT.read_text(encoding="utf-8")
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = \"--session\" ]; then session=$2; shift 2; fi\n"
        "cmd=${1:-}; shift || true\n"
        "case \"$cmd\" in\n"
        "  open|set|wait|close|hover) exit 0 ;;\n"
        "  mouse) exit 0 ;;\n"
        "  eval)\n"
        "    js=\"$*\"\n"
        "    if [[ \"$js\" == *\"htd-swiper-stabilize-v1\"* ]]; then\n"
        "      echo '{\"marker\":\"htd-swiper-stabilize-v1\",\"ok\":true,"
        "\"count\":1,\"rows\":[{\"ok\":true}]}'\n"
        "    elif [[ \"$js\" == *\"htd-swiper-verify-v1\"* ]]; then\n"
        "      echo '{\"marker\":\"htd-swiper-verify-v1\",\"ok\":true,"
        "\"count\":1,\"orphaned\":0,\"rows\":[{\"ok\":true}]}'\n"
        "    elif [[ \"$js\" == *\"out.slice(0, maxN)\"* ]]; then\n"
        "      property='all'; duration='0s'; delay='0s'\n"
        "      if [ \"${HTD_EARLY_DELTA:-0}\" = '1' ]; then\n"
        "        property='opacity'; duration='0.3s'; delay='0.8s'\n"
        "      fi\n"
        "      echo '[{\"tag\":\"DIV\",\"cls\":\"delayed-card\","
        "\"txt\":\"Details\",\"x\":20,\"y\":20,\"w\":40,\"h\":20,"
        "\"area\":800,\"cursor\":\"pointer\",\"hasTrans\":false,"
        "\"idle\":{\"opacity\":\"1\"},"
        "\"trans\":{\"transitionProperty\":\"'\"$property\"'\","
        "\"transitionDuration\":\"'\"$duration\"'\","
        "\"transitionTimingFunction\":\"ease\","
        "\"transitionDelay\":\"'\"$delay\"'\"}}]'\n"
        "    elif [[ \"$js\" == *\"const points =\"* ]]; then\n"
        "      echo '[{\"i\":0,\"tag\":\"DIV\",\"cls\":\"delayed-card\","
        "\"txt\":\"Details\",\"x\":20,\"y\":20,\"w\":40,\"h\":20,"
        "\"match\":\"semantic-text\",\"idle\":{\"opacity\":\"1\"},"
        "\"trans\":{\"transitionProperty\":\"opacity\","
        "\"transitionDuration\":\"0.3s\","
        "\"transitionTimingFunction\":\"ease\","
        "\"transitionDelay\":\"0.8s\"}}]'\n"
        "    elif [[ \"$js\" == *\"data-htd-target-\"* && \"$js\" == *\"Boolean\"* ]]; then\n"
        "      echo 'true'\n"
        "    elif [[ \"$js\" == *\"stepX\"* ]]; then\n"
        "      echo '{\"found\":true,\"x\":20,\"y\":20}'\n"
        "    elif [[ \"$js\" == *\"getComputedStyle\"* ]]; then\n"
        "      if [ \"${HTD_EARLY_DELTA:-0}\" = '1' ]; then\n"
        "        echo '{\"opacity\":\"0.8\",\"__hovered\":true}'\n"
        "      elif [[ \"$session\" == *\"-ref\" ]] "
        "&& [ \"${HOVER_MAX_WAIT:-0}\" -ge 1000 ]; then\n"
        "        echo '{\"opacity\":\"0.5\",\"__hovered\":true}'\n"
        "      else\n"
        "        echo '{\"opacity\":\"1\",\"__hovered\":true}'\n"
        "      fi\n"
        "    else\n"
        "      echo 'ok'\n"
        "    fi\n"
        "    exit 0 ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}:{env['PATH']}",
        "WAIT_MS": "0",
        "HOVER_WAIT": "600",
        "RESET_WAIT": "0",
        "SWIPER_SETTLE_MS": "0",
        "MAX_ELEMENTS": "1",
        "LC_ALL": "C",
        "LANG": "C",
    })

    cases = (
        ("settled", "3000", "0"),
        ("capped", "700", "0"),
        ("capped-early-delta", "700", "1"),
    )
    for case, max_wait, early_delta in cases:
        out_dir = tmp_path / case
        env["HOVER_MAX_WAIT"] = max_wait
        env["HTD_EARLY_DELTA"] = early_delta
        proc = subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "sess",
                "https://ref.test",
                "http://impl.test",
                str(out_dir),
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )

        assert proc.returncode == 1, proc.stdout + proc.stderr
        rows = json.loads(
            (out_dir / "hover-tree-diff.json").read_text(encoding="utf-8")
        )
        assert rows[0]["sev"] == "critical"
        if case == "settled":
            assert any(
                diff[3] == "missing-hover-effect"
                for diff in rows[0]["delta_diffs"]
            )
            assert rows[0]["observations"]["ref"]["settled"] is True
            assert rows[0]["observations"]["ref"]["wait_ms"] >= 1100
        elif case == "capped":
            assert rows[0]["observed_hover_deltas"] == []
            assert rows[0]["observations"]["ref"]["settled"] is False
            assert any("observation capped" in issue for issue in rows[0]["issues"])
        else:
            assert rows[0]["timing_diffs"] == []
            assert rows[0]["delta_diffs"] == []
            assert rows[0]["observed_hover_deltas"] == [
                ["impl", "opacity"],
                ["ref", "opacity"],
            ]
            assert all(
                observation["settled"] is False
                for observation in rows[0]["observations"].values()
            )
            assert any("observation capped" in issue for issue in rows[0]["issues"])
