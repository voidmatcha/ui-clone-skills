#!/usr/bin/env python3
"""Measure source-attributed context pressure in Claude Code transcripts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BUCKETS = (
    "skill_docs",
    "tmp_ref_artifacts",
    "agent_browser_eval",
    "agent_browser_other",
    "bash_output",
    "other",
)

USAGE_FIELDS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)
INPUT_USAGE_FIELDS = USAGE_FIELDS[:3]
DOMINANCE_SHARE_THRESHOLD = 0.5

_AGENT_BROWSER_RE = re.compile(r"(?<![\w.-])(?:[^\s;&|]*/)?agent-browser(?:\s|$)")
_EVAL_RE = re.compile(r"(?<![\w.-])eval(?:\s|$)")
_DIRECT_READ_RE = re.compile(r"(?<![\w.-])(?:cat|sed|head|tail|nl|jq|rg|grep|awk|wc|less)(?:\s|$)")
_SKILL_PATH_RE = re.compile(r"(?<![\w./-])(?:\./)?(skills/[^\s'\";|()<>]+\.md)")
_TMP_REF_PATH_RE = re.compile(r"(?<![\w./-])(?:\./)?(tmp/ref/[^\s'\";|()<>]+)")


@dataclass(frozen=True)
class SourceInfo:
    bucket: str
    detail: str = ""
    command_shape: str = ""


@dataclass(frozen=True)
class ToolCall:
    name: str
    tool_input: dict[str, Any]
    source: SourceInfo


@dataclass
class BucketStat:
    introduced_estimated_tokens: int = 0
    introduced_bytes: int = 0
    synthetic_estimated_tokens: int = 0
    events: int = 0
    allocated: dict[str, float] = field(
        default_factory=lambda: {name: 0.0 for name in INPUT_USAGE_FIELDS}
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "introduced_estimated_tokens": self.introduced_estimated_tokens,
            "introduced_bytes": self.introduced_bytes,
            "synthetic_estimated_tokens": self.synthetic_estimated_tokens,
            "events": self.events,
            "allocated": dict(self.allocated),
        }


@dataclass
class DetailStat:
    bucket: str
    introduced_estimated_tokens: int = 0
    introduced_bytes: int = 0
    synthetic_estimated_tokens: int = 0
    events: int = 0


@dataclass
class SessionReport:
    path: Path
    bytes_scanned: int
    source_bytes_at_snapshot: int = 0
    source_mtime_ns: int = 0
    sha256: str = ""
    line_count: int = 0
    malformed_lines: int = 0
    api_calls: int = 0
    usage: dict[str, int] = field(default_factory=lambda: {name: 0 for name in USAGE_FIELDS})
    buckets: dict[str, BucketStat] = field(
        default_factory=lambda: {name: BucketStat() for name in BUCKETS}
    )
    active: dict[str, float] = field(default_factory=lambda: {name: 0.0 for name in BUCKETS})
    tool_calls: dict[str, ToolCall] = field(default_factory=dict)
    tool_counts: Counter[str] = field(default_factory=Counter)
    seen_usage: set[str] = field(default_factory=set)
    seen_content: set[str] = field(default_factory=set)
    seen_results: set[str] = field(default_factory=set)
    file_stats: dict[str, DetailStat] = field(default_factory=dict)
    command_stats: dict[str, DetailStat] = field(default_factory=dict)
    active_files: dict[str, float] = field(default_factory=dict)
    active_commands: dict[str, float] = field(default_factory=dict)
    compact_events: list[dict[str, Any]] = field(default_factory=list)
    compactions: int = 0
    max_precompact_tokens: int = 0
    segments: int = 1
    observable_tokens_at_usage: float = 0.0
    effective_input_tokens: int = 0
    max_effective_input_tokens: int = 0
    last_effective_input_tokens: int = 0
    clone_tool_events: int = 0
    first_timestamp: str = ""
    last_timestamp: str = ""

    def reconciliation(self) -> dict[str, float]:
        return {
            field_name: float(self.usage[field_name])
            - sum(stat.allocated[field_name] for stat in self.buckets.values())
            for field_name in INPUT_USAGE_FIELDS
        }

    def to_dict(self) -> dict[str, Any]:
        top_files: list[dict[str, Any]] = [
            {
                "path": path,
                "bucket": stat.bucket,
                "introduced_estimated_tokens": stat.introduced_estimated_tokens,
                "introduced_bytes": stat.introduced_bytes,
                "synthetic_estimated_tokens": stat.synthetic_estimated_tokens,
                "events": stat.events,
            }
            for path, stat in self.file_stats.items()
        ]
        top_files.sort(key=lambda row: (-int(row["introduced_estimated_tokens"]), str(row["path"])))
        top_commands: list[dict[str, Any]] = [
            {
                "shape": shape,
                "bucket": stat.bucket,
                "introduced_estimated_tokens": stat.introduced_estimated_tokens,
                "introduced_bytes": stat.introduced_bytes,
                "events": stat.events,
            }
            for shape, stat in self.command_stats.items()
        ]
        top_commands.sort(
            key=lambda row: (-int(row["introduced_estimated_tokens"]), str(row["shape"]))
        )
        return {
            "path": _redact_display_path(str(self.path)),
            "bytes_scanned": self.bytes_scanned,
            "source_bytes_at_snapshot": self.source_bytes_at_snapshot,
            "source_mtime_ns": self.source_mtime_ns,
            "sha256": self.sha256,
            "line_count": self.line_count,
            "malformed_lines": self.malformed_lines,
            "api_calls": self.api_calls,
            "usage": dict(self.usage),
            "buckets": {name: self.buckets[name].to_dict() for name in BUCKETS},
            "reconciliation": self.reconciliation(),
            "compactions": self.compactions,
            "max_precompact_tokens": self.max_precompact_tokens,
            "compact_events": list(self.compact_events),
            "segments": self.segments,
            "top_files": top_files,
            "top_commands": top_commands,
            "tool_counts": dict(self.tool_counts),
            "effective_input_tokens": self.effective_input_tokens,
            "max_effective_input_tokens": self.max_effective_input_tokens,
            "last_effective_input_tokens": self.last_effective_input_tokens,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "duration_hours": _duration_hours(self.first_timestamp, self.last_timestamp),
            "session": self.path.stem,
            "clone_tool_events": self.clone_tool_events,
            "is_clone_session": self.clone_tool_events >= 5,
            "observable_context_coverage": (
                self.observable_tokens_at_usage / self.effective_input_tokens
                if self.effective_input_tokens
                else 0.0
            ),
        }

    def add_introduction(
        self,
        source: SourceInfo,
        text: str,
        *,
        synthetic: bool = False,
    ) -> None:
        encoded_bytes = len(text.encode("utf-8", errors="replace"))
        estimated_tokens = estimate_text_tokens(text)
        stat = self.buckets[source.bucket]
        stat.introduced_estimated_tokens += estimated_tokens
        stat.introduced_bytes += encoded_bytes
        stat.synthetic_estimated_tokens += estimated_tokens if synthetic else 0
        stat.events += 1
        self.active[source.bucket] += estimated_tokens

        if source.detail:
            detail = self.file_stats.setdefault(source.detail, DetailStat(source.bucket))
            detail.introduced_estimated_tokens += estimated_tokens
            detail.introduced_bytes += encoded_bytes
            detail.synthetic_estimated_tokens += estimated_tokens if synthetic else 0
            detail.events += 1
            self.active_files[source.detail] = (
                self.active_files.get(source.detail, 0.0) + estimated_tokens
            )
        if source.command_shape:
            command = self.command_stats.setdefault(source.command_shape, DetailStat(source.bucket))
            command.introduced_estimated_tokens += estimated_tokens
            command.introduced_bytes += encoded_bytes
            command.events += 1
            self.active_commands[source.command_shape] = (
                self.active_commands.get(source.command_shape, 0.0) + estimated_tokens
            )

    def add_usage(self, usage: dict[str, int]) -> None:
        self.api_calls += 1
        for field_name in USAGE_FIELDS:
            self.usage[field_name] += usage[field_name]

        effective_input = sum(usage[name] for name in INPUT_USAGE_FIELDS)
        self.effective_input_tokens += effective_input
        self.max_effective_input_tokens = max(self.max_effective_input_tokens, effective_input)
        self.last_effective_input_tokens = effective_input
        if effective_input <= 0:
            return

        observed = sum(self.active.values())
        self.observable_tokens_at_usage += min(observed, effective_input)
        weights = dict(self.active)
        if observed <= 0:
            weights["other"] = float(effective_input)
        elif observed < effective_input:
            weights["other"] += effective_input - observed
        elif observed > effective_input:
            scale = effective_input / observed
            weights = {name: value * scale for name, value in weights.items()}

        denominator = sum(weights.values())
        if denominator <= 0:
            weights["other"] = float(effective_input)
            denominator = float(effective_input)
        for field_name in INPUT_USAGE_FIELDS:
            amount = usage[field_name]
            for bucket, weight in weights.items():
                self.buckets[bucket].allocated[field_name] += amount * weight / denominator

    def compact(self, obj: dict[str, Any]) -> None:
        metadata = obj.get("compactMetadata") or {}
        pre_tokens = _safe_int(metadata.get("preTokens"))
        post_tokens = _safe_int(metadata.get("postTokens"))
        dominant = (
            max(self.active, key=lambda name: self.active[name])
            if any(self.active.values())
            else "other"
        )
        top_files: list[dict[str, Any]] = [
            {"path": path, "estimated_tokens": tokens} for path, tokens in self.active_files.items()
        ]
        top_files.sort(key=lambda row: (-float(str(row["estimated_tokens"])), str(row["path"])))
        top_commands: list[dict[str, Any]] = [
            {"shape": shape, "estimated_tokens": tokens}
            for shape, tokens in self.active_commands.items()
        ]
        top_commands.sort(key=lambda row: (-float(str(row["estimated_tokens"])), str(row["shape"])))
        self.compact_events.append(
            {
                "segment": self.segments,
                "timestamp": obj.get("timestamp"),
                "trigger": metadata.get("trigger"),
                "pre_tokens": pre_tokens,
                "post_tokens": post_tokens,
                "dominant_bucket": dominant,
                "active_estimated_tokens": dict(self.active),
                "top_files": top_files[:5],
                "top_commands": top_commands[:5],
            }
        )
        self.compactions += 1
        self.segments += 1
        self.max_precompact_tokens = max(self.max_precompact_tokens, pre_tokens)
        self.active = {name: 0.0 for name in BUCKETS}
        self.active["other"] = float(post_tokens)
        self.active_files = {}
        self.active_commands = {}


@dataclass
class CorpusReport:
    reports: list[SessionReport]
    long_clone_reports: list[SessionReport]
    snapshot_at: str

    def to_dict(self) -> dict[str, Any]:
        aggregate = _aggregate_reports(self.reports)
        long_clone = _aggregate_reports(self.long_clone_reports)
        clone_reports = [report for report in self.reports if report.clone_tool_events >= 5]
        non_clone_reports = [report for report in self.reports if report.clone_tool_events < 5]
        manifest_text = "\n".join(
            f"{report.path.name}\t{report.bytes_scanned}\t{report.sha256}"
            for report in sorted(self.reports, key=lambda item: item.path.name)
        )
        aggregate["snapshot_at"] = self.snapshot_at
        aggregate["manifest_sha256"] = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
        aggregate["sessions"] = [_session_summary(report) for report in self.reports]
        aggregate["long_clone"] = long_clone
        aggregate["session_type_cohorts"] = {
            "clone": _aggregate_reports(clone_reports),
            "non_clone": _aggregate_reports(non_clone_reports),
        }
        aggregate["long_clone_sessions"] = [
            _session_summary(report) for report in self.long_clone_reports
        ]
        aggregate["high_context_sessions"] = [
            _session_summary(report)
            for report in sorted(
                (
                    item
                    for item in self.reports
                    if max(item.max_precompact_tokens, item.max_effective_input_tokens) >= 100_000
                ),
                key=lambda item: (
                    -max(item.max_precompact_tokens, item.max_effective_input_tokens),
                    -item.effective_input_tokens,
                    item.path.name,
                ),
            )[:20]
        ]
        aggregate["compact_events"] = [
            {"session": report.path.stem, **event}
            for report in self.reports
            for event in report.compact_events
        ]
        return aggregate


def normalize_repo_path(raw_path: str, repo_root: Path) -> str:
    """Return a stable repo-relative path and redact the user's home prefix."""
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    path = path.resolve(strict=False)
    root = repo_root.resolve(strict=False)
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        pass
    return _redact_display_path(path.as_posix())


def _redact_display_path(raw_path: str) -> str:
    path = Path(raw_path).expanduser().resolve(strict=False).as_posix()
    home = Path.home().resolve(strict=False).as_posix()
    if path == home:
        display = "~"
    elif path.startswith(home + "/"):
        display = "~/" + path[len(home) + 1 :]
    else:
        display = re.sub(
            r"^/(?:private/)?var/folders/[^/]+/[^/]+/T/(?:pytest-of-[^/]+/)?",
            "<temp>/",
            path,
        )
        display = re.sub(r"^/(?:private/)?tmp/", "<temp>/", display)
    return display.replace(Path.home().name, "<user>")


def _path_from_input(tool_input: dict[str, Any]) -> str:
    for key in ("file_path", "path", "relative_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _command_paths(command: str, repo_root: Path) -> tuple[list[str], list[str]]:
    normalized = command.replace(repo_root.resolve(strict=False).as_posix() + "/", "")
    skill_paths = [
        normalize_repo_path(match, repo_root) for match in _SKILL_PATH_RE.findall(normalized)
    ]
    tmp_ref_paths = [
        normalize_repo_path(match, repo_root) for match in _TMP_REF_PATH_RE.findall(normalized)
    ]
    return skill_paths, tmp_ref_paths


def _command_shape(command: str, repo_root: Path) -> str:
    compact = " ".join(command.split())
    compact = compact.replace(repo_root.resolve(strict=False).as_posix() + "/", "")
    compact = re.sub(r"https?://\S+", "<url>", compact)
    compact = re.sub(r"(['\"]).*?\1", "<arg>", compact)
    compact = re.sub(r"\b[A-Z][A-Z0-9_]*=[^\s;&|]+", "<env>", compact)
    compact = re.sub(
        r"/(?:Users/[^/]+|(?:private/)?var/folders/[^/]+/[^/]+/T|private/tmp|tmp)/[^\s;&|]+",
        "<path>",
        compact,
    )
    compact = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b|\b[0-9a-f]{24,}\b",
        "<id>",
        compact,
        flags=re.IGNORECASE,
    )
    compact = re.sub(r"\b\d+(?:\.\d+)?\b", "<n>", compact)
    return compact[:180]


def classify_tool_source(
    tool_name: str,
    tool_input: dict[str, Any],
    repo_root: Path,
) -> SourceInfo:
    """Classify the result payload produced by one tool invocation."""
    raw_path = _path_from_input(tool_input)
    if raw_path:
        normalized = normalize_repo_path(raw_path, repo_root)
        if normalized.startswith("skills/") and normalized.endswith(".md"):
            return SourceInfo("skill_docs", normalized)
        if normalized.startswith("tmp/ref/"):
            return SourceInfo("tmp_ref_artifacts", normalized)

    if tool_name != "Bash":
        return SourceInfo("other", normalized if raw_path else "")

    command = str(tool_input.get("command") or "")
    shape = _command_shape(command, repo_root)
    if _AGENT_BROWSER_RE.search(command):
        bucket = "agent_browser_eval" if _EVAL_RE.search(command) else "agent_browser_other"
        return SourceInfo(bucket, command_shape=shape)

    skill_paths, tmp_ref_paths = _command_paths(command, repo_root)
    if _DIRECT_READ_RE.search(command):
        if skill_paths and not tmp_ref_paths:
            return SourceInfo("skill_docs", skill_paths[0], shape)
        if tmp_ref_paths and not skill_paths:
            return SourceInfo("tmp_ref_artifacts", tmp_ref_paths[0], shape)
    return SourceInfo("bash_output", command_shape=shape)


def estimate_text_tokens(text: str) -> int:
    """Estimate text tokens consistently without claiming tokenizer exactness."""
    if not text:
        return 0
    return max(1, math.ceil(len(text.encode("utf-8", errors="replace")) / 4))


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _duration_hours(start: str, end: str) -> float:
    if not start or not end:
        return 0.0
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (end_dt - start_dt).total_seconds() / 3600)


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (_content_text(item) for item in value)))
    if not isinstance(value, dict):
        return "" if value is None else str(value)

    content_type = value.get("type")
    if content_type in {"image", "image_url"}:
        return "[image payload omitted from text-token estimate]"
    if content_type == "thinking":
        thinking = value.get("thinking")
        return thinking if isinstance(thinking, str) else ""
    if isinstance(value.get("text"), str):
        return str(value["text"])
    if "content" in value:
        return _content_text(value["content"])

    safe_value = {
        key: item for key, item in value.items() if key not in {"signature", "data", "source"}
    }
    return json.dumps(safe_value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _usage_from_message(message: dict[str, Any]) -> dict[str, int] | None:
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    return {name: _safe_int(usage.get(name)) for name in USAGE_FIELDS}


def _usage_key(obj: dict[str, Any], message: dict[str, Any], usage: dict[str, int]) -> str:
    identity = obj.get("requestId") or message.get("id") or obj.get("uuid")
    if identity:
        return str(identity)
    return hashlib.sha256(
        json.dumps(usage, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _content_key(message_id: str, block: Any) -> str:
    if isinstance(block, dict) and block.get("id"):
        return f"{message_id}:id:{block['id']}"
    encoded = json.dumps(block, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{message_id}:sha:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _message_role(obj: dict[str, Any]) -> str:
    record_type = obj.get("type")
    if record_type in {"assistant", "user"}:
        return str(record_type)
    if record_type == "message":
        message = obj.get("message") or {}
        role = message.get("role")
        return str(role) if role in {"assistant", "user"} else ""
    return ""


def _repo_skill_path(tool_input: dict[str, Any], repo_root: Path) -> Path | None:
    skill_name = tool_input.get("skill")
    if not isinstance(skill_name, str) or not skill_name:
        return None
    local_name = skill_name.rsplit(":", 1)[-1]
    candidate = repo_root / "skills" / local_name / "SKILL.md"
    return candidate if candidate.is_file() else None


def _looks_like_clone_tool(tool_name: str, tool_input: dict[str, Any]) -> bool:
    blob = json.dumps(tool_input, ensure_ascii=False, sort_keys=True).lower()
    if tool_name == "Skill" and any(
        name in blob for name in ("ui-reverse-engineering", "ui-capture", "visual-debug")
    ):
        return True
    return any(
        signal in blob
        for signal in (
            "tmp/ref/",
            "ui_clone.gate",
            "ui_clone.pipeline",
            "ui_clone.goal",
            "section-compare",
            "transition-compare",
            "visual-debug",
            "agent-browser",
        )
    )


def _handle_assistant(report: SessionReport, obj: dict[str, Any], repo_root: Path) -> None:
    message = obj.get("message") or {}
    if not isinstance(message, dict):
        return
    usage = _usage_from_message(message)
    if usage is not None:
        usage_key = _usage_key(obj, message, usage)
        if usage_key not in report.seen_usage:
            report.seen_usage.add(usage_key)
            report.add_usage(usage)

    message_id = str(message.get("id") or obj.get("requestId") or obj.get("uuid") or "")
    content = message.get("content") or []
    blocks = content if isinstance(content, list) else [content]
    for block in blocks:
        key = _content_key(message_id, block)
        if key in report.seen_content:
            continue
        report.seen_content.add(key)
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tool_id = str(block.get("id") or "")
            tool_name = str(block.get("name") or "")
            tool_input = block.get("input") or {}
            if not isinstance(tool_input, dict):
                tool_input = {"value": tool_input}
            source = classify_tool_source(tool_name, tool_input, repo_root)
            if tool_id:
                report.tool_calls[tool_id] = ToolCall(tool_name, tool_input, source)
            report.tool_counts[tool_name] += 1
            if _looks_like_clone_tool(tool_name, tool_input):
                report.clone_tool_events += 1
            report.add_introduction(
                SourceInfo("other"),
                json.dumps(
                    {"name": tool_name, "input": tool_input},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            continue
        text = _content_text(block)
        if text:
            report.add_introduction(SourceInfo("other"), text)


def _handle_tool_result(
    report: SessionReport,
    result: dict[str, Any],
    repo_root: Path,
) -> None:
    tool_id = str(result.get("tool_use_id") or result.get("toolUseID") or "")
    text = _content_text(result.get("content"))
    if not text:
        stdout = result.get("stdout")
        stderr = result.get("stderr")
        text = "\n".join(value for value in (stdout, stderr) if isinstance(value, str) and value)
    result_key = f"{tool_id}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
    if result_key in report.seen_results:
        return
    report.seen_results.add(result_key)

    call = report.tool_calls.get(tool_id)
    source = call.source if call else SourceInfo("other", "unmatched_tool_result")
    report.add_introduction(source, text)
    if call and call.name == "Skill":
        skill_path = _repo_skill_path(call.tool_input, repo_root)
        if skill_path is not None:
            skill_text = skill_path.read_text(encoding="utf-8", errors="replace")
            report.add_introduction(
                SourceInfo("skill_docs", normalize_repo_path(str(skill_path), repo_root)),
                skill_text,
                synthetic=True,
            )


def _handle_user(report: SessionReport, obj: dict[str, Any], repo_root: Path) -> None:
    message = obj.get("message") or {}
    if not isinstance(message, dict):
        return
    content = message.get("content") or []
    blocks = content if isinstance(content, list) else [content]
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            _handle_tool_result(report, block, repo_root)
            continue
        text = _content_text(block)
        if text:
            report.add_introduction(SourceInfo("other"), text)


def analyze_session(
    path: Path,
    repo_root: Path,
    *,
    byte_limit: int | None = None,
    source_mtime_ns: int | None = None,
) -> SessionReport:
    """Stream one top-level Claude JSONL transcript into bounded aggregates."""
    stat = path.stat()
    limit = max(0, stat.st_size if byte_limit is None else byte_limit)
    report = SessionReport(
        path=path,
        bytes_scanned=0,
        source_bytes_at_snapshot=limit,
        source_mtime_ns=stat.st_mtime_ns if source_mtime_ns is None else source_mtime_ns,
    )
    digest = hashlib.sha256()
    with path.open("rb") as transcript:
        for raw_line in transcript:
            if report.bytes_scanned + len(raw_line) > limit:
                break
            report.bytes_scanned += len(raw_line)
            digest.update(raw_line)
            report.line_count += 1
            try:
                obj = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                report.malformed_lines += 1
                continue
            if not isinstance(obj, dict):
                continue
            timestamp = obj.get("timestamp")
            if isinstance(timestamp, str) and timestamp:
                if not report.first_timestamp:
                    report.first_timestamp = timestamp
                report.last_timestamp = timestamp
            if obj.get("type") == "system" and obj.get("subtype") == "compact_boundary":
                report.compact(obj)
                continue
            role = _message_role(obj)
            if role == "assistant":
                _handle_assistant(report, obj, repo_root)
            elif role == "user":
                _handle_user(report, obj, repo_root)
            elif obj.get("type") == "attachment":
                attachment = obj.get("attachment") or {}
                if isinstance(attachment, dict) and attachment.get("type") == "tool_result":
                    _handle_tool_result(report, attachment, repo_root)
    report.sha256 = digest.hexdigest()
    return report


def _session_summary(report: SessionReport) -> dict[str, Any]:
    return {
        "session": report.path.stem,
        "path": _redact_display_path(str(report.path)),
        "bytes_scanned": report.bytes_scanned,
        "source_bytes_at_snapshot": report.source_bytes_at_snapshot,
        "source_mtime_ns": report.source_mtime_ns,
        "sha256": report.sha256,
        "line_count": report.line_count,
        "api_calls": report.api_calls,
        "usage": dict(report.usage),
        "effective_input_tokens": report.effective_input_tokens,
        "max_effective_input_tokens": report.max_effective_input_tokens,
        "last_effective_input_tokens": report.last_effective_input_tokens,
        "compactions": report.compactions,
        "max_precompact_tokens": report.max_precompact_tokens,
        "first_timestamp": report.first_timestamp,
        "last_timestamp": report.last_timestamp,
        "duration_hours": _duration_hours(report.first_timestamp, report.last_timestamp),
        "clone_tool_events": report.clone_tool_events,
        "is_clone_session": report.clone_tool_events >= 5,
        "observable_context_coverage": (
            report.observable_tokens_at_usage / report.effective_input_tokens
            if report.effective_input_tokens
            else 0.0
        ),
    }


def _aggregate_reports(reports: list[SessionReport]) -> dict[str, Any]:
    usage = {name: sum(report.usage[name] for report in reports) for name in USAGE_FIELDS}
    bucket_rows: dict[str, dict[str, Any]] = {}
    for bucket in BUCKETS:
        bucket_rows[bucket] = {
            "introduced_estimated_tokens": sum(
                report.buckets[bucket].introduced_estimated_tokens for report in reports
            ),
            "introduced_bytes": sum(report.buckets[bucket].introduced_bytes for report in reports),
            "synthetic_estimated_tokens": sum(
                report.buckets[bucket].synthetic_estimated_tokens for report in reports
            ),
            "events": sum(report.buckets[bucket].events for report in reports),
            "allocated": {
                field_name: sum(report.buckets[bucket].allocated[field_name] for report in reports)
                for field_name in INPUT_USAGE_FIELDS
            },
        }

    file_totals: dict[str, dict[str, Any]] = {}
    command_totals: dict[str, dict[str, Any]] = {}
    for report in reports:
        for path, stat in report.file_stats.items():
            total = file_totals.setdefault(
                path,
                {
                    "path": path,
                    "bucket": stat.bucket,
                    "introduced_estimated_tokens": 0,
                    "introduced_bytes": 0,
                    "synthetic_estimated_tokens": 0,
                    "events": 0,
                    "sessions": 0,
                },
            )
            total["introduced_estimated_tokens"] += stat.introduced_estimated_tokens
            total["introduced_bytes"] += stat.introduced_bytes
            total["synthetic_estimated_tokens"] += stat.synthetic_estimated_tokens
            total["events"] += stat.events
            total["sessions"] += 1
        for shape, stat in report.command_stats.items():
            total = command_totals.setdefault(
                shape,
                {
                    "shape": shape,
                    "bucket": stat.bucket,
                    "introduced_estimated_tokens": 0,
                    "introduced_bytes": 0,
                    "events": 0,
                    "sessions": 0,
                },
            )
            total["introduced_estimated_tokens"] += stat.introduced_estimated_tokens
            total["introduced_bytes"] += stat.introduced_bytes
            total["events"] += stat.events
            total["sessions"] += 1

    allocated_totals = {
        field_name: sum(bucket_rows[bucket]["allocated"][field_name] for bucket in BUCKETS)
        for field_name in INPUT_USAGE_FIELDS
    }
    effective_input = sum(usage[name] for name in INPUT_USAGE_FIELDS)
    observable = sum(report.observable_tokens_at_usage for report in reports)
    top_files = sorted(
        file_totals.values(),
        key=lambda row: (-int(row["introduced_estimated_tokens"]), str(row["path"])),
    )
    top_commands = sorted(
        command_totals.values(),
        key=lambda row: (-int(row["introduced_estimated_tokens"]), str(row["shape"])),
    )
    return {
        "file_count": len(reports),
        "bytes_scanned": sum(report.bytes_scanned for report in reports),
        "source_bytes_at_snapshot": sum(report.source_bytes_at_snapshot for report in reports),
        "line_count": sum(report.line_count for report in reports),
        "malformed_lines": sum(report.malformed_lines for report in reports),
        "api_calls": sum(report.api_calls for report in reports),
        "usage": usage,
        "effective_input_tokens": effective_input,
        "buckets": bucket_rows,
        "reconciliation": {
            field_name: float(usage[field_name]) - allocated_totals[field_name]
            for field_name in INPUT_USAGE_FIELDS
        },
        "compactions": sum(report.compactions for report in reports),
        "max_precompact_tokens": max(
            (report.max_precompact_tokens for report in reports), default=0
        ),
        "max_effective_input_tokens": max(
            (report.max_effective_input_tokens for report in reports), default=0
        ),
        "observable_context_coverage": observable / effective_input if effective_input else 0.0,
        "top_files": top_files[:50],
        "top_files_by_bucket": {
            bucket: [row for row in top_files if row["bucket"] == bucket][:20] for bucket in BUCKETS
        },
        "top_commands": top_commands[:50],
        "top_commands_by_bucket": {
            bucket: [row for row in top_commands if row["bucket"] == bucket][:20]
            for bucket in BUCKETS
        },
    }


def analyze_corpus(
    paths: list[Path],
    repo_root: Path,
    *,
    long_session_count: int = 10,
) -> CorpusReport:
    """Analyze transcripts and retain a token-cost-ranked long clone cohort."""
    snapshot_at = datetime.now(UTC).isoformat()
    snapshots = [(path, path.stat()) for path in paths]
    reports = [
        analyze_session(
            path,
            repo_root,
            byte_limit=stat.st_size,
            source_mtime_ns=stat.st_mtime_ns,
        )
        for path, stat in snapshots
    ]
    reports.sort(key=lambda report: (-report.effective_input_tokens, report.path.name))
    clone_reports = [report for report in reports if report.clone_tool_events >= 5]
    return CorpusReport(
        reports,
        clone_reports[: max(0, long_session_count)],
        snapshot_at,
    )


def _integer(value: int | float) -> str:
    return f"{value:,.0f}"


def _percentage(numerator: int | float, denominator: int | float) -> str:
    return f"{100 * numerator / denominator:.2f}%" if denominator else "0.00%"


def _bucket_table(aggregate: dict[str, Any]) -> list[str]:
    rows = [
        "| Bucket | Introduced est. | Synthetic est. | input_tokens | "
        "cache_creation_input_tokens | cache_read_input_tokens | Effective share |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    total_effective = aggregate["effective_input_tokens"]
    for bucket in BUCKETS:
        stat = aggregate["buckets"][bucket]
        allocated = stat["allocated"]
        effective = sum(allocated.values())
        rows.append(
            "| {bucket} | {introduced} | {synthetic} | {input_tokens} | "
            "{cache_creation} | {cache_read} | {share} |".format(
                bucket=bucket,
                introduced=_integer(stat["introduced_estimated_tokens"]),
                synthetic=_integer(stat["synthetic_estimated_tokens"]),
                input_tokens=_integer(allocated["input_tokens"]),
                cache_creation=_integer(allocated["cache_creation_input_tokens"]),
                cache_read=_integer(allocated["cache_read_input_tokens"]),
                share=_percentage(effective, total_effective),
            )
        )
    return rows


def render_markdown(corpus: CorpusReport) -> str:
    """Render a bounded evidence report from the same aggregate used for JSON."""
    payload = corpus.to_dict()
    long_clone = payload["long_clone"]
    long_effective = long_clone["effective_input_tokens"]
    skill_stat = long_clone["buckets"]["skill_docs"]
    bash_stat = long_clone["buckets"]["bash_output"]
    skill_effective = sum(skill_stat["allocated"].values())
    bash_effective = sum(bash_stat["allocated"].values())
    explicit_skill_tokens = (
        skill_stat["introduced_estimated_tokens"] - skill_stat["synthetic_estimated_tokens"]
    )
    skill_share = skill_effective / long_effective if long_effective else 0.0
    long_coverage = long_clone["observable_context_coverage"]
    if long_clone["file_count"] == 0 or long_effective == 0 or long_coverage == 0:
        finding = (
            "The measured long-clone attribution is **inconclusive** because no "
            "non-empty observable long-clone cohort was available."
        )
    elif skill_share > DOMINANCE_SHARE_THRESHOLD:
        finding = (
            "The measured long-clone allocation **supports the claim that skill docs "
            f"dominate**: `skill_docs` accounts for {_percentage(skill_effective, long_effective)}, "
            f"above the {_percentage(DOMINANCE_SHARE_THRESHOLD, 1)} dominance threshold."
        )
    else:
        finding = (
            "The measured long-clone allocation does **not** support the claim that skill "
            f"docs dominate: `skill_docs` accounts for {_percentage(skill_effective, long_effective)} "
            f"of effective input versus {_percentage(bash_effective, long_effective)} for generic "
            f"Bash output, below the {_percentage(DOMINANCE_SHARE_THRESHOLD, 1)} dominance threshold."
        )
    lines = [
        "# Claude context source attribution",
        "",
        "## Method",
        "",
        "- Usage fields are model-reported and deduplicated by request/message identity.",
        "- Source payload tokens are UTF-8 bytes / 4 estimates; they are not tokenizer-exact.",
        "- Per-source input/cache values are proportional allocations over observable active context.",
        "- Repo skill invocations add a labeled synthetic estimate from the current matching `SKILL.md`; explicit reads remain separately derivable.",
        "- `skill_docs` is a conservative repo-doc bucket: a read-shaped Bash result is counted when a repo-relative `skills/**/*.md` path is its only matched special source.",
        "- Hidden system/tool prompts and compact summaries remain in `other`; compact boundaries reset source attribution conservatively.",
        f"- Dominance means more than {_percentage(DOMINANCE_SHARE_THRESHOLD, 1)} of effective input in a non-empty cohort with observable context.",
        "",
        "## Corpus",
        "",
        f"- Snapshot: `{payload['snapshot_at']}`",
        f"- Manifest SHA-256: `{payload['manifest_sha256']}`",
        f"- Files: {_integer(payload['file_count'])}",
        f"- Bytes streamed: {_integer(payload['bytes_scanned'])}",
        f"- API calls: {_integer(payload['api_calls'])}",
        f"- Compactions: {_integer(payload['compactions'])}",
        f"- Observable context coverage: {_percentage(payload['observable_context_coverage'], 1)}",
        "",
        *(_bucket_table(payload)),
        "",
        "## Session types",
        "",
        "Cohorts are token-weighted; clone sessions have at least five clone-tool events.",
        "",
        "| Type | Sessions | Effective input | API calls | input_tokens | cache_creation_input_tokens | cache_read_input_tokens | output_tokens | Compactions | Max API context | Max pre-compact |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        *[
            "| {name} | {files} | {effective} | {calls} | {input_tokens} | {cache_creation} | {cache_read} | {output_tokens} | {compactions} | {max_api} | {max_precompact} |".format(
                name=name,
                files=_integer(stat["file_count"]),
                effective=_integer(stat["effective_input_tokens"]),
                calls=_integer(stat["api_calls"]),
                input_tokens=_integer(stat["usage"]["input_tokens"]),
                cache_creation=_integer(stat["usage"]["cache_creation_input_tokens"]),
                cache_read=_integer(stat["usage"]["cache_read_input_tokens"]),
                output_tokens=_integer(stat["usage"]["output_tokens"]),
                compactions=_integer(stat["compactions"]),
                max_api=_integer(stat["max_effective_input_tokens"]),
                max_precompact=_integer(stat["max_precompact_tokens"]),
            )
            for name, stat in payload["session_type_cohorts"].items()
        ],
        "",
        "## Long clone cohort",
        "",
        f"Top {_integer(long_clone['file_count'])} clone sessions by effective input usage.",
        "",
        *(_bucket_table(long_clone)),
        "",
        "## Finding",
        "",
        finding,
        "",
        (
            f"The cohort introduced an estimated {_integer(explicit_skill_tokens)} tokens from "
            f"explicit skill-doc reads plus {_integer(skill_stat['synthetic_estimated_tokens'])} "
            "synthetic tokens for repo skill invocations. The `other` residual remains a material "
            "limit on causal attribution, so the finding applies only within the measured and "
            "conservatively allocated scope."
        ),
        "",
        "| Session | Effective input | API calls | Max API context | Last API context | Compactions | Max pre-compact | Hours | Coverage |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for session in payload["long_clone_sessions"]:
        lines.append(
            "| {session} | {effective} | {calls} | {max_api} | {last_api} | {compacts} | {pre} | {hours:.1f} | {coverage} |".format(
                session=session["session"],
                effective=_integer(session["effective_input_tokens"]),
                calls=_integer(session["api_calls"]),
                max_api=_integer(session["max_effective_input_tokens"]),
                last_api=_integer(session["last_effective_input_tokens"]),
                compacts=_integer(session["compactions"]),
                pre=_integer(session["max_precompact_tokens"]),
                hours=session["duration_hours"],
                coverage=_percentage(session["observable_context_coverage"], 1),
            )
        )

    lines.extend(
        [
            "",
            "## High-context sessions",
            "",
            "These are ranked by the larger of model-reported API context and compact pre-token high-water; no fixed model limit is assumed.",
            "",
            "| Session | Max API context | Max pre-compact | Last API context | Compactions |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for session in payload["high_context_sessions"][:10]:
        lines.append(
            "| {session} | {max_api} | {pre} | {last_api} | {compacts} |".format(
                session=session["session"],
                max_api=_integer(session["max_effective_input_tokens"]),
                pre=_integer(session["max_precompact_tokens"]),
                last_api=_integer(session["last_effective_input_tokens"]),
                compacts=_integer(session["compactions"]),
            )
        )

    lines.extend(
        [
            "",
            "## Pre-compact pressure",
            "",
            "| Session | Segment | Pre tokens | Dominant bucket | Top file | Top command |",
            "|---|---:|---:|---|---|---|",
        ]
    )
    compact_events = sorted(
        payload["compact_events"],
        key=lambda event: (-int(event["pre_tokens"]), str(event["session"])),
    )
    for event in compact_events:
        top_file = event["top_files"][0]["path"] if event["top_files"] else ""
        top_command = event["top_commands"][0]["shape"] if event["top_commands"] else ""
        lines.append(
            "| {session} | {segment} | {pre} | {bucket} | `{file}` | `{command}` |".format(
                session=event["session"],
                segment=event["segment"],
                pre=_integer(event["pre_tokens"]),
                bucket=event["dominant_bucket"],
                file=str(top_file).replace("|", "\\|"),
                command=str(top_command).replace("|", "\\|")[:180],
            )
        )

    lines.extend(
        [
            "",
            "## Top files",
            "",
            "| Path | Bucket | Introduced est. | Events | Sessions |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in long_clone["top_files"][:20]:
        lines.append(
            f"| `{row['path']}` | {row['bucket']} | "
            f"{_integer(row['introduced_estimated_tokens'])} | "
            f"{_integer(row['events'])} | {_integer(row['sessions'])} |"
        )

    for bucket, title in (
        ("skill_docs", "Skill-document files"),
        ("tmp_ref_artifacts", "tmp/ref artifact paths"),
    ):
        lines.extend(
            [
                "",
                f"### {title}",
                "",
                "| Path | Introduced est. | Synthetic est. | Events | Sessions |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for row in long_clone["top_files_by_bucket"][bucket][:10]:
            lines.append(
                f"| `{row['path']}` | {_integer(row['introduced_estimated_tokens'])} | "
                f"{_integer(row['synthetic_estimated_tokens'])} | "
                f"{_integer(row['events'])} | {_integer(row['sessions'])} |"
            )

    lines.extend(
        [
            "",
            "## Top command shapes",
            "",
            "| Shape | Bucket | Introduced est. | Events | Sessions |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in long_clone["top_commands"][:20]:
        escaped = row["shape"].replace("|", "\\|")
        lines.append(
            f"| `{escaped}` | {row['bucket']} | "
            f"{_integer(row['introduced_estimated_tokens'])} | "
            f"{_integer(row['events'])} | {_integer(row['sessions'])} |"
        )

    lines.extend(
        [
            "",
            "### agent-browser eval shapes",
            "",
            "| Shape | Introduced est. | Events | Sessions |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in long_clone["top_commands_by_bucket"]["agent_browser_eval"][:10]:
        escaped = row["shape"].replace("|", "\\|")
        lines.append(
            f"| `{escaped}` | {_integer(row['introduced_estimated_tokens'])} | "
            f"{_integer(row['events'])} | {_integer(row['sessions'])} |"
        )

    lines.extend(
        [
            "",
            "## Validation",
            "",
            f"- Malformed lines: {_integer(payload['malformed_lines'])}",
            "- Allocation reconciliation residuals: "
            + ", ".join(f"{name}={value:.9f}" for name, value in payload["reconciliation"].items()),
            "",
        ]
    )
    return "\n".join(lines)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stream Claude Code JSONL transcripts and attribute context by source."
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--markdown-out", type=Path, required=True)
    parser.add_argument("--long-session-count", type=int, default=10)
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Include nested subagent JSONL files; default is top-level sessions only.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    pattern = "**/*.jsonl" if args.recursive else "*.jsonl"
    paths = sorted(path for path in args.input_dir.glob(pattern) if path.is_file())
    if not paths:
        print(f"No JSONL files found under {args.input_dir}", file=sys.stderr)
        return 2

    corpus = analyze_corpus(
        paths,
        args.repo_root.resolve(strict=False),
        long_session_count=max(0, args.long_session_count),
    )
    payload = corpus.to_dict()
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.markdown_out.write_text(render_markdown(corpus), encoding="utf-8")
    print(
        "context-token-attribution: "
        f"files={payload['file_count']} api_calls={payload['api_calls']} "
        f"bytes={payload['bytes_scanned']} malformed={payload['malformed_lines']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
