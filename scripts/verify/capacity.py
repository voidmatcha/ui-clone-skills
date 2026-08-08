#!/usr/bin/env python3
"""Estimate safe browser-concurrency capacity for visual verification.

The probe is intentionally dependency-free and conservative. It reports a
recommended wave size for browser-heavy checks so agents can avoid starting too
many 60fps/video/parallel comparison jobs on memory-constrained machines.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

_DEFAULT_BROWSER_MB = 4400
_DEFAULT_RESERVE_MB = 4096
_DEFAULT_PRESSURE_BUDGET = 0.55


@dataclass
class CapacityReport:
    schemaVersion: int
    source: str
    platform: str
    totalMemoryMb: int
    availableMemoryMb: int
    browserBudgetMb: int
    reserveMb: int
    pressureBudget: float
    usableMemoryMb: int
    maxConcurrentBrowsers: int
    recommendedWaveSize: int
    serialBackendRequired: bool
    leanResources: bool
    notes: list[str]


def _sysconf_memory() -> tuple[int, int]:
    total = 0
    available = 0
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        total = pages * page_size // (1024 * 1024)
    except (OSError, ValueError, AttributeError):
        total = 0
    for key in ("SC_AVPHYS_PAGES", "SC_FREE_PAGES"):
        try:
            pages = int(os.sysconf(key))
        except (OSError, ValueError, AttributeError):
            continue
        if pages > 0:
            available = pages * int(os.sysconf("SC_PAGE_SIZE")) // (1024 * 1024)
            break
    return total, available


def _mac_vm_stat_available_mb() -> int:
    try:
        proc = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if proc.returncode != 0:
        return 0
    page_size = 4096
    free = inactive = speculative = 0
    for line in proc.stdout.splitlines():
        if "page size of" in line:
            digits = "".join(ch for ch in line if ch.isdigit())
            if digits:
                page_size = int(digits)
        value_text = line.split(":", 1)[-1].strip().rstrip(".").replace(".", "")
        try:
            value = int(value_text)
        except ValueError:
            continue
        if line.startswith("Pages free"):
            free = value
        elif line.startswith("Pages inactive"):
            inactive = value
        elif line.startswith("Pages speculative"):
            speculative = value
    pages = free + inactive + speculative
    return pages * page_size // (1024 * 1024) if pages > 0 else 0


def detect_memory_mb() -> tuple[int, int, list[str]]:
    total, available = _sysconf_memory()
    notes: list[str] = []
    if platform.system() == "Darwin":
        mac_available = _mac_vm_stat_available_mb()
        if mac_available > 0:
            available = mac_available
            notes.append("available memory estimated from vm_stat free+inactive+speculative pages")
    if total <= 0:
        notes.append("total memory unavailable; using conservative fallback")
        total = 8192
    if available <= 0:
        notes.append("available memory unavailable; using 35% of total as conservative fallback")
        available = max(512, int(total * 0.35))
    return total, available, notes


def build_capacity_report(
    *,
    total_mb: int | None = None,
    available_mb: int | None = None,
    browser_budget_mb: int = _DEFAULT_BROWSER_MB,
    reserve_mb: int = _DEFAULT_RESERVE_MB,
    pressure_budget: float = _DEFAULT_PRESSURE_BUDGET,
) -> CapacityReport:
    notes: list[str] = []
    if total_mb is None or available_mb is None:
        detected_total, detected_available, detected_notes = detect_memory_mb()
        notes.extend(detected_notes)
        total_mb = detected_total if total_mb is None else total_mb
        available_mb = detected_available if available_mb is None else available_mb

    pressure_limited = int(max(0, total_mb) * pressure_budget)
    usable = max(0, min(max(0, available_mb), pressure_limited) - max(0, reserve_mb))
    max_browsers = max(1, usable // max(1, browser_budget_mb))
    recommended = max(1, min(max_browsers, 3))
    lean = usable < browser_budget_mb * 2
    serial = max_browsers <= 1
    if lean:
        notes.append("leanResources=true: prefer standard/quick tier or serial browser checks")
    return CapacityReport(
        schemaVersion=1,
        source="scripts/verify/capacity.py",
        platform=platform.system().lower() or "unknown",
        totalMemoryMb=int(total_mb),
        availableMemoryMb=int(available_mb),
        browserBudgetMb=int(browser_budget_mb),
        reserveMb=int(reserve_mb),
        pressureBudget=float(pressure_budget),
        usableMemoryMb=int(usable),
        maxConcurrentBrowsers=int(max_browsers),
        recommendedWaveSize=int(recommended),
        serialBackendRequired=serial,
        leanResources=lean,
        notes=notes,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Estimate visual verification browser capacity")
    parser.add_argument("--browser-mb", type=int, default=int(os.environ.get("UI_CLONE_BROWSER_MB", _DEFAULT_BROWSER_MB)))
    parser.add_argument("--reserve-mb", type=int, default=int(os.environ.get("UI_CLONE_RESERVE_MB", _DEFAULT_RESERVE_MB)))
    parser.add_argument("--pressure-budget", type=float, default=float(os.environ.get("UI_CLONE_PRESSURE_BUDGET", _DEFAULT_PRESSURE_BUDGET)))
    parser.add_argument("--total-mb", type=int, default=None, help="Override total memory for tests/planning")
    parser.add_argument("--available-mb", type=int, default=None, help="Override available memory for tests/planning")
    parser.add_argument("--out", type=Path, default=None, help="Optional JSON output path")
    args = parser.parse_args(argv)

    report = build_capacity_report(
        total_mb=args.total_mb,
        available_mb=args.available_mb,
        browser_budget_mb=args.browser_mb,
        reserve_mb=args.reserve_mb,
        pressure_budget=args.pressure_budget,
    )
    payload = asdict(report)
    text = json.dumps(payload, indent=2)
    print(text)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
