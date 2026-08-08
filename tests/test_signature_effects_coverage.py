from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "signature-effects-coverage-check.sh"

_EFFECT = {
    "name": "PerCharacterScrollReveal",
    "effectType": "per-character-scroll-scrub",
    "trigger": {"type": "scroll", "scrub": True},
    "animation": {"properties": ["color"], "perCharacter": True},
    "component": "components/ui/PerCharacterScrollReveal.tsx",
}


def _run(ref: Path, impl: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl / "src")],
        capture_output=True, text=True, timeout=120,
    )


def _scaffold(tmp_path: Path, signature_effects: object) -> tuple[Path, Path]:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "generation-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "signatureEffects": signature_effects}),
        encoding="utf-8",
    )
    return ref, impl


def test_declared_and_implemented_passes(tmp_path: Path) -> None:
    ref, impl = _scaffold(tmp_path, [_EFFECT])
    (impl / "src" / "PerCharacterScrollReveal.tsx").write_text(
        "const {scrollYProgress}=useScroll();"
        "const chars=text.split('');const totalChars=chars.length;\n",
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = json.loads((ref / "signature-effects-coverage.json").read_text())
    assert proc.returncode == 0, art
    assert art["status"] == "pass"


def test_declared_but_not_implemented_fails(tmp_path: Path) -> None:
    ref, impl = _scaffold(tmp_path, [_EFFECT])
    (impl / "src" / "Hero.tsx").write_text(
        "export const Hero=()=> <h1>Real Food Wins</h1>;\n", encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = json.loads((ref / "signature-effects-coverage.json").read_text())
    assert proc.returncode == 1, art
    assert art["status"] == "fail"
    assert art["missing"] and art["missing"][0]["missingPrimitives"]


def test_generic_scroll_without_per_char_split_fails(tmp_path: Path) -> None:
    """A scroll binding plus only `letterSpacing`/`letter-spacing` CSS (no real
    per-character split) must NOT satisfy a per-character effect — guards the
    `letter` substring false-pass."""
    ref, impl = _scaffold(tmp_path, [_EFFECT])
    (impl / "src" / "Hero.tsx").write_text(
        "const {scrollYProgress}=useScroll();"
        "const s={letterSpacing:'0.1em'};return <h1 style={s}>Hi</h1>;\n",
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = json.loads((ref / "signature-effects-coverage.json").read_text())
    assert proc.returncode == 1, art
    assert art["status"] == "fail"
    assert "per-character" in str(art["missing"][0]["missingPrimitives"])


_WORD_EFFECT = {
    "name": "ScrollWordHighlight",
    "effectType": "per-word-scroll-highlight",
    "trigger": {"type": "scroll", "scrub": True},
    "animation": {"properties": ["color"], "perWord": True},
    "component": "src/lib/ScrollWordHighlight.tsx",
}


def test_per_word_declared_but_not_implemented_fails(tmp_path: Path) -> None:
    """A declared per-word scroll highlight with no word split + scroll binding
    must fail — static-colour text is the gap this closes."""
    ref, impl = _scaffold(tmp_path, [_WORD_EFFECT])
    (impl / "src" / "Hero.tsx").write_text(
        "export const Hero=()=> <h1>Real Food Wins</h1>;\n", encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = json.loads((ref / "signature-effects-coverage.json").read_text())
    assert proc.returncode == 1, art
    assert art["status"] == "fail"
    assert any("per-word" in str(m["missingPrimitives"]) for m in art["missing"])


def test_per_word_with_primitive_passes(tmp_path: Path) -> None:
    """Using the emitted ScrollWordHighlight primitive (word split + scroll)
    satisfies the per-word coverage."""
    ref, impl = _scaffold(tmp_path, [_WORD_EFFECT])
    (impl / "src" / "Hero.tsx").write_text(
        'import ScrollWordHighlight from "./lib/ScrollWordHighlight";\n'
        "const {scrollYProgress}=useScroll();\n"
        'export const Hero=()=> <ScrollWordHighlight text="Real Food Wins" />;\n',
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = json.loads((ref / "signature-effects-coverage.json").read_text())
    assert proc.returncode == 0, art
    assert art["status"] == "pass"


def test_emitted_helpers_present_but_unwired_fails(tmp_path: Path) -> None:
    """The emitted ScrollScrub/ScrollWordHighlight definitions live in src/lib/ and
    contain every primitive token. Their mere PRESENCE must NOT satisfy coverage —
    the gate must fail when nothing imports/uses them (emitted-but-unwired)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src" / "lib").mkdir(parents=True)
    ref.mkdir()
    (ref / "generation-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "signatureEffects": [_WORD_EFFECT],
        "scrollScrub": {"required": True, "sites": [{
            "offset": ["start end", "end start"],
            "transforms": [{"input": "[0,1]", "output": "[0.9,1.1]", "property": "scale"}],
        }]},
    }), encoding="utf-8")
    # Emitted helper definitions: full of useScroll/useTransform/useSpring/scale/
    # split(" ")/useMotionValueEvent/ScrollScrub/ScrollWordHighlight tokens.
    (impl / "src" / "lib" / "ScrollScrub.tsx").write_text(
        "import {useScroll,useTransform,useSpring} from 'framer-motion';"
        "export default function ScrollScrub(){const{scrollYProgress}=useScroll();"
        "const s=useTransform(scrollYProgress,[0,1],[0.9,1.1]);return <div style={{scale:s}} data-scroll-scrub='1'/>;}\n",
        encoding="utf-8",
    )
    (impl / "src" / "lib" / "ScrollWordHighlight.tsx").write_text(
        "import {useScroll,useMotionValueEvent} from 'framer-motion';"
        "export default function ScrollWordHighlight({text}){const w=text.split(\" \");"
        "return <span data-scroll-word-highlight='1'>{w}</span>;}\n",
        encoding="utf-8",
    )
    # App never imports them.
    (impl / "src" / "App.tsx").write_text(
        "export const App=()=> <main className='scale-105'>static clone</main>;\n",
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = json.loads((ref / "signature-effects-coverage.json").read_text())
    assert proc.returncode == 1, art
    assert art["status"] == "fail"
    names = {m["name"] for m in art["missing"]}
    assert "scroll-scrub-scale" in names
    assert "ScrollWordHighlight" in names


def test_scroll_scrub_used_for_opacity_only_still_fails_scale(tmp_path: Path) -> None:
    """ScrollScrub imported but driving only opacity/y (no scale band) must FAIL
    when a scale band is declared — the background zoom is the #3 ask, and a real
    loop wired opacity/y while leaving the scale site unused."""
    ref, impl = _scaffold_scrub(tmp_path)
    (impl / "src" / "Problem.tsx").write_text(
        'import ScrollScrub from "./lib/ScrollScrub";\n'
        "export const Problem=()=> (\n"
        '  <ScrollScrub opacity={[[0,0.3],[0.4,1]]} offset={["start end","end start"]}>\n'
        "    <div>stat</div>\n"
        "  </ScrollScrub>\n"
        ");\n",
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = json.loads((ref / "signature-effects-coverage.json").read_text())
    assert proc.returncode == 1, art
    assert art["status"] == "fail"
    assert "scroll-scrub-scale" in {m["name"] for m in art["missing"]}


def test_no_signature_effects_skips(tmp_path: Path) -> None:
    ref, impl = _scaffold(tmp_path, None)
    (impl / "src" / "Hero.tsx").write_text("x", encoding="utf-8")
    proc = _run(ref, impl)
    art = json.loads((ref / "signature-effects-coverage.json").read_text())
    assert proc.returncode == 0
    assert art["status"] == "skip"


# --- Component-contract coverage (F2): effects whose name does not trip the
# scroll/per-char/per-word keyword heuristics (e.g. MarqueeStrip, PlaygroundCanvas,
# CardStackReveal) still declare a `component` contract. Declaring one and never
# building it (no components/ui/X.tsx, name not wired anywhere) must FAIL — the
# prior heuristic-only gate vacuously passed these.

_NON_KEYWORD_EFFECT = {
    "name": "MarqueeStrip",
    "component": "components/ui/MarqueeStrip.tsx",
    "library": "framer-motion",
    "description": "Infinite horizontal marquee of partner logos.",
}


def test_component_declared_but_missing_and_unwired_fails(tmp_path: Path) -> None:
    ref, impl = _scaffold(tmp_path, [_NON_KEYWORD_EFFECT])
    (impl / "src" / "Hero.tsx").write_text(
        "export const Hero=()=> <h1>eBay Playbook</h1>;\n", encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = json.loads((ref / "signature-effects-coverage.json").read_text())
    assert proc.returncode == 1, art
    assert art["status"] == "fail"
    assert any(m.get("name") == "MarqueeStrip" for m in art["missing"]), art


def test_component_declared_and_file_present_passes(tmp_path: Path) -> None:
    ref, impl = _scaffold(tmp_path, [_NON_KEYWORD_EFFECT])
    ui_dir = impl / "src" / "components" / "ui"
    ui_dir.mkdir(parents=True)
    (ui_dir / "MarqueeStrip.tsx").write_text(
        "export default function MarqueeStrip(){return <div className='marquee'/>;}\n",
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = json.loads((ref / "signature-effects-coverage.json").read_text())
    assert proc.returncode == 0, art
    assert art["status"] == "pass"


def test_component_declared_and_name_wired_inline_passes(tmp_path: Path) -> None:
    ref, impl = _scaffold(tmp_path, [_NON_KEYWORD_EFFECT])
    # Implemented inline in a section (no separate ui/ file) but the named
    # component is referenced — treated as wired.
    (impl / "src" / "Section.tsx").write_text(
        "import MarqueeStrip from './x';\nexport const S=()=> <MarqueeStrip/>;\n",
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = json.loads((ref / "signature-effects-coverage.json").read_text())
    assert proc.returncode == 0, art
    assert art["status"] == "pass"


_SCRUB_PLAN = {
    "schemaVersion": 1,
    "signatureEffects": None,
    "scrollScrub": {
        "required": True,
        "sites": [{
            "offset": ["start end", "end start"],
            "transforms": [{"input": "[0,1]", "output": "[0.9,1.1]", "property": "scale"}],
        }],
    },
}


def _scaffold_scrub(tmp_path: Path) -> tuple[Path, Path]:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "generation-plan.json").write_text(json.dumps(_SCRUB_PLAN), encoding="utf-8")
    return ref, impl


def test_scroll_scrub_scale_declared_but_static_fails(tmp_path: Path) -> None:
    """A scrollScrub scale band (the #3 background zoom) with an impl that wires
    no scroll-driven scale must fail — declaring it then shipping a static
    background is exactly the gap this gate closes."""
    ref, impl = _scaffold_scrub(tmp_path)
    (impl / "src" / "App.tsx").write_text(
        "export const App=()=> <div className='bg'>hero</div>;\n", encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = json.loads((ref / "signature-effects-coverage.json").read_text())
    assert proc.returncode == 1, art
    assert art["status"] == "fail"
    assert art["scrollScrubScaleRequired"] is True
    assert any("scale" in str(m["missingPrimitives"]) for m in art["missing"])


def test_scroll_scrub_scale_with_primitive_passes(tmp_path: Path) -> None:
    """Wrapping the scrubbed element in the emitted ScrollScrub primitive
    satisfies the gate."""
    ref, impl = _scaffold_scrub(tmp_path)
    (impl / "src" / "App.tsx").write_text(
        'import ScrollScrub from "./lib/ScrollScrub";\n'
        "export const App=()=> <ScrollScrub scale={[[0,1],[0.9,1.1]]}>hero</ScrollScrub>;\n",
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = json.loads((ref / "signature-effects-coverage.json").read_text())
    assert proc.returncode == 0, art
    assert art["status"] == "pass"


def test_scroll_scrub_scale_with_idiomatic_impl_passes(tmp_path: Path) -> None:
    """A hand-rolled scroll-bound scale (useScroll + useTransform onto scale) is
    accepted — the gate must not force the emitted component specifically."""
    ref, impl = _scaffold_scrub(tmp_path)
    (impl / "src" / "Bg.tsx").write_text(
        "const {scrollYProgress}=useScroll();"
        "const scale=useTransform(scrollYProgress,[0,1],[0.9,1.1]);"
        "return <motion.div style={{scale}}/>;\n",
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = json.loads((ref / "signature-effects-coverage.json").read_text())
    assert proc.returncode == 0, art
    assert art["status"] == "pass"
