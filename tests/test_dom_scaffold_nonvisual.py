from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "dom-scaffold.sh"


def _run_dom_scaffold(
    tmp_path: Path,
    structure: dict[str, Any],
    sections: list[dict[str, Any]],
) -> dict[str, Any]:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "structure.json").write_text(
        json.dumps(structure, ensure_ascii=False),
        encoding="utf-8",
    )
    (ref / "styles.json").write_text("{}", encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": sections}),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return cast(
        dict[str, Any],
        json.loads((ref / "dom-scaffold.json").read_text(encoding="utf-8")),
    )


def test_dom_scaffold_drops_non_rendered_subtrees_and_sections(tmp_path: Path) -> None:
    payloads = {
        "script": 'self.__next_f.push([1, "262KB RSC payload"])',
        "style": ".runtime-shell { display: none; }",
        "noscript": "Fallback payload that is not live rendered",
        "template": "Detached template payload",
    }
    non_rendered: list[dict[str, Any]] = [
        {
            "tag": tag,
            "id": f"{tag}-payload",
            "text": payload,
            "children": [
                {"tag": "span", "text": f"nested {tag} payload", "children": []},
            ],
        }
        for tag, payload in payloads.items()
    ]
    non_rendered[0]["children"].append(
        {
            "tag": "main",
            "id": "content",
            "styles": {"padding": "999px"},
            "children": [],
        },
    )
    structure = {
        "tag": "body",
        "children": [
            *non_rendered,
            {
                "tag": "main",
                "id": "content",
                "text": "Visible copy",
                "styles": {"padding": "24px"},
                "children": [],
            },
        ],
    }
    sections = [
        {
            "index": index,
            "tag": tag,
            "id": f"{tag}-payload",
            "top": index,
            "height": 1,
        }
        for index, tag in enumerate(payloads)
    ]
    sections.append(
        {
            "index": len(sections),
            "tag": "main",
            "id": "content",
            "top": 0,
            "height": 100,
        },
    )

    scaffold = _run_dom_scaffold(tmp_path, structure, sections)
    blob = json.dumps(scaffold, ensure_ascii=False)

    assert [section["tag"] for section in scaffold["sections"]] == ["main"]
    assert scaffold["sections"][0]["styles"]["padding"] == "24px"
    for tag, payload in payloads.items():
        assert f'"tag": "{tag}"' not in blob
        assert payload not in blob
        assert f"nested {tag} payload" not in blob


def test_dom_scaffold_preserves_visible_interleaved_text_and_styles(
    tmp_path: Path,
) -> None:
    structure = {
        "tag": "main",
        "id": "content",
        "children": [
            {
                "tag": "p",
                "class": "lede",
                "text": "Before  after",
                "textFull": "Before emphasized after",
                "styles": {
                    "font-size": "20px",
                    "color": "rgb(10, 20, 30)",
                },
                "children": [
                    {
                        "tag": "span",
                        "text": "emphasized",
                        "styles": {"font-weight": "700"},
                        "children": [],
                    },
                    {
                        "tag": "script",
                        "text": "window.__runtime_payload__ = 'not visible'",
                        "children": [],
                    },
                    {
                        "tag": "svg",
                        "class": "icon",
                        "styles": {"width": "16px", "height": "16px"},
                        "children": [
                            {"tag": "path", "styles": {"opacity": "0.5"}, "children": []},
                        ],
                    },
                ],
            },
        ],
    }
    sections = [
        {
            "index": 0,
            "tag": "main",
            "id": "content",
            "top": 0,
            "height": 100,
        },
    ]

    scaffold = _run_dom_scaffold(tmp_path, structure, sections)
    paragraph = scaffold["tree"]["children"][0]

    assert paragraph["text"] == "Before  after"
    assert paragraph["textFull"] == "Before emphasized after"
    assert paragraph["styles"]["fs"] == "20px"
    assert paragraph["styles"]["color"] == "rgb(10, 20, 30)"
    assert [child["tag"] for child in paragraph["children"]] == ["span", "svg"]
    assert paragraph["children"][0]["text"] == "emphasized"
    assert paragraph["children"][0]["styles"]["fw"] == "700"
    assert paragraph["children"][1]["styles"] == {
        "width": "16px",
        "height": "16px",
    }
    assert paragraph["children"][1]["children"][0]["styles"]["opacity"] == "0.5"


def test_dom_scaffold_preserves_user_facing_evidence_attributes(
    tmp_path: Path,
) -> None:
    structure = {
        "tag": "main",
        "children": [
            {
                "tag": "button",
                "aria-label": "문서 검색 열기",
                "title": "검색",
                "children": [
                    {
                        "tag": "img",
                        "alt": "GitHub 로고",
                        "children": [],
                    }
                ],
            }
        ],
    }
    scaffold = _run_dom_scaffold(
        tmp_path,
        structure,
        [{"index": 0, "tag": "main", "top": 0, "height": 100}],
    )

    button = scaffold["tree"]["children"][0]
    assert button["aria-label"] == "문서 검색 열기"
    assert button["title"] == "검색"
    assert button["children"][0]["alt"] == "GitHub 로고"


def test_dom_scaffold_preserves_deep_attribute_only_svg_evidence(
    tmp_path: Path,
) -> None:
    node: dict[str, object] = {
        "tag": "svg",
        "aria-label": "(external site)",
        "children": [{"tag": "path", "children": []}],
    }
    for _ in range(10):
        node = {"tag": "div", "children": [node]}
    structure = {"tag": "main", "children": [node]}

    scaffold = _run_dom_scaffold(
        tmp_path,
        structure,
        [{"index": 0, "tag": "main", "top": 0, "height": 100}],
    )

    cursor = scaffold["tree"]["children"][0]
    for _ in range(10):
        cursor = cursor["children"][0]
    assert cursor == {"tag": "svg", "aria-label": "(external site)"}


def test_dom_scaffold_preserves_class_tokens_past_depth_cap(
    tmp_path: Path,
) -> None:
    node: dict[str, object] = {
        "tag": "div",
        "class": "item-data",
        "children": [
            {
                "tag": "h3",
                "class": "item-title",
                "text": "Tech for People",
                "children": [],
            }
        ],
    }
    for index in range(10):
        node = {
            "tag": "div",
            "class": f"wrapper-{index}",
            "children": [node],
        }
    structure = {"tag": "main", "children": [node]}

    scaffold = _run_dom_scaffold(
        tmp_path,
        structure,
        [{"index": 0, "tag": "main", "top": 0, "height": 100}],
    )

    blob = json.dumps(scaffold["tree"], ensure_ascii=False)
    assert '"class": "item-data"' in blob
    assert '"class": "item-title"' in blob
