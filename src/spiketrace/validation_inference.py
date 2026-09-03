from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .constants import SAMPLING_CONTRACT
from .domain import ActionWindow
from .errors import ValidationError
from .events import merge_action_windows_with_provenance
from .inference import _file_sha256
from .ml import frames_to_tensor, load_checkpoint, require_torch, resolve_device
from .validation_truth import ValidationTruth
from .video import inspect_video, iter_sequential_video_clip_batches, iter_window_times_range


@dataclass(frozen=True, slots=True)
class InferenceSegment:
    segment_id: str
    set_index: int
    start_seconds: float
    end_seconds: float
    team_side: Literal["near", "far"]
    crop: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class ValidationWindow:
    window_index: int
    segment_id: str
    set_index: int
    team_side: str
    start_seconds: float
    end_seconds: float
    action: str
    confidence: float


@dataclass(frozen=True, slots=True)
class ValidationPrediction:
    prediction_id: str
    segment_id: str
    set_index: int
    team_side: str
    start_seconds: float
    end_seconds: float
    action: str
    confidence: float
    source_window_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ValidationInferenceResult:
    windows: tuple[ValidationWindow, ...]
    predictions: tuple[ValidationPrediction, ...]
    settings: dict[str, object]
    checkpoint_sha256: str
    video_sha256: str


def _segments_from_truth(truth: ValidationTruth) -> tuple[InferenceSegment, ...]:
    segments: list[InferenceSegment] = []
    for coverage in truth.coverage:
        if coverage.status not in {"rally", "non_rally", "unusable", "pending"}:
            raise ValidationError("coverage status is invalid")
        if coverage.status != "rally":
            continue
        if isinstance(coverage.set_index, bool) or not isinstance(coverage.set_index, int):
            raise ValidationError("inference segment set_index is invalid")
        for value in (coverage.start_seconds, coverage.end_seconds):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValidationError("inference segment bounds are invalid")
        if coverage.team_side is not None and coverage.team_side not in {"near", "far"}:
            raise ValidationError("inference segment team_side is invalid")
        if coverage.crop is not None and (not isinstance(coverage.crop, (tuple, list)) or len(coverage.crop) != 4):
            raise ValidationError("inference segment crop is invalid")
        if coverage.team_side in {"near", "far"} and coverage.crop is not None:
            segments.append(
                InferenceSegment(
                    coverage.segment_id,
                    int(coverage.set_index),
                    float(coverage.start_seconds),
                    float(coverage.end_seconds),
                    coverage.team_side,
                    tuple(coverage.crop),
                )
            )
            continue
        for side_index, side in enumerate(truth.side_intervals):
            try:
                if int(side.get("set_index")) != int(coverage.set_index):
                    continue
                side_start = float(side["start_seconds"])
                side_end = float(side["end_seconds"])
                team_side = side.get("team_side")
                crop = side.get("crop")
            except (KeyError, TypeError, ValueError):
                continue
            if team_side not in {"near", "far"} or not isinstance(crop, (list, tuple)) or len(crop) != 4:
                continue
            start = max(float(coverage.start_seconds), side_start)
            end = min(float(coverage.end_seconds), side_end)
            if end <= start:
                continue
            segments.append(
                InferenceSegment(
                    f"{coverage.segment_id}-{team_side}-{side_index + 1}",
                    int(coverage.set_index),
                    start,
                    end,
                    team_side,
                    tuple(crop),
                )
            )
    return tuple(segments)


def _validate_segments(segments: tuple[InferenceSegment, ...], *, duration: float, width: int, height: int) -> None:
    previous_start: float | None = None
    previous_end: float | None = None
    for segment in segments:
        if isinstance(segment.set_index, bool) or not isinstance(segment.set_index, int):
            raise ValidationError("inference segment set_index is invalid")
        if segment.team_side not in {"near", "far"}:
            raise ValidationError("inference segment team_side is invalid")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in segment.crop):
            raise ValidationError("inference segment crop is invalid")
        if any(not math.isfinite(float(value)) for value in (segment.start_seconds, segment.end_seconds)):
            raise ValidationError("inference segment bounds are invalid")
        if previous_start is not None and segment.start_seconds < previous_start - 1e-9:
            raise ValidationError("inference segments are out of order")
        if previous_end is not None and segment.start_seconds < previous_end - 1e-9:
            raise ValidationError("inference segments overlap")
        if segment.start_seconds < 0 or segment.end_seconds <= segment.start_seconds or segment.end_seconds > duration + 1e-9:
            raise ValidationError("inference segment bounds are invalid")
        x1, y1, x2, y2 = segment.crop
        if x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1 or x2 > width or y2 > height:
            raise ValidationError(f"Crop {segment.crop} exceeds video frame {width}x{height}.")
        previous_start = segment.start_seconds
        previous_end = segment.end_seconds


def infer_locked_validation(
    video_path: str | Path,
    checkpoint_path: str | Path,
    truth: ValidationTruth,
    *,
    stride_seconds: float = 0.4,
    confidence_threshold: float = 0.5,
    merge_gap_seconds: float = 0.25,
    min_event_seconds: float = 0.2,
    batch_size: int = 8,
    device: str = "auto",
) -> ValidationInferenceResult:
    if not truth.locked:
        raise ValidationError("locked truth is required before validation inference")
    source = Path(video_path).expanduser().resolve()
    checkpoint = Path(checkpoint_path).expanduser().resolve()
    if source != truth.video.video_path.resolve():
        raise ValidationError("video path does not match locked truth binding")
    try:
        initial_video_sha256 = _file_sha256(source)
        initial_checkpoint_sha256 = _file_sha256(checkpoint)
    except (OSError, ValidationError) as exc:
        raise ValidationError("video or checkpoint is unreadable") from exc
    if initial_video_sha256.lower() != truth.video.sha256.lower():
        raise ValidationError("video SHA-256 does not match locked truth binding")
    metadata = inspect_video(source)
    expected_metadata = truth.video.metadata
    for field in ("fps", "frame_count", "width", "height", "duration_seconds"):
        expected = getattr(expected_metadata, field)
        observed = getattr(metadata, field)
        if isinstance(expected, float):
            if abs(float(observed) - float(expected)) > 1e-6:
                raise ValidationError("video metadata does not match locked truth binding")
        elif observed != expected:
            raise ValidationError("video metadata does not match locked truth binding")
    segments = _segments_from_truth(truth)
    _validate_segments(segments, duration=metadata.duration_seconds, width=metadata.width, height=metadata.height)
    if batch_size <= 0:
        raise ValidationError("batch_size must be positive")

    torch = require_torch()
    selected_device = resolve_device(device)
    model, checkpoint_data = load_checkpoint(checkpoint, device=selected_device)
    num_frames = int(checkpoint_data["num_frames"])
    image_size = int(checkpoint_data["image_size"])
    window_seconds = float(checkpoint_data["window_seconds"])
    labels = list(checkpoint_data["labels"])
    model_version = str(checkpoint_data["model_version"])

    all_windows: list[ValidationWindow] = []
    predictions: list[ValidationPrediction] = []
    segment_settings: list[dict[str, object]] = []
    for segment in segments:
        times = list(iter_window_times_range(segment.start_seconds, segment.end_seconds, window_seconds=window_seconds, stride_seconds=stride_seconds))
        segment_windows: list[ActionWindow] = []
        for batch_times, clips in iter_sequential_video_clip_batches(source, times, num_frames=num_frames, image_size=image_size, batch_size=batch_size, crop=segment.crop):
            tensors = [frames_to_tensor(clip) for clip in clips]
            batch = torch.stack(tensors).to(selected_device)
            with torch.no_grad():
                probabilities = torch.softmax(model(batch), dim=1).detach().cpu()
            scores, indices = probabilities.max(dim=1)
            for (start, end), score, index in zip(batch_times, scores, indices):
                segment_windows.append(ActionWindow(round(start, 6), round(end, 6), labels[int(index)], round(float(score), 6)))
        segment_offset = len(all_windows)
        for local_index, window in enumerate(segment_windows):
            all_windows.append(ValidationWindow(segment_offset + local_index, segment.segment_id, segment.set_index, segment.team_side, window.start_seconds, window.end_seconds, window.action, window.confidence))
        events, provenance = merge_action_windows_with_provenance(segment_windows, video_id=truth.video.match_id, model_version=model_version, confidence_threshold=confidence_threshold, merge_gap_seconds=merge_gap_seconds, min_event_seconds=min_event_seconds)
        for prediction_index, event in enumerate(events, start=1):
            local_indices = tuple(provenance.get(event.event_id, ()))
            predictions.append(ValidationPrediction(f"{truth.video.match_id}:set-{segment.set_index:02d}:{segment.segment_id}:pred-{prediction_index:06d}", segment.segment_id, segment.set_index, segment.team_side, event.start_ms / 1000.0, event.end_ms / 1000.0, event.action, event.confidence, tuple(segment_offset + index for index in local_indices)))
        segment_settings.append({"segment_id": segment.segment_id, "set_index": segment.set_index, "start_seconds": segment.start_seconds, "end_seconds": segment.end_seconds, "team_side": segment.team_side, "crop": list(segment.crop), "window_count": len(segment_windows)})

    try:
        final_video_sha256 = _file_sha256(source)
        final_checkpoint_sha256 = _file_sha256(checkpoint)
    except (OSError, ValidationError) as exc:
        raise ValidationError("video or checkpoint became unreadable during validation inference") from exc
    if final_video_sha256 != initial_video_sha256:
        raise ValidationError("source video changed during validation inference")
    if final_checkpoint_sha256 != initial_checkpoint_sha256:
        raise ValidationError("checkpoint changed during validation inference")
    settings: dict[str, object] = {
        "sampling_contract": SAMPLING_CONTRACT,
        "model_version": model_version,
        "device": selected_device,
        "num_frames": num_frames,
        "image_size": image_size,
        "window_seconds": window_seconds,
        "stride_seconds": stride_seconds,
        "confidence_threshold": confidence_threshold,
        "merge_gap_seconds": merge_gap_seconds,
        "min_event_seconds": min_event_seconds,
        "batch_size": batch_size,
        "match_id": truth.video.match_id,
        "video": metadata.to_dict(),
        "checkpoint": str(checkpoint),
        "locked_truth_sha256": truth.locked_sha256,
        "segments": segment_settings,
        "non_rally_inference": "omitted: no team_side/crop",
        "checkpoint_sha256": initial_checkpoint_sha256,
        "video_sha256": initial_video_sha256,
    }
    return ValidationInferenceResult(tuple(all_windows), tuple(predictions), settings, initial_checkpoint_sha256, initial_video_sha256)
