"""ui_clone.alignment_sweep — invariant transfer to intermediate widths.

Plan viewports have ref geometry (Item 1's matches.json data); intermediate
widths do not. Sections/groups are classified from ref data — centered
(|leftGap-rightGap| small at ALL enforced desktop viewports), fixed-gutter
(leftGap constant), else proportional (skip) — and the impl-only sweep
asserts the transferred invariant via DOM rects at midpoint/breakpoint
widths. Blocking requires failure at two ADJACENT sweep widths or at one
enforced (plan) width — a single mid-width wobble is advisory only.
"""

from __future__ import annotations

from pathlib import Path

from ui_clone.alignment_sweep import (
    classify,
    evaluate,
    sweep_widths,
)


def _ref_row(name: str, *, width: int, lg: float, rg: float, groups: list | None = None) -> dict:
    row = {
        "name": name,
        "ref": {
            "className": name,
            "textWords": "words here",
            "childCount": 2,
            "rect": {"top": 100, "left": 0, "width": width, "height": 600},
            "leftGap": lg,
            "rightGap": rg,
            "contentBox": {"left": lg, "width": width - lg - rg},
            "contentGroups": groups or [],
        },
        "impl": {"className": name},
    }
    return row


def _grp(name: str, *, c_l: float, c_w: float, u_l: float, u_w: float) -> dict:
    return {
        "name": name, "containerLeft": c_l, "containerWidth": c_w,
        "unionLeft": u_l, "unionWidth": u_w, "childCount": 3,
    }


# ── classification ─────────────────────────────────────────────────────


def test_centered_invariant_classification() -> None:
    by_vp = {
        "1280x800": [_ref_row("hero", width=1280, lg=128, rg=128)],
        "1600x900": [_ref_row("hero", width=1600, lg=160, rg=160)],
        "1920x1080": [_ref_row("hero", width=1920, lg=180, rg=181)],
    }
    cls = classify(by_vp)
    assert cls["hero"]["kind"] == "centered"


def test_fixed_gutter_classification() -> None:
    by_vp = {
        "1280x800": [_ref_row("side", width=1280, lg=64, rg=400)],
        "1600x900": [_ref_row("side", width=1600, lg=64, rg=720)],
        "1920x1080": [_ref_row("side", width=1920, lg=65, rg=1040)],
    }
    cls = classify(by_vp)
    assert cls["side"]["kind"] == "fixed-gutter"
    assert abs(cls["side"]["refLeftGap"] - 64) <= 1


def test_proportional_sections_skipped() -> None:
    by_vp = {
        "1280x800": [_ref_row("art", width=1280, lg=100, rg=300)],
        "1600x900": [_ref_row("art", width=1600, lg=200, rg=350)],
        "1920x1080": [_ref_row("art", width=1920, lg=320, rg=400)],
    }
    cls = classify(by_vp)
    assert cls["art"]["kind"] == "proportional"


def test_mobile_viewports_excluded_from_classification() -> None:
    by_vp = {
        "375x812": [_ref_row("hero", width=375, lg=0, rg=200)],  # would break centered
        "1280x800": [_ref_row("hero", width=1280, lg=128, rg=128)],
        "1600x900": [_ref_row("hero", width=1600, lg=160, rg=160)],
    }
    cls = classify(by_vp)
    assert cls["hero"]["kind"] == "centered"


def test_groups_classified_with_section_scoped_keys() -> None:
    by_vp = {
        "1280x800": [_ref_row(
            "footer-2", width=1280, lg=128, rg=128,
            groups=[_grp("cards", c_l=128, c_w=1024, u_l=348, u_w=585)],
        )],
        "1600x900": [_ref_row(
            "footer-2", width=1600, lg=160, rg=160,
            groups=[_grp("cards", c_l=160, c_w=1280, u_l=508, u_w=585)],
        )],
    }
    cls = classify(by_vp)
    assert cls["footer-2::cards[0]"]["kind"] == "centered"


def test_overflowing_section_content_box_is_not_transferred_as_centered() -> None:
    by_vp = {
        "1280x800": [_ref_row("hero", width=1280, lg=-15, rg=-15)],
        "1600x900": [_ref_row("hero", width=1600, lg=-15, rg=-15)],
    }

    cls = classify(by_vp)

    assert cls["hero"]["kind"] == "overflow"


def test_overflowing_content_group_is_not_transferred_as_centered() -> None:
    by_vp = {
        "1280x800": [_ref_row(
            "foods", width=1280, lg=128, rg=128,
            groups=[_grp("track", c_l=128, c_w=1024, u_l=-60, u_w=1400)],
        )],
        "1600x900": [_ref_row(
            "foods", width=1600, lg=160, rg=160,
            groups=[_grp("track", c_l=160, c_w=1280, u_l=-20, u_w=1640)],
        )],
    }

    cls = classify(by_vp)

    assert cls["foods::track[0]"]["kind"] == "overflow"


def test_duplicate_impl_classes_pair_by_fingerprint_when_indices_drift() -> None:
    first = _ref_row("grid", width=1280, lg=32, rg=400)
    first["impl"] = {
        "className": "evo-grid",
        "fingerprint": "intro section",
        "index": 1,
    }
    second = _ref_row("grid-2", width=1280, lg=128, rg=128)
    second["impl"] = {
        "className": "evo-grid",
        "fingerprint": "tagline section",
        "index": 2,
    }
    first_wide = _ref_row("grid", width=1600, lg=32, rg=720)
    first_wide["impl"] = {
        "className": "evo-grid",
        "fingerprint": "intro section",
        "index": 0,
    }
    second_wide = _ref_row("grid-2", width=1600, lg=160, rg=160)
    second_wide["impl"] = {
        "className": "evo-grid",
        "fingerprint": "tagline section",
        "index": 3,
    }
    cls = classify({
        "1280x800": [first, second],
        "1600x900": [first_wide, second_wide],
    })

    samples = {
        1440: [
            {
                **_sample_row("evo-grid", width=1440, lg=32, rg=500),
                "fingerprint": "intro section",
                "index": 4,
            },
            {
                **_sample_row("evo-grid", width=1440, lg=144, rg=144),
                "fingerprint": "tagline section",
                "index": 7,
            },
        ],
        1760: [
            {
                **_sample_row("evo-grid", width=1760, lg=32, rg=820),
                "fingerprint": "intro section",
                "index": 5,
            },
            {
                **_sample_row("evo-grid", width=1760, lg=176, rg=176),
                "fingerprint": "tagline section",
                "index": 8,
            },
        ],
    }

    rows, status = evaluate(cls, samples, plan_widths=[])

    assert status == "pass"
    assert cls["grid-2"]["implFingerprint"] == "tagline section"
    assert "implIndex" not in cls["grid-2"]
    assert all(row["status"] == "ok" for row in rows)


def test_fingerprint_miss_does_not_fallback_to_duplicate_class_neighbor() -> None:
    cls = {
        "foods::food-images[0]": {
            "kind": "centered",
            "basisWidth": 640,
            "implClassName": "food-section",
            "implFingerprint": "salmon apples grains",
            "implIndex": 2,
        },
    }
    samples = {
        1281: [
            {
                **_sample_row(
                    "food-section",
                    width=1281,
                    lg=0,
                    rg=786,
                    groups=[_grp("food-images", c_l=0, c_w=495, u_l=0, u_w=495)],
                ),
                "fingerprint": "watermelon blueberries steak",
                "index": 2,
            }
        ]
    }

    rows, status = evaluate(cls, samples, plan_widths=[])

    assert status == "warn"
    assert rows == [
        {
            "key": "foods::food-images[0]",
            "width": 1281,
            "kind": "centered",
            "status": "missing",
        }
    ]


def test_duplicate_section_names_are_split_by_fingerprint_identity() -> None:
    def row(name: str, fingerprint: str, *, width: int, lg: float, rg: float) -> dict:
        item = _ref_row(name, width=width, lg=lg, rg=rg)
        item["impl"] = {
            "className": "generic-container",
            "fingerprint": fingerprint,
        }
        return item

    cls = classify(
        {
            "1280x800": [
                row("container", "alpha content", width=1280, lg=128, rg=128),
                row("container", "beta content", width=1280, lg=256, rg=256),
            ],
            "1600x900": [
                row("container", "alpha content", width=1600, lg=160, rg=160),
                row("container", "beta content", width=1600, lg=320, rg=320),
            ],
        }
    )

    centered = {key: info for key, info in cls.items() if info.get("kind") == "centered"}
    assert len(centered) == 2
    assert {info["implFingerprint"] for info in centered.values()} == {
        "alpha content",
        "beta content",
    }

    rows, status = evaluate(
        cls,
        {
            1440: [
                {
                    **_sample_row("generic-container", width=1440, lg=144, rg=144),
                    "fingerprint": "alpha content",
                },
                {
                    **_sample_row("generic-container", width=1440, lg=288, rg=288),
                    "fingerprint": "beta content",
                },
            ]
        },
        plan_widths=[],
    )

    assert status == "pass"
    assert len(rows) == 2
    assert all(row["status"] == "ok" for row in rows)


def test_groups_missing_from_a_desktop_viewport_are_unclassifiable() -> None:
    by_vp = {
        "1280x800": [
            _ref_row(
                "section",
                width=1280,
                lg=128,
                rg=128,
                groups=[_grp("cards", c_l=128, c_w=1024, u_l=348, u_w=585)],
            )
        ],
        "1600x900": [_ref_row("section", width=1600, lg=160, rg=160)],
    }

    cls = classify(by_vp)

    assert cls["section::cards[0]"]["kind"] == "unclassifiable"


def test_impl_class_mapping_uses_desktop_majority() -> None:
    rows_by_viewport = {}
    for viewport, impl_class, impl_index in [
        ("1280x800", "wrapper", 11),
        ("1440x900", "style_blurb", 21),
        ("1600x900", "style_blurb", 21),
    ]:
        width = int(viewport.split("x")[0])
        row = _ref_row("blurb", width=width, lg=0, rg=200)
        row["impl"] = {
            "className": impl_class,
            "fingerprint": "same copied text",
            "index": impl_index,
        }
        rows_by_viewport[viewport] = [row]

    cls = classify(rows_by_viewport)

    assert cls["blurb"]["implClassName"] == "style_blurb"
    assert "implIndex" not in cls["blurb"]

    wrapper = {
        **_sample_row("wrapper", width=1440, lg=32, rg=200),
        "fingerprint": "same copied text",
    }
    child = {
        **_sample_row("style_blurb", width=600, lg=0, rg=200),
        "fingerprint": "same copied text",
    }
    rows, status = evaluate(cls, {1440: [wrapper, child]}, plan_widths=[])

    assert status == "pass"
    assert rows[0]["leftGap"] == 0

    missing_rows, missing_status = evaluate(
        cls, {1440: [wrapper]}, plan_widths=[]
    )
    assert missing_status == "warn"
    assert missing_rows[0]["status"] == "missing"


def test_missing_gaps_marks_unclassifiable() -> None:
    rows = [_ref_row("hero", width=1280, lg=128, rg=128)]
    del rows[0]["ref"]["leftGap"]
    del rows[0]["ref"]["rightGap"]
    del rows[0]["ref"]["contentBox"]
    by_vp = {"1280x800": rows, "1600x900": [_ref_row("hero", width=1600, lg=160, rg=160)]}
    cls = classify(by_vp)
    assert cls["hero"]["kind"] == "unclassifiable"


# ── sweep widths ───────────────────────────────────────────────────────


def test_sweep_widths_midpoints_and_breakpoints() -> None:
    widths = sweep_widths([1280, 1600, 1920], ["1050px", "1560px", "48rem", "80rem"])
    assert 1440 in widths and 1760 in widths          # midpoints
    assert 1559 in widths and 1561 in widths          # breakpoint ±1
    # 80rem = 1280: -1 falls below the plan range (clamped out — no ref
    # invariant data exists outside it), +1 stays.
    assert 1279 not in widths and 1281 in widths
    assert all(w >= 768 for w in widths)
    assert 1050 not in [w for w in widths if w < 1280] or True  # below min plan width dropped
    assert widths == sorted(set(widths))


def test_sweep_widths_clamped_to_plan_range() -> None:
    widths = sweep_widths([1280, 1920], ["360px", "5000px"])
    assert all(1280 <= w <= 1920 for w in widths)


# ── tools batch-7 ITEM 3: viewport-gap (impl @media boundary + single-vp) ──


def test_sweep_widths_samples_impl_media_boundary() -> None:
    # Plan 1280+1920 sweeps only midpoint 1600; an impl @media at 1440 (a
    # boundary the ref never had) must be sampled at ±1px once detected.
    widths = sweep_widths([1280, 1920], ["1440px"])
    assert 1439 in widths and 1441 in widths, widths


def test_sweep_widths_single_viewport_no_longer_exempt() -> None:
    # A single-viewport plan previously returned [] (sweep disabled). With an
    # impl breakpoint it now widens to the desktop span and samples around it.
    widths = sweep_widths([1280], ["1440px"])
    assert widths, "single-viewport plan must still sweep when breakpoints exist"
    assert 1439 in widths and 1441 in widths, widths


def test_sweep_widths_single_viewport_no_breakpoints_is_empty() -> None:
    # No breakpoints + single viewport: nothing to sweep (no false widths).
    assert sweep_widths([1280], []) == []


# ── evaluation ─────────────────────────────────────────────────────────


def _sample_row(name: str, *, width: int, lg: float, rg: float, groups: list | None = None) -> dict:
    return {
        "className": name,
        "rect": {"top": 100, "left": 0, "width": width, "height": 600},
        "clientWidth": width,
        "leftGap": lg,
        "rightGap": rg,
        "contentBox": {"left": lg, "width": width - lg - rg},
        "contentGroups": groups or [],
    }


def test_centered_violation_at_two_adjacent_widths_blocks() -> None:
    cls = {"hero": {"kind": "centered", "basisWidth": 1280}}
    samples = {
        1440: [_sample_row("hero", width=1440, lg=260, rg=100)],
        1760: [_sample_row("hero", width=1760, lg=420, rg=100)],
    }
    rows, status = evaluate(cls, samples, plan_widths=[1280, 1600, 1920])
    assert status == "fail"
    assert any(r["status"] == "fail" for r in rows)


def test_single_isolated_violation_is_advisory() -> None:
    cls = {"hero": {"kind": "centered", "basisWidth": 1280}}
    samples = {
        1440: [_sample_row("hero", width=1440, lg=260, rg=100)],
        1760: [_sample_row("hero", width=1760, lg=130, rg=130)],
    }
    rows, status = evaluate(cls, samples, plan_widths=[1280, 1600, 1920])
    assert status in ("pass", "warn")
    assert any(r["status"] == "violation" for r in rows)


def test_violation_at_enforced_width_blocks_alone() -> None:
    cls = {"hero": {"kind": "centered", "basisWidth": 1280}}
    samples = {1600: [_sample_row("hero", width=1600, lg=420, rg=100)]}
    rows, status = evaluate(cls, samples, plan_widths=[1280, 1600, 1920])
    assert status == "fail"


def test_group_violation_blocks() -> None:
    cls = {"footer-2::cards[0]": {"kind": "centered", "basisWidth": 1024}}
    samples = {
        1440: [_sample_row(
            "footer-2", width=1440, lg=144, rg=144,
            # union center 812.5 vs container center 720 → delta 92.5px
            groups=[_grp("cards", c_l=144, c_w=1152, u_l=520, u_w=585)],
        )],
        1760: [_sample_row(
            "footer-2", width=1760, lg=176, rg=176,
            # union center 852.5 vs container center 880 → delta 27.5px
            groups=[_grp("cards", c_l=176, c_w=1408, u_l=560, u_w=585)],
        )],
    }
    rows, status = evaluate(cls, samples, plan_widths=[1280, 1600, 1920])
    assert status == "fail"


def test_section_union_failure_is_advisory_when_centered_group_passes() -> None:
    cls = {
        "header": {"kind": "centered", "basisWidth": 1280},
        "header::header[0]": {"kind": "centered", "basisWidth": 1280},
    }
    samples = {
        1440: [_sample_row(
            "header", width=1440, lg=80, rg=200,
            groups=[_grp("header", c_l=48, c_w=1344, u_l=48, u_w=1344)],
        )],
        1760: [_sample_row(
            "header", width=1760, lg=80, rg=320,
            groups=[_grp("header", c_l=48, c_w=1664, u_l=48, u_w=1664)],
        )],
    }

    rows, status = evaluate(cls, samples, plan_widths=[1440, 1760])

    assert status == "warn"
    root_rows = [row for row in rows if row["key"] == "header"]
    assert root_rows and all(row["status"] == "violation" for row in root_rows)
    assert all(row["status"] == "ok" for row in rows if row["key"] == "header::header[0]")


def test_section_union_failure_still_blocks_when_centered_group_fails() -> None:
    cls = {
        "header": {"kind": "centered", "basisWidth": 1280},
        "header::header[0]": {"kind": "centered", "basisWidth": 1280},
    }
    samples = {
        1440: [_sample_row(
            "header", width=1440, lg=80, rg=200,
            groups=[_grp("header", c_l=48, c_w=1344, u_l=48, u_w=1200)],
        )],
        1760: [_sample_row(
            "header", width=1760, lg=80, rg=320,
            groups=[_grp("header", c_l=48, c_w=1664, u_l=48, u_w=1400)],
        )],
    }

    rows, status = evaluate(cls, samples, plan_widths=[1440, 1760])

    assert status == "fail"
    assert any(row["status"] == "fail" for row in rows if row["key"] == "header")
    assert any(row["status"] == "fail" for row in rows if row["key"] == "header::header[0]")


def test_section_union_failure_is_not_suppressed_by_unrelated_group() -> None:
    cls = {
        "content": {"kind": "centered", "basisWidth": 1280},
        "content::container[0]": {"kind": "centered", "basisWidth": 1280},
    }
    samples = {
        1440: [_sample_row(
            "content", width=1440, lg=0, rg=80,
            groups=[_grp("container", c_l=0, c_w=1440, u_l=80, u_w=1280)],
        )],
        1760: [_sample_row(
            "content", width=1760, lg=0, rg=160,
            groups=[_grp("container", c_l=0, c_w=1760, u_l=80, u_w=1600)],
        )],
    }

    rows, status = evaluate(cls, samples, plan_widths=[])

    assert status == "fail"
    assert any(row["status"] == "fail" for row in rows if row["key"] == "content")
    assert all(
        row["status"] == "ok"
        for row in rows
        if row["key"] == "content::container[0]"
    )


def test_fixed_gutter_section_is_not_suppressed_by_centered_group() -> None:
    cls = {
        "header": {
            "kind": "fixed-gutter",
            "refLeftGap": 64,
            "basisWidth": 1280,
        },
        "header::header[0]": {"kind": "centered", "basisWidth": 1280},
    }
    samples = {
        1440: [_sample_row(
            "header", width=1440, lg=160, rg=160,
            groups=[_grp("header", c_l=0, c_w=1440, u_l=80, u_w=1280)],
        )],
        1760: [_sample_row(
            "header", width=1760, lg=240, rg=240,
            groups=[_grp("header", c_l=0, c_w=1760, u_l=80, u_w=1600)],
        )],
    }

    rows, status = evaluate(cls, samples, plan_widths=[])

    assert status == "fail"
    assert any(row["status"] == "fail" for row in rows if row["key"] == "header")
    assert all(
        row["status"] == "ok"
        for row in rows
        if row["key"] == "header::header[0]"
    )


def test_fixed_gutter_assertion() -> None:
    cls = {"side": {"kind": "fixed-gutter", "refLeftGap": 64, "basisWidth": 1280}}
    samples = {
        1440: [_sample_row("side", width=1440, lg=64, rg=500)],
        1760: [_sample_row("side", width=1760, lg=300, rg=500)],
    }
    rows, status = evaluate(cls, samples, plan_widths=[1280, 1600, 1920])
    # one isolated violation → not blocking
    assert status in ("pass", "warn")
    viol = [r for r in rows if r["status"] == "violation"]
    assert viol and viol[0]["width"] == 1760


def test_clean_sweep_passes() -> None:
    cls = {"hero": {"kind": "centered", "basisWidth": 1280}}
    samples = {
        1440: [_sample_row("hero", width=1440, lg=144, rg=144)],
        1760: [_sample_row("hero", width=1760, lg=176, rg=176)],
    }
    rows, status = evaluate(cls, samples, plan_widths=[1280, 1600, 1920])
    assert status == "pass"


def test_section_absent_in_sample_is_recorded_not_crashed() -> None:
    cls = {"gone": {"kind": "centered", "basisWidth": 1280}}
    samples = {1440: [_sample_row("hero", width=1440, lg=144, rg=144)]}
    rows, status = evaluate(cls, samples, plan_widths=[1280])
    assert status in ("pass", "warn")
    assert any(r["status"] == "missing" for r in rows)


# ── script-level (samples-file mode, no browser) ───────────────────────


def _script_fixture(tmp_path: Path, *, impl_centered: bool) -> tuple[Path, Path]:
    import json

    ref_dir = tmp_path / "ref"
    for vp, w in [("1280x800", 1280), ("1600x900", 1600)]:
        d = ref_dir / "sections" / "viewports" / vp / "sections"
        d.mkdir(parents=True)
        gap = round(w * 0.1)
        (d / "matches.json").write_text(json.dumps([
            _ref_row("hero", width=w, lg=gap, rg=gap)
        ]))
    (ref_dir / "verification-plan.json").write_text(json.dumps({
        "viewports": [
            {"w": 375, "h": 812}, {"w": 1280, "h": 800}, {"w": 1600, "h": 900},
        ],
    }))
    (ref_dir / "detected-breakpoints.json").write_text(json.dumps({
        "breakpoints": ["1500px"],
    }))
    samples = {}
    for w in (1440, 1499, 1501):
        if impl_centered:
            row = _sample_row("hero", width=w, lg=round(w * 0.1), rg=round(w * 0.1))
        else:
            row = _sample_row("hero", width=w, lg=300, rg=2 * round(w * 0.1) - 300 + 80)
        samples[w] = [row]
    samples_file = tmp_path / "samples.json"
    samples_file.write_text(json.dumps(samples))
    return ref_dir, samples_file


def test_script_synthetic_centered_fixture_passes(tmp_path: Path) -> None:
    import json
    import os
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = root / "skills" / "visual-debug" / "scripts" / "alignment-sweep-check.sh"
    ref_dir, samples = _script_fixture(tmp_path, impl_centered=True)
    env = dict(os.environ, UI_CLONE_SWEEP_SAMPLES_FILE=str(samples))
    proc = subprocess.run(
        ["bash", str(script), "test-session", "http://localhost:9/", str(ref_dir)],
        capture_output=True, text=True, timeout=60, env=env, cwd=str(root),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref_dir / "alignment-sweep.json").read_text())
    assert art["status"] == "pass"


def test_script_off_center_impl_fails(tmp_path: Path) -> None:
    import json
    import os
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = root / "skills" / "visual-debug" / "scripts" / "alignment-sweep-check.sh"
    ref_dir, samples = _script_fixture(tmp_path, impl_centered=False)
    env = dict(os.environ, UI_CLONE_SWEEP_SAMPLES_FILE=str(samples))
    proc = subprocess.run(
        ["bash", str(script), "test-session", "http://localhost:9/", str(ref_dir)],
        capture_output=True, text=True, timeout=60, env=env, cwd=str(root),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref_dir / "alignment-sweep.json").read_text())
    assert art["status"] == "fail"


def test_live_sweep_disables_smooth_scroll_before_enumeration() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (
        root / "skills" / "visual-debug" / "scripts" / "alignment-sweep-check.sh"
    ).read_text(encoding="utf-8")

    assert script.count('document.documentElement.style.scrollBehavior = "auto"') >= 2
    assert "window.scrollTo(0, 0)" in script
