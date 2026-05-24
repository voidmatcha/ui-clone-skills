"""Font-Parity gate.

Extracted from ui_clone/gate.py. Each function takes `self: "Gate"` and is
rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .base import CheckResult

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401

def gate_font_parity(self: Gate) -> list[CheckResult]:
    """Check that the impl loads the same font as the ref, OR that the substitution is declared.

    Reads tmp/ref/<c>/font-parity.json (produced by font-parity-check.sh).
    - parity: "match" → PASS.
    - parity: "mismatch" + asset-substitution.json with at least one font entry → PASS (declared).
    - parity: "mismatch" + no asset-substitution.json → FAIL.

    Catches the class of bug where commercial-font substitution makes section-compare
    report 100% FAIL forever because every section renders the substituted asset.
    See asset-substitution.md.
    """
    results = []
    path = self.ref_dir / "font-parity.json"
    fix_msg = (
        "Run: bash skills/visual-debug/scripts/font-parity-check.sh "
        "<session> <ref-url> <impl-url> $(pwd)/tmp/ref/<component>"
    )
    if not path.is_file():
        results.append(
            CheckResult(
                "font-parity.json",
                "fail",
                "font-parity.json — MISSING (font-parity-check.sh has not been run)",
                fix=fix_msg,
            )
        )
        return results

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        results.append(
            CheckResult(
                "font-parity.json",
                "fail",
                f"font-parity.json — unreadable ({e})",
                fix=fix_msg,
            )
        )
        return results

    if not isinstance(data, dict):
        results.append(
            CheckResult(
                "font-parity.json",
                "fail",
                "font-parity.json — must be a JSON object",
                fix=fix_msg,
            )
        )
        return results

    parity = data.get("parity")
    ref_obj = data.get("ref") or {}
    impl_obj = data.get("impl") or {}
    if parity == "match":
        # Silent-fallback guard: ref and impl declare the same family, but the
        # impl's FontFace failed to load (paid font 404'd, expired Typekit ID,
        # CORS-blocked). computedStyle.fontFamily lies in this case — we use
        # document.fonts.check() result captured by font-parity-check.sh.
        ref_loaded = ref_obj.get("loaded", True)
        impl_loaded = impl_obj.get("loaded", True)
        family = (impl_obj.get("family") or ref_obj.get("family") or "?")
        if not ref_loaded and not impl_loaded:
            # Both sides report the FontFace is not loaded. The parity result
            # is meaningless — neither side is actually rendering the declared
            # family, so any "match" is matching two fallbacks. Could be a
            # transient network issue (re-run) or a real config bug (paid
            # font CDN unreachable from both deploys).
            results.append(
                CheckResult(
                    "font load failure (both sides)",
                    "fail",
                    f"Both ref and impl declare '{family}' but neither has the "
                    "FontFace actually loaded — both are rendering with a fallback. "
                    "The parity 'match' is between two fallbacks, not the declared font.",
                    fix=(
                        "Re-run font-parity-check.sh with WAIT_MS bumped (slow networks "
                        "may not resolve the FontFace within 2.5s). If the failure persists, "
                        "the declared font CDN is unreachable — fix the source, or substitute "
                        "and declare via asset-substitution.json."
                    ),
                )
            )
            return results
        if ref_loaded and not impl_loaded:
            results.append(
                CheckResult(
                    "font load failure",
                    "fail",
                    f"Impl declares '{family}' (matches ref) but the FontFace is NOT actually loaded "
                    "— browser is silently rendering with a fallback. Likely causes: 404, CORS, "
                    "expired Typekit/Adobe Fonts ID, or missing license file in deploy.",
                    fix=(
                        "Open DevTools → Network → filter 'font' on the impl URL. "
                        "Look for failed font requests. Either: (A) fix the loading issue "
                        "(restore @font-face, add CDN auth, refresh Typekit kit ID), "
                        "or (B) intentionally substitute and declare it in asset-substitution.json."
                    ),
                )
            )
            return results
        results.append(CheckResult("font-parity", "pass", "Ref and impl load the same primary font"))
        return results

    if parity != "mismatch":
        results.append(
            CheckResult(
                "font-parity.json",
                "fail",
                f"font-parity.json — `parity` must be 'match' or 'mismatch' (got {parity!r})",
                fix=fix_msg,
            )
        )
        return results

    # Mismatch — must be acknowledged via asset-substitution.json
    sub_path = self.ref_dir / "asset-substitution.json"
    ref_family = (data.get("ref") or {}).get("family", "?")
    impl_family = (data.get("impl") or {}).get("family", "?")
    if not sub_path.is_file():
        results.append(
            CheckResult(
                "font substitution undeclared",
                "fail",
                f"Ref loads '{ref_family}' but impl loads '{impl_family}'. "
                "If this is intentional (e.g. commercial-font replacement), declare it in "
                "asset-substitution.json. Otherwise fix the impl to load the original font.",
                fix=(
                    "Either: (A) fix impl to load the same font as ref, "
                    "or (B) write tmp/ref/<c>/asset-substitution.json per asset-substitution.md "
                    "with a fonts[] entry covering the substitution."
                ),
            )
        )
        return results

    try:
        sub_data = json.loads(sub_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        results.append(
            CheckResult(
                "asset-substitution.json",
                "fail",
                f"asset-substitution.json — unreadable ({e})",
            )
        )
        return results

    fonts = sub_data.get("fonts", []) if isinstance(sub_data, dict) else []
    if not (isinstance(fonts, list) and len(fonts) > 0):
        results.append(
            CheckResult(
                "font substitution undeclared",
                "fail",
                f"asset-substitution.json exists but has no fonts[] entry. "
                f"Ref loads '{ref_family}', impl loads '{impl_family}'.",
                fix=(
                    "Add a fonts[] entry to asset-substitution.json describing the substitution, "
                    "or fix the impl to load the original font."
                ),
            )
        )
        return results

    results.append(
        CheckResult(
            "font-parity",
            "pass",
            f"Font mismatch declared in asset-substitution.json ({ref_family} → {impl_family})",
        )
    )
    return results

