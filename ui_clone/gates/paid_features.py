"""Paid-Features gate.

Extracted from ui_clone/gate.py. Each function takes `self: "Gate"` and is
rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from .base import CheckResult

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401

def _check_paid_font_substitution(self: Gate) -> list[CheckResult]:
    """FAIL early if any paid font is marked decision='substitute' but the
    substitution is not declared in asset-substitution.json.

    Why: 'substitute' is a promise — the agent picked a free family at 5c-c
    and font-parity will verify the swap at runtime. Without an
    asset-substitution.json fonts[] entry, font-parity FAILs much later
    (after Step 7 generation and section-compare have already run). Surfacing
    the missing declaration at spec time saves the wasted generation pass.

    Only paid-features with decision='substitute' are checked. 'use' and
    'skip' do not require asset-substitution.json. Empty findings pass.

    Also defensively flags the "agent skipped the paid-features gate" case:
    when paid-features.json is missing but extraction artifacts (fonts.json,
    head.json) contain known paid CDN hostnames, fail spec gate with a
    pointer to run paid-features-detect.sh.
    """
    results: list[CheckResult] = []
    paid = self._load_json("paid-features.json")
    if not paid:
        # Defensive: if extraction artifacts already prove paid CDNs are
        # in play, the paid-features gate should have run before spec.
        corpus = ""
        for fname in ("fonts.json", "head.json", "external-sdks.json"):
            fp = self.ref_dir / fname
            if fp.is_file():
                try:
                    corpus += fp.read_text(encoding="utf-8")
                except OSError:
                    pass
        hits = [h for h in self._PAID_FONT_CDN_HOSTS if h in corpus]
        if hits:
            shown = ", ".join(hits[:3]) + ("..." if len(hits) > 3 else "")
            results.append(
                CheckResult(
                    "paid-features.json missing",
                    "fail",
                    f"Paid font CDN host(s) detected in extraction artifacts ({shown}) "
                    "but paid-features.json is missing — the `paid-features` gate has not run.",
                    fix=(
                        "Run: bash skills/visual-debug/scripts/paid-features-detect.sh "
                        "$(pwd)/tmp/ref/<component> — then re-run the `paid-features` gate "
                        "before `spec` so substitution decisions are recorded."
                    ),
                )
            )
        return results

    substitutes = [
        item
        for item in paid.get("paidFonts", [])
        if isinstance(item, dict) and item.get("decision") == "substitute"
    ]
    if not substitutes:
        return results

    sub_path = self.ref_dir / "asset-substitution.json"
    cdns = ", ".join(str(item.get("cdn", "?")) for item in substitutes[:5]) + (
        "..." if len(substitutes) > 5 else ""
    )
    if not sub_path.is_file():
        results.append(
            CheckResult(
                "paid-font substitution undeclared",
                "fail",
                f"{len(substitutes)} paid font(s) marked decision='substitute' "
                f"({cdns}) but asset-substitution.json is missing.",
                fix=(
                    "Write tmp/ref/<c>/asset-substitution.json with a fonts[] entry "
                    "for each substituted CDN. See ui-reverse-engineering/asset-substitution.md."
                ),
            )
        )
        return results

    sub_data = self._load_json("asset-substitution.json")
    fonts = sub_data.get("fonts", []) if sub_data else []
    if not (isinstance(fonts, list) and len(fonts) > 0):
        results.append(
            CheckResult(
                "paid-font substitution undeclared",
                "fail",
                f"asset-substitution.json exists but has no fonts[] entries — "
                f"{len(substitutes)} paid font(s) marked decision='substitute' "
                f"({cdns}) need declaration.",
                fix=(
                    "Add a fonts[] entry to asset-substitution.json for each "
                    "substituted CDN. See ui-reverse-engineering/asset-substitution.md."
                ),
            )
        )
        return results

    log = self._load_json("download-log.json") or {}
    attempts = log.get("attempts") if isinstance(log, dict) else None
    attempted_urls: list[str] = []
    if isinstance(attempts, list):
        attempted_urls = [
            str(a.get("url") or "") for a in attempts if isinstance(a, dict)
        ]
    missing_download: list[str] = []
    for item in substitutes:
        family = str(item.get("cdn") or item.get("family") or "").strip()
        if not family:
            continue
        # Build keyword(s) to match against the URL list.
        # "Die Grotesk" → tokens ["Die", "Grotesk"]; require both AND-match
        # against at least one URL (case-insensitive). This catches the
        # common shapes (foundry CDN + self-hosted) without overreaching.
        tokens = [t for t in re.split(r"\s+", family) if t]
        hit = False
        for url in attempted_urls:
            u = url.lower()
            if all(tok.lower() in u for tok in tokens):
                hit = True
                break
        if not hit:
            missing_download.append(family)
    if missing_download:
        sample = ", ".join(missing_download[:3])
        results.append(
            CheckResult(
                "paid-font substitution — download attempt missing",
                "fail",
                f"{len(missing_download)} paid font(s) marked decision='substitute' "
                f"({sample}) but download-log.json shows zero attempts for the "
                "family. Research-mode policy: a substitution is only valid AFTER "
                "an HTTP download attempt has been made and recorded — "
                "iteration-discipline.md 'Asset substitution policy' section.",
                fix=(
                    "Identify the woff2/otf/ttf URLs for the commercial family "
                    "(check head.json + bundle-extraction.json for @font-face src), "
                    "add them to the asset-download targets, re-run "
                    "scripts/extract/asset-download.sh, and confirm "
                    "download-log.json records the attempt. Substitution is then "
                    "valid if the attempt returned non-200."
                ),
            )
        )
        return results

    results.append(
        CheckResult(
            "paid-font substitution",
            "pass",
            f"{len(substitutes)} substitute decision(s) declared and download "
            "attempts recorded in download-log.json",
        )
    )
    return results


def gate_paid_features(self: Gate) -> list[CheckResult]:
    """Verify the agent has *consciously decided* what to do about paid fonts.

    Reads tmp/ref/<c>/paid-features.json (produced by paid-features-detect.sh).
    The script greps downloaded bundles + CSS for paid font CDN domains and
    writes findings with `decision: null`.

    For every entry:
      - decision == null  → FAIL (the agent has not made a choice yet)
      - decision == "use"        → PASS (license is in place; agent confirmed)
      - decision == "substitute" → PASS (using a free alternative; downstream
                                    font-parity gate enforces declaration)
      - decision == "skip"       → PASS (intentionally not replicating)

    Why early: catches expensive scope problems BEFORE Step 7 generation.
    Declaring paid-font substitution upfront avoids a section-compare loop
    that can never close (every text-bearing section reports 100% mismatch
    forever when the impl silently falls back to default sans-serif).

    Licensing changes over time. The detector only checks dependency families
    listed in paid-features-detect.sh; update that table when a provider's
    licensing changes.
    """
    results = []
    path = self.ref_dir / "paid-features.json"
    fix_msg = (
        "Run: bash skills/visual-debug/scripts/paid-features-detect.sh "
        "$(pwd)/tmp/ref/<component>"
    )
    if not path.is_file():
        results.append(
            CheckResult(
                "paid-features.json",
                "fail",
                "paid-features.json — MISSING (paid-features-detect.sh has not been run)",
                fix=fix_msg,
            )
        )
        return results

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        results.append(
            CheckResult(
                "paid-features.json",
                "fail",
                f"paid-features.json — unreadable ({e})",
                fix=fix_msg,
            )
        )
        return results

    if not isinstance(data, dict):
        results.append(
            CheckResult(
                "paid-features.json",
                "fail",
                "paid-features.json — must be a JSON object",
                fix=fix_msg,
            )
        )
        return results

    valid_decisions = {"use", "substitute", "skip"}
    pending: list[str] = []
    invalid: list[str] = []
    total = 0
    items = data.get("paidFonts", [])
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            total += 1
            name = item.get("family") or item.get("cdn") or "?"
            decision = item.get("decision")
            label = f"paidFont:{name}"
            if decision is None:
                pending.append(label)
            elif decision not in valid_decisions:
                invalid.append(f"{label} (decision={decision!r})")

    if total == 0:
        results.append(
            CheckResult(
                "paid-features",
                "pass",
                "No paid fonts detected",
            )
        )
        return results

    if invalid:
        results.append(
            CheckResult(
                "paid-features decisions",
                "fail",
                f"{len(invalid)} item(s) have invalid `decision`: {', '.join(invalid[:5])}"
                + ("..." if len(invalid) > 5 else ""),
                fix="Set `decision` to one of: 'use', 'substitute', 'skip'",
            )
        )
        return results

    if pending:
        results.append(
            CheckResult(
                "paid-features decisions",
                "fail",
                f"{len(pending)}/{total} paid item(s) have decision=null: "
                f"{', '.join(pending[:5])}"
                + ("..." if len(pending) > 5 else ""),
                fix=(
                    "Edit paid-features.json — set each `decision` to one of: "
                    "'use' (you have the license), "
                    "'substitute' (using free alternative — must back with asset-substitution.json), "
                    "'skip' (intentionally not replicating). "
                    "Decide BEFORE generation to avoid wasted Step 7 work."
                ),
            )
        )
        return results

    results.append(
        CheckResult(
            "paid-features",
            "pass",
            f"All {total} paid item(s) have a decision recorded",
        )
    )
    return results

