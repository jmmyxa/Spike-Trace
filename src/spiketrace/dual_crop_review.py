from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from .constants import SAMPLING_CONTRACT

_INFERENCE_ROOT_FIELDS = (
    "format_version", "video", "model_version", "settings", "events", "windows"
)
_VIDEO_FIELDS = (
    "path", "fps", "frame_count", "width", "height", "duration_seconds"
)
_INFERENCE_SETTINGS_FIELDS = (
    "device", "checkpoint", "checkpoint_sha256", "video_sha256",
    "opencv_version", "torch_version", "torchvision_version", "video",
    "num_frames", "image_size", "window_seconds", "stride_seconds",
    "confidence_threshold", "merge_gap_seconds", "min_event_seconds",
    "batch_size", "crop", "sampling_contract",
)
_SOURCE_EVENT_FIELDS = (
    "video_id", "event_id", "start_ms", "end_ms", "action", "confidence",
    "team_side", "player_number", "status", "model_version", "source",
    "source_window_indices",
)
_WINDOW_FIELDS = (
    "window_index", "start_seconds", "end_seconds", "action", "confidence"
)
_MERGED_ROOT_FIELDS = (
    "format_version", "merge_format_version", "video", "model_version",
    "settings", "input_runs", "events", "duplicate_groups", "conflict_groups",
)
_MERGE_SETTINGS_FIELDS = (
    "source", "algorithm_version", "time_unit", "interval_semantics",
    "duplicate_rule", "conflict_rule", "input_runs",
)
_SOURCE_AUDIT_FIELDS = (
    "source_file", "source_file_sha256", "normalized_payload_sha256"
)
_CSV_FIELDS = (
    "video_id", "event_id", "start_ms", "end_ms", "action", "confidence",
    "team_side", "player_number", "status", "model_version", "source", "side",
    "observed_sides", "duplicate_group_id", "conflict_group_id",
    "merge_decision", "source_event_ids", "source_event_refs",
    "source_window_count", "source_window_max_confidence",
    "primary_source_event_id", "review_reason",
)
_SIDE_ORDER = {"far": 0, "near": 1}
_EXPECTED_CROPS = {
    "far": [0, 0, 1920, 645],
    "near": [0, 255, 1920, 1080],
}
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")
_SIX_PLACES = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class _Candidate:
    side: str
    source_event_id: str
    candidate_id: str
    video_id: str
    model_version: str
    start_ms: int
    end_ms: int
    action: str
    event_confidence: int | float
    member_window_indices: tuple[int, ...]
    member_window_confidences: tuple[int | float, ...]

    @property
    def key(self) -> tuple[int, int, str, int, str]:
        return (
            self.start_ms,
            self.end_ms,
            self.action,
            _SIDE_ORDER[self.side],
            self.source_event_id,
        )

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def max_member_confidence(self) -> int | float:
        return max(self.member_window_confidences)


def build_dual_crop_review(
    far_path: str | Path,
    near_path: str | Path,
    output_dir: str | Path,
    *,
    repo_root: str | Path,
) -> dict[str, object]:
    root = Path(repo_root).expanduser().resolve()
    source_paths = {
        "far": Path(far_path).expanduser().resolve(),
        "near": Path(near_path).expanduser().resolve(),
    }
    normalized_runs: dict[str, dict[str, Any]] = {}
    audits: dict[str, dict[str, str]] = {}
    for side in ("far", "near"):
        source_path = source_paths[side]
        source_bytes = source_path.read_bytes()
        source_payload = _load_json_bytes(source_bytes, description=f"{side} input")
        normalized = _normalize_inference_payload(
            source_payload,
            side=side,
            repo_root=root,
            paths_are_normalized=False,
        )
        normalized_runs[side] = normalized
        audits[side] = {
            "source_file": _normalize_path(source_path, root),
            "source_file_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "normalized_payload_sha256": _canonical_sha256(normalized),
        }

    _validate_cross_run_contract(normalized_runs["far"], normalized_runs["near"])
    payload = _assemble_payload(normalized_runs, audits)
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "merged_candidates.json"
    csv_path = destination / "merged_candidates.csv"
    json_path.write_bytes(_presentation_json_bytes(payload))
    csv_path.write_bytes(_render_csv(payload["events"]))
    verify_dual_crop_review(json_path, csv_path=csv_path)
    return payload


def verify_dual_crop_review(
    json_path: str | Path,
    *,
    csv_path: str | Path | None = None,
) -> dict[str, object]:
    artifact_path = Path(json_path).expanduser().resolve()
    artifact_bytes = artifact_path.read_bytes()
    artifact = _load_json_bytes(artifact_bytes, description="merged artifact")
    _require_fields(artifact, _MERGED_ROOT_FIELDS, "merged artifact", ordered=True)
    if artifact["format_version"] != 2 or artifact["merge_format_version"] != 2:
        raise ValueError("Merged artifact must use format version 2.")

    settings = _mapping(artifact["settings"], "merged settings")
    _require_fields(settings, _MERGE_SETTINGS_FIELDS, "merged settings", ordered=True)
    input_runs = _mapping(artifact["input_runs"], "input_runs")
    _require_fields(input_runs, ("far", "near"), "input_runs", ordered=True)
    audit_runs = _mapping(settings["input_runs"], "settings.input_runs")
    _require_fields(audit_runs, ("far", "near"), "settings.input_runs", ordered=True)

    normalized_runs: dict[str, dict[str, Any]] = {}
    audits: dict[str, dict[str, str]] = {}
    for side in ("far", "near"):
        audit = _mapping(audit_runs[side], f"{side} source audit")
        _require_fields(
            audit, _SOURCE_AUDIT_FIELDS, f"{side} source audit", ordered=True
        )
        source_file = _display_path(audit["source_file"], f"{side} source_file")
        source_sha = _sha256(audit["source_file_sha256"], f"{side} source SHA-256")
        normalized_sha = _sha256(
            audit["normalized_payload_sha256"],
            f"{side} normalized payload SHA-256",
        )
        normalized = _normalize_inference_payload(
            input_runs[side],
            side=side,
            repo_root=None,
            paths_are_normalized=True,
        )
        recomputed_sha = _canonical_sha256(normalized)
        if normalized_sha != recomputed_sha:
            raise ValueError(f"{side} normalized payload SHA-256 does not match.")
        normalized_runs[side] = normalized
        audits[side] = {
            "source_file": source_file,
            "source_file_sha256": source_sha,
            "normalized_payload_sha256": recomputed_sha,
        }

    _validate_cross_run_contract(normalized_runs["far"], normalized_runs["near"])
    expected = _assemble_payload(normalized_runs, audits)
    if artifact != expected:
        raise ValueError("Merged artifact does not match independent recomputation.")
    if artifact_bytes != _presentation_json_bytes(expected):
        raise ValueError("Merged JSON bytes are not in deterministic presentation form.")

    csv_checked = csv_path is not None
    merged_csv_sha256: str | None = None
    if csv_path is not None:
        merged_csv_bytes = Path(csv_path).expanduser().resolve().read_bytes()
        if merged_csv_bytes != _render_csv(expected["events"]):
            raise ValueError("Merged CSV does not match recomputed rows.")
        merged_csv_sha256 = hashlib.sha256(merged_csv_bytes).hexdigest()

    duplicate_links = sum(
        len(group["links"]) for group in expected["duplicate_groups"]
    )
    conflict_links = sum(
        len(group["source_links"]) for group in expected["conflict_groups"]
    )
    far_run = normalized_runs["far"]
    near_run = normalized_runs["near"]
    return {
        "verified": True,
        "format_version": 2,
        "merge_format_version": 2,
        "csv_checked": csv_checked,
        "counts": {
            "far_events": len(far_run["events"]),
            "near_events": len(near_run["events"]),
            "far_windows": len(far_run["windows"]),
            "near_windows": len(near_run["windows"]),
            "source_candidates": len(far_run["events"]) + len(near_run["events"]),
            "canonical_events": len(expected["events"]),
            "duplicate_links": duplicate_links,
            "duplicate_groups": len(expected["duplicate_groups"]),
            "conflict_links": conflict_links,
            "conflict_groups": len(expected["conflict_groups"]),
        },
        "hashes": {
            "merged_json_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
            "merged_csv_sha256": merged_csv_sha256,
            "far_source_file_sha256": audits["far"]["source_file_sha256"],
            "near_source_file_sha256": audits["near"]["source_file_sha256"],
            "far_normalized_payload_sha256": audits["far"][
                "normalized_payload_sha256"
            ],
            "near_normalized_payload_sha256": audits["near"][
                "normalized_payload_sha256"
            ],
        },
    }


def _load_json_bytes(raw: bytes, *, description: str) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{description} contains duplicate key {key!r}.")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"{description} contains non-finite number {value}.")

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{description} is not valid UTF-8 JSON.") from exc
    return _mapping(parsed, description)


def _normalize_inference_payload(
    raw_payload: object,
    *,
    side: str,
    repo_root: Path | None,
    paths_are_normalized: bool,
) -> dict[str, Any]:
    payload = _mapping(raw_payload, f"{side} inference payload")
    _require_fields(payload, _INFERENCE_ROOT_FIELDS, f"{side} inference payload")
    if type(payload["format_version"]) is not int or payload["format_version"] != 2:
        raise ValueError(f"{side} inference input must use format version 2.")

    video = _normalize_video(
        payload["video"],
        description=f"{side} video",
        repo_root=repo_root,
        paths_are_normalized=paths_are_normalized,
    )
    model_version = _nonempty_string(
        payload["model_version"], f"{side} model_version"
    )
    settings = _normalize_inference_settings(
        payload["settings"],
        side=side,
        repo_root=repo_root,
        paths_are_normalized=paths_are_normalized,
    )
    if settings["video"] != video:
        raise ValueError(f"{side} settings.video must match the root video metadata.")

    raw_windows = _list(payload["windows"], f"{side} windows")
    windows: list[dict[str, Any]] = []
    window_map: dict[int, dict[str, Any]] = {}
    window_bounds: dict[int, tuple[int, int]] = {}
    for raw_window in raw_windows:
        window = _mapping(raw_window, f"{side} window")
        _require_fields(window, _WINDOW_FIELDS, f"{side} window")
        window_index = _integer(window["window_index"], f"{side} window_index", minimum=0)
        if window_index in window_map:
            raise ValueError(f"{side} window_index values must be unique.")
        start_seconds = _finite_number(
            window["start_seconds"], f"{side} window start", minimum=0
        )
        end_seconds = _finite_number(
            window["end_seconds"], f"{side} window end", minimum=0
        )
        start_ms = _milliseconds(start_seconds, f"{side} window start")
        end_ms = _milliseconds(end_seconds, f"{side} window end")
        if end_ms <= start_ms:
            raise ValueError(f"{side} windows must have positive half-open duration.")
        action = _nonempty_string(window["action"], f"{side} window action")
        confidence = _finite_number(
            window["confidence"],
            f"{side} window confidence",
            minimum=0,
            maximum=1,
        )
        normalized_window = {
            "window_index": window_index,
            "start_seconds": start_seconds,
            "end_seconds": end_seconds,
            "action": action,
            "confidence": confidence,
        }
        windows.append(normalized_window)
        window_map[window_index] = normalized_window
        window_bounds[window_index] = (start_ms, end_ms)
    if sorted(window_map) != list(range(len(windows))):
        raise ValueError(f"{side} window indexes must be complete and dense.")
    windows.sort(key=lambda item: item["window_index"])

    threshold = settings["confidence_threshold"]
    events: list[dict[str, Any]] = []
    event_ids: set[str] = set()
    assigned_indices: set[int] = set()
    raw_events = _list(payload["events"], f"{side} events")
    for raw_event in raw_events:
        event = _mapping(raw_event, f"{side} source event")
        _require_fields(event, _SOURCE_EVENT_FIELDS, f"{side} source event")
        event_id = _nonempty_string(event["event_id"], f"{side} event_id")
        if event_id in event_ids:
            raise ValueError(f"{side} event IDs must be unique.")
        event_ids.add(event_id)
        video_id = _nonempty_string(event["video_id"], f"{side} event video_id")
        if video_id != Path(video["path"]).stem:
            raise ValueError(f"{side} event video_id does not match the run video.")
        start_ms = _integer(event["start_ms"], f"{side} event start_ms", minimum=0)
        end_ms = _integer(event["end_ms"], f"{side} event end_ms", minimum=0)
        if end_ms <= start_ms:
            raise ValueError(f"{side} source events must have positive duration.")
        action = _nonempty_string(event["action"], f"{side} event action")
        confidence = _finite_number(
            event["confidence"],
            f"{side} event confidence",
            minimum=0,
            maximum=1,
        )
        if event["team_side"] is not None or event["player_number"] is not None:
            raise ValueError(f"{side} inference events must not claim team or player.")
        if event["status"] != "predicted":
            raise ValueError(f"{side} inference event status must be predicted.")
        if event["model_version"] != model_version:
            raise ValueError(f"{side} event model_version does not match the run.")
        if event["source"] != "sliding_window":
            raise ValueError(f"{side} event source must be sliding_window.")
        member_indices = _list(
            event["source_window_indices"], f"{side} event source_window_indices"
        )
        if not member_indices or any(type(index) is not int for index in member_indices):
            raise ValueError(f"{side} event member indexes must be nonempty integers.")
        if member_indices != sorted(member_indices) or len(member_indices) != len(
            set(member_indices)
        ):
            raise ValueError(
                f"{side} event member indexes must be unique and strictly increasing."
            )
        member_starts: list[int] = []
        member_ends: list[int] = []
        for index in member_indices:
            if index not in window_map:
                raise ValueError(f"{side} event references a missing window.")
            if index in assigned_indices:
                raise ValueError(f"{side} windows may belong to only one source event.")
            member_window = window_map[index]
            if member_window["action"] != action:
                raise ValueError(f"{side} event member action does not match.")
            if member_window["confidence"] < threshold:
                raise ValueError(f"{side} event member is below the confidence threshold.")
            assigned_indices.add(index)
            member_start, member_end = window_bounds[index]
            member_starts.append(member_start)
            member_ends.append(member_end)
        if start_ms != min(member_starts) or end_ms != max(member_ends):
            raise ValueError(f"{side} event bounds do not match its member windows.")
        events.append(
            {
                "video_id": video_id,
                "event_id": event_id,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "action": action,
                "confidence": confidence,
                "team_side": None,
                "player_number": None,
                "status": "predicted",
                "model_version": model_version,
                "source": "sliding_window",
                "source_window_indices": list(member_indices),
            }
        )
    events.sort(
        key=lambda item: (
            item["start_ms"], item["end_ms"], item["action"], item["event_id"]
        )
    )
    return {
        "format_version": 2,
        "video": video,
        "model_version": model_version,
        "settings": settings,
        "events": events,
        "windows": windows,
    }


def _normalize_video(
    raw_video: object,
    *,
    description: str,
    repo_root: Path | None,
    paths_are_normalized: bool,
) -> dict[str, Any]:
    video = _mapping(raw_video, description)
    _require_fields(video, _VIDEO_FIELDS, description)
    raw_path = _nonempty_string(video["path"], f"{description} path")
    if paths_are_normalized:
        normalized_path = _display_path(raw_path, f"{description} path")
    else:
        if repo_root is None:
            raise ValueError("repo_root is required for source path normalization.")
        normalized_path = _normalize_path(raw_path, repo_root)
    return {
        "path": normalized_path,
        "fps": _finite_number(video["fps"], f"{description} fps", minimum=0, strict=True),
        "frame_count": _integer(
            video["frame_count"], f"{description} frame_count", minimum=1
        ),
        "width": _integer(video["width"], f"{description} width", minimum=1),
        "height": _integer(video["height"], f"{description} height", minimum=1),
        "duration_seconds": _finite_number(
            video["duration_seconds"],
            f"{description} duration_seconds",
            minimum=0,
            strict=True,
        ),
    }


def _normalize_inference_settings(
    raw_settings: object,
    *,
    side: str,
    repo_root: Path | None,
    paths_are_normalized: bool,
) -> dict[str, Any]:
    settings = _mapping(raw_settings, f"{side} inference settings")
    _require_fields(settings, _INFERENCE_SETTINGS_FIELDS, f"{side} inference settings")
    checkpoint = _nonempty_string(settings["checkpoint"], f"{side} checkpoint")
    if paths_are_normalized:
        normalized_checkpoint = _display_path(checkpoint, f"{side} checkpoint")
    else:
        if repo_root is None:
            raise ValueError("repo_root is required for checkpoint normalization.")
        normalized_checkpoint = _normalize_path(checkpoint, repo_root)
    crop = _list(settings["crop"], f"{side} crop")
    if any(type(coordinate) is not int for coordinate in crop) or crop != _EXPECTED_CROPS[side]:
        raise ValueError(f"{side} crop must be {_EXPECTED_CROPS[side]}.")
    if settings["sampling_contract"] != SAMPLING_CONTRACT:
        raise ValueError(f"{side} sampling contract is unsupported.")
    return {
        "device": _nonempty_string(settings["device"], f"{side} device"),
        "checkpoint": normalized_checkpoint,
        "checkpoint_sha256": _sha256(
            settings["checkpoint_sha256"], f"{side} checkpoint SHA-256"
        ),
        "video_sha256": _sha256(
            settings["video_sha256"], f"{side} video SHA-256"
        ),
        "opencv_version": _nonempty_string(
            settings["opencv_version"], f"{side} OpenCV version"
        ),
        "torch_version": _nonempty_string(
            settings["torch_version"], f"{side} torch version"
        ),
        "torchvision_version": _nonempty_string(
            settings["torchvision_version"], f"{side} torchvision version"
        ),
        "video": _normalize_video(
            settings["video"],
            description=f"{side} settings video",
            repo_root=repo_root,
            paths_are_normalized=paths_are_normalized,
        ),
        "num_frames": _integer(
            settings["num_frames"], f"{side} num_frames", minimum=1
        ),
        "image_size": _integer(
            settings["image_size"], f"{side} image_size", minimum=1
        ),
        "window_seconds": _finite_number(
            settings["window_seconds"], f"{side} window_seconds", minimum=0, strict=True
        ),
        "stride_seconds": _finite_number(
            settings["stride_seconds"], f"{side} stride_seconds", minimum=0, strict=True
        ),
        "confidence_threshold": _finite_number(
            settings["confidence_threshold"],
            f"{side} confidence_threshold",
            minimum=0,
            maximum=1,
        ),
        "merge_gap_seconds": _finite_number(
            settings["merge_gap_seconds"], f"{side} merge_gap_seconds", minimum=0
        ),
        "min_event_seconds": _finite_number(
            settings["min_event_seconds"], f"{side} min_event_seconds", minimum=0
        ),
        "batch_size": _integer(settings["batch_size"], f"{side} batch_size", minimum=1),
        "crop": list(crop),
        "sampling_contract": SAMPLING_CONTRACT,
    }


def _validate_cross_run_contract(
    far: dict[str, Any], near: dict[str, Any]
) -> None:
    if far["video"] != near["video"]:
        raise ValueError("Far and near video metadata must match.")
    if far["model_version"] != near["model_version"]:
        raise ValueError("Far and near model versions must match.")
    far_settings = copy.deepcopy(far["settings"])
    near_settings = copy.deepcopy(near["settings"])
    far_settings.pop("crop")
    far_settings.pop("device")
    near_settings.pop("crop")
    near_settings.pop("device")
    if far_settings != near_settings:
        raise ValueError("Far and near inference settings must match except crop/device.")


def _assemble_payload(
    input_runs: dict[str, dict[str, Any]],
    audits: dict[str, dict[str, str]],
) -> dict[str, Any]:
    candidates = _build_candidates(input_runs)
    duplicate_links, conflict_links = _find_cross_side_links(candidates)
    components = _duplicate_components(candidates, duplicate_links)
    canonical_ids = {
        candidate.candidate_id: f"evt_merged_{component_index:06d}"
        for component_index, component in enumerate(components, start=1)
        for candidate in component
    }
    duplicate_group_ids: dict[str, str] = {}
    next_duplicate_group = 1
    for component_index, component in enumerate(components, start=1):
        if len(component) > 1 and len({candidate.side for candidate in component}) > 1:
            canonical_id = f"evt_merged_{component_index:06d}"
            duplicate_group_ids[canonical_id] = f"dg_{next_duplicate_group:06d}"
            next_duplicate_group += 1

    conflict_groups, conflict_group_ids = _build_conflict_groups(
        components, canonical_ids, conflict_links
    )
    events: list[dict[str, Any]] = []
    duplicate_groups: list[dict[str, Any]] = []
    for component_index, component in enumerate(components, start=1):
        canonical_id = f"evt_merged_{component_index:06d}"
        primary = min(component, key=_primary_key)
        duplicate_group_id = duplicate_group_ids.get(canonical_id)
        conflict_group_id = conflict_group_ids.get(canonical_id)
        refs = [
            {
                "side": candidate.side,
                "candidate_id": candidate.candidate_id,
                "source_event_id": candidate.source_event_id,
                "member_window_indices": list(candidate.member_window_indices),
                "selected_as_primary": candidate == primary,
            }
            for candidate in component
        ]
        observed_sides = [
            side for side in ("far", "near") if any(item.side == side for item in component)
        ]
        reason = (
            "same_action_cross_side_duplicate"
            if duplicate_group_id is not None
            else "single_source_candidate"
        )
        if conflict_group_id is not None:
            reason += "|different_action_cross_side_conflict"
        events.append(
            {
                "video_id": primary.video_id,
                "event_id": canonical_id,
                "start_ms": min(candidate.start_ms for candidate in component),
                "end_ms": max(candidate.end_ms for candidate in component),
                "action": primary.action,
                "confidence": primary.event_confidence,
                "team_side": None,
                "player_number": None,
                "status": "needs_review" if conflict_group_id else "predicted",
                "model_version": primary.model_version,
                "source": "dual_crop_merge",
                "side": primary.side,
                "observed_sides": observed_sides,
                "source_event_refs": refs,
                "duplicate_group_id": duplicate_group_id,
                "conflict_group_id": conflict_group_id,
                "merge_decision": (
                    "same_action_cross_side_deduped"
                    if duplicate_group_id is not None
                    else "single_source"
                ),
                "source_event_ids": [ref["candidate_id"] for ref in refs],
                "source_window_count": sum(
                    len(candidate.member_window_indices) for candidate in component
                ),
                "source_window_max_confidence": max(
                    candidate.max_member_confidence for candidate in component
                ),
                "primary_source_event_id": primary.candidate_id,
                "review_reason": reason,
            }
        )
        if duplicate_group_id is not None:
            member_ids = {candidate.candidate_id for candidate in component}
            links = [
                link
                for link in duplicate_links
                if link["candidate_a_id"] in member_ids
                and link["candidate_b_id"] in member_ids
            ]
            links.sort(key=_link_pair)
            duplicate_groups.append(
                {
                    "duplicate_group_id": duplicate_group_id,
                    "canonical_event_id": canonical_id,
                    "action": primary.action,
                    "primary_source_event_id": primary.candidate_id,
                    "source_event_ids": [candidate.candidate_id for candidate in component],
                    "observed_sides": observed_sides,
                    "links": links,
                }
            )

    return {
        "format_version": 2,
        "merge_format_version": 2,
        "video": copy.deepcopy(input_runs["far"]["video"]),
        "model_version": input_runs["far"]["model_version"],
        "settings": {
            "source": "dual_crop_merge",
            "algorithm_version": "dual-crop-merge-v2",
            "time_unit": "ms",
            "interval_semantics": "half_open",
            "duplicate_rule": {
                "same_action_only": True,
                "min_overlap_ms": 400,
                "min_coverage_shorter": 0.5,
                "max_center_gap_ms": 500,
            },
            "conflict_rule": {
                "different_actions_are_retained": True,
                "min_overlap_ms": 400,
                "max_center_gap_ms": 500,
            },
            "input_runs": {
                "far": copy.deepcopy(audits["far"]),
                "near": copy.deepcopy(audits["near"]),
            },
        },
        "input_runs": {
            "far": copy.deepcopy(input_runs["far"]),
            "near": copy.deepcopy(input_runs["near"]),
        },
        "events": events,
        "duplicate_groups": duplicate_groups,
        "conflict_groups": conflict_groups,
    }


def _build_candidates(input_runs: dict[str, dict[str, Any]]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for side in ("far", "near"):
        run = input_runs[side]
        window_map = {window["window_index"]: window for window in run["windows"]}
        for event in run["events"]:
            member_indices = tuple(event["source_window_indices"])
            candidates.append(
                _Candidate(
                    side=side,
                    source_event_id=event["event_id"],
                    candidate_id=f"{side}:{event['event_id']}",
                    video_id=event["video_id"],
                    model_version=event["model_version"],
                    start_ms=event["start_ms"],
                    end_ms=event["end_ms"],
                    action=event["action"],
                    event_confidence=event["confidence"],
                    member_window_indices=member_indices,
                    member_window_confidences=tuple(
                        window_map[index]["confidence"] for index in member_indices
                    ),
                )
            )
    candidates.sort(key=lambda candidate: candidate.key)
    return candidates


def _find_cross_side_links(
    candidates: list[_Candidate],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    duplicate_links: list[dict[str, Any]] = []
    conflict_links: list[dict[str, Any]] = []
    active: dict[str, list[_Candidate]] = {"far": [], "near": []}
    for candidate in candidates:
        start_doubled = 2 * candidate.start_ms
        opposite_side = "near" if candidate.side == "far" else "far"
        retained: list[_Candidate] = []
        for earlier in active[opposite_side]:
            if start_doubled > max(
                2 * earlier.end_ms - 800,
                earlier.start_ms + earlier.end_ms + 1000,
            ):
                continue
            retained.append(earlier)
            metrics, center_gap_doubled = _link_metrics(earlier, candidate)
            overlap_ms = metrics["overlap_ms"]
            if (
                earlier.action == candidate.action
                and overlap_ms >= 400
                and (
                    2 * overlap_ms >= metrics["shorter_ms"]
                    or center_gap_doubled <= 1000
                )
            ):
                duplicate_links.append(_link(earlier, candidate, metrics))
            elif earlier.action != candidate.action and (
                overlap_ms >= 400 or center_gap_doubled <= 1000
            ):
                conflict_links.append(_link(earlier, candidate, metrics))
        active[opposite_side] = retained
        active[candidate.side].append(candidate)
    duplicate_links.sort(key=_link_pair)
    conflict_links.sort(key=_link_pair)
    return duplicate_links, conflict_links


def _link_metrics(
    first: _Candidate, second: _Candidate
) -> tuple[dict[str, int | float], int]:
    overlap_ms = max(
        0,
        min(first.end_ms, second.end_ms) - max(first.start_ms, second.start_ms),
    )
    union_ms = max(first.end_ms, second.end_ms) - min(
        first.start_ms, second.start_ms
    )
    shorter_ms = min(first.duration_ms, second.duration_ms)
    center_gap_doubled = abs(
        first.start_ms + first.end_ms - second.start_ms - second.end_ms
    )
    center_gap_ms: int | float
    if center_gap_doubled % 2 == 0:
        center_gap_ms = center_gap_doubled // 2
    else:
        center_gap_ms = center_gap_doubled / 2
    return (
        {
            "overlap_ms": overlap_ms,
            "union_ms": union_ms,
            "shorter_ms": shorter_ms,
            "coverage_shorter": _rounded_ratio(overlap_ms, shorter_ms),
            "temporal_iou": _rounded_ratio(overlap_ms, union_ms),
            "center_gap_ms": center_gap_ms,
        },
        center_gap_doubled,
    )


def _link(
    first: _Candidate,
    second: _Candidate,
    metrics: dict[str, int | float],
) -> dict[str, Any]:
    return {
        "candidate_a_id": first.candidate_id,
        "candidate_b_id": second.candidate_id,
        "metrics": metrics,
    }


def _duplicate_components(
    candidates: list[_Candidate], duplicate_links: list[dict[str, Any]]
) -> list[list[_Candidate]]:
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    parent = {candidate.candidate_id: candidate.candidate_id for candidate in candidates}

    def find(candidate_id: str) -> str:
        while parent[candidate_id] != candidate_id:
            parent[candidate_id] = parent[parent[candidate_id]]
            candidate_id = parent[candidate_id]
        return candidate_id

    def union(first_id: str, second_id: str) -> None:
        first_root = find(first_id)
        second_root = find(second_id)
        if first_root != second_root:
            parent[second_root] = first_root

    for link in duplicate_links:
        union(link["candidate_a_id"], link["candidate_b_id"])
    grouped: dict[str, list[_Candidate]] = {}
    for candidate_id, candidate in candidate_by_id.items():
        grouped.setdefault(find(candidate_id), []).append(candidate)
    components = [sorted(component, key=lambda item: item.key) for component in grouped.values()]
    components.sort(key=lambda component: component[0].key)
    return components


def _build_conflict_groups(
    components: list[list[_Candidate]],
    canonical_ids: dict[str, str],
    conflict_links: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    canonical_order = {
        f"evt_merged_{index:06d}": index for index in range(1, len(components) + 1)
    }
    canonical_candidates = {
        f"evt_merged_{index:06d}": component
        for index, component in enumerate(components, start=1)
    }
    retained_links: list[dict[str, Any]] = []
    parent = {canonical_id: canonical_id for canonical_id in canonical_candidates}

    def find(canonical_id: str) -> str:
        while parent[canonical_id] != canonical_id:
            parent[canonical_id] = parent[parent[canonical_id]]
            canonical_id = parent[canonical_id]
        return canonical_id

    def union(first_id: str, second_id: str) -> None:
        first_root = find(first_id)
        second_root = find(second_id)
        if first_root != second_root:
            parent[second_root] = first_root

    for link in conflict_links:
        first_canonical = canonical_ids[link["candidate_a_id"]]
        second_canonical = canonical_ids[link["candidate_b_id"]]
        if first_canonical == second_canonical:
            continue
        retained_links.append(link)
        union(first_canonical, second_canonical)

    grouped: dict[str, set[str]] = {}
    for link in retained_links:
        first_canonical = canonical_ids[link["candidate_a_id"]]
        second_canonical = canonical_ids[link["candidate_b_id"]]
        root = find(first_canonical)
        grouped.setdefault(root, set()).update((first_canonical, second_canonical))
    conflict_components = list(grouped.values())
    conflict_components.sort(
        key=lambda group: min(
            candidate.key
            for canonical_id in group
            for candidate in canonical_candidates[canonical_id]
        )
    )

    groups: list[dict[str, Any]] = []
    canonical_to_group: dict[str, str] = {}
    for index, group in enumerate(conflict_components, start=1):
        conflict_group_id = f"cg_{index:06d}"
        canonical_event_ids = sorted(group, key=canonical_order.__getitem__)
        for canonical_id in canonical_event_ids:
            canonical_to_group[canonical_id] = conflict_group_id
        source_links = [
            link
            for link in retained_links
            if canonical_ids[link["candidate_a_id"]] in group
            and canonical_ids[link["candidate_b_id"]] in group
        ]
        source_links.sort(key=_link_pair)
        groups.append(
            {
                "conflict_group_id": conflict_group_id,
                "conflict_type": "different_action_cross_side",
                "canonical_event_ids": canonical_event_ids,
                "source_links": source_links,
            }
        )
    return groups, canonical_to_group


def _primary_key(candidate: _Candidate) -> tuple[Decimal, Decimal, int, int, int, str]:
    return (
        -Decimal(str(candidate.event_confidence)),
        -Decimal(str(candidate.max_member_confidence)),
        -len(candidate.member_window_indices),
        candidate.duration_ms,
        _SIDE_ORDER[candidate.side],
        candidate.source_event_id,
    )


def _render_csv(events: object) -> bytes:
    event_items = _list(events, "merged events")
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=_CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for event in event_items:
        writer.writerow(
            {
                "video_id": event["video_id"],
                "event_id": event["event_id"],
                "start_ms": str(event["start_ms"]),
                "end_ms": str(event["end_ms"]),
                "action": event["action"],
                "confidence": _decimal_csv(event["confidence"]),
                "team_side": "" if event["team_side"] is None else event["team_side"],
                "player_number": (
                    "" if event["player_number"] is None else event["player_number"]
                ),
                "status": event["status"],
                "model_version": event["model_version"],
                "source": event["source"],
                "side": event["side"],
                "observed_sides": _canonical_json(event["observed_sides"]),
                "duplicate_group_id": event["duplicate_group_id"] or "",
                "conflict_group_id": event["conflict_group_id"] or "",
                "merge_decision": event["merge_decision"],
                "source_event_ids": _canonical_json(event["source_event_ids"]),
                "source_event_refs": _canonical_json(event["source_event_refs"]),
                "source_window_count": str(event["source_window_count"]),
                "source_window_max_confidence": _decimal_csv(
                    event["source_window_max_confidence"]
                ),
                "primary_source_event_id": event["primary_source_event_id"],
                "review_reason": event["review_reason"],
            }
        )
    return b"\xef\xbb\xbf" + text.getvalue().encode("utf-8")


def _presentation_json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _decimal_csv(value: object) -> str:
    number = _finite_number(value, "CSV decimal")
    return format(
        Decimal(str(number)).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP), "f"
    )


def _rounded_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        raise ValueError("Link metric denominator must be positive.")
    value = (Decimal(numerator) / Decimal(denominator)).quantize(
        _SIX_PLACES, rounding=ROUND_HALF_UP
    )
    return float(value)


def _milliseconds(value: object, description: str) -> int:
    number = _finite_number(value, description, minimum=0)
    decimal = Decimal(str(number))
    return int(
        (decimal * 1000 + Decimal("0.5")).to_integral_value(rounding=ROUND_FLOOR)
    )


def _normalize_path(raw_path: str | Path, repo_root: Path) -> str:
    candidate = Path(raw_path).expanduser().resolve()
    try:
        return candidate.relative_to(repo_root).as_posix()
    except ValueError:
        return candidate.name


def _display_path(value: object, description: str) -> str:
    path = _nonempty_string(value, description)
    if (
        _WINDOWS_DRIVE_PATTERN.match(path)
        or path.startswith(("/", "\\", "//"))
        or "\\" in path
        or ".." in path.split("/")
    ):
        raise ValueError(f"{description} must be a normalized relative POSIX path.")
    return path


def _sha256(value: object, description: str) -> str:
    text = _nonempty_string(value, description)
    if _SHA256_PATTERN.fullmatch(text) is None:
        raise ValueError(f"{description} must be lowercase 64-character hexadecimal.")
    return text


def _mapping(value: object, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be an object.")  # noqa: TRY004
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{description} keys must be strings.")
    return value


def _list(value: object, description: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{description} must be an array.")  # noqa: TRY004
    return value


def _require_fields(
    value: dict[str, Any],
    expected: tuple[str, ...],
    description: str,
    *,
    ordered: bool = False,
) -> None:
    if set(value) != set(expected):
        raise ValueError(f"{description} has unexpected or missing fields.")
    if ordered and tuple(value) != expected:
        raise ValueError(f"{description} fields are not in canonical order.")


def _nonempty_string(value: object, description: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{description} must be a nonempty string.")
    return value


def _integer(
    value: object,
    description: str,
    *,
    minimum: int | None = None,
) -> int:
    if type(value) is not int:
        raise ValueError(f"{description} must be an integer.")
    if minimum is not None and value < minimum:
        raise ValueError(f"{description} is below its minimum.")
    return value


def _finite_number(
    value: object,
    description: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    strict: bool = False,
) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{description} must be numeric.")  # noqa: TRY004
    if not math.isfinite(value):
        raise ValueError(f"{description} must be finite.")
    if minimum is not None and (value <= minimum if strict else value < minimum):
        raise ValueError(f"{description} is below its minimum.")
    if maximum is not None and value > maximum:
        raise ValueError(f"{description} is above its maximum.")
    return value


def _link_pair(link: dict[str, Any]) -> tuple[str, str]:
    return link["candidate_a_id"], link["candidate_b_id"]
