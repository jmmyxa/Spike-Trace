from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, Mapping, Sequence

from .domain import VideoMetadata
from .errors import ValidationError, VideoError
from .validation_contract import ValidationVideoBinding, canonical_json_bytes, write_new_bytes
from .video import inspect_video, write_proxy_video


@dataclass(frozen=True, slots=True)
class RallyDetectionSettings:
    sample_seconds: float = 0.5
    motion_threshold: float = 12.0
    dead_ball_seconds: float = 2.0
    merge_gap_seconds: float = 1.0
    buffer_before_seconds: float = 3.0
    buffer_after_seconds: float = 3.0


@dataclass(frozen=True, slots=True)
class RallySegment:
    segment_id: str
    source_segment_id: str | None
    set_index: int | None
    rally_id: str
    start_seconds: float
    end_seconds: float
    status: Literal["pending", "rally", "non_rally", "unusable"]
    team_side: Literal["near", "far"] | None
    crop: tuple[int, int, int, int] | None
    buffer_before_seconds: float
    buffer_after_seconds: float
    boundary_source: Literal["motion", "manual"]
    coverage_confirmed: bool
    all_c2_actions_checked: bool
    no_c2_action: bool | None


def detect_rally_candidates(video_path: str | Path, *, settings: RallyDetectionSettings) -> tuple[tuple[float, float], ...]:
    import cv2
    import numpy as np
    metadata = inspect_video(video_path)
    capture = cv2.VideoCapture(str(Path(video_path).expanduser().resolve()))
    if not capture.isOpened():
        raise VideoError(f"OpenCV could not open video: {video_path}")
    try:
        samples: list[tuple[float, float]] = []
        previous = None
        t = 0.0
        while t < metadata.duration_seconds:
            capture.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                t += settings.sample_seconds
                continue
            gray = cv2.cvtColor(cv2.resize(frame, (64, 36)), cv2.COLOR_BGR2GRAY).astype(np.float32)
            diff = float(np.mean(np.abs(gray - previous))) if previous is not None else 0.0
            samples.append((t, diff))
            previous = gray
            t += settings.sample_seconds
    finally:
        capture.release()
    active = [t for t, d in samples if d >= settings.motion_threshold]
    if not active:
        return ()
    runs: list[list[float]] = [[active[0], active[0] + settings.sample_seconds]]
    for t in active[1:]:
        if t - runs[-1][1] < settings.merge_gap_seconds:
            runs[-1][1] = t + settings.sample_seconds
        else:
            runs.append([t, t + settings.sample_seconds])
    merged: list[tuple[float, float]] = []
    for start, end in runs:
        if merged and start - merged[-1][1] < settings.dead_ball_seconds:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((max(0.0, start - settings.buffer_before_seconds), min(metadata.duration_seconds, end + settings.buffer_after_seconds)))
    return tuple(merged)


def _segment(segment_id: str, start: float, end: float, status: str, rally_id: str = "") -> RallySegment:
    return RallySegment(segment_id, None, None, rally_id, start, end, status, None, None, 0.0, 0.0, "motion", False, False, None)


def complete_coverage(candidates: Sequence[tuple[float, float]], *, duration_seconds: float, binding: ValidationVideoBinding) -> tuple[RallySegment, ...]:
    normalized = sorted((float(a), float(b)) for a, b in candidates)
    previous = 0.0
    output: list[RallySegment] = []
    rally_num = 0
    for start, end in normalized:
        if start < 0 or end <= start or end > duration_seconds:
            raise ValidationError("candidate interval is out of bounds")
        if start < previous:
            raise ValidationError("candidate intervals overlap")
        if start > previous:
            output.append(_segment(f"non-rally-{len(output)+1:06d}", previous, start, "non_rally"))
        rally_num += 1
        output.append(_segment(f"rally-{rally_num:06d}", start, end, "rally", f"rally-{rally_num:06d}"))
        previous = end
    if previous < duration_seconds:
        output.append(_segment(f"non-rally-{len(output)+1:06d}", previous, duration_seconds, "non_rally"))
    return tuple(output)


def apply_side_map(segments: Sequence[RallySegment], *, set_intervals: Sequence[Mapping[str, object]], side_intervals: Sequence[Mapping[str, object]], metadata: VideoMetadata) -> tuple[RallySegment, ...]:
    result: list[RallySegment] = []
    for segment in segments:
        if segment.status != "rally":
            result.append(segment); continue
        sets = [s for s in set_intervals if float(s["start_seconds"]) <= segment.start_seconds and float(s["end_seconds"]) >= segment.end_seconds]
        if len(sets) != 1:
            raise ValidationError(f"Expected exactly one set interval for {segment.segment_id}")
        set_index = int(sets[0]["set_index"])
        matches = [s for s in side_intervals if int(s.get("set_index", set_index)) == set_index and float(s["start_seconds"]) < segment.end_seconds and float(s["end_seconds"]) > segment.start_seconds]
        if not matches:
            raise ValidationError(f"No side interval for {segment.segment_id}")
        cursor = segment.start_seconds
        for side in sorted(matches, key=lambda x: float(x["start_seconds"])):
            start, end = max(cursor, float(side["start_seconds"])), min(segment.end_seconds, float(side["end_seconds"]))
            if end <= start: continue
            crop = tuple(int(v) for v in side["crop"])
            if len(crop) != 4 or crop[0] < 0 or crop[1] < 0 or crop[2] <= crop[0] or crop[3] <= crop[1] or crop[2] > metadata.width or crop[3] > metadata.height:
                raise ValidationError("crop exceeds video geometry")
            result.append(replace(segment, segment_id=f"{segment.segment_id}-{len(result)+1}", source_segment_id=segment.segment_id, set_index=set_index, start_seconds=start, end_seconds=end, team_side=side["team_side"], crop=crop, boundary_source="manual"))
            cursor = end
        if cursor < segment.end_seconds - 1e-9:
            raise ValidationError("side intervals do not cover rally")
    return tuple(result)


def validate_rally_queue(segments: Sequence[RallySegment], *, binding: ValidationVideoBinding, require_complete: bool = False) -> None:
    previous = 0.0
    for segment in segments:
        if segment.start_seconds < 0 or segment.end_seconds <= segment.start_seconds or segment.end_seconds > binding.metadata.duration_seconds + 1e-9:
            raise ValidationError("segment bounds are invalid")
        if segment.start_seconds < previous - 1e-9:
            raise ValidationError("segments overlap")
        previous = segment.end_seconds
    if require_complete and (not segments or abs(segments[0].start_seconds) > 1e-9 or abs(previous - binding.metadata.duration_seconds) > 1e-9):
        raise ValidationError("queue coverage is incomplete")


def write_rally_queue(path: str | Path, *, binding: ValidationVideoBinding, segments: Sequence[RallySegment], set_intervals: Sequence[Mapping[str, object]], side_intervals: Sequence[Mapping[str, object]], settings: RallyDetectionSettings, code_sha: str) -> Path:
    validate_rally_queue(segments, binding=binding)
    payload = {"format_version": 1, "binding": {"match_id": binding.match_id, "video_path": binding.repo_video_path, "sha256": binding.sha256, "metadata": binding.metadata.to_dict()}, "settings": asdict(settings), "set_intervals": list(set_intervals), "side_intervals": list(side_intervals), "segments": [asdict(s) for s in segments], "code_sha": code_sha}
    destination = Path(path).expanduser().resolve(); write_new_bytes(destination, canonical_json_bytes(payload)); return destination


def load_rally_queue(path: str | Path, *, binding: ValidationVideoBinding) -> tuple[RallySegment, ...]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("binding", {}).get("sha256", "").lower() != binding.sha256.lower(): raise ValidationError("Queue binding mismatch")
    return tuple(RallySegment(**{**item, "crop": tuple(item["crop"]) if item.get("crop") else None}) for item in data["segments"])


def write_rally_proxies(queue: Sequence[RallySegment], output_dir: str | Path, *, video_root: str | Path | None = None, repo_root: str | Path, output_fps: float = 15.0, max_width: int = 960) -> dict[str, object]:
    output = Path(output_dir).expanduser().resolve()
    if output.exists(): raise ValidationError(f"Output directory already exists: {output}")
    output.mkdir(parents=True)
    clips = output / "clips"; clips.mkdir()
    manifest: list[dict[str, object]] = []
    try:
        root = Path(video_root or repo_root).expanduser().resolve()
        videos = [p for p in root.rglob("*") if p.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv"}]
        if len(videos) != 1:
            raise ValidationError("Unable to resolve the bound source video")
        source = videos[0]
        for segment in queue:
            if segment.status not in {"pending", "rally"}: continue
            destination = clips / f"{segment.segment_id}.mp4"
            write_proxy_video(source, destination, segment.start_seconds, segment.end_seconds, output_fps=output_fps, max_width=max_width, codec="mp4v")
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            manifest.append({"segment_id": segment.segment_id, "path": f"clips/{destination.name}", "sha256": digest, "start_seconds": segment.start_seconds, "end_seconds": segment.end_seconds})
        payload = {"format_version": 1, "proxies": manifest}
        write_new_bytes(output / "proxy-manifest.json", canonical_json_bytes(payload))
        return payload
    except Exception:
        import shutil; shutil.rmtree(output, ignore_errors=True); raise
