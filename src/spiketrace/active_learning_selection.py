from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from .dual_crop_review import verify_dual_crop_review
from .errors import ActiveLearningError

SELECTION_ROOT_FIELDS = (
    "format_version",
    "selection_algorithm_version",
    "batch_id",
    "round_id",
    "round_number",
    "source",
    "video",
    "settings",
    "previous_selections",
    "quota_summary",
    "coverage",
    "clips",
)
_SOURCE_FIELDS = (
    "merged_json",
    "merged_json_sha256",
    "checkpoint",
    "checkpoint_sha256",
    "inference_runs",
    "format_version",
    "merge_format_version",
    "model_version",
)
_VIDEO_FIELDS = (
    "video_id",
    "path",
    "sha256",
    "fps",
    "frame_count",
    "width",
    "height",
    "duration_seconds",
    "crops",
)
_INFERENCE_RUN_FIELDS = (
    "source_file",
    "source_file_sha256",
    "normalized_payload_sha256",
)
_SHA256_LENGTH = 64


def validate_merged_review_source(
    merged_json_path: str | Path,
    *,
    repo_root: str | Path,
    require_video: bool = True,
) -> dict[str, object]:
    root = Path(repo_root).expanduser().resolve()
    merged_path = _resolved_path(merged_json_path, root, "merged JSON")
    try:
        merged = _load_json_bytes(
            merged_path.read_bytes(), description="merged review source"
        )
        verify_dual_crop_review(merged_path)
        input_runs = _mapping(merged["input_runs"], "input_runs")
        far_settings = _mapping(
            _mapping(input_runs["far"], "far run")["settings"], "far settings"
        )
        near_settings = _mapping(
            _mapping(input_runs["near"], "near run")["settings"], "near settings"
        )
        checkpoint = _relative_posix_path(far_settings["checkpoint"], "far checkpoint")
        checkpoint_sha256 = _sha256(
            far_settings["checkpoint_sha256"], "far checkpoint SHA-256"
        )
        if (
            _relative_posix_path(near_settings["checkpoint"], "near checkpoint")
            != checkpoint
            or _sha256(near_settings["checkpoint_sha256"], "near checkpoint SHA-256")
            != checkpoint_sha256
        ):
            raise ActiveLearningError(
                "Far and near runs must pin the same checkpoint path and SHA-256."
            )
        video_sha256 = _sha256(far_settings["video_sha256"], "far video SHA-256")
        if _sha256(near_settings["video_sha256"], "near video SHA-256") != video_sha256:
            raise ActiveLearningError(
                "Far and near runs must pin the same video SHA-256."
            )

        merged_video = _mapping(merged["video"], "merged video")
        normalized_video_path = _relative_posix_path(
            merged_video["path"], "merged video path"
        )
        video_path = _resolved_path(normalized_video_path, root, "source video")
        if video_path.exists():
            if _sha256_file(video_path) != video_sha256:
                raise ActiveLearningError("Source video SHA-256 does not match.")
        elif require_video:
            raise ActiveLearningError(f"Source video does not exist: {video_path}")

        checkpoint_path = _resolved_path(checkpoint, root, "checkpoint")
        checkpoint_file_checked = checkpoint_path.exists()
        if (
            checkpoint_file_checked
            and _sha256_file(checkpoint_path) != checkpoint_sha256
        ):
            raise ActiveLearningError("Checkpoint SHA-256 does not match.")

        audits = _mapping(
            _mapping(merged["settings"], "merged settings")["input_runs"],
            "settings.input_runs",
        )
        inference_runs = {
            "far": _source_audit(audits["far"], "far inference run"),
            "near": _source_audit(audits["near"], "near inference run"),
        }
        source = {
            "merged_json": _normalized_path(merged_path, root, "merged JSON"),
            "merged_json_sha256": _sha256_file(merged_path),
            "checkpoint": checkpoint,
            "checkpoint_sha256": checkpoint_sha256,
            "inference_runs": inference_runs,
            "format_version": merged["format_version"],
            "merge_format_version": merged["merge_format_version"],
            "model_version": merged["model_version"],
        }
        video = {
            "video_id": Path(normalized_video_path).stem,
            "path": normalized_video_path,
            "sha256": video_sha256,
            "fps": merged_video["fps"],
            "frame_count": merged_video["frame_count"],
            "width": merged_video["width"],
            "height": merged_video["height"],
            "duration_seconds": merged_video["duration_seconds"],
            "crops": {
                "far": far_settings["crop"],
                "near": near_settings["crop"],
            },
        }
        _validate_json_value(source, "source")
        _validate_json_value(video, "video")
    except ActiveLearningError:
        raise
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise ActiveLearningError(f"Invalid merged review source: {exc}") from exc
    return {
        "merged": merged,
        "source": source,
        "video": video,
        "verification": {"checkpoint_file_checked": checkpoint_file_checked},
    }


def write_review_selection(
    payload: object,
    output_path: str | Path,
    *,
    repo_root: str | Path,
) -> dict[str, object]:
    root = Path(repo_root).expanduser().resolve()
    output = _resolved_path(output_path, root, "selection output")
    validated = _validate_selection_payload(payload, root, require_video=True)
    _write_new_json(output, validated)
    return validated


def load_review_selection(
    selection_path: str | Path,
    *,
    repo_root: str | Path,
    require_video: bool = True,
) -> dict[str, object]:
    root = Path(repo_root).expanduser().resolve()
    path = _resolved_path(selection_path, root, "selection JSON")
    try:
        payload = _load_json_bytes(path.read_bytes(), description="selection JSON")
    except ActiveLearningError:
        raise
    except OSError as exc:
        raise ActiveLearningError(f"Cannot read selection JSON: {path}") from exc
    return _validate_selection_payload(payload, root, require_video=require_video)


def _validate_selection_payload(
    payload: object,
    repo_root: Path,
    *,
    require_video: bool,
) -> dict[str, object]:
    selection = _mapping(payload, "selection")
    _require_exact_fields(selection, SELECTION_ROOT_FIELDS, "selection")
    if type(selection["format_version"]) is not int or selection["format_version"] != 1:
        raise ActiveLearningError("Selection must use format version 1.")
    if selection["selection_algorithm_version"] != "active-learning-selection-v1":
        raise ActiveLearningError("Selection must use active-learning-selection-v1.")
    _nonempty_string(selection["batch_id"], "batch_id")
    _nonempty_string(selection["round_id"], "round_id")
    if type(selection["round_number"]) is not int or selection["round_number"] < 1:
        raise ActiveLearningError("round_number must be a positive integer.")

    selected_source = _mapping(selection["source"], "selection source")
    _require_exact_fields(selected_source, _SOURCE_FIELDS, "selection source")
    selected_video = _mapping(selection["video"], "selection video")
    _require_exact_fields(selected_video, _VIDEO_FIELDS, "selection video")
    merged_path = _resolved_path(
        selected_source["merged_json"], repo_root, "source merged JSON"
    )
    if _sha256_file(merged_path) != _sha256(
        selected_source["merged_json_sha256"], "merged JSON SHA-256"
    ):
        raise ActiveLearningError("Merged JSON SHA-256 does not match.")
    verified = validate_merged_review_source(
        merged_path, repo_root=repo_root, require_video=require_video
    )
    if selected_source != verified["source"]:
        raise ActiveLearningError(
            "Selection source fields do not match the verified merged JSON."
        )
    if selected_video != verified["video"]:
        raise ActiveLearningError(
            "Selection video fields do not match the verified merged JSON."
        )

    if not isinstance(selection["settings"], dict):
        raise ActiveLearningError("settings must be an object.")
    if not isinstance(selection["previous_selections"], list):
        raise ActiveLearningError("previous_selections must be an array.")
    if not isinstance(selection["quota_summary"], list):
        raise ActiveLearningError("quota_summary must be an array.")
    if not isinstance(selection["coverage"], dict):
        raise ActiveLearningError("coverage must be an object.")
    _validate_clips(
        selection["clips"], video_duration=selected_video["duration_seconds"]
    )
    _validate_json_value(selection, "selection")
    return selection


def _validate_clips(value: object, *, video_duration: object) -> None:
    if not isinstance(value, list) or len(value) != 40:
        raise ActiveLearningError("Selection must contain exactly 40 clips.")
    duration = _finite_number(video_duration, "video duration_seconds")
    seen_ids: set[str] = set()
    previous_end: int | float | None = None
    for expected_ordinal, raw_clip in enumerate(value, start=1):
        clip = _mapping(raw_clip, f"clip {expected_ordinal}")
        for field in ("clip_id", "ordinal", "start_seconds", "end_seconds"):
            if field not in clip:
                raise ActiveLearningError(
                    f"clip {expected_ordinal} is missing field {field!r}."
                )
        clip_id = _nonempty_string(clip["clip_id"], "clip_id")
        if clip_id in seen_ids:
            raise ActiveLearningError("Clip IDs must be unique.")
        seen_ids.add(clip_id)
        if type(clip["ordinal"]) is not int or clip["ordinal"] != expected_ordinal:
            raise ActiveLearningError("Clip ordinals must be exactly 1 through 40.")
        start = _finite_number(clip["start_seconds"], "clip start_seconds")
        end = _finite_number(clip["end_seconds"], "clip end_seconds")
        if start < 0 or end <= start or end > duration:
            raise ActiveLearningError(
                "Clip bounds must satisfy 0 <= start < end <= video duration."
            )
        if previous_end is not None and start < previous_end:
            raise ActiveLearningError(
                "Clips must be time-ordered and must not overlap."
            )
        previous_end = end
        if "duration_seconds" in clip:
            clip_duration = _finite_number(
                clip["duration_seconds"], "clip duration_seconds"
            )
            if clip_duration != end - start:
                raise ActiveLearningError(
                    "Clip duration_seconds must equal end_seconds - start_seconds."
                )


def _write_new_json(path: Path, payload: object) -> None:
    if path.exists():
        raise ActiveLearningError(f"Output already exists: {path}")
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ActiveLearningError(f"Output already exists: {path}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _load_json_bytes(raw: bytes, *, description: str) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ActiveLearningError(
                    f"{description} contains duplicate key {key!r}."
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ActiveLearningError(f"{description} contains non-finite number {value}.")

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActiveLearningError(f"{description} is not valid UTF-8 JSON.") from exc
    return _mapping(parsed, description)


def _resolved_path(value: str | Path, root: Path, description: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ActiveLearningError(
            f"{description} escapes repository root: {value}"
        ) from exc
    return resolved


def _normalized_path(path: Path, root: Path, description: str) -> str:
    resolved = _resolved_path(path, root, description)
    return resolved.relative_to(root).as_posix()


def _relative_posix_path(value: object, description: str) -> str:
    text = _nonempty_string(value, description)
    candidate = Path(text)
    if (
        candidate.drive
        or candidate.is_absolute()
        or "\\" in text
        or text.startswith("/")
        or ".." in candidate.parts
    ):
        raise ActiveLearningError(
            f"{description} must be a repository-relative POSIX path."
        )
    return candidate.as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ActiveLearningError(f"Cannot hash file: {path}") from exc
    return digest.hexdigest()


def _sha256(value: object, description: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ActiveLearningError(f"{description} must be a lowercase SHA-256.")
    return value


def _source_audit(value: object, description: str) -> dict[str, str]:
    audit = _mapping(value, description)
    _require_exact_fields(audit, _INFERENCE_RUN_FIELDS, description)
    return {
        "source_file": _relative_posix_path(
            audit["source_file"], f"{description} source_file"
        ),
        "source_file_sha256": _sha256(
            audit["source_file_sha256"], f"{description} source_file_sha256"
        ),
        "normalized_payload_sha256": _sha256(
            audit["normalized_payload_sha256"],
            f"{description} normalized_payload_sha256",
        ),
    }


def _mapping(value: object, description: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ActiveLearningError(f"{description} must be a JSON object.")
    return value


def _require_exact_fields(
    value: dict[str, Any], fields: tuple[str, ...], description: str
) -> None:
    if tuple(value) != fields:
        raise ActiveLearningError(
            f"{description} fields must be exactly {', '.join(fields)} in order."
        )


def _nonempty_string(value: object, description: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActiveLearningError(f"{description} must be a non-empty string.")
    return value


def _finite_number(value: object, description: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ActiveLearningError(f"{description} must be a finite number.")
    try:
        finite = math.isfinite(value)
    except OverflowError as exc:
        raise ActiveLearningError(f"{description} must be a finite number.") from exc
    if not finite:
        raise ActiveLearningError(f"{description} must be a finite number.")
    return value


def _validate_json_value(value: object, description: str) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)):
        _finite_number(value, description)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, description)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _validate_json_value(item, description)
        return
    raise ActiveLearningError(f"{description} contains a non-JSON value.")
