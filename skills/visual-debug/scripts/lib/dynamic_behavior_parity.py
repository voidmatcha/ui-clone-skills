from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

MODE, SESSION, REF_URL, IMPL_URL, REF_DIR_ARG, MEAS_FILE = sys.argv[1:7]
REF_DIR = Path(REF_DIR_ARG)
ARTIFACT = REF_DIR / "dynamic-behavior-parity.json"

UTC = timezone.utc  # noqa: UP017 - macOS /usr/bin/python3 is still 3.9.

TIME_KEYWORDS = ("autoplay", "timer", "interval", "carousel", "ticker")
DEFAULT_DELTA_MS = 4000
MAX_DELTA_MS = 10000
PERIOD_TOLERANCE = 0.25


# ── small helpers ───────────────────────────────────────────────────────────
def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def as_num(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) and value > 0:
        return value
    return None


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def write_artifact(payload: dict[str, Any]) -> None:
    payload.setdefault("schemaVersion", 1)
    payload.setdefault("generatedAt", now_iso())
    ARTIFACT.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def decode(raw: str) -> Any:
    """agent-browser eval output may be double/triple JSON-encoded."""
    value = (raw or "").strip()
    for _ in range(3):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                break
        else:
            break
    return value


# ── region discovery ────────────────────────────────────────────────────────
def spec_entries(spec: Any) -> list[dict[str, Any]]:
    if isinstance(spec, list):
        rows = spec
    elif isinstance(spec, dict):
        rows = spec.get("transitions") or spec.get("entries") or []
    else:
        rows = []
    return [r for r in rows if isinstance(r, dict)]


def target_selectors(target: Any) -> list[str]:
    """A spec target is a comma-list string or a {selector,...} dict."""
    if isinstance(target, dict):
        sel = target.get("selector")
        return [str(sel).strip()] if sel else []
    out = []
    for sel in str(target or "").split(","):
        sel = sel.strip()
        if sel:
            out.append(sel)
    return out


def masked_selectors(spec: Any) -> list[str]:
    try:
        from ui_clone.gates.masked_region_static import (
            select_masked_selectors,
        )
        return cast(list[str], select_masked_selectors(spec))
    except Exception:
        out, seen = [], set()
        for entry in spec_entries(spec):
            if not entry.get("dynamic"):
                continue
            for sel in target_selectors(entry.get("target")):
                if sel not in seen:
                    seen.add(sel)
                    out.append(sel)
        return out


def discover_regions(ref_dir: Path) -> tuple[list[dict[str, Any]], str]:
    # priority 1: curated dynamic-regions.json
    curated = load_json(ref_dir / "dynamic-regions.json")
    if isinstance(curated, dict) and isinstance(curated.get("regions"), list):
        out = []
        for reg in curated["regions"]:
            if isinstance(reg, dict) and reg.get("selector"):
                out.append({
                    "selector": str(reg["selector"]),
                    "label": reg.get("label"),
                    "periodMs": as_num(reg.get("periodMs")),
                    # Curated regions are asserted-to-exist: a curated selector
                    # that is missing on the ref is a probe/selector problem
                    # (honest-unmeasurable), never no-dynamics evidence.
                    "curated": True,
                })
        if out:
            return out, "dynamic-regions.json"

    spec = load_json(ref_dir / "transition-spec.json")

    # priority 2: time-driven transition-spec entries
    out, seen = [], set()
    for entry in spec_entries(spec):
        trigger = str(entry.get("trigger") or "").lower()
        anim = entry.get("animation")
        atype = str(anim.get("type") if isinstance(anim, dict) else "").lower()
        if any(kw in trigger for kw in TIME_KEYWORDS) or "carousel" in atype:
            for sel in target_selectors(entry.get("target")):
                if sel not in seen:
                    seen.add(sel)
                    out.append({"selector": sel, "label": entry.get("id"), "periodMs": None})
    if out:
        return out, "transition-spec time-driven entries"

    # priority 3: dynamic:true masked selectors
    sels = masked_selectors(spec)
    out = [{"selector": s, "label": None, "periodMs": None} for s in sels]
    if out:
        return out, "dynamic:true masked selectors"

    return [], "none"


# ── browser probes ──────────────────────────────────────────────────────────
def ab(session: str, *args: str, timeout: int = 60) -> tuple[bool, str]:
    """Run an agent-browser command. Returns (ok, stdout); ok is False on a
    nonzero exit, a timeout, or any subprocess exception (all command-level
    infra failures, distinct from a probe that ran and returned an in-page
    error)."""
    try:
        proc = subprocess.run(
            ["agent-browser", "--session", session, *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode == 0, proc.stdout or ""
    except Exception:
        return False, ""


def do_eval(session: str, js: str) -> tuple[dict[str, Any] | None, bool]:
    """Returns (result_dict, probe_failed). probe_failed is True when the eval
    command itself failed (nonzero rc, timeout, empty output, or unparseable
    output) — an infra failure, NOT a measurement limitation. A dict result
    (including one carrying an in-page {"error":...}) is a successful probe."""
    ok, raw = ab(session, "eval", js)
    if not ok or not raw.strip():
        return None, True
    value = decode(raw)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            pass
    if isinstance(value, dict):
        return value, False
    return None, True


def fp_js(selector: str) -> str:
    sel = json.dumps(selector)
    return (
        "(() => { try {"
        "  var sel = " + sel + ";"
        "  var el = document.querySelector(sel);"
        "  if (!el) return JSON.stringify({present:false});"
        "  var norm = function(s){ return (s||'').replace(/\\s+/g,' ').trim(); };"
        "  var t = norm(el.textContent);"
        "  var h = 0; for (var i=0;i<t.length;i++){ h = ((h<<5)-h + t.charCodeAt(i))|0; }"
        "  var sig = function(e){ var c = window.getComputedStyle(e); return c.transform + '|' + c.opacity; };"
        "  var kids = Array.prototype.slice.call(el.children,0,8);"
        "  var styleSig = [sig(el)].concat(kids.map(sig)).join(';');"
        "  var r = el.getBoundingClientRect();"
        "  var rectSig = [Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)].join(',');"
        "  var media = [];"
        "  if (typeof HTMLMediaElement !== 'undefined' && el instanceof HTMLMediaElement) media.push(el);"
        "  if (el.querySelectorAll) media = media.concat(Array.prototype.slice.call(el.querySelectorAll('video,audio'),0,8));"
        "  var mediaSig = media.map(function(m){"
        "    return [m.tagName,Math.round((Number(m.currentTime)||0)*10)/10,!!m.paused,Number(m.readyState)||0].join('|');"
        "  }).join(';');"
        "  var images = [];"
        "  if (typeof HTMLImageElement !== 'undefined' && el instanceof HTMLImageElement) images.push(el);"
        "  if (el.querySelectorAll) images = images.concat(Array.prototype.slice.call(el.querySelectorAll('img,source'),0,8));"
        "  images = images.slice(0,8);"
        "  var imageSig = images.map(function(img){"
        "    return [img.tagName,img.currentSrc||'',img.getAttribute('src')||'',img.getAttribute('srcset')||''].join('|');"
        "  }).join(';');"
        "  var paintNodes = [el].concat(kids).slice(0,9);"
        "  var backgroundSig = paintNodes.map(function(e){"
        "    var c = window.getComputedStyle(e);"
        "    return [c.backgroundImage,c.backgroundPosition,c.backgroundSize].join('|');"
        "  }).join(';');"
        "  return JSON.stringify({present:true, textHash:String(h), styleSig:styleSig, rectSig:rectSig, mediaSig:mediaSig, imageSig:imageSig, backgroundSig:backgroundSig});"
        "} catch(e){ return JSON.stringify({error:String(e)}); } })()"
    )


def period_js(selector: str) -> str:
    sel = json.dumps(selector)
    return (
        "(() => { try {"
        "  var sel = " + sel + ";"
        "  var el = document.querySelector(sel);"
        "  if (!el) return JSON.stringify({periodMs:null});"
        "  var host = (el.closest && el.closest('.swiper'))"
        "    || (el.querySelector && el.querySelector('.swiper')) || el;"
        "  var inst = host && host.swiper;"
        "  var d = inst && inst.params && inst.params.autoplay && inst.params.autoplay.delay;"
        "  return JSON.stringify({periodMs: (typeof d==='number' && d>0) ? d : null});"
        "} catch(e){ return JSON.stringify({periodMs:null}); } })()"
    )


def presence_js(selector: str) -> str:
    """Report whether the selector exists; scroll it (or the page) so a
    viewport-triggered lazy mount gets a chance to attach before we retry."""
    sel = json.dumps(selector)
    return (
        "(() => { try {"
        "  var sel = " + sel + ";"
        "  var el = document.querySelector(sel);"
        "  if (el) {"
        "    var sec = (el.closest && el.closest('section, [class*=section], [class*=hero]')) || el;"
        "    try { sec.scrollIntoView({block:'center'}); } catch(e){}"
        "    return JSON.stringify({present:true});"
        "  }"
        "  try { window.scrollBy(0, Math.round(window.innerHeight * 0.9)); } catch(e){}"
        "  return JSON.stringify({present:false});"
        "} catch(e){ return JSON.stringify({present:false, error:String(e)}); } })()"
    )


def ensure_present(
    session: str, selector: str, attempts: int = 3, gap_s: float = 1.5
) -> tuple[bool, bool]:
    """Bounded retry so a lazy-mounted element (e.g. hero <video>) is not
    missed by a single immediate querySelector. Returns (present, probe_failed):
    present True once found, False if still absent after all attempts;
    probe_failed True on a command-level browser-eval failure."""
    for i in range(attempts):
        res, pf = do_eval(session, presence_js(selector))
        if pf:
            return False, True
        if res and res.get("present"):
            return True, False
        if i < attempts - 1:
            time.sleep(gap_s)
    return False, False


def side_from(fp0: Any, fp1: Any) -> dict[str, Any]:
    if fp0 is None and fp1 is None:
        return {"error": "eval returned no result"}
    for fp in (fp0, fp1):
        if isinstance(fp, dict) and fp.get("error"):
            return {"error": str(fp["error"])}
    present = not (isinstance(fp0, dict) and fp0.get("present") is False)
    return {"present": present, "fp0": fp0, "fp1": fp1}


def collect(
    session: str,
    ref_url: str,
    impl_url: str,
    regions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool, str]:
    """Drive both live pages and populate each region's ref/impl measurement.
    Returns (regions, open_ok, open_reason). open_ok is False when either
    session fails to open — a setup error the caller must surface as exit 2."""
    ref_s, impl_s = f"{session}-dp-ref", f"{session}-dp-impl"
    ok_ref, _ = ab(ref_s, "open", ref_url)
    ok_impl, _ = ab(impl_s, "open", impl_url)
    if not (ok_ref and ok_impl):
        failed = [name for name, ok in (("ref", ok_ref), ("impl", ok_impl)) if not ok]
        ab(ref_s, "close")
        ab(impl_s, "close")
        return regions, False, f"agent-browser open failed for session(s): {', '.join(failed)}"

    ab(ref_s, "set", "viewport", "1440", "900")
    ab(impl_s, "set", "viewport", "1440", "900")
    ab(ref_s, "wait", "2500")
    ab(impl_s, "wait", "2500")

    for reg in regions:
        sel = reg["selector"]
        # Give a lazy-mounted element (viewport-triggered hero media, deferred
        # feed) up to 3 tries on both sessions before we fingerprint it, so a
        # single immediate querySelector miss does not read as "missing".
        _, ref_present_pf = ensure_present(ref_s, sel)
        _, impl_present_pf = ensure_present(impl_s, sel)
        rp, _ = do_eval(ref_s, period_js(sel))
        ip, _ = do_eval(impl_s, period_js(sel))
        ref_period = as_num(reg.get("periodMs")) or as_num((rp or {}).get("periodMs"))
        impl_period = as_num((ip or {}).get("periodMs"))
        period = ref_period or as_num(reg.get("periodMs"))
        delta = min(2 * period, MAX_DELTA_MS) if period else DEFAULT_DELTA_MS

        ref_fp0, ref_pf0 = do_eval(ref_s, fp_js(sel))
        impl_fp0, impl_pf0 = do_eval(impl_s, fp_js(sel))
        time.sleep(delta / 1000.0)
        ref_fp1, ref_pf1 = do_eval(ref_s, fp_js(sel))
        impl_fp1, impl_pf1 = do_eval(impl_s, fp_js(sel))

        reg["deltaMs"] = delta
        reg["refPeriodMs"] = ref_period
        reg["implPeriodMs"] = impl_period
        # A command-level fingerprint-probe failure on either sample is an infra
        # failure, not a measurement limitation: mark the side probeFailed so the
        # run escalates to status=error (never a green pass).
        reg["ref"] = ({"probeFailed": True, "reason": "eval command failed on ref session"}
                      if (ref_present_pf or ref_pf0 or ref_pf1) else side_from(ref_fp0, ref_fp1))
        reg["impl"] = ({"probeFailed": True, "reason": "eval command failed on impl session"}
                       if (impl_present_pf or impl_pf0 or impl_pf1) else side_from(impl_fp0, impl_fp1))

    ab(ref_s, "close")
    ab(impl_s, "close")
    return regions, True, ""


# ── verdict (shared by collect and --judge) ─────────────────────────────────
def side_changed(side: Any) -> tuple[bool | str | None, str | None]:
    """Return (state, reason). state is True/False/'missing'/None(unmeasurable)."""
    if not isinstance(side, dict):
        return None, "no measurement"
    if side.get("error"):
        return None, str(side["error"])
    if side.get("present") is False:
        return "missing", None
    changed = side.get("changed")
    if changed is not None:
        return bool(changed), None
    fp0, fp1 = side.get("fp0"), side.get("fp1")
    if fp0 is None or fp1 is None:
        return None, "missing fingerprint samples"
    if isinstance(fp0, dict) and fp0.get("present") is False:
        return "missing", None
    if isinstance(fp1, dict) and fp1.get("present") is False:
        # element vanished across the window — that is a change (dynamic).
        return True, None
    return (json.dumps(fp0, sort_keys=True) != json.dumps(fp1, sort_keys=True)), None


def verdict_for(
    region: dict[str, Any],
) -> tuple[str, bool | None, bool | None, str | None]:
    ref: dict[str, Any] = (
        cast(dict[str, Any], region.get("ref"))
        if isinstance(region.get("ref"), dict)
        else {}
    )
    impl: dict[str, Any] = (
        cast(dict[str, Any], region.get("impl"))
        if isinstance(region.get("impl"), dict)
        else {}
    )
    # Infra probe-failure (command-level) escalates the whole run to error —
    # it is never a pass and never absorbed as honest-unmeasurable.
    if ref.get("probeFailed") or impl.get("probeFailed"):
        which = "ref" if ref.get("probeFailed") else "impl"
        reason = (ref if which == "ref" else impl).get("reason") or "probe failed"
        return "probe-failed", None, None, f"{which} probe failed: {reason}"

    ref_state, ref_reason = side_changed(region.get("ref"))
    impl_state, impl_reason = side_changed(region.get("impl"))
    ref_period = as_num(region.get("refPeriodMs"))
    impl_period = as_num(region.get("implPeriodMs"))

    def impl_changed_bool() -> bool | None:
        return impl_state is True if isinstance(impl_state, bool) else None

    if ref_state is None:
        return "honest-unmeasurable", None, impl_changed_bool(), f"ref unmeasurable: {ref_reason}"
    if ref_state == "missing":
        # A CURATED region is asserted-to-exist: missing-on-ref is a probe/
        # selector problem, not evidence that the ref has no dynamics. Fail
        # closed as honest-unmeasurable (and it counts toward unverifiedCurated)
        # instead of a vacuous no-dynamics pass. Spec-discovered regions keep
        # no-dynamics semantics.
        if region.get("curated"):
            return ("honest-unmeasurable", None, impl_changed_bool(),
                    "curated region not found on ref — probe timing or selector drift")
        return "no-dynamics-in-window", False, impl_changed_bool(), "element missing on ref"
    if ref_state is False:
        return "no-dynamics-in-window", False, impl_changed_bool(), None

    # ref showed dynamics in the window → impl must too.
    if impl_state is None:
        return "honest-unmeasurable", True, None, f"impl unmeasurable: {impl_reason}"
    if impl_state == "missing":
        return "static-in-impl", True, False, "element missing on impl"
    if impl_state is False:
        return "static-in-impl", True, False, None

    # both changed — cadence check when the reference cadence is known.
    if ref_period:
        if impl_period:
            lo, hi = ref_period * (1 - PERIOD_TOLERANCE), ref_period * (1 + PERIOD_TOLERANCE)
            if lo <= impl_period <= hi:
                return "behavior-match", True, True, None
            return (
                "behavior-match-period-off", True, True,
                f"impl period {impl_period}ms vs ref {ref_period}ms (>{int(PERIOD_TOLERANCE * 100)}%)",
            )
        # Ref cadence is known but the impl cadence is undetectable: fail closed
        # rather than call it a match. Curators who do not care about cadence
        # should omit periodMs so the region takes plain behavior-match below.
        return (
            "behavior-match-period-unverified", True, True,
            f"ref period {ref_period}ms known but impl cadence is undetectable",
        )
    return "behavior-match", True, True, None


FAIL_VERDICTS = {
    "static-in-impl",
    "behavior-match-period-off",
    "behavior-match-period-unverified",
}
ERROR_VERDICTS = {"probe-failed"}
# Verdicts that did NOT produce a real behavioral measurement of the ref.
UNVERIFIED_VERDICTS = {"honest-unmeasurable", "probe-failed"}


def judge(regions: list[dict[str, Any]], source: str) -> int:
    rows = []
    status = "pass"
    has_error = False
    curated_total = 0
    curated_unverified = 0
    any_real_measurement = False
    for reg in regions:
        verdict, ref_changed, impl_changed, note = verdict_for(reg)
        row = {
            "selector": reg.get("selector"),
            "verdict": verdict,
            "refChanged": ref_changed,
            "implChanged": impl_changed,
            "deltaMs": reg.get("deltaMs"),
        }
        if as_num(reg.get("refPeriodMs")) is not None:
            row["refPeriodMs"] = reg.get("refPeriodMs")
        if as_num(reg.get("implPeriodMs")) is not None:
            row["implPeriodMs"] = reg.get("implPeriodMs")
        if reg.get("label"):
            row["label"] = reg["label"]
        if reg.get("curated"):
            row["curated"] = True
        if note:
            row["note"] = note
        rows.append(row)
        if verdict in ERROR_VERDICTS:
            has_error = True
        elif verdict in FAIL_VERDICTS:
            status = "fail"
        # A "real measurement" requires the REF element to have been present
        # and successfully probed — a spec region that is merely MISSING on
        # the ref (no-dynamics-in-window by absence) measured nothing, and
        # counting it would let a mixed curated/spec artifact where every
        # curated region went unverified slip past the guard below as a
        # vacuous pass (codex round-2 P2).
        _ref_side = reg.get("ref") or {}
        _ref_probed = bool(_ref_side.get("present")) and not _ref_side.get("probeFailed")
        if verdict not in UNVERIFIED_VERDICTS and _ref_probed:
            any_real_measurement = True
        if reg.get("curated"):
            curated_total += 1
            if verdict in UNVERIFIED_VERDICTS:
                curated_unverified += 1

    # A probe-failure means we could not trust the measurement — the run is an
    # infra error, which outranks pass/fail and must never read as green.
    if has_error:
        status = "error"
    # A parity run whose curated (asserted-to-exist) regions ALL went unverified
    # and that produced no real measurement anywhere measured nothing — that is
    # not parity evidence, so it is a hard error rather than a vacuous pass.
    if curated_total > 0 and curated_unverified == curated_total and not any_real_measurement:
        status = "error"

    write_artifact({"status": status, "source": source,
                    "unverifiedCurated": curated_unverified, "regions": rows})
    print(f"dynamic-behavior-parity: status={status} regions={len(rows)} "
          f"source={source} unverifiedCurated={curated_unverified}")
    for row in rows:
        if row["verdict"] in ERROR_VERDICTS:
            print(f"  ERROR {row['verdict']} {row['selector']}"
                  + (f" — {row['note']}" if row.get("note") else ""))
        elif row["verdict"] in FAIL_VERDICTS:
            print(f"  FAIL {row['verdict']} {row['selector']}"
                  + (f" — {row['note']}" if row.get("note") else ""))
    if status == "error":
        return 2
    return 1 if status == "fail" else 0


# ── main ────────────────────────────────────────────────────────────────────
def main() -> int:
    if MODE == "judge":
        meas = load_json(Path(MEAS_FILE))
        if not isinstance(meas, dict):
            write_artifact({"status": "error", "regions": [],
                            "note": f"could not read measurements: {MEAS_FILE}"})
            return 2
        # A recorded setup failure (e.g. a failed page open) is a hard error.
        if meas.get("setupError"):
            write_artifact({"status": "error", "regions": [],
                            "source": meas.get("source", "judge"),
                            "note": str(meas["setupError"])})
            print(f"dynamic-behavior-parity: setup error — {meas['setupError']}", file=sys.stderr)
            return 2
        regions = meas.get("regions") or []
        return judge(regions, meas.get("source", "judge"))

    if MODE == "discover":
        regions, source = discover_regions(REF_DIR)
        write_artifact({"status": "discovered", "source": source, "regions": regions})
        print(f"dynamic-behavior-parity: discovered {len(regions)} region(s) source={source}")
        return 0

    regions, source = discover_regions(REF_DIR)
    if not regions:
        write_artifact({"status": "pass", "regions": [],
                        "note": "no dynamic regions declared", "source": source})
        print("dynamic-behavior-parity: status=pass regions=0 (no dynamic regions declared)")
        return 0

    if not shutil.which("agent-browser"):
        write_artifact({"status": "error", "regions": [], "source": source,
                        "note": "agent-browser not found in PATH"})
        print("dynamic-behavior-parity: setup error — agent-browser not found", file=sys.stderr)
        return 2

    regions, open_ok, open_reason = collect(SESSION, REF_URL, IMPL_URL, regions)
    if not open_ok:
        write_artifact({"status": "error", "regions": [], "source": source,
                        "note": open_reason})
        print(f"dynamic-behavior-parity: setup error — {open_reason}", file=sys.stderr)
        return 2
    return judge(regions, source)


sys.exit(main())
