"""Responsive sizing classifier — Step 4-C2 (responsive-detection.md).

Pure diff/classify logic with NO browser dependency, so it is unit-testable in
isolation: `responsive-sweep.sh` measures a ref at three viewports and writes
`responsive/sizing-<vp>.json`; this module reads those per-viewport samples,
recovers the original CSS expression class for each element property, and writes
the selector-keyed `responsive/sizing-expressions.json` the pre-generate gate
expects.

Output shape is a BARE selector-keyed map (matching responsive-detection.md's
own node script and tests/conftest.py's accepted fixture), e.g.

    {
      ".hero": {
        "width":  {"type": "vw", "value": "83.3vw", "samples": {"768": 640, ...}},
        "display": {"type": "switched", "samples": {"768": "block", "1280": "flex", ...}}
      }
    }

This deliberately has no top-level ``sentinel`` / ``observation`` /
``expressions`` keys, so it never trips
``ui_clone.extraction_artifacts.sizing_expressions_is_unfilled_sentinel``
(which flags ``sentinel: true`` / ``observation ==
'single-viewport-sizing-summary'`` / ``expressions == []``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# The three sweep viewports Step 4-C2 mandates.
VIEWPORTS: tuple[int, ...] = (768, 1280, 1440)

# Numeric properties recovered as sizing expressions.
NUMERIC_PROPS: tuple[str, ...] = ("width", "height", "paddingLeft", "paddingRight", "fontSize")
# Categorical properties that only matter when they SWITCH across viewports
# (e.g. a flex row collapsing to a block stack at a breakpoint).
CATEGORICAL_PROPS: tuple[str, ...] = ("display", "position")

# All expression types this classifier can emit (for meta/reporting + tests).
EXPRESSION_TYPES: tuple[str, ...] = (
    "fixed-px", "calc", "vw", "linear", "breakpoint-jump", "switched",
)


def _round(value: float, ndigits: int = 0) -> float | int:
    r = round(value, ndigits)
    return int(r) if ndigits == 0 else r


def classify_numeric(samples: dict[int, float]) -> dict[str, Any] | None:
    """Classify a viewport→px map into one expression, or None with <2 samples.

    Mirrors responsive-detection.md Step 4-C2's classifier tolerances exactly:
    fixed-px (<1px spread) → calc(100vw-const) (offset stable <3) → vw (percent
    stable <1.5) → linear (slope+intercept fit <3, |slope|>0.05) → breakpoint-jump.
    """
    points = sorted(
        (int(vp), float(v)) for vp, v in samples.items() if v is not None
    )
    if len(points) < 2:
        return None
    sample_map = {str(vp): _round(v, 2) for vp, v in points}
    values = [v for _, v in points]

    # fixed-px — identical across viewports.
    if all(abs(v - values[0]) < 1 for v in values):
        return {"type": "fixed-px", "value": f"{_round(values[0])}px", "samples": sample_map}

    # calc(100vw - const) — value tracks viewport with a constant offset.
    offsets = [vp - v for vp, v in points]
    if all(abs(o - offsets[0]) < 3 for o in offsets):
        return {"type": "calc", "value": f"calc(100vw - {_round(offsets[0])}px)", "samples": sample_map}

    # vw — value is a stable percentage of the viewport width.
    pcts = [(v / vp) * 100 for vp, v in points]
    if all(abs(p - pcts[0]) < 1.5 for p in pcts):
        return {"type": "vw", "value": f"{_round(pcts[0], 1)}vw", "samples": sample_map}

    # linear — value fits slope*vw + intercept across the whole range.
    x1, y1 = points[0]
    x2, y2 = points[-1]
    if x2 != x1:
        slope = (y2 - y1) / (x2 - x1)
        intercept = y1 - slope * x1
        if abs(slope) > 0.05 and all(
            abs(v - (slope * vp + intercept)) < 3 for vp, v in points
        ):
            return {
                "type": "linear",
                "value": f"calc({_round(slope * 100, 1)}vw + {_round(intercept)}px)",
                "samples": sample_map,
            }

    # breakpoint-jump — differs with no continuous relationship; use responsive
    # utilities keyed on the per-viewport values.
    return {"type": "breakpoint-jump", "value": None, "samples": sample_map}


def classify_categorical(samples: dict[int, str]) -> dict[str, Any] | None:
    """Emit a ``switched`` expression only when a categorical property (display,
    position) actually changes across viewports; a constant value is not a
    responsive expression and returns None."""
    present = {int(vp): str(v) for vp, v in samples.items() if v not in (None, "")}
    if len(present) < 2:
        return None
    if len(set(present.values())) < 2:
        return None
    return {
        "type": "switched",
        "samples": {str(vp): present[vp] for vp in sorted(present)},
    }


def build_expressions(
    per_viewport: dict[int, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Combine per-viewport element metric maps into the bare selector-keyed
    expression map. ``per_viewport`` is ``{viewport: {selector: {prop: value}}}``."""
    selectors: set[str] = set()
    for elements in per_viewport.values():
        selectors.update(elements.keys())

    out: dict[str, dict[str, Any]] = {}
    for selector in sorted(selectors):
        props: dict[str, Any] = {}
        for prop in NUMERIC_PROPS:
            samples = {
                vp: elements[selector][prop]
                for vp, elements in per_viewport.items()
                if selector in elements and elements[selector].get(prop) is not None
            }
            result = classify_numeric(samples)
            if result is not None:
                props[prop] = result
        for prop in CATEGORICAL_PROPS:
            samples_c = {
                vp: elements[selector][prop]
                for vp, elements in per_viewport.items()
                if selector in elements and elements[selector].get(prop) is not None
            }
            result = classify_categorical(samples_c)
            if result is not None:
                props[prop] = result
        if props:
            out[selector] = props
    return out


def type_histogram(expressions: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts = {t: 0 for t in EXPRESSION_TYPES}
    for props in expressions.values():
        for entry in props.values():
            t = entry.get("type")
            if t in counts:
                counts[t] += 1
    return counts


def load_viewport_samples(
    ref_dir: Path, viewports: tuple[int, ...] = VIEWPORTS,
) -> dict[int, dict[str, dict[str, Any]]]:
    """Read responsive/sizing-<vp>.json ({viewport, elements}) for each viewport
    that exists. Missing files are skipped (the classifier tolerates <3)."""
    per_viewport: dict[int, dict[str, dict[str, Any]]] = {}
    for vp in viewports:
        path = ref_dir / "responsive" / f"sizing-{vp}.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        elements = data.get("elements") if isinstance(data, dict) else None
        if isinstance(elements, dict):
            per_viewport[int(vp)] = elements
    return per_viewport


def write_outputs(
    ref_dir: Path,
    expressions: dict[str, dict[str, Any]],
    viewports: tuple[int, ...],
    measured_viewports: list[int],
) -> tuple[Path, Path]:
    """Write the bare selector-keyed sizing-expressions.json plus a meta sidecar
    (sizing-sweep.json) carrying provenance/counts that would otherwise pollute
    the bare map."""
    resp = ref_dir / "responsive"
    resp_path = resp / "sizing-expressions.json"
    meta_path = resp / "sizing-sweep.json"
    resp.mkdir(parents=True, exist_ok=True)
    resp_path.write_text(json.dumps(expressions, indent=2) + "\n", encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "source": "scripts/extract/responsive-sweep.sh",
                "method": "multi-viewport-computed-sweep",
                "viewports": list(viewports),
                "measuredViewports": sorted(measured_viewports),
                "selectorCount": len(expressions),
                "byType": type_histogram(expressions),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return resp_path, meta_path


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: _responsive_classify.py <ref_dir>", file=sys.stderr)
        return 2
    ref_dir = Path(argv[0])
    per_viewport = load_viewport_samples(ref_dir)
    expressions = build_expressions(per_viewport)
    resp_path, _ = write_outputs(
        ref_dir, expressions, VIEWPORTS, sorted(per_viewport.keys())
    )
    hist = type_histogram(expressions)
    hist_str = ", ".join(f"{t}={n}" for t, n in hist.items() if n)
    print(
        f"responsive-classify: {len(expressions)} selectors, "
        f"{sum(hist.values())} expressions ({hist_str or 'none'}) "
        f"from {len(per_viewport)} viewport(s) → {resp_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
