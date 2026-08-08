"""D21 (loop-nvti-1): an unescaped double quote inside a JS comment terminated
the PHASE2_TEMPLATE="…" assignment early — bash then executed the rest of the
JS as a command name, PHASE2 ended up EMPTY, the whole scroll/hover/scrub probe
never ran, and every PHASE2-dependent entry degraded to "element not found".
`bash -n` passes (the quotes pair up syntactically), so a source-level lint
cannot catch this class. This test drives the real script against a fake
agent-browser and asserts the phase-2 probe JS actually reaches the browser."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[2]
          / "skills" / "visual-debug" / "scripts" / "transition-fires-check.sh")

FAKE_AGENT_BROWSER = """#!/usr/bin/env bash
# Records every invocation; answers eval with an empty JSON object so the
# script proceeds through all phases without a real browser.
printf '%s\\n' "ARGS: $*" >> "$FAKE_AB_LOG"
for a in "$@"; do printf '%s\\n' "$a" >> "$FAKE_AB_LOG.args"; done
case " $* " in
  *" eval "*)
    if [[ "$*" == *"const CHUNK = new Set"* ]] \
      && [ "${FAKE_AB_PHASE2_ERROR:-0}" = "1" ]; then
      echo "synthetic phase2 failure" >&2
      exit 9
    fi
    if [[ "$*" == *"const CHUNK = new Set"* ]] \
      && [ "${FAKE_AB_PHASE2_QUOTED:-0}" = "1" ]; then
      echo '"{\\"0\\":{\\"found\\":true,\\"after\\":{\\"backgroundImage\\":\\"url(\\\\\\"x.png\\\\\\")\\"}}}"'
    elif [[ "$*" == *"const CHUNK = new Set"* ]] \
      && [ "${FAKE_AB_PHASE2_PARTIAL:-0}" = "1" ]; then
      echo '"{\\"0\\":{\\"found\\":true,\\"after\\":{}}}"'
    elif [[ "$*" == *"const CHUNK = new Set"* ]] \
      && [ "${FAKE_AB_PHASE2_FOUND:-0}" = "1" ]; then
      echo '"{\\"0\\":{\\"found\\":true,\\"after\\":{}}}"'
    elif [[ "$*" == *"const PRE = {}"* ]] \
      && [ "${FAKE_AB_PHASE1_TWO:-0}" = "1" ]; then
      echo '"{\\"0\\":{\\"found\\":true,\\"before\\":{}},\\"1\\":{\\"found\\":true,\\"before\\":{}}}"'
    elif [[ "$*" == *"const PRE = {}"* ]] \
      && [ "${FAKE_AB_PHASE1_NOT_FOUND:-0}" = "1" ]; then
      echo '"{\\"0\\":{\\"found\\":false}}"'
    elif [[ "$*" == *"const PRE = {}"* ]] \
      && [ "${FAKE_AB_PHASE1_FOUND:-0}" = "1" ]; then
      echo '"{\\"0\\":{\\"found\\":true,\\"before\\":{}}}"'
    else
      echo '"{}"'
    fi
    ;;
  *) echo '{}' ;;
esac
exit 0
"""

SCRUB_SPEC = {
    "transitions": [
        {
            "id": "t_scrub",
            "trigger": "scroll-scrub",
            "target": ".scroller",
            "animation": {"type": "scrub", "scrub": True,
                          "property": "transform:translateY"},
        }
    ]
}

IO_REVEAL_SPEC = {
    "transitions": [
        {
            "id": "cardstack-inview-reveal",
            "trigger": "viewport in-view reveal (intersection observer flip)",
            "target": ".home-card-stack .style_card__axFC1",
            "animation": {"property": "transform/opacity reveal"},
        }
    ]
}


def _run_script(
    tmp_path: Path,
    spec: dict,
    *,
    extra_env: dict[str, str] | None = None,
    preseed_artifact: bool = False,
) -> tuple:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps(spec), encoding="utf-8")
    if preseed_artifact:
        (ref / "transition-fires.json").write_text(
            '{"status":"stale-sentinel"}\n',
            encoding="utf-8",
        )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "agent-browser"
    fake.write_text(FAKE_AGENT_BROWSER, encoding="utf-8")
    fake.chmod(0o755)
    log = tmp_path / "ab.log"
    log.write_text("", encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_AB_LOG"] = str(log)
    env.update(extra_env or {})
    proc = subprocess.run(
        ["bash", str(SCRIPT), "t-sess", "http://impl.invalid", str(ref)],
        capture_output=True, text=True, timeout=300, env=env,
    )
    args_log = (log.with_suffix(".log.args")
                if log.with_suffix(".log.args").is_file()
                else Path(str(log) + ".args"))
    payload = args_log.read_text(encoding="utf-8") if args_log.is_file() else ""
    return proc, payload


def test_phase2_eval_failure_is_setup_error(tmp_path: Path) -> None:
    proc, _ = _run_script(
        tmp_path,
        SCRUB_SPEC,
        extra_env={
            "FAKE_AB_PHASE1_FOUND": "1",
            "FAKE_AB_PHASE2_ERROR": "1",
        },
        preseed_artifact=True,
    )

    assert proc.returncode == 2, proc.stderr
    assert "phase2 chunk eval failed" in proc.stderr
    assert "synthetic phase2 failure" in proc.stderr
    assert not (tmp_path / "ref" / "transition-fires.json").exists()


def test_phase2_missing_found_record_is_setup_error(tmp_path: Path) -> None:
    proc, _ = _run_script(
        tmp_path,
        SCRUB_SPEC,
        extra_env={"FAKE_AB_PHASE1_FOUND": "1"},
        preseed_artifact=True,
    )

    assert proc.returncode == 2, proc.stderr
    assert "phase2 chunk incomplete" in proc.stderr
    assert "t_scrub" in proc.stderr
    assert not (tmp_path / "ref" / "transition-fires.json").exists()


def test_phase2_partial_multi_entry_chunk_is_setup_error(tmp_path: Path) -> None:
    spec = {
        "transitions": [
            {
                **SCRUB_SPEC["transitions"][0],
                "id": "t_scrub_0",
            },
            {
                **SCRUB_SPEC["transitions"][0],
                "id": "t_scrub_1",
                "target": ".scroller-2",
            },
        ]
    }
    proc, _ = _run_script(
        tmp_path,
        spec,
        extra_env={
            "FAKE_AB_PHASE1_TWO": "1",
            "FAKE_AB_PHASE2_PARTIAL": "1",
            "TF_CHUNK_SIZE": "2",
        },
        preseed_artifact=True,
    )

    assert proc.returncode == 2, proc.stderr
    assert "phase2 chunk incomplete" in proc.stderr
    assert "t_scrub_1" in proc.stderr
    assert not (tmp_path / "ref" / "transition-fires.json").exists()


def test_phase2_double_encoded_quotes_are_decoded_losslessly(
    tmp_path: Path,
) -> None:
    proc, _ = _run_script(
        tmp_path,
        SCRUB_SPEC,
        extra_env={
            "FAKE_AB_PHASE1_FOUND": "1",
            "FAKE_AB_PHASE2_QUOTED": "1",
        },
    )

    assert proc.returncode != 2, proc.stderr
    artifact = json.loads(
        (tmp_path / "ref" / "transition-fires.json").read_text(encoding="utf-8")
    )
    assert artifact["entries"], artifact


def test_phase1_found_false_reaches_normal_verdict(tmp_path: Path) -> None:
    proc, _ = _run_script(
        tmp_path,
        SCRUB_SPEC,
        extra_env={"FAKE_AB_PHASE1_NOT_FOUND": "1"},
    )

    assert proc.returncode != 2, proc.stderr
    artifact = json.loads(
        (tmp_path / "ref" / "transition-fires.json").read_text(encoding="utf-8")
    )
    observed = str(artifact["entries"][0].get("observed", ""))
    assert "element not found" in observed


def test_phase2_complete_found_record_reaches_verdict(tmp_path: Path) -> None:
    proc, _ = _run_script(
        tmp_path,
        SCRUB_SPEC,
        extra_env={
            "FAKE_AB_PHASE1_FOUND": "1",
            "FAKE_AB_PHASE2_FOUND": "1",
        },
    )

    assert proc.returncode != 2, proc.stderr
    artifact = json.loads(
        (tmp_path / "ref" / "transition-fires.json").read_text(encoding="utf-8")
    )
    observed = str(artifact["entries"][0].get("observed", ""))
    assert "element not found" not in observed


def test_phase2_probe_js_reaches_the_browser(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps(SCRUB_SPEC), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "agent-browser"
    fake.write_text(FAKE_AGENT_BROWSER, encoding="utf-8")
    fake.chmod(0o755)
    log = tmp_path / "ab.log"
    log.write_text("", encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_AB_LOG"] = str(log)
    proc = subprocess.run(
        ["bash", str(SCRIPT), "t-sess", "http://impl.invalid", str(ref)],
        capture_output=True, text=True, timeout=300, env=env,
    )

    # The quoting-regression signature: bash tries to execute the JS tail as a
    # command ("File name too long" / "command not found" mentioning JS text).
    assert "File name too long" not in proc.stderr, proc.stderr[-2000:]

    args_log = (log.with_suffix(".log.args")
                if log.with_suffix(".log.args").is_file()
                else Path(str(log) + ".args"))
    payload = args_log.read_text(encoding="utf-8") if args_log.is_file() else ""
    assert "globalSweep" in payload, (
        "phase-2 scrub probe JS never reached the browser — PHASE2_TEMPLATE "
        "is empty or truncated (the D21 quoting class); stderr tail: "
        + proc.stderr[-1500:]
    )


def test_reveal_reprobe_js_reaches_the_browser(tmp_path: Path) -> None:
    """C4 (D21 class): the fresh-context reveal re-probe is its own
    double-quoted JS eval — guard it against the same quoting-truncation bug.
    With the fake browser returning an empty object, an IO reveal entry reads
    identical-final in the main pass, so REVEAL_TARGETS flags it and the
    re-probe eval must actually reach the browser (its snapR marker appears in
    the args log), not silently truncate."""
    proc, payload = _run_script(tmp_path, IO_REVEAL_SPEC)

    assert "File name too long" not in proc.stderr, proc.stderr[-2000:]
    assert "snapR" in payload, (
        "reveal re-probe eval JS never reached the browser — it is empty or "
        "truncated (the D21 quoting class), or REVEAL_TARGETS did not flag the "
        "identical-final IO reveal; stderr tail: " + proc.stderr[-1500:]
    )


def test_video_probe_observes_autoplay_without_forcing_playback() -> None:
    """F2: the video probe must OBSERVE natural autoplay, never force it. Calling
    `v.play()` (after `v.muted = true`) advances currentTime even when the clone's
    <video> carries no autoplay/muted attributes — so a video that never plays for
    a real user still earns a passing transition. That measures 'the asset can
    decode', not 'the impl autoplays like the ref'. A faithful clone autoplays on
    its own (muted+autoplay attrs or a mount controller), so the probe must let it
    run untouched and read the natural currentTime delta."""
    txt = SCRIPT.read_text(encoding="utf-8")
    assert "v.play()" not in txt, (
        "video probe must not force v.play() — it masks a non-autoplaying clone "
        "video as a passing transition (F2 false-pass)"
    )


def test_explicit_swiper_next_drives_real_instance() -> None:
    """A swiper-next obligation must exercise the captured imperative action.

    Waiting for autoplay makes the outcome depend on entry order and the
    chunk-wide wait budget: the first 4s Swiper can pass while the second is
    sampled too early and falsely reported dead.
    """
    txt = SCRIPT.read_text(encoding="utf-8")
    assert '"trigger": str(t.get("trigger", ""))' in txt
    assert (
        "String(e.trigger || '').trim().toLowerCase() === 'swiper-next'"
        in txt
    )
    assert "inst0 && typeof inst0.slideNext === 'function'" in txt
    assert "inst0.slideNext();" in txt


def test_non_explicit_carousel_preserves_natural_autoplay_observation() -> None:
    """Other carousel specs still wait for their own autoplay cycle."""
    txt = SCRIPT.read_text(encoding="utf-8")
    branch = txt[txt.index("const explicitSwiperNext ="):txt.index(
        "rec.after = snap(el, e);", txt.index("const explicitSwiperNext =")
    )]
    assert "} else {" in branch
    assert "inst0.params.autoplay.delay" in branch
    assert "carWaitBudget -= carWait;" in branch
    assert "await wait(carWait);" in branch


def test_hover_snapshot_includes_font_weight_and_pseudo_state() -> None:
    """Header hovers commonly change only weight or an arrow pseudo-element."""
    txt = SCRIPT.read_text(encoding="utf-8")
    assert "s.fontWeight = cs.fontWeight;" in txt
    assert "s.pseudoBefore = pseudoSig('::before');" in txt
    assert "s.pseudoAfter = pseudoSig('::after');" in txt
    assert '"fontWeight", "pseudoBefore", "pseudoAfter",' in txt
    pseudo_block = txt[txt.index("const pseudoSig ="):txt.index(
        "s.pseudoBefore =", txt.index("const pseudoSig =")
    )]
    assert "ps.content" not in pseudo_block, (
        "computed pseudo content is intentionally excluded from the stable "
        "hover signature; generated quote-heavy content is not a motion channel"
    )


def test_hidden_hover_target_opens_visible_nav_owner_first() -> None:
    """A submenu link cannot be pointed at until its visible nav item is open."""
    txt = SCRIPT.read_text(encoding="utf-8")
    owner_hover = (
        'agent-browser --session "$SESSION" hover '
        '"[data-tf-hover-owner=\'$HIDX\']"'
    )
    target_scroll = 'agent-browser --session "$SESSION" scrollintoview "$HSEL"'
    target_hover = 'agent-browser --session "$SESSION" hover "$HSEL"'
    assert "let cur = el && el.parentElement;" in txt
    assert "/nav|menu|gnb|lnb/i.test(label)" in txt
    assert "cur.matches('li,[role=menuitem]')" in txt
    assert txt.index(owner_hover) < txt.index(target_scroll) < txt.index(target_hover)


HASHED_HOVER_SPEC = {
    "transitions": [
        {
            "id": "link-hover-opacity",
            "trigger": "hover",
            "target": ".nav_link__aB3xy",
            "animation": {"property": "opacity"},
        }
    ]
}


def test_hover_reprobe_tries_class_star_fallback_for_hashed_target() -> None:
    """F6: the real-pointer hover re-probe (the only pass that can activate CSS
    :hover) must carry the same [class*=]/hash-strip selector fallbacks the main
    pass has. A CSS-module-hashed hover target (.nav_link__aB3xy) resolves in the
    main pass via fallback but, without it here, the CDP hover pass resolves
    nothing and a working CSS-only hover is judged dead (the persistent
    link-hover-opacity FAIL). The re-probe must attempt a [class*="nav_link"]
    fallback selector."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        proc, payload = _run_script(Path(td), HASHED_HOVER_SPEC)
    assert '[class*="nav_link"]' in payload or "[class*=\\\"nav_link\\\"]" in payload, (
        "hover re-probe never tried a class*= fallback for the hashed target; "
        f"payload sample:\n{payload[:1500]}"
    )


def test_pre_snapshot_loop_has_hash_strip_fallback() -> None:
    """F7: the PRE (pristine before-state) snapshot loop must resolve hash-only
    CSS-module targets with the SAME Fix-78b hash-strip the main pass uses, else a
    target that matches only after the module-hash suffix is stripped gets no
    pristine before-state and a one-shot reveal reads final->final and false-fails.
    Scoped to the PRE loop (const PRE = {} .. mount-sweep) so we assert the
    fallback is present exactly where the pristine snapshot is taken."""
    txt = SCRIPT.read_text(encoding="utf-8")
    start = txt.index("const PRE = {")
    # the PRE loop ends where the mount-sweep scroll begins (scrollTo(0, y))
    end = txt.index("scrollTo(0, y)", start)
    pre_block = txt[start:end]
    assert pre_block, "could not isolate the PRE snapshot loop"
    assert "__[A-Za-z0-9_-]{4,}" in pre_block, (
        "PRE snapshot loop lacks the Fix-78b hash-strip fallback (F7); a hash-only "
        "target gets no pristine before-state and false-fails as dead"
    )


def test_hover_reprobe_result_omits_quote_bearing_selector() -> None:
    """F6 companion (found by live validation): the hover re-probe must NOT echo
    the resolved selector in its JSON result. F6's fallbacks are [class*="…"]
    selectors containing double quotes; round-tripped through agent-browser's
    double-JSON encoding + unwrap they arrive as \\" and make the HOVER_PATCH blob
    invalid JSON, so the merge's json.loads() throws and silently drops the patch
    (except: continue) — a hashed CSS-module hover then false-fails even though the
    fallback resolved and CDP hover fired. The merge only needs found + after."""
    txt = SCRIPT.read_text(encoding="utf-8")
    assert "selector: atob(" not in txt, (
        "hover re-probe must not echo the quote-bearing fallback selector — it "
        "corrupts the HOVER_PATCH JSON round-trip and silently drops the patch"
    )
