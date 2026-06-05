"""Deterministic detector for signature scroll effects in JS bundles.

Purpose: the per-character scroll-scrubbed color/opacity reveal (and similar
high-signal scroll effects) was detected only as a generic library token by
download-chunks.sh, so it never reached generation-plan.signatureEffects and
was never reproduced in clones. This module turns raw bundle text into
structured, confidence-graded signature-effect candidates that
generation-plan.sh can populate signatureEffects from (the LLM enrichment pass
then refines names/components/selectors — it does not replace this).

Design constraints (from review):
- Multi-signal: require >= 2 corroborating signals before emitting a candidate,
  so a bare framer-motion/scroll import is not flagged.
- Never guess a selector from minified code — emit selector: null /
  selectorConfidence: "none" unless a literal is present.
- Per-character scroll-scrub is identified by the distinctive co-occurrence of
  `totalChars` + `scrollYProgress` (generic scroll uses scrollYProgress alone).

Public entry points:
    extract_candidates(bundle_texts: list[str]) -> list[dict]
    main(argv) -> int   # argv = [ref_dir, out_path]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Literal selector forms we trust (high confidence). Minified destructuring
# like `{char,index,totalChars,...}` carries no selector — stays null.
_SELECTOR_LITERAL = re.compile(
    r"""(?:SplitText|gsap\.(?:from|to|fromTo)|scrollTrigger\s*:\s*\{[^}]*trigger)\s*\(?\s*['"]([.#][\w-]+)['"]"""
)


def _selector_from(text: str) -> str | None:
    m = _SELECTOR_LITERAL.search(text)
    return m.group(1) if m else None


def extract_candidates(bundle_texts: list[str]) -> list[dict]:
    """Return signature-effect candidates from raw bundle text(s).

    Currently detects the per-character scroll-scrub reveal (framer-motion
    `scrollYProgress` driven, per-character via `totalChars`). Returns [] when
    the distinctive signals are absent (generic scroll is not flagged).
    """
    candidates: list[dict] = []
    seen: set[str] = set()
    for idx, text in enumerate(bundle_texts):
        if not text:
            continue
        has_total = "totalChars" in text
        has_scroll = "scrollYProgress" in text
        has_perchar = ("char:" in text) or ("index:" in text) or ("prevChar" in text)
        has_color = ("color" in text) or ("opacity" in text)
        # Per-character scroll-scrub: the totalChars + scrollYProgress pairing
        # is the distinctive signal (generic scroll has scrollYProgress alone).
        if has_total and has_scroll:
            signals = [s for s, present in (
                ("totalChars", has_total), ("scrollYProgress", has_scroll),
                ("per-char", has_perchar), ("color/opacity", has_color),
            ) if present]
            if len(signals) < 2:
                continue
            props = [p for p in ("color", "opacity") if p in text]
            high = has_total and has_scroll and has_perchar and has_color
            key = "per-character-scroll-scrub"
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "id": f"sig-{len(candidates) + 1:03d}",
                "name": "PerCharacterScrollReveal",
                "effectType": "per-character-scroll-scrub",
                "selector": _selector_from(text),
                "selectorConfidence": "high" if _selector_from(text) else "none",
                "library": "framer-motion",
                "trigger": {"type": "scroll", "scrub": True},
                "animation": {"properties": props or ["color", "opacity"], "perCharacter": True},
                "confidence": "high" if high else "medium",
                "evidence": {"signals": signals, "sourceChunkIndex": idx},
            })

        # Per-WORD scroll-progress highlight: words/lines toggle between a
        # highlighted and a dimmed colour as scroll progress advances an active
        # index (a `highlighted`+`dimmed` class pair toggled under a
        # scrollYProgress threshold over a word-split). Distinct from the
        # per-character scrub above (color:inherit disintegration). The
        # co-occurrence of the highlight/dim PAIR + a word split is the
        # distinctive signal; require scroll on top.
        has_hi_dim = ("highlighted" in text) and ("dimmed" in text)
        has_word_split = ('split(" ")' in text) or ("split(' ')" in text)
        if has_hi_dim and has_scroll and "per-word-scroll-highlight" not in seen:
            w_signals = [s for s, present in (
                ("highlighted+dimmed", has_hi_dim),
                ("scrollYProgress", has_scroll),
                ("word-split", has_word_split),
            ) if present]
            if len(w_signals) >= 2:
                seen.add("per-word-scroll-highlight")
                candidates.append({
                    "id": f"sig-{len(candidates) + 1:03d}",
                    "name": "ScrollWordHighlight",
                    "effectType": "per-word-scroll-highlight",
                    "selector": _selector_from(text),
                    "selectorConfidence": "high" if _selector_from(text) else "none",
                    "library": "framer-motion",
                    "trigger": {"type": "scroll", "scrub": True},
                    "animation": {"properties": ["color"], "perWord": True},
                    "confidence": "high" if has_word_split else "medium",
                    "evidence": {"signals": w_signals, "sourceChunkIndex": idx},
                })
    return candidates


def parse_ref(ref_dir: Path) -> dict:
    bundles = sorted((ref_dir / "bundles").glob("*.js")) if (ref_dir / "bundles").is_dir() else []
    texts = []
    for b in bundles:
        try:
            texts.append(b.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return {
        "schemaVersion": 1,
        "producer": "scripts/extract/_signature_effects.py",
        "candidates": extract_candidates(texts),
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: _signature_effects.py <ref_dir> <out_path>", file=sys.stderr)
        return 2
    ref_dir = Path(argv[0])
    out_path = Path(argv[1])
    result = parse_ref(ref_dir)
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"signature-effects: {len(result['candidates'])} candidate(s) → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
