"""Behavioral tests for ui_clone.check_inputs (B1 per-check staleness).

Covers the friction-fix granularity (a CSS-only edit must not invalidate a
JS-only check), the holes the design reviews flagged (Next.js app/ coverage,
ref-artifact invalidation, rollup-constituent invalidation, rename-busts-cache),
and the registered-empty / unregistered policy distinction.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ui_clone.check_inputs import (
    ENTRY,
    MAX_HASH_BYTES,
    PKG,
    PUBLIC,
    SRC,
    InputFingerprintUnavailable,
    compute_check_input_hash,
    get_check_inputs,
    newest_input_mtime,
)


def _impl_tree(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "src" / "Hero.tsx").write_text("export const Hero = () => <div/>;\n")
    (root / "src" / "Hero.css").write_text(".hero{color:red}\n")
    (root / "package.json").write_text('{"name":"impl"}\n')


def _h(impl: Path, ref: Path, cid: str) -> str | None:
    return compute_check_input_hash(str(impl), str(ref), cid)


def test_unregistered_check_returns_none(tmp_path: Path) -> None:
    assert get_check_inputs("totally-unknown-check") is None
    assert _h(tmp_path, tmp_path, "totally-unknown-check") is None


def test_input_independent_check_returns_empty(tmp_path: Path) -> None:
    # capacity-probe is registered with no inputs → "" (never stale), NOT None.
    assert get_check_inputs("capacity-probe") is not None
    assert _h(tmp_path, tmp_path, "capacity-probe") == ""


def test_declared_side_with_zero_matches_is_unavailable(tmp_path: Path) -> None:
    """A declared side with no matches is not canonical cache evidence."""
    impl = tmp_path / "impl"
    ref = tmp_path / "ref"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (impl / "src" / "x.tsx").write_text("export const X = () => null;\n")  # no CSS yet
    (ref / "bundle-map.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(InputFingerprintUnavailable, match="implementation.*matched no files"):
        _h(impl, ref, "css-mirror")
    assert newest_input_mtime(impl, ref, "css-mirror") is None

    # Once every declared side is represented, canonical evidence is available.
    (impl / "src" / "x.css").write_text(".x{}\n")  # a declared CSS input appears
    h_with = _h(impl, ref, "css-mirror")
    assert h_with

    # Contrast: a genuinely input-independent check IS the "" sentinel.
    assert _h(impl, ref, "capacity-probe") == ""


def test_css_edit_does_not_invalidate_js_only_check(tmp_path: Path) -> None:
    """The core friction fix: editing only CSS re-hashes the CSS check but
    leaves a JS-only check (hydration-check = JS+PKG) untouched."""
    impl = tmp_path / "impl"
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text("{}\n", encoding="utf-8")
    _impl_tree(impl)

    css0 = _h(impl, ref, "css-mirror")
    js0 = _h(impl, ref, "hydration-check")

    (impl / "src" / "Hero.css").write_text(".hero{color:blue}\n")  # CSS-only edit
    css1 = _h(impl, ref, "css-mirror")
    js1 = _h(impl, ref, "hydration-check")

    assert css1 != css0, "CSS check must re-hash on a CSS edit"
    assert js1 == js0, "JS-only check must NOT be invalidated by a CSS edit"


def test_js_edit_invalidates_js_check_not_css_check(tmp_path: Path) -> None:
    impl = tmp_path / "impl"
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text("{}\n", encoding="utf-8")
    _impl_tree(impl)

    css0 = _h(impl, ref, "css-mirror")
    js0 = _h(impl, ref, "hydration-check")

    (impl / "src" / "Hero.tsx").write_text("export const Hero = () => <span/>;\n")
    css1 = _h(impl, ref, "css-mirror")
    js1 = _h(impl, ref, "hydration-check")

    assert js1 != js0, "JS check must re-hash on a JS edit"
    assert css1 == css0, "pure-CSS check must NOT be invalidated by a JS edit"


def test_nextjs_app_dir_is_covered(tmp_path: Path) -> None:
    """Editing app/page.tsx must invalidate JS checks (the 8-root brace covers
    Next.js app/ — the legacy find-newer scanned it; the hash must too)."""
    impl = tmp_path / "impl"
    ref = tmp_path / "ref"
    ref.mkdir()
    (impl / "app").mkdir(parents=True)
    (impl / "app" / "page.tsx").write_text("export default function Page(){return null}\n")
    (impl / "package.json").write_text('{"name":"impl"}\n')

    h0 = _h(impl, ref, "hydration-check")
    (impl / "app" / "page.tsx").write_text("export default function Page(){return <div/>}\n")
    h1 = _h(impl, ref, "hydration-check")
    assert h1 != h0


def test_ref_artifact_invalidation_without_impl_change(tmp_path: Path) -> None:
    """alignment-parity's entire input is ref/sections/matches.json. Rewriting
    it (re-extract / re-run section-compare) must bust the hash even though no
    impl file changed."""
    impl = tmp_path / "impl"
    ref = tmp_path / "ref"
    (ref / "sections").mkdir(parents=True)
    _impl_tree(impl)
    matches = ref / "sections" / "matches.json"
    matches.write_text('{"pairs":[{"ae":10}]}\n')

    h0 = _h(impl, ref, "alignment-parity")
    matches.write_text('{"pairs":[{"ae":999}]}\n')  # ref artifact regenerated
    h1 = _h(impl, ref, "alignment-parity")
    assert h1 != h0


def test_optional_font_transfer_report_has_required_extraction_anchor(
    tmp_path: Path,
) -> None:
    """No font-transfer.json is a valid PASS state for the producer. The input
    hash stays available via extracted.json, and a report appearing later must
    invalidate that verdict."""
    impl = tmp_path / "impl"
    ref = tmp_path / "ref"
    ref.mkdir()
    _impl_tree(impl)
    (ref / "extracted.json").write_text('{"fonts":[]}\n', encoding="utf-8")

    without_report = _h(impl, ref, "font-binaries-present")
    assert without_report
    assert newest_input_mtime(impl, ref, "font-binaries-present") is not None

    (ref / "font-transfer.json").write_text(
        '{"totals":{"referenced":1}}\n', encoding="utf-8"
    )
    with_report = _h(impl, ref, "font-binaries-present")
    assert with_report and with_report != without_report


def test_rollup_constituent_invalidation(tmp_path: Path) -> None:
    """runtime-proof declares its constituent artifacts as ref inputs, so a
    regenerated constituent busts the rollup hash even though the read-path
    never re-dispatches it."""
    impl = tmp_path / "impl"
    ref = tmp_path / "ref"
    ref.mkdir()
    _impl_tree(impl)
    constituent = ref / "runtime-dom-parity.json"
    constituent.write_text('{"status":"pass"}\n')

    h0 = _h(impl, ref, "runtime-proof")
    constituent.write_text('{"status":"fail"}\n')
    h1 = _h(impl, ref, "runtime-proof")
    assert h1 != h0


@pytest.mark.parametrize("check_id", ("runtime-proof", "transition-proof"))
def test_rollup_verification_plan_mutation_invalidates_hash(
    tmp_path: Path,
    check_id: str,
) -> None:
    """The plan changes which missing artifacts a rollup treats as required."""
    impl = tmp_path / "impl"
    ref = tmp_path / "ref"
    ref.mkdir()
    _impl_tree(impl)
    plan = ref / "verification-plan.json"
    initial = '{"requiredChecks":[{"produces":"runtime-proof.json"}]}\n'
    changed = '{"requiredChecks":[{"produces":"transition-proof.json"}]}\n'
    plan.write_text(initial, encoding="utf-8")

    initial_hash = _h(impl, ref, check_id)
    plan.write_text(changed, encoding="utf-8")
    changed_hash = _h(impl, ref, check_id)
    assert changed_hash != initial_hash

    # Regenerating an identical plan remains content-hash stable.
    plan.write_text(changed, encoding="utf-8")
    assert _h(impl, ref, check_id) == changed_hash


def test_transition_rollup_plain_text_constituents_invalidate_hash(
    tmp_path: Path,
) -> None:
    """Every plain-text verdict consumed by transition-proof must bust cache."""
    impl = tmp_path / "impl"
    ref = tmp_path / "ref"
    transitions = ref / "transitions"
    transitions.mkdir(parents=True)
    _impl_tree(impl)
    paths = (
        transitions / "video-motion-result.txt",
        transitions / "result.txt",
        transitions / "hover-state-result.txt",
    )
    for path in paths:
        path.write_text("✅ PASS\n", encoding="utf-8")

    previous = _h(impl, ref, "transition-proof")
    assert previous
    for index, path in enumerate(paths):
        path.write_text(
            path.read_text(encoding="utf-8") + f"mode-{index}: ✅ PASS\n",
            encoding="utf-8",
        )
        current = _h(impl, ref, "transition-proof")
        assert current and current != previous
        previous = current


def test_runtime_text_sequence_tracks_impl_runtime_and_ref_text_inputs() -> None:
    spec = get_check_inputs("runtime-text-sequence")
    assert spec is not None
    assert spec.impl == SRC + PUBLIC + ENTRY + PKG
    assert spec.ref == ("dom-scaffold.json", "runtime-text.json")


def test_runtime_text_sequence_invalidates_on_css_entry_and_package_changes(
    tmp_path: Path,
) -> None:
    impl = tmp_path / "impl"
    ref = tmp_path / "ref"
    _impl_tree(impl)
    ref.mkdir()
    (impl / "index.html").write_text("<main></main>\n", encoding="utf-8")
    (ref / "dom-scaffold.json").write_text('{"tree":{}}\n', encoding="utf-8")

    previous = _h(impl, ref, "runtime-text-sequence")
    assert previous
    for path, content in (
        (impl / "src" / "Hero.css", ".hero{color:blue}\n"),
        (impl / "index.html", "<main id=\"root\"></main>\n"),
        (impl / "package.json", '{"name":"impl","scripts":{"dev":"vite"}}\n'),
    ):
        path.write_text(content, encoding="utf-8")
        current = _h(impl, ref, "runtime-text-sequence")
        assert current and current != previous
        previous = current


def test_identical_bytes_rename_busts_cache(tmp_path: Path) -> None:
    """Hash is over (relpath, content), so a pure rename (identical bytes) busts
    the cache for placement/identity-sensitive checks."""
    impl = tmp_path / "impl"
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text("{}\n", encoding="utf-8")
    (impl / "src").mkdir(parents=True)
    a = impl / "src" / "A.css"
    a.write_text(".x{color:red}\n")

    h0 = _h(impl, ref, "css-mirror")
    a.rename(impl / "src" / "B.css")  # same bytes, new path
    h1 = _h(impl, ref, "css-mirror")
    assert h1 != h0


def test_prune_dirs_excluded(tmp_path: Path) -> None:
    impl = tmp_path / "impl"
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text("{}\n", encoding="utf-8")
    _impl_tree(impl)
    nm = impl / "node_modules" / "pkg"
    nm.mkdir(parents=True)

    h0 = _h(impl, ref, "css-mirror")
    (nm / "junk.css").write_text(".vendor{color:green}\n")  # inside node_modules
    h1 = _h(impl, ref, "css-mirror")
    assert h1 == h0, "node_modules must be pruned from the input set"


def test_large_binary_media_fingerprinted_by_size(tmp_path: Path) -> None:
    """Known binary media retain bounded size-only hashing above the threshold."""
    impl = tmp_path / "impl"
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "visible-images.json").write_text('{"images":[]}\n', encoding="utf-8")
    (impl / "public").mkdir(parents=True)
    big = impl / "public" / "video.bin"
    big.write_bytes(b"\x00" * (MAX_HASH_BYTES + 100))

    h0 = _h(impl, ref, "asset-transfer")  # asset-transfer impl profile = PUBLIC
    big.write_bytes(b"\x01" * (MAX_HASH_BYTES + 100))  # same size, new content
    h1 = _h(impl, ref, "asset-transfer")
    assert h1 == h0, "same-size large file must not re-hash (size fingerprint)"
    big.write_bytes(b"\x01" * (MAX_HASH_BYTES + 200))  # larger
    h2 = _h(impl, ref, "asset-transfer")
    assert h2 != h0, "size change must re-hash"


def test_large_same_size_locale_json_is_content_hashed(tmp_path: Path) -> None:
    """Text catalogs cannot collide merely because they are >1 MiB and same-size."""
    impl = tmp_path / "impl"
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "visible-images.json").write_text('{"images":[]}\n', encoding="utf-8")
    locale_dir = impl / "public" / "locales"
    locale_dir.mkdir(parents=True)
    locale = locale_dir / "ko.json"
    locale.write_text(
        '{"copy":"' + ("가" * (MAX_HASH_BYTES // 3 + 100)) + '"}\n',
        encoding="utf-8",
    )
    original_size = locale.stat().st_size
    initial_hash = _h(impl, ref, "asset-transfer")

    locale.write_text(
        '{"copy":"' + ("나" * (MAX_HASH_BYTES // 3 + 100)) + '"}\n',
        encoding="utf-8",
    )
    assert locale.stat().st_size == original_size
    assert _h(impl, ref, "asset-transfer") != initial_hash


def test_real_unreadable_traversal_is_unavailable(tmp_path: Path) -> None:
    """A chmod-denied declared subtree is not collapsed into a zero-match scan."""
    impl = tmp_path / "impl"
    src = impl / "src"
    src.mkdir(parents=True)
    (impl / "package.json").write_text("{}\n", encoding="utf-8")
    (src / "App.tsx").write_text("export default () => null;\n", encoding="utf-8")
    src.chmod(0)
    try:
        if os.access(src, os.R_OK):
            pytest.skip("test process can read chmod(0) directories")
        with pytest.raises(InputFingerprintUnavailable, match="cannot traverse"):
            compute_check_input_hash(impl, None, "hydration-check")
        assert newest_input_mtime(impl, None, "hydration-check") is None
    finally:
        src.chmod(0o700)
