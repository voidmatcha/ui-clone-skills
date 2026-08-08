"""Text fidelity must inspect copy throughout the bounded captured DOM tree."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"


def test_deep_cjk_copy_is_grounded_in_dom_scaffold(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    copy = "깊게 중첩된 실제 사용자 문구입니다"
    node: dict[str, object] = {
        "tag": "p",
        "text": copy,
        "children": [],
    }
    for _ in range(18):
        node = {"tag": "div", "children": [node]}
    (ref / "dom-scaffold.json").write_text(
        json.dumps({"tree": node}, ensure_ascii=False),
        encoding="utf-8",
    )
    (impl / "src" / "App.tsx").write_text(
        "export default function App() {\n"
        "  return (\n"
        "    <p>\n"
        f"      {copy}\n"
        "    </p>\n"
        "  );\n"
        "}\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "pass"
    assert result["required_meaningful_strings"] == 1
    assert result["fabrications_count"] == 0
    assert result["missing_count"] == 0


def test_captured_accessibility_copy_is_allowlist_evidence(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    label = "문서 검색 열기"
    (ref / "dom-scaffold.json").write_text(
        json.dumps(
            {
                "tree": {
                    "tag": "button",
                    "aria-label": label,
                    "children": [],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (impl / "src" / "App.tsx").write_text(
        f'export default function App() {{ return <button aria-label="{label}" />; }}\n',
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "pass"
    assert result["allowlist_size"] == 1
    assert result["fabrications_count"] == 0


def test_https_link_with_multiline_copy_is_not_erased_as_a_comment(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    copy = "전문가 서비스"
    (ref / "dom-scaffold.json").write_text(
        json.dumps(
            {"tree": {"tag": "a", "text": copy, "children": []}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (impl / "src" / "App.tsx").write_text(
        "export default function App() {\n"
        "  return (\n"
        '    <a href="https://services.github.com">\n'
        f"      {copy}\n"
        "    </a>\n"
        "  );\n"
        "}\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "pass"
    assert result["missing_count"] == 0


def test_sticky_class_is_not_misclassified_as_cky_cookie_overlay(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    copy = "Copilot에 검색하거나 질문하기"
    (ref / "dom-scaffold.json").write_text(
        json.dumps(
            {
                "tree": {
                    "tag": "header",
                    "class": "position-sticky top-0",
                    "aria-label": "Main",
                    "children": [{"tag": "span", "text": copy}],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (impl / "src" / "App.tsx").write_text(
        "export default function App() {\n"
        f'  return <header className="position-sticky"><span>{copy}</span></header>;\n'
        "}\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "pass"
    assert result["required_meaningful_strings"] == 1


def test_cjk_copy_after_jsx_whitespace_expression_is_detected(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    copy = "아니요"
    (ref / "dom-scaffold.json").write_text(
        json.dumps(
            {"tree": {"tag": "label", "text": copy}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (impl / "src" / "App.tsx").write_text(
        "export default function App() {\n"
        "  return (\n"
        "    <label>\n"
        "      <svg><path /></svg>\n"
        "      {' '}\n"
        f"      {copy}\n"
        "    </label>\n"
        "  );\n"
        "}\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "pass"
    assert result["missing_count"] == 0


def test_extraction_icon_marker_is_not_required_visible_copy(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "dom-scaffold.json").write_text(
        json.dumps(
            {
                "tree": {
                    "tag": "button",
                    "aria-label": "문서 검색 열기",
                    "children": [{"tag": "span", "text": "{{icon}}"}],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (impl / "src" / "App.tsx").write_text(
        'export default function App() { return <button aria-label="문서 검색 열기"><svg /></button>; }\n',
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "pass"
    assert result["required_meaningful_strings"] == 0
    assert result["missing_count"] == 0


def test_typescript_generics_are_not_scanned_as_jsx_text(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    label = "자동멈춤"
    (ref / "dom-scaffold.json").write_text(
        json.dumps(
            {
                "tree": {
                    "tag": "button",
                    "aria-label": label,
                    "children": [],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (impl / "src" / "Activator.tsx").write_text(
        "export default function Activator() {\n"
        "  const nodes = document.querySelectorAll<HTMLElement>(\".swiper\");\n"
        "  const first = nodes[0]?.querySelector<HTMLButtonElement>(\"button\") ?? null;\n"
        f'  first?.setAttribute(\"aria-label\", \"{label}\");\n'
        "  return null;\n"
        "}\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "pass"
    assert result["fabrications_count"] == 0


def test_cjk_copy_split_by_br_reconstructs_required_aggregate(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    copy = "질문 하나면 탐색부터 실행까지 AI탭으로 한 번에 해결하세요"
    (ref / "dom-scaffold.json").write_text(
        json.dumps(
            {"tree": {"tag": "p", "text": copy}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (impl / "src" / "App.tsx").write_text(
        "export default function App() {\n"
        "  return <p>질문 하나면 탐색부터 실행까지<br />AI탭으로 한 번에 해결하세요</p>;\n"
        "}\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "pass"
    assert result["missing_count"] == 0
