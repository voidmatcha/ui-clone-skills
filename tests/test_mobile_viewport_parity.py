from __future__ import annotations

import json
import os
import re
import subprocess
import textwrap
from pathlib import Path
from typing import Any, cast

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "visual-debug"
    / "scripts"
    / "mobile-viewport-parity-check.sh"
)


def _probe(
    landmark_counts: dict[str, int],
    *,
    mobile_nav_count: int = 0,
    title: str = "Reference title",
) -> dict[str, object]:
    return {
        "ok": True,
        "viewport": [375, 812],
        "scrollWidth": 375,
        "scrollHeight": 2400,
        "overflowPx": 0,
        "bodyChildren": 1,
        "mobileNavCount": mobile_nav_count,
        "sectionCount": sum(landmark_counts.values()),
        "landmarkCategories": [
            category for category, count in landmark_counts.items() if count > 0
        ],
        "landmarkCounts": landmark_counts,
        "textLen": 200,
        "title": title,
    }


def _run_gate(
    tmp_path: Path,
    *,
    ref_counts: dict[str, int],
    impl_counts: dict[str, int],
    ref_mobile_nav_count: int = 0,
    impl_mobile_nav_count: int = 0,
    ref_title: str = "Reference title",
    impl_title: str = "Reference title",
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "agent-browser"
    fake.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json
            import sys

            args = sys.argv[1:]
            session = args[args.index("--session") + 1]
            if "eval" in args:
                script = args[-1]
                if "window.innerWidth" in script and "landmarkSelector" not in script:
                    print("375")
                elif "-mvp-ref" in session:
                    print(json.dumps(json.dumps({_probe(ref_counts, mobile_nav_count=ref_mobile_nav_count, title=ref_title)!r})))
                else:
                    print(json.dumps(json.dumps({_probe(impl_counts, mobile_nav_count=impl_mobile_nav_count, title=impl_title)!r})))
            sys.exit(0)
            """
        ),
        encoding="utf-8",
    )
    fake.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sleep.chmod(0o755)

    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    proc = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "landmark-test",
            "https://ref.example/",
            "https://impl.example/",
            str(ref_dir),
        ],
        check=False,
        capture_output=True,
        env=env,
        text=True,
        timeout=10,
    )
    artifact = cast(
        dict[str, Any],
        json.loads(
            (ref_dir / "mobile-viewport-parity.json").read_text(encoding="utf-8")
        ),
    )
    return proc, artifact


def test_docs_like_nested_landmarks_use_stable_per_category_counts(
    tmp_path: Path,
) -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "document.querySelectorAll(landmarkSelector)" in source
    assert "const visibleLandmarks = new Set(" in source
    assert "categoryByElement.get(ancestor) === category" in source
    assert "const sectionCount = stableLandmarks.length" in source

    proc, artifact = _run_gate(
        tmp_path,
        ref_counts={
            "banner": 1,
            "navigation": 2,
            "main": 1,
            "contentinfo": 1,
            "region": 2,
        },
        impl_counts={
            "banner": 1,
            "navigation": 2,
            "main": 1,
            "contentinfo": 1,
            "region": 2,
        },
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert artifact["status"] == "pass"


def test_major_core_landmark_loss_fails(
    tmp_path: Path,
) -> None:
    proc, artifact = _run_gate(
        tmp_path,
        ref_counts={
            "banner": 1,
            "navigation": 2,
            "main": 1,
            "contentinfo": 1,
            "region": 2,
        },
        impl_counts={
            "banner": 0,
            "navigation": 0,
            "main": 1,
            "contentinfo": 0,
            "region": 2,
        },
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert artifact["status"] == "fail"
    assert any(
        "stable major landmarks" in reason for reason in artifact["reasons"]
    )
    assert any(
        "core banner landmark" in reason for reason in artifact["reasons"]
    )


def test_region_tag_count_difference_alone_does_not_fail(tmp_path: Path) -> None:
    proc, artifact = _run_gate(
        tmp_path,
        ref_counts={"banner": 1, "main": 1, "contentinfo": 1, "region": 20},
        impl_counts={"banner": 1, "main": 1, "contentinfo": 1, "region": 1},
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert artifact["status"] == "pass"


def test_single_core_landmark_loss_fails(tmp_path: Path) -> None:
    proc, artifact = _run_gate(
        tmp_path,
        ref_counts={"banner": 1, "main": 1, "contentinfo": 1, "region": 2},
        impl_counts={"banner": 1, "main": 0, "contentinfo": 1, "region": 2},
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert any(
        "core main landmark" in reason for reason in artifact["reasons"]
    )


def test_mobile_nav_probe_counts_unique_visible_menu_intent_controls() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'data-testid*=\\"mobile-menu\\" i' in source
    assert "for (let node = el; node instanceof Element;" in source
    assert "const mobileNavControls = new Set(" in source
    assert ".filter((el) => isVisible(el))" in source
    assert "const mobileNavCount = mobileNavControls.size" in source
    assert '"button[aria-expanded]"' not in source


def test_mobile_nav_probe_counts_descendant_control_but_not_wrapper(
    tmp_path: Path,
) -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"PROBE_JS='\n(.*?)\n'\n\nprobe\(\)", source, re.DOTALL)
    assert match is not None
    probe_js = match.group(1)
    harness = tmp_path / "probe.js"
    harness.write_text(
        textwrap.dedent(
            f"""\
            let wrapperHasButton = true;
            class Element {{
              constructor(kind, parent = null) {{
                this.kind = kind;
                this.parentElement = parent;
              }}
              matches(selector) {{
                return this.kind === "button" && selector.includes("button");
              }}
              querySelectorAll(selector) {{
                if (
                  wrapperHasButton &&
                  this.kind === "wrapper" &&
                  selector.includes("button")
                ) {{
                  return [button];
                }}
                return [];
              }}
              getBoundingClientRect() {{
                return {{ width: 32, height: 32 }};
              }}
            }}
            const wrapper = new Element("wrapper");
            const button = new Element("button", wrapper);
            global.Element = Element;
            global.getComputedStyle = () => ({{
              display: "block",
              visibility: "visible",
              opacity: "1",
            }});
            global.window = {{ innerWidth: 375, innerHeight: 812 }};
            global.document = {{
              body: {{
                scrollWidth: 375,
                scrollHeight: 1200,
                childElementCount: 1,
                innerText: "rendered mobile page content",
                className: "",
              }},
              documentElement: {{ className: "" }},
              title: "Reference title",
              querySelectorAll(selector) {{
                if (selector.includes("mobile-menu")) return [wrapper];
                return [];
              }},
            }};
            console.log({probe_js});
            wrapperHasButton = false;
            console.log({probe_js});
            """
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["node", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    probes = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [probe["mobileNavCount"] for probe in probes] == [1, 0]


def test_mobile_nav_parity_uses_probe_count(tmp_path: Path) -> None:
    proc, artifact = _run_gate(
        tmp_path,
        ref_counts={"main": 1},
        impl_counts={"main": 1},
        ref_mobile_nav_count=1,
        impl_mobile_nav_count=0,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert any("mobile-nav element" in reason for reason in artifact["reasons"])


def test_nonempty_document_title_mismatch_fails(tmp_path: Path) -> None:
    proc, artifact = _run_gate(
        tmp_path,
        ref_counts={"main": 1},
        impl_counts={"main": 1},
        ref_title="  GitHub   Docs ",
        impl_title="GitHub 문서 클론 검증",
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert any("document title differs" in reason for reason in artifact["reasons"])


def test_nonempty_reference_title_rejects_empty_impl_title(tmp_path: Path) -> None:
    proc, artifact = _run_gate(
        tmp_path,
        ref_counts={"main": 1},
        impl_counts={"main": 1},
        ref_title="GitHub Docs",
        impl_title="   ",
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert any("document title differs" in reason for reason in artifact["reasons"])


def test_document_title_comparison_normalizes_whitespace(tmp_path: Path) -> None:
    proc, artifact = _run_gate(
        tmp_path,
        ref_counts={"main": 1},
        impl_counts={"main": 1},
        ref_title="  GitHub   Docs ",
        impl_title="GitHub Docs",
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert artifact["status"] == "pass"


def test_document_title_comparison_requires_both_titles(tmp_path: Path) -> None:
    proc, artifact = _run_gate(
        tmp_path,
        ref_counts={"main": 1},
        impl_counts={"main": 1},
        ref_title="",
        impl_title="Implementation title",
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert artifact["status"] == "pass"
