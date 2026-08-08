"""junk-token-check.sh — stringified-junk lint over impl source + runtime DOM.

Loop-9/10 regression class: NavStateMachine.tsx shipped
`classList.toggle('undefined', ...)` so live nav dots carry a literal
"undefined" class. Serialization junk ('undefined' / 'null' / 'NaN' /
'[object Object]') appearing as standalone tokens in className, id, src,
alt, or style values is always a generation defect — flag it statically in
impl source and (when a URL is provided) in the runtime DOM.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "junk-token-check.sh"


def _run(ref: Path, impl_src: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl_src)],
        capture_output=True, text=True, timeout=120,
    )


def _fixture(tmp_path: Path, source: str, name: str = "Widget.tsx") -> tuple[Path, Path]:
    ref = tmp_path / "ref"
    ref.mkdir(exist_ok=True)
    src = tmp_path / "impl" / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / name).write_text(source, encoding="utf-8")
    return ref, src


def _art(ref: Path) -> dict:
    data = json.loads((ref / "junk-token.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_classlist_toggle_undefined_fails(tmp_path: Path) -> None:
    """The exact loop-9 defect: classList.toggle('undefined', cond) creates a
    literal 'undefined' class on live elements."""
    ref, src = _fixture(
        tmp_path,
        "el.classList.toggle('undefined', state.active);\n",
        "NavStateMachine.tsx",
    )
    proc = _run(ref, src)
    art = _art(ref)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert art["status"] == "fail"
    assert any("NavStateMachine.tsx" in f["file"] for f in art["staticFindings"])


def test_classname_string_with_undefined_token_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, '<div className="card undefined" />\n')
    proc = _run(ref, src)
    assert proc.returncode == 1
    assert _art(ref)["status"] == "fail"


def test_src_with_undefined_segment_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, '<img src="/img/undefined.png" alt="x" />\n')
    proc = _run(ref, src)
    assert proc.returncode == 1


def test_object_object_in_alt_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, '<img src="/a.png" alt="[object Object]" />\n')
    proc = _run(ref, src)
    assert proc.returncode == 1


def test_nan_in_style_value_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, '<div style="width: NaNpx" />\n')
    proc = _run(ref, src)
    assert proc.returncode == 1


def test_clean_source_passes(tmp_path: Path) -> None:
    ref, src = _fixture(
        tmp_path,
        '<div className="card active" id="hero">'
        '<img src="/img/steak.png" alt="steak" /></div>\n'
        "el.classList.toggle('open', isOpen);\n",
    )
    proc = _run(ref, src)
    art = _art(ref)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "pass"


def test_hyphenated_token_not_flagged(tmp_path: Path) -> None:
    """'not-undefined' / 'null-state' are real class tokens, not junk —
    standalone-token matching must not fire on hyphenated compounds."""
    ref, src = _fixture(
        tmp_path,
        '<div className="not-undefined null-state nan-guard" />\n',
    )
    proc = _run(ref, src)
    art = _art(ref)
    assert proc.returncode == 0, json.dumps(art.get("staticFindings"))
    assert art["status"] == "pass"


def test_jsx_null_expression_not_flagged(tmp_path: Path) -> None:
    """src={null} / className={undefined} are JS expressions (legit), not
    string junk."""
    ref, src = _fixture(
        tmp_path,
        "<img src={null} className={undefined} alt={name ?? null} />\n",
    )
    proc = _run(ref, src)
    art = _art(ref)
    assert proc.returncode == 0, json.dumps(art.get("staticFindings"))
    assert art["status"] == "pass"


def test_confusable_unicode_junk_token_fails(tmp_path: Path) -> None:
    """tools batch-6 ITEM 5(b): a Cyrillic small e (U+0435) in 'undefinеd'
    reads as 'undefined' to a human reviewer but the ASCII-exact JUNK set misses
    it. NFKC + confusable folding must catch it."""
    ref, src = _fixture(tmp_path, '<div className="nav_dot undefinеd" />\n')
    proc = _run(ref, src)
    art = _art(ref)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert art["status"] == "fail"


def test_confusable_does_not_overflag_real_tokens(tmp_path: Path) -> None:
    """A real (all-ASCII) class token that merely contains junk letters must not
    fire — folding only normalizes confusable homoglyphs, it does not loosen the
    standalone-token rule."""
    ref, src = _fixture(tmp_path, '<div className="undefined-guard not-null" />\n')
    proc = _run(ref, src)
    art = _art(ref)
    assert proc.returncode == 0, json.dumps(art.get("staticFindings"))
    assert art["status"] == "pass"


# ── tools batch-7 ITEM 5: widened attribute/context + zero-width strip ──


def test_data_attr_junk_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, '<div data-state="undefined" data-id="[object Object]" />\n')
    assert _run(ref, src).returncode == 1
    assert _art(ref)["status"] == "fail"


def test_aria_attr_junk_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, '<div aria-label="undefined" role="status" />\n')
    assert _run(ref, src).returncode == 1


def test_svg_presentation_attr_junk_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, '<rect fill="undefined" stroke="NaN" />\n', "Icon.tsx")
    assert _run(ref, src).returncode == 1


def test_zero_width_confusable_zwj_fails(tmp_path: Path) -> None:
    # U+200D ZWJ inside the token; NFKC keeps it, but the Cf-strip removes it.
    ref, src = _fixture(tmp_path, '<div className="dot u‍ndefined" />\n')
    assert _run(ref, src).returncode == 1


def test_zero_width_confusable_zwsp_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, '<div className="dot u​ndefined" />\n')
    assert _run(ref, src).returncode == 1


def test_json_value_junk_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, '{"label": "undefined", "klass": "card null"}\n', "data.json")
    assert _run(ref, src).returncode == 1


def test_astro_class_junk_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, '<div className="nav_dot undefined" />\n', "Nav.astro")
    assert _run(ref, src).returncode == 1


def test_data_aria_legit_values_pass(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, '<div data-state="open" aria-label="Hero" data-count="3" />\n')
    proc = _run(ref, src)
    assert proc.returncode == 0, json.dumps(_art(ref).get("staticFindings"))


def test_data_attr_jsx_expression_not_flagged(tmp_path: Path) -> None:
    # data-x={undefined} is a JS expression (legit), not a string literal.
    ref, src = _fixture(tmp_path, "<div data-x={undefined} aria-y={null} />\n")
    proc = _run(ref, src)
    assert proc.returncode == 0, json.dumps(_art(ref).get("staticFindings"))


def test_json_prose_value_not_overflagged(tmp_path: Path) -> None:
    # a human sentence containing the word 'undefined' is not standalone junk.
    ref, src = _fixture(tmp_path, '{"note": "undefined behavior is documented"}\n', "config.json")
    proc = _run(ref, src)
    assert proc.returncode == 0, json.dumps(_art(ref).get("staticFindings"))


def test_runtime_not_scanned_is_recorded(tmp_path: Path) -> None:
    """Without a session/impl-url the artifact must say the runtime DOM was
    not scanned — static pass alone is not full coverage."""
    ref, src = _fixture(tmp_path, '<div className="ok" />\n')
    proc = _run(ref, src)
    art = _art(ref)
    assert proc.returncode == 0
    assert art["runtimeScanned"] is False


def test_missing_impl_src_setup_error(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(tmp_path / "nope")],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 2


def test_runtime_attempted_but_failed_downgrades_to_warn(tmp_path: Path) -> None:
    """Review-1 MAJOR 3: runtime args supplied but the scan failed must not
    produce a clean pass — the artifact downgrades to warn with a reason."""
    ref, src = _fixture(tmp_path, '<div className="ok" />\n')
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(src),
         "no-such-session", "http://localhost:1/unreachable"],
        capture_output=True, text=True, timeout=120,
    )
    art = _art(ref)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["runtimeScanned"] is False
    assert art["status"] == "warn", art
    assert "runtime" in json.dumps(art).lower()


def test_artifact_records_impl_src_dir(tmp_path: Path) -> None:
    """Review-1 MINOR 4: the artifact must name the scanned impl source dir
    so staleness/path validation can cross-check it."""
    ref, src = _fixture(tmp_path, '<div className="ok" />\n')
    _run(ref, src)
    art = _art(ref)
    assert art["implSrcDir"] == str(src.resolve())


# ── batch-8 ITEM 6: CSS-context junk + unscanned JS sinks. The SOURCE is the
# authoritative block: junk in a context the runtime sweep cannot reach (CSS
# content/fill, interaction-gated setAttribute/dataset) must fail statically. ──


def test_css_content_junk_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, '.badge::after { content: "undefined"; }\n', "styles.css")
    assert _run(ref, src).returncode == 1
    assert _art(ref)["status"] == "fail"


def test_css_fill_junk_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, '.icon { fill: undefined; }\n', "styles.css")
    assert _run(ref, src).returncode == 1


def test_setattribute_class_junk_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, 'el.setAttribute("class", "undefined");\n', "route.js")
    assert _run(ref, src).returncode == 1


def test_setattribute_null_junk_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, "el.setAttribute('class', 'null');\n", "route.js")
    assert _run(ref, src).returncode == 1


def test_dataset_assignment_junk_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, 'el.dataset.state = "undefined";\n', "app.js")
    assert _run(ref, src).returncode == 1


def test_csstext_assignment_junk_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, 'el.style.cssText = "color: undefined";\n', "app.js")
    assert _run(ref, src).returncode == 1


def test_css_legit_values_pass(tmp_path: Path) -> None:
    # false-positive guard: real CSS values, and a hyphenated compound that
    # merely CONTAINS 'undefined' (not a standalone junk token), must not flag.
    ref, src = _fixture(
        tmp_path,
        '.x::after { content: "menu"; } .y { fill: #0a0; color: var(--undefined-token); }\n',
        "styles.css",
    )
    proc = _run(ref, src)
    assert proc.returncode == 0, json.dumps(_art(ref).get("staticFindings"))


def test_setattribute_dynamic_value_not_flagged(tmp_path: Path) -> None:
    # false-positive guard: an unquoted JS identifier value is a legit dynamic
    # assignment, not a string literal — must not flag.
    ref, src = _fixture(
        tmp_path, "el.setAttribute('class', clsName);\nel.dataset.x = value;\n", "app.js"
    )
    proc = _run(ref, src)
    assert proc.returncode == 0, json.dumps(_art(ref).get("staticFindings"))


# ── batch-9 ITEM 4: classList ALL-args + dataset bracket-form widening ──────
# The old CLASSLIST_RE caught junk only as the FIRST string-literal arg, and
# DATASET_ASSIGN_RE only matched dot notation — classList.add('ok','undefined')
# and el.dataset["x"]="undefined" both shipped junk past the gate.


def test_classlist_add_junk_in_second_arg_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, "el.classList.add('ok', 'undefined');\n", "app.js")
    _run(ref, src)
    assert _art(ref)["status"] == "fail"


def test_classlist_remove_junk_in_middle_arg_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, "el.classList.remove('a', 'NaN', 'b');\n", "app.js")
    _run(ref, src)
    assert _art(ref)["status"] == "fail"


def test_classlist_replace_junk_in_second_arg_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, "el.classList.replace('old', 'undefined');\n", "app.js")
    _run(ref, src)
    assert _art(ref)["status"] == "fail"


def test_classlist_add_legit_multi_tokens_passes(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, "el.classList.add('card', 'active', 'open');\n", "app.js")
    proc = _run(ref, src)
    assert proc.returncode == 0, json.dumps(_art(ref).get("staticFindings"))
    assert _art(ref)["status"] == "pass"


def test_dataset_bracket_double_quote_undefined_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, 'el.dataset["state"] = "undefined";\n', "app.js")
    _run(ref, src)
    assert _art(ref)["status"] == "fail"


def test_dataset_bracket_single_quote_null_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, "el.dataset['count'] = 'null';\n", "app.js")
    _run(ref, src)
    assert _art(ref)["status"] == "fail"


def test_dataset_bracket_object_object_fails(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, 'el.dataset["meta"] = "[object Object]";\n', "app.js")
    _run(ref, src)
    assert _art(ref)["status"] == "fail"


def test_dataset_bracket_legit_value_passes(tmp_path: Path) -> None:
    ref, src = _fixture(tmp_path, 'el.dataset["color"] = "blue";\n', "app.js")
    proc = _run(ref, src)
    assert proc.returncode == 0, json.dumps(_art(ref).get("staticFindings"))
    assert _art(ref)["status"] == "pass"


def test_dataset_dynamic_bracket_key_not_flagged(tmp_path: Path) -> None:
    # A dynamic (unquoted) bracket key is computed JS — excluded like
    # setAttribute(name, x); only a string-literal key + value pair is scanned.
    ref, src = _fixture(tmp_path, "el.dataset[varKey] = 'undefined';\n", "app.js")
    proc = _run(ref, src)
    assert proc.returncode == 0, json.dumps(_art(ref).get("staticFindings"))
    assert _art(ref)["status"] == "pass"
