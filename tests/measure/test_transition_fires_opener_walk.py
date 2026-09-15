"""Click-to-open opener walk in transition-fires-check.sh (real-pointer hover pass).

Entries 03/04/05 on navercorp.com/tech/innovation target buttons inside
.btn-lang__list and .search-tab__box, panels that are display:none until
.btn-selected / .btn-search is CLICKED. The owner walk only hovers nav-like
ancestors, so those entries measured a non-rendered element and reported
"style change on hover=False" although their :hover rules were in the
stylesheet. The fallback walk clicks a disclosure control to reveal the
target, measures, and hands the page back.

Two layers are covered here:

* the shared library (TF_OPENER_LIB_JS) under node against a fake DOM —
  candidate refusals, ranking, the no-re-click set, the attempt cap, the
  fingerprint and the toggle -> Escape -> outside restore ladder;
* the shell orchestration against a fake agent-browser — the walk runs only
  for entries the normal pass could not measure, a failed in-page hand-back
  is followed by a fresh navigate before the next entry, every outcome lands
  in the artifact (hoverOpener per entry, hoverOpenerRestore summary), a
  walked entry is judged (revealed, idle) vs (revealed, hovered) from a
  settled idle baseline, every attempt that did not reveal the target is
  handed back through a fresh navigate (its in-page restore and fpChanged are
  recorded, not trusted; route residue and URL changes force one too), a
  refused re-baseline cannot pass on scroll position alone, and every eval
  blob the walk sends survives shell expansion as valid JS.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "skills" / "visual-debug" / "scripts" / "transition-fires-check.sh"
)
HARNESS = Path(__file__).with_name("opener_walk_harness.js")
LIB_START = "read -r -d '' TF_OPENER_LIB_JS <<'JSEOF' || true\n"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _lib_source() -> str:
    txt = SCRIPT.read_text(encoding="utf-8")
    start = txt.index(LIB_START) + len(LIB_START)
    end = txt.index("\nJSEOF\n", start)
    return txt[start:end]


@pytest.fixture(scope="module")
def walk() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib.js"
        lib.write_text(_lib_source(), encoding="utf-8")
        check = subprocess.run(
            ["node", "--check", str(lib)], capture_output=True, text=True, timeout=60,
        )
        assert check.returncode == 0, check.stderr
        proc = subprocess.run(
            ["node", str(HARNESS), str(lib)], capture_output=True, text=True, timeout=120,
        )
    assert proc.returncode == 0, proc.stderr
    out = cast(dict[str, Any], json.loads(proc.stdout))
    errors = {k: v["error"] for k, v in out.items() if isinstance(v, dict) and "error" in v}
    assert not errors, errors
    return out


# ── library: candidate predicate ─────────────────────────────────────────


def test_refusals_cover_links_submits_selection_controls_and_steppers(
    walk: dict[str, Any],
) -> None:
    r = walk["refusals"]
    assert r["submit"] == "form-submit"
    assert r["bareInForm"] == "form-submit", "a bare <button> inside <form> defaults to submit"
    assert r["inLink"] == "link"
    assert r["tab"].startswith("role-") or r["tab"] == "selection-state"
    assert r["plainInTablist"] == "selection-group"
    assert r["pressed"] == "selection-state"
    assert r["switchRole"] == "role-switch"
    assert r["dialogOpener"] == "opens-dialog"
    assert r["alreadyOpen"] == "already-open"
    assert r["swiperNext"].startswith("label-")
    assert r["loadMore"].startswith("label-")
    assert r["modalTrigger"].startswith("label-")
    assert r["hidden"] == "not-rendered"


def test_refusals_admit_recognisable_toggles_and_typed_plain_buttons(
    walk: dict[str, Any],
) -> None:
    r = walk["refusals"]
    for name in ("plain", "typedInForm", "expanded", "menuButton", "summary", "roleButton"):
        assert r[name] == "", f"{name} should be clickable, got {r[name]!r}"


# ── library: ranking and the two navercorp shapes ────────────────────────


def test_lang_list_opens_via_sole_control_and_toggles_back(walk: dict[str, Any]) -> None:
    s = walk["lang_toggle"]
    assert s["walk"]["opened"] is True
    assert s["walk"]["pickedBy"] == "sole-control"
    assert s["walk"]["opener"] == "button.nclick-target.btn-selected"
    assert s["walk"]["depth"] == 2, "li -> ul.btn-lang__list -> div.btn-lang"
    assert s["revealedDuring"] is True
    assert s["close"] == {"closed": True, "via": "toggle", "routed": 0, "blocked": 0}
    assert s["listRenderedAfterClose"] is False
    assert s["langClassAfterClose"] == "btn-lang"
    assert s["selectedClicks"] == 2 and s["searchClicks"] == 0


def test_search_box_opens_via_name_affinity_not_the_other_button(
    walk: dict[str, Any],
) -> None:
    s = walk["search_affinity"]
    assert s["walk"]["opened"] is True
    assert s["walk"]["pickedBy"] == "name-affinity"
    assert s["walk"]["opener"] == "button.nclick-target.btn-search"
    assert s["selectedClicks"] == 0, "btn-selected shares no name token with search-tab"
    assert s["close"]["closed"] is True and s["close"]["via"] == "toggle"
    assert s["tabRenderedAfterClose"] is False


def test_ambiguous_level_clicks_nothing(walk: dict[str, Any]) -> None:
    s = walk["ambiguous_not_clicked"]
    assert s["walk"]["opened"] is False
    assert s["walk"]["attempts"] == []
    assert "ambiguous" in s["walk"]["levels"]
    assert s["searchClicks"] == 0 and s["selectedClicks"] == 0


def test_aria_controls_outranks_an_unrelated_toggle(walk: dict[str, Any]) -> None:
    s = walk["aria_controls_wins"]
    assert s["walk"]["pickedBy"] == "aria-controls"
    assert s["toggleClicks"] == 0 and s["namedClicks"] == 2
    assert s["close"]["closed"] is True


def test_details_summary_is_closed_through_the_open_property(walk: dict[str, Any]) -> None:
    s = walk["details_summary"]
    assert s["walk"]["pickedBy"] == "toggle"
    assert s["close"]["via"] == "toggle"
    assert s["openAfterClose"] is False
    assert s["summaryClicks"] == 1, "close sets details.open=false instead of re-clicking"


def test_walk_is_a_noop_when_a_seed_is_already_rendered(walk: dict[str, Any]) -> None:
    s = walk["already_rendered_noop"]
    assert s["walk"]["opened"] is False and s["walk"]["attempts"] == []
    assert s["selectedClicks"] == 0


# ── library: the three non-idempotent control classes ────────────────────


def test_open_only_modal_is_closed_by_escape_not_assumed_toggled(
    walk: dict[str, Any],
) -> None:
    s = walk["open_only_modal"]
    assert s["walk"]["opened"] is True
    assert s["close"]["closed"] is True and s["close"]["via"] == "escape"
    assert s["modalRenderedAfterClose"] is False


def test_outside_click_rung_closes_a_click_outside_panel(walk: dict[str, Any]) -> None:
    s = walk["outside_click_closes"]
    assert s["close"]["closed"] is True and s["close"]["via"] == "outside"
    assert s["panelRenderedAfterClose"] is False


def test_step_advancing_control_stops_the_walk_and_reports_dirty(
    walk: dict[str, Any],
) -> None:
    s = walk["step_advancing_stops_walk"]
    assert s["walk"]["opened"] is False
    assert s["walk"]["dirty"] is True
    assert len(s["walk"]["attempts"]) == 1
    assert s["walk"]["attempts"][0]["restored"] is None
    assert s["goClicks"] == 2, "one open click plus the ladder's toggle rung, nothing more"
    assert s["otherClicks"] == 0, "no further opener is tried on a page that was not handed back"


def test_tab_controls_are_never_clicked(walk: dict[str, Any]) -> None:
    s = walk["radio_tab_never_clicked"]
    assert s["refusal"] != ""
    assert s["tabAClicks"] == 0 and s["tabBClicks"] == 0
    assert s["walk"]["attempts"] == []


# ── library: bounded mutation ────────────────────────────────────────────


def test_failed_opener_is_not_reclicked_at_higher_levels(walk: dict[str, Any]) -> None:
    s = walk["no_reclick_across_levels"]
    assert len(s["walk"]["attempts"]) == 1
    assert s["walk"]["attempts"][0]["restored"] == "toggle"
    # the side panel is shown/hidden through an inline style only, which the
    # fingerprint deliberately ignores: the toggle rung "restored" a
    # fingerprint that never moved, and the attempt says so
    assert s["walk"]["attempts"][0]["fpChanged"] is False
    assert s["xClicks"] == 2
    assert s["sideRendered"] is False


def test_attempt_reports_whether_the_fingerprint_moved_before_it_was_restored(
    walk: dict[str, Any],
) -> None:
    """restored:'toggle' means the fingerprint equals fp0 after the rung. That
    is a hand-back only when the fingerprint left fp0 first; fpChanged tells
    the two apart so the shell can force a navigate on the blind one."""
    s = walk["visible_then_blind_restore"]
    attempts = s["walk"]["attempts"]
    assert [a["opener"] for a in attempts] == ["button.visible-toggle", "button.blind-toggle"]
    assert [a["restored"] for a in attempts] == ["toggle", "toggle"]
    assert [a["fpChanged"] for a in attempts] == [True, False]
    assert s["walk"]["dirty"] is False and s["walk"]["opened"] is False
    assert s["innerClass"] == "inner" and s["blindRendered"] is False
    # a walk that revealed nothing leaves the close eval nothing to undo, so
    # the shell's hand-back for these attempts can only be the navigate
    assert s["close"]["closed"] is False and s["close"]["reason"] == "no-opener"


def test_settled_reading_waits_for_two_equal_samples(walk: dict[str, Any]) -> None:
    """The revealed idle baseline is taken through tfSettled: a panel still
    opening is polled until two consecutive samples agree; one that never
    stops (a marquee) is reported unstable within the cap; a still one costs
    exactly two samples."""
    s = walk["settled_reading"]
    assert s["opening"] == {"stable": True, "value": {"height": 40}, "polls": 5}
    assert s["marquee"]["stable"] is False
    assert s["marquee"]["polls"] == 9, "2000ms cap at 250ms: eight waits after the first sample"
    assert s["still"] == {"stable": True, "value": {"height": 40}, "polls": 2}
    assert s["stillPolls"] == 2


def test_attempt_cap_bounds_the_number_of_controls_clicked(walk: dict[str, Any]) -> None:
    s = walk["attempt_cap"]
    assert len(s["walk"]["attempts"]) == 2
    assert s["clicks"] == [2, 2, 0]


def test_fingerprint_catches_body_class_residue_and_escalates(walk: dict[str, Any]) -> None:
    s = walk["scroll_lock_residue"]
    assert s["close"]["closed"] is True
    assert s["close"]["via"] == "escape", "toggle hid the panel but left body.no-scroll"
    assert s["bodyClassAfterClose"] == "page"


# ── shell orchestration against a fake agent-browser ─────────────────────

FAKE_AGENT_BROWSER = r"""#!/usr/bin/env bash
# Records every invocation (records separated by \x1e) and every eval blob to
# its own file, and models the page as a two-bit state machine so the hover
# pass in transition-fires-check.sh can be driven without a browser:
#   open    — the walk revealed the panel (set by a walk answer that opened,
#             cleared by a close answer that closed and by every navigate)
#   hovered — the real pointer rests on the target (set by `hover` on anything
#             but the owner marker, cleared by `mouse move` and navigate)
#   residue — a walk attempt left a side effect the page fingerprint cannot
#             see (FAKE_HWALK_RESIDUE=1): a sibling panel shown through an
#             inline style, a SPA route. Only a navigate clears it; a close
#             eval cannot (nothing was opened, so there is no window.__tfOpener
#             to undo). While it stands, the snapshot of the entry named by
#             FAKE_RESIDUE_TARGET (default 1) answers FAKE_HSNAP_RESIDUE — that
#             entry's target is rendered by the residue, not by its own hover.
# Snapshot answers are chosen from that state, so a measurement the script
# takes in the wrong state — hidden instead of revealed, hovered instead of
# idle — is visible to the tests. Tests that set no open-state answers get the
# closed-state answer for every snapshot, as before.
printf '%s\x1e' "$*" >> "$FAKE_DIR/calls.log"
args=("$@")
if [ "${args[0]}" = "--session" ]; then args=("${args[@]:2}"); fi
case "${args[0]}" in
  navigate) rm -f "$FAKE_DIR/open" "$FAKE_DIR/hovered" "$FAKE_DIR/residue"; echo '{}'; exit 0 ;;
  mouse) rm -f "$FAKE_DIR/hovered"; echo '{}'; exit 0 ;;
  hover)
    case "${args[1]}" in *data-tf-hover-owner*) ;; *) : > "$FAKE_DIR/hovered" ;; esac
    echo '{}'; exit 0 ;;
  eval) ;;
  *) echo '{}'; exit 0 ;;
esac
js="${args[1]}"
n=$(( $(cat "$FAKE_DIR/count" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$FAKE_DIR/count"
printf '%s' "$js" > "$FAKE_DIR/eval-$n.js"
open=0; [ -e "$FAKE_DIR/open" ] && open=1
hovered=0; [ -e "$FAKE_DIR/hovered" ] && hovered=1
# $1 closed-state answer, $2 open+idle answer, $3 open+hovered answer (names
# of env vars; unset open-state answers fall back to the closed-state one).
snap_answer() {
  if [ "$open" = 1 ]; then
    if [ "$hovered" = 1 ] && [ -n "${!3:-}" ]; then printf '%s\n' "${!3}"; return; fi
    if [ -n "${!2:-}" ]; then printf '%s\n' "${!2}"; return; fi
  fi
  printf '%s\n' "${!1}"
}
case "$js" in
  *"const PRE = {};"*) printf '%s\n' "$FAKE_PHASE1" ;;
  *"const CHUNK = new Set("*) printf '%s\n' "$FAKE_PHASE2" ;;
  *"TF_STAGE = 'opener-walk'"*)
    if [ "${FAKE_HWALK_LOST:-0}" = "1" ]; then echo "execution context destroyed" >&2; exit 9; fi
    if [ "${FAKE_HWALK_OPENS:-0}" = "1" ]; then : > "$FAKE_DIR/open"; fi
    if [ "${FAKE_HWALK_RESIDUE:-0}" = "1" ]; then : > "$FAKE_DIR/residue"; fi
    printf '%s\n' "$FAKE_HWALK" ;;
  *"TF_STAGE = 'opener-close'"*)
    if [ "$open" = 1 ]; then
      if [ "${FAKE_HCLOSE_CLOSES:-1}" = "1" ]; then rm -f "$FAKE_DIR/open"; fi
      printf '%s\n' "$FAKE_HCLOSE"
    else
      # tfOpenerClose finds no window.__tfOpener when nothing was opened.
      printf '%s\n' "$FAKE_HCLOSE_NOOPENER"
    fi ;;
  *"TF_STAGE = 'opener-verify'"*) printf '%s\n' "$FAKE_HVERIFY" ;;
  *"TF_STAGE = 'opener-baseline'"*) snap_answer FAKE_HSNAP FAKE_HBASE_OPEN_IDLE FAKE_HBASE_OPEN_HOVER ;;
  *"after: snap(el, { kind: 'hover' })"*)
    if [ -e "$FAKE_DIR/residue" ] && [ -n "${FAKE_HSNAP_RESIDUE:-}" ]; then
      case "$js" in
        *"[data-tf-hover-target=\"${FAKE_RESIDUE_TARGET:-1}\"]"*) printf '%s\n' "$FAKE_HSNAP_RESIDUE"; exit 0 ;;
      esac
    fi
    snap_answer FAKE_HSNAP FAKE_HSNAP_OPEN_IDLE FAKE_HSNAP_OPEN_HOVER ;;
  *"{ href: location.href }"*) printf '%s\n' "$FAKE_HREF" ;;
  *) echo '"{}"' ;;
esac
exit 0
"""

WALK_SPEC = {
    "transitions": [
        {
            "id": "lang-item-hover",
            "trigger": "hover",
            "target": ".header .btn-lang__list button",
            "animation": {"property": "backgroundColor"},
        },
        {
            "id": "search-box-hover",
            "trigger": "hover",
            "target": ".header .search-tab__box",
            "animation": {"property": "backgroundColor"},
        },
    ]
}

HREF = "http://impl.invalid/"
WALK_RESULT = {
    "opened": True, "dirty": False, "opener": "button.nclick-target.btn-selected",
    "depth": 2, "pickedBy": "sole-control",
    "attempts": [{"opener": "button.nclick-target.btn-selected", "depth": 2,
                  "pickedBy": "sole-control", "revealed": True, "restored": None}],
    "levels": ["no-candidate", "no-candidate"], "routed": 0, "blocked": 0,
    "stage": "opener-walk", "href": HREF,
}
NAV_RE = re.compile(r"^--session \S+ navigate ")


FAKE_SLEEP = """#!/usr/bin/env bash
printf 'sleep %s\\x1e' "$*" >> "$FAKE_DIR/calls.log"
"""


def _enc(obj: object) -> str:
    # agent-browser JSON-encodes a JavaScript string result; the script's
    # unwrap() decodes that outer layer.
    return json.dumps(json.dumps(obj))


def _run(
    tmp_path: Path,
    env_extra: dict[str, str],
    *,
    spec: dict[str, Any] | None = None,
    splash_summary: dict[str, Any] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str], dict[str, Any], Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps(spec or WALK_SPEC), encoding="utf-8")
    if splash_summary is not None:
        (ref / "states" / "splash").mkdir(parents=True)
        (ref / "states" / "splash" / "summary.json").write_text(
            json.dumps(splash_summary), encoding="utf-8",
        )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "agent-browser"
    fake.write_text(FAKE_AGENT_BROWSER, encoding="utf-8")
    fake.chmod(0o755)
    # The script's waits go through `sleep`, an external command; logging it
    # into the same call log makes the wait itself observable (and instant).
    fake_sleep = bin_dir / "sleep"
    fake_sleep.write_text(FAKE_SLEEP, encoding="utf-8")
    fake_sleep.chmod(0o755)
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_DIR"] = str(fake_dir)
    env["WAIT_MS"] = "1"
    env["FAKE_HREF"] = _enc({"href": HREF})
    env["FAKE_HSNAP"] = _enc({"found": True, "rendered": True, "hidden": 0, "after": {}})
    env["FAKE_HWALK"] = _enc(WALK_RESULT)
    env["FAKE_HCLOSE"] = _enc({"closed": True, "via": "toggle", "routed": 0, "blocked": 0,
                               "stage": "opener-close", "href": HREF})
    env["FAKE_HVERIFY"] = _enc({"stage": "opener-verify", "hidden": 1, "href": HREF})
    env["FAKE_HCLOSE_NOOPENER"] = _enc({"closed": False, "via": None, "reason": "no-opener",
                                        "routed": 0, "blocked": 0, "stage": "opener-close",
                                        "href": HREF})
    env["FAKE_PHASE1"] = _enc({})
    env["FAKE_PHASE2"] = _enc({})
    env.update(env_extra)
    # The fake's page state follows what its own answers claim happened.
    walk_answer = cast(dict[str, Any], json.loads(json.loads(env["FAKE_HWALK"])))
    env["FAKE_HWALK_OPENS"] = "1" if walk_answer.get("opened") else "0"
    close_answer = cast(dict[str, Any], json.loads(json.loads(env["FAKE_HCLOSE"])))
    env["FAKE_HCLOSE_CLOSES"] = "1" if close_answer.get("closed") else "0"
    proc = subprocess.run(
        ["bash", str(SCRIPT), "t-sess", "http://impl.invalid", str(ref)],
        capture_output=True, text=True, timeout=300, env=env,
    )
    log = fake_dir / "calls.log"
    calls = [c for c in log.read_text(encoding="utf-8").split("\x1e") if c] if log.is_file() else []
    artifact_path = ref / "transition-fires.json"
    artifact: dict[str, Any] = {}
    if artifact_path.is_file():
        artifact = cast(dict[str, Any], json.loads(artifact_path.read_text(encoding="utf-8")))
    return proc, calls, artifact, fake_dir


def _stage_indices(calls: list[str], stage: str) -> list[int]:
    return [i for i, c in enumerate(calls) if f"TF_STAGE = '{stage}'" in c]


def _navigate_indices(calls: list[str]) -> list[int]:
    return [i for i, c in enumerate(calls) if NAV_RE.match(c)]


def _sleep_seconds(calls: list[str]) -> list[tuple[int, str]]:
    return [(i, c[len("sleep "):]) for i, c in enumerate(calls) if c.startswith("sleep ")]


def _by_id(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("id")): row for row in artifact.get("entries", []) if isinstance(row, dict)}


def test_rendered_target_never_triggers_the_walk(tmp_path: Path) -> None:
    """Property 4: an entry the normal pass renders is never walked, so a
    passing verdict cannot be touched by a click."""
    proc, calls, artifact, _ = _run(tmp_path, {})
    assert _stage_indices(calls, "opener-walk") == [], "walk eval issued for a rendered target"
    assert _stage_indices(calls, "opener-close") == []
    assert "hoverOpenerRestore" not in artifact
    assert all("hoverOpener" not in row for row in _by_id(artifact).values())


def test_hidden_matches_with_a_measured_delta_do_not_trigger_the_walk(tmp_path: Path) -> None:
    """A rendered match that already changed vs. baseline is a measured entry
    even when other matches of the selector are hidden."""
    _, calls, _, _ = _run(tmp_path, {
        "FAKE_HSNAP": _enc({"found": True, "rendered": True, "hidden": 2, "after": {"color": "red"}}),
    })
    assert _stage_indices(calls, "opener-walk") == []


def test_hidden_matches_without_a_delta_trigger_the_walk(tmp_path: Path) -> None:
    """Entry 04: `.btn-lang button[class^=btn-]` also matches the visible
    .btn-selected, which has no delta; the hidden .btn-ko/.btn-en carry the
    hover rule. The walk must run so the revealed match is the one measured."""
    _, calls, artifact, _ = _run(tmp_path, {
        "FAKE_HSNAP": _enc({"found": True, "rendered": True, "hidden": 2, "after": {}}),
    })
    assert len(_stage_indices(calls, "opener-walk")) == 2
    rows = _by_id(artifact)
    assert rows["lang-item-hover"]["hoverOpener"]["opened"] is True


def test_walk_runs_after_the_owner_hover_and_measures_then_closes(tmp_path: Path) -> None:
    proc, calls, artifact, _ = _run(tmp_path, {
        "FAKE_HSNAP": _enc({"found": True, "rendered": False, "hidden": 1, "after": {}}),
    })
    walks = _stage_indices(calls, "opener-walk")
    closes = _stage_indices(calls, "opener-close")
    assert len(walks) == 2 and len(closes) == 2
    first_walk = walks[0]
    owner_hover = next(i for i, c in enumerate(calls) if "hover [data-tf-hover-owner='0']" in c)
    assert owner_hover < first_walk, "the walk is a fallback AFTER the existing owner hover"
    # opened -> re-hover the (re-marked) target and re-snapshot BEFORE closing
    between = calls[first_walk + 1:closes[0]]
    assert any("hover [data-tf-hover-target='0']" in c for c in between)
    assert any("after: snap(el, { kind: 'hover' })" in c for c in between)
    # a verified toggle close needs no navigate: the only navigate calls are the
    # ones the script issues regardless of the walk
    baseline_navs = len(_navigate_indices(_run(tmp_path / "b", {})[1]))
    assert len(_navigate_indices(calls)) == baseline_navs
    rows = _by_id(artifact)
    assert rows["lang-item-hover"]["hoverOpener"]["restored"] == "toggle"
    assert rows["lang-item-hover"]["hoverOpener"]["opener"] == "button.nclick-target.btn-selected"
    summary = artifact["hoverOpenerRestore"]
    assert summary["walked"] == ["lang-item-hover", "search-box-hover"]
    assert summary["opened"] == ["lang-item-hover", "search-box-hover"]
    assert summary["escalated"] == [] and summary["navigated"] == [] and summary["failed"] == []
    assert "WARNING" not in proc.stdout


def test_escalated_close_is_recorded(tmp_path: Path) -> None:
    _, _, artifact, _ = _run(tmp_path, {
        "FAKE_HSNAP": _enc({"found": True, "rendered": False, "hidden": 1, "after": {}}),
        "FAKE_HCLOSE": _enc({"closed": True, "via": "escape", "routed": 0, "blocked": 0,
                             "stage": "opener-close", "href": HREF}),
    })
    rows = _by_id(artifact)
    assert rows["lang-item-hover"]["hoverOpener"]["restored"] == "escape"
    assert artifact["hoverOpenerRestore"]["escalated"] == ["lang-item-hover", "search-box-hover"]
    assert artifact["hoverOpenerRestore"]["navigated"] == []


def test_unverified_close_forces_a_navigate_before_the_next_entry(tmp_path: Path) -> None:
    """Property 1 + 2: when the ladder cannot confirm the hand-back the page is
    re-navigated before the next entry is probed, verified, and reported."""
    proc, calls, artifact, fake_dir = _run(tmp_path, {
        "FAKE_HSNAP": _enc({"found": True, "rendered": False, "hidden": 1, "after": {}}),
        "FAKE_HCLOSE": _enc({"closed": False, "via": None, "routed": 0, "blocked": 0,
                             "stage": "opener-close", "href": HREF}),
    })
    closes = _stage_indices(calls, "opener-close")
    verifies = _stage_indices(calls, "opener-verify")
    navs = _navigate_indices(calls)
    assert len(closes) == 2 and len(verifies) == 2
    for close_i, verify_i in zip(closes, verifies, strict=True):
        assert any(close_i < n < verify_i for n in navs), (
            "no navigate between the failed close and its verification"
        )
    # the second entry's owner hover happens only after the first reset
    second_owner = next(i for i, c in enumerate(calls) if "hover [data-tf-hover-owner='1']" in c)
    assert verifies[0] < second_owner
    rows = _by_id(artifact)
    assert rows["lang-item-hover"]["hoverOpener"]["restored"] == "navigate"
    assert rows["lang-item-hover"]["hoverOpener"]["closeVia"] is None
    assert artifact["hoverOpenerRestore"]["navigated"] == ["lang-item-hover", "search-box-hover"]
    assert artifact["hoverOpenerRestore"]["failed"] == []
    assert "WARNING: transition-fires hover opener for lang-item-hover" in proc.stdout
    # every eval the script sent — HOWNER, HSNAP, walk, close, verify included —
    # is valid JavaScript after shell expansion
    blobs = sorted(fake_dir.glob("eval-*.js"))
    assert blobs
    stages = [b for b in blobs if "TF_STAGE = 'opener-" in b.read_text(encoding="utf-8")]
    assert len(stages) >= 6
    for blob in blobs:
        check = subprocess.run(
            ["node", "--check", str(blob)], capture_output=True, text=True, timeout=60,
        )
        assert check.returncode == 0, f"{blob.name}: {check.stderr[:800]}"


def test_changed_url_after_close_is_not_accepted_as_restored(tmp_path: Path) -> None:
    """location.* navigation cannot be intercepted in-page; a URL that differs
    from the pre-walk one falls through to the navigate rung."""
    _, calls, artifact, _ = _run(tmp_path, {
        "FAKE_HSNAP": _enc({"found": True, "rendered": False, "hidden": 1, "after": {}}),
        "FAKE_HCLOSE": _enc({"closed": True, "via": "toggle", "routed": 0, "blocked": 0,
                             "stage": "opener-close", "href": "http://impl.invalid/search?q=x"}),
    })
    assert len(_stage_indices(calls, "opener-verify")) == 2
    rows = _by_id(artifact)
    assert rows["lang-item-hover"]["hoverOpener"]["restored"] == "navigate"
    assert rows["lang-item-hover"]["hoverOpener"]["closeVia"] == "toggle"


def test_lost_walk_eval_is_treated_as_a_click_that_must_be_undone(tmp_path: Path) -> None:
    """A click that navigates or reloads kills the eval; nothing comes back.
    That is not a no-op — it is handed back through the navigate rung."""
    _, calls, artifact, _ = _run(tmp_path, {
        "FAKE_HSNAP": _enc({"found": True, "rendered": False, "hidden": 1, "after": {}}),
        "FAKE_HWALK_LOST": "1",
        "FAKE_HCLOSE": _enc({"closed": False, "via": None, "reason": "no-opener",
                             "routed": 0, "blocked": 0, "stage": "opener-close", "href": HREF}),
    })
    assert len(_stage_indices(calls, "opener-close")) == 2
    rows = _by_id(artifact)
    info = rows["lang-item-hover"]["hoverOpener"]
    assert info["walk"] == "eval-lost"
    assert info["restored"] == "navigate"


def test_failed_restore_names_the_entries_measured_afterwards(tmp_path: Path) -> None:
    """Property 2: even the navigate rung is verified; when it does not bring
    the idle state back the artifact says so and lists the entries that ran on
    the mutated page."""
    proc, _, artifact, _ = _run(tmp_path, {
        "FAKE_HSNAP": _enc({"found": True, "rendered": False, "hidden": 1, "after": {}}),
        "FAKE_HCLOSE": _enc({"closed": False, "via": None, "routed": 0, "blocked": 0,
                             "stage": "opener-close", "href": HREF}),
        "FAKE_HVERIFY": _enc({"stage": "opener-verify", "hidden": 0, "href": HREF}),
    })
    rows = _by_id(artifact)
    assert rows["lang-item-hover"]["hoverOpener"]["restored"] == "failed"
    summary = artifact["hoverOpenerRestore"]
    assert summary["failed"] == ["lang-item-hover", "search-box-hover"]
    assert summary["entriesAfterUnrestored"] == ["search-box-hover"]
    assert "was NOT restored" in proc.stdout


# ── shell orchestration: what a walked entry is judged on ────────────────
#
# The walk's purpose is to measure a target that is display:none until an
# opener is clicked. The verdict must then compare (revealed, not hovered)
# against (revealed, hovered): a stale hidden-state baseline turns the reveal
# itself (0 -> 40px) into a "hover delta", and a clone that wired the click
# but has no :hover rule passes the hover gate on exactly the entries the
# walk exists for.

SNAP_HIDDEN = {
    "opacity": 1, "transform": "none", "height": 0, "top": 0, "width": 0,
    "color": "rgb(255, 255, 255)", "backgroundColor": "rgba(0, 0, 0, 0)",
    "borderColor": "rgb(0, 0, 0)", "outlineColor": "rgb(0, 0, 0)",
    "textDecorationColor": "rgb(255, 255, 255)", "boxShadow": "none", "filter": "none",
    "backgroundImage": "none", "fontWeight": "400",
    "pseudoBefore": "1|none|rgb(0, 0, 0)|rgba(0, 0, 0, 0)|rgb(0, 0, 0)|auto|auto",
    "pseudoAfter": "1|none|rgb(0, 0, 0)|rgba(0, 0, 0, 0)|rgb(0, 0, 0)|auto|auto",
    "childSig": "",
}
SNAP_OPEN_IDLE = {**SNAP_HIDDEN, "height": 40, "top": 120, "width": 88}
SNAP_OPEN_HOVER = {**SNAP_OPEN_IDLE, "backgroundColor": "rgb(3, 199, 90)"}
SINGLE_SPEC = {"transitions": [WALK_SPEC["transitions"][0]]}


def _walked_page(*, hover_rule: bool) -> dict[str, str]:
    """A page whose target is display:none until the walk opens it. With
    hover_rule the revealed target repaints under the pointer; without it the
    revealed target looks the same hovered or not."""
    hovered = SNAP_OPEN_HOVER if hover_rule else SNAP_OPEN_IDLE
    return {
        "FAKE_PHASE1": _enc({"0": {"found": True, "before": SNAP_HIDDEN}}),
        "FAKE_PHASE2": _enc({"0": {"found": True, "after": SNAP_HIDDEN}}),
        "FAKE_HSNAP": _enc({"found": True, "rendered": False, "hidden": 1, "after": SNAP_HIDDEN}),
        "FAKE_HSNAP_OPEN_IDLE": _enc(
            {"found": True, "rendered": True, "hidden": 0, "after": SNAP_OPEN_IDLE}),
        "FAKE_HSNAP_OPEN_HOVER": _enc(
            {"found": True, "rendered": True, "hidden": 0, "after": hovered}),
        "FAKE_HBASE_OPEN_IDLE": _enc(
            {"found": True, "rendered": True, "stable": True, "before": SNAP_OPEN_IDLE}),
        "FAKE_HBASE_OPEN_HOVER": _enc(
            {"found": True, "rendered": True, "stable": True, "before": hovered}),
    }


def test_revealed_target_without_a_hover_rule_fails_the_gate(tmp_path: Path) -> None:
    """A clone that opens the panel on click but styles nothing on hover has
    no hover transition; the reveal geometry must not stand in for one."""
    _, _, artifact, _ = _run(tmp_path, _walked_page(hover_rule=False), spec=SINGLE_SPEC)
    row = _by_id(artifact)["lang-item-hover"]
    assert row["hoverOpener"]["opened"] is True, "the walk did not run on this entry"
    assert row["status"] == "fail", row.get("observed")
    assert artifact["status"] != "pass"


def test_revealed_target_with_a_hover_rule_passes_the_gate(tmp_path: Path) -> None:
    """The same page with a real :hover repaint passes — and only because the
    baseline was taken with the pointer parked: a baseline taken under the
    pointer would read hovered -> hovered and fail a correct clone."""
    _, _, artifact, _ = _run(tmp_path, _walked_page(hover_rule=True), spec=SINGLE_SPEC)
    row = _by_id(artifact)["lang-item-hover"]
    assert row["hoverOpener"]["opened"] is True
    assert row["status"] == "pass", row.get("observed")


def test_revealed_target_whose_open_animation_never_settles_is_not_rebaselined(
    tmp_path: Path,
) -> None:
    """The idle baseline is snapped only once the revealed target has stopped
    moving. When it never does within the cap, the reading is not an idle
    state and cannot serve as one: the entry keeps its pre-walk record (no
    hover delta measurable), and the artifact says why."""
    page = _walked_page(hover_rule=True)
    page["FAKE_HBASE_OPEN_IDLE"] = _enc(
        {"found": True, "rendered": True, "stable": False, "before": SNAP_OPEN_IDLE})
    _, _, artifact, _ = _run(tmp_path, page, spec=SINGLE_SPEC)
    row = _by_id(artifact)["lang-item-hover"]
    assert row["hoverOpener"]["opened"] is True
    assert row["hoverOpener"]["rebaseline"] == "unstable"
    assert row["status"] == "fail", row.get("observed")
    settled = _run(tmp_path / "s", _walked_page(hover_rule=True), spec=SINGLE_SPEC)[2]
    assert _by_id(settled)["lang-item-hover"]["hoverOpener"]["rebaseline"] == "revealed"


SNAP_UNRENDERED_SCROLLED = {**SNAP_HIDDEN, "top": 120}


@pytest.mark.parametrize(("idle_baseline", "note"), [
    pytest.param({"found": True, "rendered": True, "stable": False, "before": SNAP_OPEN_IDLE},
                 "unstable", id="unstable"),
    pytest.param({"found": True, "rendered": False, "stable": True, "before": SNAP_HIDDEN},
                 "no-idle-baseline", id="no-idle-baseline"),
])
def test_pre_walk_fallback_record_cannot_pass_on_scroll_position_alone(
    tmp_path: Path, idle_baseline: dict[str, Any], note: str,
) -> None:
    """Auditor probes T1/T2: when the revealed re-baseline is refused, the
    entry falls back to its pre-walk record. That record's `after` was taken
    after scrollintoview while the PHASE1 `before` was taken at scroll-top, so
    for any below-fold target `top` differs with no :hover rule involved. The
    hover verdict must not count viewport `top` (the reveal branch already
    excludes it for the same reason), or the fallback passes a clone whose
    revealed target repaints nothing under the pointer."""
    page = _walked_page(hover_rule=False)
    page["FAKE_HSNAP"] = _enc(
        {"found": True, "rendered": False, "hidden": 1, "after": SNAP_UNRENDERED_SCROLLED})
    page["FAKE_HBASE_OPEN_IDLE"] = _enc(idle_baseline)
    _, _, artifact, _ = _run(tmp_path, page, spec=SINGLE_SPEC)
    row = _by_id(artifact)["lang-item-hover"]
    assert row["hoverOpener"]["opened"] is True
    assert row["status"] == "fail", row.get("observed")
    assert artifact["status"] != "pass"
    assert row["hoverOpener"]["rebaseline"] == note


def test_attempts_handed_back_inside_the_walk_are_still_navigated(tmp_path: Path) -> None:
    """navercorp-esg-sustainability 04/05/06: every opener the walk clicked
    failed to reveal the target and reported restored:'toggle' with the page
    fingerprint having moved and come back (fpChanged). That is the strongest
    report the walk can give, and it is still not a hand-back: the fingerprint
    cannot see an open-only inline-style panel the click showed (auditor probe
    T5), so the restore proves the transient class is gone, not that the page
    is idle. Every attempt is handed back through a fresh navigate before the
    next entry, exactly as before fpChanged existed, and the artifact records
    why. An earlier version of this test asserted the opposite — no close, no
    navigate — and pinned the hole."""
    walk = {
        **WALK_RESULT, "opened": False, "opener": None, "depth": None, "pickedBy": None,
        "attempts": [
            {"opener": "button.nclick-target.btn-selected", "depth": 2,
             "pickedBy": "sole-control", "revealed": False, "restored": "toggle",
             "fpChanged": True},
            {"opener": "button.nclick-target.btn-search", "depth": 3,
             "pickedBy": "sole-control", "revealed": False, "restored": "toggle",
             "fpChanged": True},
        ],
    }
    proc, calls, artifact, _ = _run(tmp_path, {
        "FAKE_HSNAP": _enc({"found": True, "rendered": False, "hidden": 1, "after": {}}),
        "FAKE_HWALK": _enc(walk),
    })
    walks = _stage_indices(calls, "opener-walk")
    verifies = _stage_indices(calls, "opener-verify")
    assert len(walks) == 2 and len(verifies) == 2
    assert len(_stage_indices(calls, "opener-close")) == 2
    navs = _navigate_indices(calls)
    for walk_i, verify_i in zip(walks, verifies, strict=True):
        assert any(walk_i < n < verify_i for n in navs), "attempt not handed back by navigate"
    second_owner = next(i for i, c in enumerate(calls) if "hover [data-tf-hover-owner='1']" in c)
    assert verifies[0] < second_owner, "second entry probed before the first was handed back"
    rows = _by_id(artifact)
    info = rows["lang-item-hover"]["hoverOpener"]
    assert len(info["attempts"]) == 2 and info["opened"] is False
    assert all(a["fpChanged"] is True for a in info["attempts"])
    assert info["forcedNavigate"] == ["unrevealed-attempt"]
    assert info["closeVia"] is None, "tfOpenerClose had nothing to undo (no-opener)"
    assert info["restored"] == "navigate"
    summary = artifact["hoverOpenerRestore"]
    assert summary["walked"] == ["lang-item-hover", "search-box-hover"]
    assert summary["navigated"] == ["lang-item-hover", "search-box-hover"]
    assert summary["failed"] == []
    assert "WARNING: transition-fires hover opener for lang-item-hover" in proc.stdout
    assert "unrevealed-attempt" in proc.stdout


def test_attempt_the_walk_could_not_hand_back_is_still_navigated(tmp_path: Path) -> None:
    """Guard for the rule above: an attempt with no restoring rung (the walk
    stopped dirty) still forces the hand-back ladder and a fresh navigate."""
    walk = {
        **WALK_RESULT, "opened": False, "opener": None, "depth": None, "pickedBy": None,
        "dirty": True,
        "attempts": [{"opener": "button.btn-go", "depth": 1, "pickedBy": "sole-control",
                      "revealed": False, "restored": None, "fpChanged": True}],
    }
    proc, calls, artifact, _ = _run(tmp_path, {
        "FAKE_HSNAP": _enc({"found": True, "rendered": False, "hidden": 1, "after": {}}),
        "FAKE_HWALK": _enc(walk),
    }, spec=SINGLE_SPEC)
    assert len(_stage_indices(calls, "opener-close")) == 1
    assert len(_stage_indices(calls, "opener-verify")) == 1
    rows = _by_id(artifact)
    assert rows["lang-item-hover"]["hoverOpener"]["restored"] == "navigate"
    assert artifact["hoverOpenerRestore"]["navigated"] == ["lang-item-hover"]
    assert "WARNING: transition-fires hover opener for lang-item-hover" in proc.stdout


_HANDED_BACK = {"opener": "button.nclick-target.btn-search", "depth": 2,
                "pickedBy": "sole-control", "revealed": False, "restored": "toggle",
                "fpChanged": True}


_NOT_OPENED = {"opened": False, "opener": None, "depth": None, "pickedBy": None}


@pytest.mark.parametrize(("walk_delta", "reasons"), [
    pytest.param({**_NOT_OPENED, "attempts": [_HANDED_BACK]}, ["unrevealed-attempt"],
                 id="fingerprint-round-trip"),
    pytest.param({**_NOT_OPENED, "attempts": [{**_HANDED_BACK, "fpChanged": False}]},
                 ["unrevealed-attempt", "fp-blind"], id="fingerprint-never-moved"),
    pytest.param({**_NOT_OPENED,
                  "attempts": [{k: v for k, v in _HANDED_BACK.items() if k != "fpChanged"}]},
                 ["unrevealed-attempt", "fp-blind"], id="fingerprint-not-reported"),
    pytest.param({**_NOT_OPENED, "attempts": [_HANDED_BACK], "routed": 1},
                 ["unrevealed-attempt", "routed"], id="router-push"),
    pytest.param({**_NOT_OPENED, "attempts": [_HANDED_BACK], "href": "http://impl.invalid/search"},
                 ["unrevealed-attempt", "href-changed"], id="url-changed"),
    # the walk DID open on its only click, but the page routed under it: the
    # verified toggle close is not accepted either
    pytest.param({"routed": 1}, ["routed"], id="opened-but-routed"),
    pytest.param({"href": "http://impl.invalid/search"}, ["href-changed"],
                 id="opened-but-url-changed"),
])
def test_residue_invisible_to_the_fingerprint_forces_a_navigate(
    tmp_path: Path, walk_delta: dict[str, Any], reasons: list[str],
) -> None:
    """A restored:'toggle' report is only as good as the signal it was
    verified against, and the fingerprint cannot see an open-only inline-style
    panel whether or not it moved on the click. Any attempt that did not
    reveal the target, a router push, or a changed URL means the page may
    still hold the click, and the only hand-back verified against the idle
    page is a fresh navigate — whatever the close eval says."""
    walk = {**WALK_RESULT, **walk_delta}
    proc, calls, artifact, _ = _run(tmp_path, {
        "FAKE_HSNAP": _enc({"found": True, "rendered": False, "hidden": 1, "after": {}}),
        "FAKE_HWALK": _enc(walk),
    }, spec=SINGLE_SPEC)
    walk_i = _stage_indices(calls, "opener-walk")[0]
    verify_i = _stage_indices(calls, "opener-verify")[0]
    assert any(walk_i < n < verify_i for n in _navigate_indices(calls)), "no re-navigate"
    info = _by_id(artifact)["lang-item-hover"]["hoverOpener"]
    assert info["forcedNavigate"] == reasons
    assert info["restored"] == "navigate"
    assert artifact["hoverOpenerRestore"]["navigated"] == ["lang-item-hover"]
    assert "WARNING: transition-fires hover opener for lang-item-hover" in proc.stdout


@pytest.mark.parametrize(("fp_changed", "reasons"), [
    pytest.param(False, ["unrevealed-attempt", "fp-blind"], id="blind-first-attempt"),
    pytest.param(True, ["unrevealed-attempt"], id="round-trip-first-attempt"),
])
def test_forced_navigate_outranks_a_close_the_fingerprint_confirmed(
    tmp_path: Path, fp_changed: bool, reasons: list[str],
) -> None:
    """A failed first attempt followed by an opener that did reveal: the close
    ladder confirms the panel shut (seeds hidden, fingerprint back), but the
    first attempt's residue is invisible to that check whether its fingerprint
    never moved or moved and came back. The close runs (and is recorded), and
    the page is re-navigated anyway."""
    walk = {
        **WALK_RESULT,
        "attempts": [
            {"opener": "button.other-toggle", "depth": 1, "pickedBy": "sole-control",
             "revealed": False, "restored": "toggle", "fpChanged": fp_changed},
            {**cast(list[dict[str, Any]], WALK_RESULT["attempts"])[0], "fpChanged": True},
        ],
    }
    _, calls, artifact, _ = _run(tmp_path, {
        **_walked_page(hover_rule=True), "FAKE_HWALK": _enc(walk),
    }, spec=SINGLE_SPEC)
    assert len(_stage_indices(calls, "opener-close")) == 1
    assert len(_stage_indices(calls, "opener-verify")) == 1
    row = _by_id(artifact)["lang-item-hover"]
    assert row["hoverOpener"]["closeVia"] == "toggle"
    assert row["hoverOpener"]["forcedNavigate"] == reasons
    assert row["hoverOpener"]["restored"] == "navigate"
    assert row["status"] == "pass", "the entry's own measurement is untouched by the hand-back"


def _two_hidden_entries() -> dict[str, str]:
    """Both WALK_SPEC targets are display:none in the idle page."""
    hidden_before = {"0": {"found": True, "before": SNAP_HIDDEN},
                     "1": {"found": True, "before": SNAP_HIDDEN}}
    hidden_after = {"0": {"found": True, "after": SNAP_HIDDEN},
                    "1": {"found": True, "after": SNAP_HIDDEN}}
    return {
        "FAKE_PHASE1": _enc(hidden_before),
        "FAKE_PHASE2": _enc(hidden_after),
        "FAKE_HSNAP": _enc({"found": True, "rendered": False, "hidden": 1, "after": SNAP_HIDDEN}),
    }


@pytest.mark.parametrize("fp_changed", [
    pytest.param(False, id="fingerprint-blind"),
    pytest.param(True, id="fingerprint-round-trip"),
])
def test_attempt_residue_cannot_leak_a_reveal_into_the_next_entry(
    tmp_path: Path, fp_changed: bool,
) -> None:
    """An attempt that revealed nothing reports restored:'toggle' when the
    fingerprint equals fp0 after the rung. That equality is not a hand-back:

    * fingerprint-blind — the side effect was never in the fingerprint (a
      sibling panel shown through an inline style, a SPA route), so the first
      rung "restores" a fingerprint that never moved;
    * fingerprint-round-trip (auditor probe T5) — the click stamped a transient
      class on the opener or body (the fingerprint moved) and the toggle rung
      took it off again (it came back), while an open-only inline-style panel
      the same click showed is still open. fpChanged is true, restored is
      'toggle', and the residue is exactly as real as in the blind case.

    Left uncleared, the residue renders the NEXT entry's target, which is then
    measured against its hidden PHASE1 baseline: the reveal geometry reads as
    a hover delta and a clone with no :hover rule passes. Every attempt is
    handed back through a fresh navigate before the next entry, as it was
    before the fpChanged signal existed."""
    walk = {
        **WALK_RESULT, "opened": False, "opener": None, "depth": None, "pickedBy": None,
        "attempts": [{"opener": "button.nclick-target.btn-search", "depth": 2,
                      "pickedBy": "sole-control", "revealed": False, "restored": "toggle",
                      "fpChanged": fp_changed}],
    }
    _, calls, artifact, _ = _run(tmp_path, {
        **_two_hidden_entries(),
        "FAKE_HWALK": _enc(walk),
        "FAKE_HWALK_RESIDUE": "1",
        # the search box, rendered by the residue, looks the same hovered or not
        "FAKE_HSNAP_RESIDUE": _enc(
            {"found": True, "rendered": True, "hidden": 0, "after": SNAP_OPEN_IDLE}),
    })
    rows = _by_id(artifact)
    assert rows["search-box-hover"]["status"] == "fail", rows["search-box-hover"].get("observed")
    assert artifact["status"] != "pass"
    first_walk = _stage_indices(calls, "opener-walk")[0]
    second_owner = next(i for i, c in enumerate(calls) if "hover [data-tf-hover-owner='1']" in c)
    assert any(first_walk < n < second_owner for n in _navigate_indices(calls)), (
        "residue of the first entry's attempt carried into the second entry"
    )
    info = rows["lang-item-hover"]["hoverOpener"]
    assert info["restored"] == "navigate"
    assert "unrevealed-attempt" in info["forcedNavigate"]


def test_post_navigate_settle_follows_the_measured_ref_settle(tmp_path: Path) -> None:
    """The verify after a forced navigate must wait for the page the reference
    was measured to need, not a fixed 2s: navercorp-esg-sustainability was
    still moving at the 5048ms capture cap. A capped measurement is a floor."""
    _, calls, artifact, _ = _run(tmp_path, {
        "FAKE_HSNAP": _enc({"found": True, "rendered": False, "hidden": 1, "after": {}}),
        "FAKE_HCLOSE": _enc({"closed": False, "via": None, "routed": 0, "blocked": 0,
                             "stage": "opener-close", "href": HREF}),
    }, spec=SINGLE_SPEC, splash_summary={
        "checked": True, "durationMs": 5048, "timedOut": True, "reason": "wall-clock-cap",
    })
    info = _by_id(artifact)["lang-item-hover"]["hoverOpener"]
    assert info["restored"] == "navigate"
    # measured settle rounded up to whole seconds, plus a one-second margin
    expected = math.ceil(5048 / 1000) + 1
    verify_i = _stage_indices(calls, "opener-verify")[0]
    nav_i = max(n for n in _navigate_indices(calls) if n < verify_i)
    waited = [secs for i, secs in _sleep_seconds(calls) if nav_i < i < verify_i]
    assert waited == [str(expected)], f"sleep between navigate and verify: {waited}"
    assert info["navigateSettleS"] == expected


def test_post_navigate_settle_never_undercuts_the_wait_floor(tmp_path: Path) -> None:
    """The measured settle is an upgrade on the WAIT_MS floor, never a discount:
    when the measurement itself is lost (the helper python died — this host OOMs
    children under browser pressure) the wait must still be the ceil(WAIT_MS)
    the pass used before the measurement existed, not a flat 2s. A shorter wait
    verifies a page that is still moving and records a restore that did not
    happen."""
    shim_venv = tmp_path / "shim-venv"
    (shim_venv / "bin").mkdir(parents=True)
    shim = shim_venv / "bin" / "python"
    # A python that works, except for the one call that measures the settle.
    shim.write_text(
        "#!/usr/bin/env bash\n"
        "for a in \"$@\"; do\n"
        "  case \"$a\" in */states/splash/summary.json) exit 1 ;; esac\n"
        "done\n"
        f"exec {sys.executable} \"$@\"\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    _, calls, artifact, _ = _run(tmp_path, {
        "VIRTUAL_ENV": str(shim_venv),
        "WAIT_MS": "4000",
        "FAKE_HSNAP": _enc({"found": True, "rendered": False, "hidden": 1, "after": {}}),
        "FAKE_HCLOSE": _enc({"closed": False, "via": None, "routed": 0, "blocked": 0,
                             "stage": "opener-close", "href": HREF}),
    }, spec=SINGLE_SPEC)
    info = _by_id(artifact)["lang-item-hover"]["hoverOpener"]
    assert info["restored"] == "navigate"
    verify_i = _stage_indices(calls, "opener-verify")[0]
    nav_i = max(n for n in _navigate_indices(calls) if n < verify_i)
    waited = [secs for i, secs in _sleep_seconds(calls) if nav_i < i < verify_i]
    assert waited == [str(math.ceil(4000 / 1000))], (
        f"sleep between navigate and verify undercuts the WAIT_MS floor: {waited}"
    )


def test_malformed_attempts_answer_still_counts_as_a_click(tmp_path: Path) -> None:
    """A walk answer whose `attempts` is non-empty but not a list of attempt
    records — a truncated or malformed eval answer, which is what a browser
    daemon under memory pressure produces — is still a page that took a click
    and must be handed back. The rule predates every later refinement: any
    attempt at all means the click ladder runs and, when it cannot verify the
    restore, a fresh navigate does. Dropping such a walk on the floor leaves
    the opened panel standing for the next entry, whose display:none baseline
    then reads the residue as a hover delta and PASSES with no :hover rule."""
    malformed = {
        "stage": "opener-walk", "opened": False, "dirty": False,
        # the list of attempt records came back as a bare selector string
        "attempts": "button.btn-search",
        "levels": [], "routed": 0, "blocked": 0, "href": HREF,
    }
    _, calls, artifact, _ = _run(tmp_path, {
        "FAKE_HSNAP": _enc({"found": True, "rendered": False, "hidden": 1, "after": {}}),
        "FAKE_HWALK": _enc(malformed),
    }, spec=SINGLE_SPEC)
    walk_i = _stage_indices(calls, "opener-walk")[0]
    close_i = _stage_indices(calls, "opener-close")
    assert close_i and close_i[0] > walk_i, (
        "no close eval after a walk that clicked: the page is handed on as-is"
    )
    verify_i = _stage_indices(calls, "opener-verify")
    assert verify_i, "no re-navigate + verify after an unverifiable hand-back"
    assert any(walk_i < n < verify_i[0] for n in _navigate_indices(calls))
    info = _by_id(artifact)["lang-item-hover"]["hoverOpener"]
    assert info.get("restored") in {"navigate", "failed"}, info


# ── static guards on the orchestration ───────────────────────────────────


def test_close_result_is_consumed_not_discarded() -> None:
    txt = SCRIPT.read_text(encoding="utf-8")
    assert 'HCLOSE_RAW=$(agent-browser --session "$SESSION" eval "$HCLOSE_JS"' in txt
    assert 'eval "$HCLOSE_JS" >/dev/null' not in txt
    assert "data-tf-hover-opener" not in txt, "the marker-and-reclick close was replaced"


def test_walk_is_gated_on_the_first_snapshot() -> None:
    txt = SCRIPT.read_text(encoding="utf-8")
    snap = txt.index('HRAW=$(agent-browser --session "$SESSION" eval "$HSNAP_JS"')
    gate = txt.index('HWALK=$(run_py - "$HJSON" "$BEFORE_TMP" "$AFTER_TMP" "$HIDX"')
    walk_blob = txt.index("TF_STAGE = 'opener-walk'")
    assert snap < gate < walk_blob
    assert 'if [ "${HWALK%%:*}" = "walk" ]; then' in txt
