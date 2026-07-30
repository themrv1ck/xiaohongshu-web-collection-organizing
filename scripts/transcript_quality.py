#!/usr/bin/env python3
"""Deterministic transcript coverage gate used before Codex classification."""

from __future__ import annotations

from typing import Any


MIN_DURATION_FOR_COVERAGE_GATE_SECONDS = 60
MIN_TRANSCRIPT_COVERAGE_RATIO = 0.30
MIN_PLATFORM_SUBTITLE_COVERAGE_RATIO = 0.60
MIN_SEGMENT_COUNT_WITH_DURATION = 2
MIN_SEGMENT_COUNT_SHORT_VIDEO = 1
MIN_TEXT_CHARS_WITH_DURATION = 40
MAX_SUBTITLE_TIMELINE_RATIO = 1.20
MAX_SUBTITLE_TIMELINE_EXTRA_SECONDS = 20.0


def parse_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value))
    text = str(value or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        parts = [float(part) for part in text.split(":")]
    except ValueError:
        return None
    if len(parts) == 1:
        return max(0.0, parts[0])
    if len(parts) == 2:
        return max(0.0, parts[0] * 60 + parts[1])
    if len(parts) == 3:
        return max(0.0, parts[0] * 3600 + parts[1] * 60 + parts[2])
    return None


def validate_transcript_coverage(
    *, video_duration: Any, segments: list[dict[str, Any]], transcript_source: str
) -> dict[str, Any]:
    duration = parse_seconds(video_duration)
    starts: list[float] = []
    ends: list[float] = []
    char_count = 0
    valid_segments = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        start = parse_seconds(segment.get("start"))
        end = parse_seconds(segment.get("end"))
        text = "".join(str(segment.get("text") or "").split())
        if start is None or end is None or end <= start or not text:
            continue
        valid_segments.append(segment)
        starts.append(start)
        ends.append(end)
        char_count += len(text)
    first_start = min(starts) if starts else None
    last_end = max(ends) if ends else None
    covered = None
    if duration is not None and starts:
        intervals = sorted(
            (max(0.0, start), min(duration, end))
            for start, end in zip(starts, ends)
            if min(duration, end) > max(0.0, start)
        )
        covered = 0.0
        if intervals:
            current_start, current_end = intervals[0]
            for start, end in intervals[1:]:
                if start <= current_end:
                    current_end = max(current_end, end)
                else:
                    covered += current_end - current_start
                    current_start, current_end = start, end
            covered += current_end - current_start
    ratio = min(1.0, max(0.0, covered / duration)) if duration and covered is not None else None
    timeline_limit = max(duration * MAX_SUBTITLE_TIMELINE_RATIO, duration + MAX_SUBTITLE_TIMELINE_EXTRA_SECONDS) if duration else None
    is_subtitle = transcript_source.startswith("subtitle_")
    threshold = MIN_PLATFORM_SUBTITLE_COVERAGE_RATIO if is_subtitle else MIN_TRANSCRIPT_COVERAGE_RATIO
    required_segment_count = (
        MIN_SEGMENT_COUNT_SHORT_VIDEO
        if duration is not None and duration < MIN_DURATION_FOR_COVERAGE_GATE_SECONDS
        else MIN_SEGMENT_COUNT_WITH_DURATION
    )
    reason = "coverage_ok"
    if duration is None:
        reason = "video_duration_missing_for_quality_gate"
    elif is_subtitle and last_end is not None and timeline_limit is not None and last_end > timeline_limit:
        reason = "subtitle_timeline_exceeds_video_duration"
    elif ratio is None or ratio < threshold:
        reason = "coverage_below_threshold"
    elif len(valid_segments) < required_segment_count:
        reason = "segment_count_too_low"
    elif char_count < MIN_TEXT_CHARS_WITH_DURATION:
        reason = "plain_text_char_count_too_low"
    return {
        "video_duration_seconds": duration,
        "transcript_first_start": first_start,
        "transcript_last_end": last_end,
        "transcript_covered_duration": covered,
        "transcript_coverage_ratio": ratio,
        "coverage_threshold": threshold,
        "transcript_segment_count": len(valid_segments),
        "required_segment_count": required_segment_count,
        "transcript_plain_text_char_count": char_count,
        "transcript_source": transcript_source,
        "transcript_quality_reason": reason,
        "transcript_quality_passed": reason == "coverage_ok",
    }
