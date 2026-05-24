import json
import os
from pathlib import Path

from ._helpers import (
    _make_stub_compare,
    _project_root,
)


def test_hover_state_compare_single_viewport_back_compat(tmp_path: Path) -> None:
    """VIEWPORTS unset → no per-viewport subdir, no per-viewport line — current
    behavior preserved bit-for-bit so single-tier callers see no cost increase.

    Critical regression guard: the fan-out was an additive capability, NOT a
    coverage upgrade for existing callers. If unset-VIEWPORTS suddenly started
    fanning out to the four verification-plan default viewports, every
    standard-tier caller would 4× their browser cost overnight.
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{"name": "btn", "triggerType": "hover", "selector": ".btn"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    _make_stub_compare(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {k: v for k, v in os.environ.items() if k != "VIEWPORTS"}
    env["PLUGIN_ROOT"] = str(plugin_root)
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert proc.returncode == 0
    result = (ref / "transitions" / "hover-state-result.txt").read_text()
    assert "viewports: <single" in result
    # No per-viewport WxH subdir under hover-state/ — the target dir sits
    # directly under hover-state/<safe-name>/.
    assert (ref / "transitions" / "hover-state" / "btn").is_dir()
    assert not (ref / "transitions" / "hover-state" / "375x812").exists()



def test_click_state_compare_fans_out_per_viewport(tmp_path: Path) -> None:
    """VIEWPORTS=\"375x812,1280x800\" → per-viewport subdirs + result.txt sections.

    Click-state's responsive divergence is the killer case: modals render as
    full-screen sheets on mobile and floating panels on desktop; menu toggles
    swap between hamburger and inline nav. A single-viewport sweep can pass
    the desktop arc cleanly while mobile drops the entire panel.
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "click": [{"name": "tabs", "triggerType": "click-cycle", "selector": ".tab"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    _make_stub_compare(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "click-state-compare.sh"
    env = {**os.environ, "PLUGIN_ROOT": str(plugin_root), "VIEWPORTS": "375x812,1280x800"}
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert proc.returncode == 0, f"click fan-out failed: {proc.stdout}\n{proc.stderr}"
    result = (ref / "transitions" / "click-state-result.txt").read_text()
    assert "viewport: 375x812" in result
    assert "viewport: 1280x800" in result
    assert (ref / "transitions" / "click-state" / "375x812" / "tabs").is_dir()
    assert (ref / "transitions" / "click-state" / "1280x800" / "tabs").is_dir()



def test_hover_state_compare_rejects_malformed_viewport(tmp_path: Path) -> None:
    """Malformed VIEWPORTS entry → exit 2 with clear error.

    A silent coerce would write garbage to VIEW_W/VIEW_H and ship a broken
    capture; exit 2 is the explicit signal that the env var is wrong.
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{"name": "btn", "triggerType": "hover", "selector": ".btn"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    _make_stub_compare(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {**os.environ, "PLUGIN_ROOT": str(plugin_root), "VIEWPORTS": "375x812,bogus"}
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert proc.returncode == 2
    assert "malformed" in proc.stderr.lower() or "bogus" in proc.stderr



def test_image_fidelity_passes_when_impl_references_all_urls(tmp_path: Path) -> None:
    """impl source mentions every visible-images.json URL → exit 0, status=pass.

    Closes the inverse failure mode: a too-strict matcher (requiring exact-URL
    match only) false-fails impls that import the same asset via a basename
    proxy or via a CDN-rewritten path. The matcher falls back: full URL →
    basename → basename-without-query → stem.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": "https://cdn.example.com/hero.jpg", "element": "img.hero"},
        {"type": "bg-image", "src": "https://cdn.example.com/banner.png", "element": "div", "width": 800, "height": 600},
    ]))
    (impl / "src" / "Hero.tsx").write_text(
        'export const Hero = () => <img src="https://cdn.example.com/hero.jpg" />;\n'
    )
    (impl / "src" / "Banner.tsx").write_text(
        'export const Banner = () => <div style={{ backgroundImage: "url(https://cdn.example.com/banner.png)", width: 800, height: 600 }} />;\n'
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["matched"] == 2
    assert artifact["implRoot"] == str(impl)
    assert artifact["implDir"] == str(impl)
    assert artifact["implSrcDir"] == str(impl / "src")
    assert artifact["implPkgJson"] == str(impl / "package.json")



def test_image_fidelity_fails_when_url_dropped(tmp_path: Path) -> None:
    """impl source missing a ref URL → exit 1, status=fail, unmatched lists it.

    This is the failure class the gate exists for: agent generated a component
    that silently dropped a hero/logo/banner image. AE/SSIM catches the pixel
    diff but the URL-level signal here points the agent at the specific asset
    to fix, not at a region of pixel-diff to investigate.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": "https://cdn.example.com/dropped.jpg", "element": "img.dropped"},
    ]))
    (impl / "src" / "Empty.tsx").write_text('export const Empty = () => null;\n')
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "fail"
    assert len(artifact["unmatched"]) == 1
    assert artifact["unmatched"][0]["src"] == "https://cdn.example.com/dropped.jpg"



def test_image_fidelity_warns_on_dimension_mismatch(tmp_path: Path) -> None:
    """impl references URL but declares a width outside DIM_TOLERANCE → status=warn.

    Warn (not fail) because CSS-driven sizing is the common case and the
    declared prop may be a min-width / hint rather than ground truth. Exit 0
    so the gate doesn't block on a soft signal — the artifact still surfaces
    the mismatch for the agent to read.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "bg-image", "src": "https://cdn.example.com/big.png", "element": "div", "width": 1000, "height": 500},
    ]))
    (impl / "src" / "Big.tsx").write_text(
        'export const Big = () => <div style={{ backgroundImage: "url(https://cdn.example.com/big.png)", width: 200, height: 500 }} />;\n'
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    # Exit 0 because warn is a soft signal — the failure class for blocking
    # is "impl dropped the URL entirely", not "impl used a different width".
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "warn"
    assert len(artifact["dimensionMismatches"]) == 1
    assert "width: ref=1000 impl=200" in artifact["dimensionMismatches"][0]["issues"]



def test_image_fidelity_fails_on_local_cdn_optimizer_runtime_path(tmp_path: Path) -> None:
    """Loop-55 regression: static basename matching passed even though the
    browser loaded `/cdn-cgi/image/widtth=.../foo.webp` from the local Next app.

    The asset existed in public/ and the source mentioned `foo.webp`, so
    image-fidelity + asset-transfer both passed. At runtime, the local app
    does not serve Cloudflare image optimizer URLs, and a JS string typo
    (`widt\\u0074h`) made the path even worse. This must be a blocking
    image-fidelity failure, not a pixel-diff-only discovery.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": "https://cdn.example.com/images/foo.webp", "element": "img.foo"},
    ]))
    (impl / "src" / "Foo.tsx").write_text(
        'export const Foo = () => <img src="/cdn-cgi/image/widt\\u0074h=640,quality=90/images/foo.webp" />;\n',
        encoding="utf-8",
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["matched"] == 1
    assert artifact["runtimeImageIssues"]
    assert artifact["runtimeImageIssues"][0]["kind"] == "local-cdn-optimizer-path"
    assert "widt\\u0074h" in artifact["runtimeImageIssues"][0]["snippet"]



def test_image_fidelity_skips_when_no_visible_images_json(tmp_path: Path) -> None:
    """Missing visible-images.json → status=pass, exit 0 (no-op, not an error).

    Mirrors runtime-spec-coverage.sh skip behavior: the verification-plan
    only wires this row when visible-images.json exists, but the script must
    still tolerate a missing input gracefully — defensive parity in case the
    script is invoked outside the dispatcher.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    impl.mkdir()
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "pass"



def test_image_fidelity_rejects_hidden_reference_manifest_only_usage(tmp_path: Path) -> None:
    """Hidden reference manifests are not rendered asset usage.

    Loop validation found impls that stuffed every ref URL into a hidden
    `reference-manifest` node so static string matching passed while the
    visible page still used placeholders. image-fidelity must ignore that
    manifest surface and fail the actually unmatched images.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    urls = [f"https://cdn.example.com/food-{i}.webp" for i in range(5)]
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": url, "element": f"img.food-{i}"}
        for i, url in enumerate(urls)
    ]))
    (impl / "src" / "reference-manifest.tsx").write_text(
        "export function ReferenceManifest() {\n"
        "  return <div className=\"reference-manifest\" hidden>\n"
        + "\n".join(f"    <span>{url}</span>" for url in urls)
        + "\n  </div>;\n}\n",
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "image-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=30,
    )

    assert proc.returncode == 1, f"hidden manifest must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "image-fidelity.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["matched"] == 0
    assert len(artifact["unmatched"]) == 5



def test_asset_utilization_rejects_hidden_reference_manifest_only_usage(tmp_path: Path) -> None:
    """asset-utilization must not count hidden reference-manifest strings as usage."""
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    urls = [f"https://cdn.example.com/asset-{i}.png" for i in range(5)]
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": url, "element": f"img.asset-{i}"}
        for i, url in enumerate(urls)
    ]))
    (impl / "src" / "App.tsx").write_text(
        "export default function App() {\n"
        "  return <div className=\"reference-manifest\" style={{ display: 'none' }}>\n"
        + "\n".join(f"    <span>{url}</span>" for url in urls)
        + "\n  </div>;\n}\n",
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "asset-utilization-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl / "src")],
        capture_output=True, text=True, timeout=30,
    )

    assert proc.returncode == 1, f"hidden manifest must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "asset-utilization.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["referenced"] == 0
    assert "reference-manifest" in artifact["reason"]



def test_asset_utilization_rejects_low_opacity_asset_rail_usage(tmp_path: Path) -> None:
    """Bulk low-opacity/offscreen asset rails are not original-position usage."""
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    urls = [f"https://cdn.example.com/photo-{i}.webp" for i in range(6)]
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": url, "element": f"section:nth-child({i + 1}) img"}
        for i, url in enumerate(urls)
    ]))
    (impl / "src" / "App.tsx").write_text(
        "export default function App() {\n"
        "  return <div className=\"asset-rail fixed bottom-0 opacity-10 pointer-events-none blur-sm\" aria-hidden>\n"
        + "\n".join(f"    <img src=\"/images/{Path(url).name}\" />" for url in urls)
        + "\n  </div>;\n}\n",
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "asset-utilization-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl / "src")],
        capture_output=True, text=True, timeout=30,
    )

    assert proc.returncode == 1, f"asset rail must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "asset-utilization.json").read_text())
    assert artifact["status"] == "fail"
    assert "asset rail" in artifact["reason"]



def test_bundle_impl_coverage_script_fails_when_libs_missing(tmp_path: Path) -> None:
    """End-to-end: bundle-map detects gsap+lenis, impl/package.json lacks both → exit 1.
    """
    import subprocess
    work = tmp_path / "benchmark" / "work" / "deadbee"
    ref = work / "ref"
    impl = work / "impl"
    ref.mkdir(parents=True)
    impl.mkdir(parents=True)
    (ref / "bundle-map.json").write_text(json.dumps({
        "chunks": {"v.js": {"role": "vendor", "libs": ["gsap-like-strings", "motion-like"]}},
        "notes": "lenis on <html>",
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl", "dependencies": {"next": "16", "react": "19", "react-dom": "19"},
    }))
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "bundle-impl-coverage-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl / "package.json")],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 1, f"missing libs must fail: {proc.stderr}"
    out = json.loads((ref / "bundle-impl-coverage.json").read_text())
    assert out["status"] == "fail"
    sigs = {m["signature"] for m in out["missingDeps"]}
    assert "gsap-like-strings" in sigs
    assert "motion-like" in sigs
    assert "lenis" in sigs



def test_bundle_impl_coverage_script_passes_when_all_installed(tmp_path: Path) -> None:
    import subprocess
    work = tmp_path / "benchmark" / "work" / "deadbee"
    ref = work / "ref"
    impl = work / "impl"
    ref.mkdir(parents=True)
    impl.mkdir(parents=True)
    (ref / "bundle-map.json").write_text(json.dumps({
        "chunks": {"v.js": {"role": "vendor", "libs": ["gsap-like-strings"]}},
        "notes": "lenis on <html>",
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl",
        "dependencies": {"next": "16", "gsap": "3.12", "lenis": "1.0"},
    }))
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "bundle-impl-coverage-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl / "package.json")],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, f"all installed must pass: {proc.stderr}"
