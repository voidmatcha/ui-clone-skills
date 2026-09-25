"""Font-service stylesheets are font assets, not the reference runtime.

AGENTS.md "Source fidelity" requires keeping the reference's fonts, which for
most sites means loading the same Google Fonts / Typekit stylesheet. Counting
those as reference code made every such scoped clone fail scoped_check.
"""

from __future__ import annotations

from ui_clone import scoped_provenance as p

IMPL_PAGE = "http://localhost:3000/"


def _hits(ref: list[dict[str, str]], impl: list[dict[str, str]]) -> list[str]:
    return p.reference_code_hits(p.code_resources(impl), IMPL_PAGE, p.code_resources(ref))


def test_font_service_stylesheets_are_not_reference_code() -> None:
    fonts = [
        {
            "url": "https://fonts.googleapis.com/css2?family=Inter:wght@400;700",
            "kind": "stylesheet",
        },
        {"url": "https://fonts.gstatic.com/s/inter/v12/abc.woff2", "kind": "css"},
        {"url": "https://use.typekit.net/xyz.css", "kind": "stylesheet"},
    ]
    assert _hits(fonts, fonts) == []


def test_reference_bundles_still_hit_next_to_font_stylesheets() -> None:
    ref = [
        {"url": "https://fonts.googleapis.com/css2?family=Inter", "kind": "stylesheet"},
        {"url": "https://cdn.example-ref.net/app.4f3a9c1e.css", "kind": "stylesheet"},
    ]
    impl = [
        {"url": "https://fonts.googleapis.com/css2?family=Inter", "kind": "stylesheet"},
        {"url": "https://cdn.example-ref.net/app.4f3a9c1e.css", "kind": "stylesheet"},
    ]
    hits = _hits(ref, impl)
    assert len(hits) == 1 and "cdn.example-ref.net" in hits[0]


def test_scripts_from_font_hosts_remain_code() -> None:
    kit = {"url": "https://use.fontawesome.com/releases/v6.0.0/js/all.js", "kind": "script"}
    assert p.is_code_resource(kit["url"], kit["kind"])
    assert _hits([kit], [kit])
