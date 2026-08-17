from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from .active_learning_selection import load_review_selection
from .constants import ACTION_LABELS
from .errors import ActiveLearningError
from .manifest import load_manifest, summarize_manifest

_DRAFT_FIELDS = {
    "format_version",
    "batch_id",
    "round_id",
    "selection",
    "selection_sha256",
    "workbook",
    "video",
    "time_precision_seconds",
    "clips",
}
_CANONICAL_COLUMNS = (
    "video_path",
    "start_seconds",
    "end_seconds",
    "label",
    "team_side",
    "player_number",
    "split",
    "crop_x1",
    "crop_y1",
    "crop_x2",
    "crop_y2",
    "match_id",
    "review_status",
    "notes",
)
_SHA256_LENGTH = 64


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ActiveLearningError(f"Cannot hash file: {path}") from exc
    return digest.hexdigest()


def _read_json(
    path: Path, description: str, *, expected_sha256: str | None = None
) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
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
        raw = path.read_bytes()
        if (
            expected_sha256 is not None
            and hashlib.sha256(raw).hexdigest() != expected_sha256
        ):
            raise ActiveLearningError("Merged JSON SHA-256 does not match.")
        value = json.loads(
            raw,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except ActiveLearningError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActiveLearningError(f"Cannot read {description}: {path}") from exc
    if not isinstance(value, dict):
        raise ActiveLearningError(f"{description} must be a JSON object.")
    return value


def _sha256(value: object, description: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ActiveLearningError(f"{description} must be a lowercase SHA-256.")
    return value


def _mapping(value: object, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ActiveLearningError(f"{description} must be an object.")
    return value


def _nonempty_text(value: object, description: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        raise ActiveLearningError(
            f"{description} must be nonempty text without control characters."
        )
    return value.strip()


def _relative_repo_path(path: Path, repo_root: Path, description: str) -> str:
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise ActiveLearningError(f"{description} must be inside repo_root.") from exc


def _resolve_repo_path(value: object, repo_root: Path, description: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ActiveLearningError(f"{description} must be a nonempty path.")
    candidate = Path(value)
    if candidate.is_absolute():
        raise ActiveLearningError(f"{description} must be repository-relative.")
    resolved = (repo_root / candidate).resolve()
    _relative_repo_path(resolved, repo_root, description)
    return resolved


def _exact_fields(value: dict[str, Any], fields: set[str], description: str) -> None:
    if set(value) != fields:
        raise ActiveLearningError(f"{description} fields do not match format v1.")


def _validate_match_ids(
    legacy_base_match_id: object, review_match_id: object
) -> tuple[str, str]:
    legacy = _nonempty_text(legacy_base_match_id, "legacy_base_match_id")
    review = _nonempty_text(review_match_id, "review_match_id")
    if legacy == review:
        raise ActiveLearningError(
            "legacy_base_match_id and review_match_id must differ."
        )
    return legacy, review


def _validate_draft(
    draft: dict[str, Any],
    *,
    selection: dict[str, Any],
    selection_path: Path,
    repo_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _exact_fields(draft, _DRAFT_FIELDS, "Review draft")
    if type(draft["format_version"]) is not int or draft["format_version"] != 1:
        raise ActiveLearningError("Review draft must use format version 1.")
    if (
        draft["batch_id"] != selection["batch_id"]
        or draft["round_id"] != selection["round_id"]
    ):
        raise ActiveLearningError(
            "Review draft batch and round must match the selection."
        )
    if draft["selection_sha256"] != _sha256_file(selection_path):
        raise ActiveLearningError("Review draft selection SHA-256 does not match.")
    draft_selection_path = _resolve_repo_path(
        draft["selection"], repo_root, "Review draft selection path"
    )
    if draft_selection_path != selection_path:
        raise ActiveLearningError("Review draft selection path does not match.")
    workbook = _mapping(draft["workbook"], "Review draft workbook")
    if set(workbook) != {"path", "sha256"}:
        raise ActiveLearningError(
            "Review draft workbook fields do not match format v1."
        )
    _resolve_repo_path(workbook["path"], repo_root, "Review draft workbook path")
    _sha256(workbook["sha256"], "Review draft workbook SHA-256")
    video = _mapping(draft["video"], "Review draft video")
    if set(video) != {"path", "sha256"} or video != {
        "path": selection["video"]["path"],
        "sha256": selection["video"]["sha256"],
    }:
        raise ActiveLearningError("Review draft video does not match the selection.")
    if (
        type(draft["time_precision_seconds"]) is not int
        or draft["time_precision_seconds"] != 1
    ):
        raise ActiveLearningError("Review draft time precision must be one second.")
    draft_clips = draft["clips"]
    selection_clips = selection["clips"]
    if not isinstance(draft_clips, list) or len(draft_clips) != 40:
        raise ActiveLearningError("Review draft must contain exactly 40 clips.")

    audited_clips: list[dict[str, Any]] = []
    absolute_actions: list[dict[str, Any]] = []
    for clip_index, (raw_draft_clip, raw_selection_clip) in enumerate(
        zip(draft_clips, selection_clips), start=1
    ):
        draft_clip = _mapping(raw_draft_clip, f"Review draft clip {clip_index}")
        selection_clip = _mapping(raw_selection_clip, f"Selection clip {clip_index}")
        _exact_fields(
            draft_clip,
            {
                "clip_id",
                "ordinal",
                "source_start_seconds",
                "source_end_seconds",
                "actions",
            },
            f"Review draft clip {clip_index}",
        )
        if (
            draft_clip["clip_id"] != selection_clip["clip_id"]
            or type(draft_clip["ordinal"]) is not int
            or draft_clip["ordinal"] != selection_clip["ordinal"]
            or draft_clip["source_start_seconds"] != selection_clip["start_seconds"]
            or draft_clip["source_end_seconds"] != selection_clip["end_seconds"]
        ):
            raise ActiveLearningError(
                "Review draft clips must preserve exact selection order and bounds."
            )
        actions = draft_clip["actions"]
        if not isinstance(actions, list) or not actions:
            raise ActiveLearningError(f"Review draft clip {clip_index} has no actions.")
        normalized_actions: list[dict[str, Any]] = []
        backgrounds = 0
        duration = selection_clip["end_seconds"] - selection_clip["start_seconds"]
        for action_slot, raw_action in enumerate(actions, start=1):
            action = _mapping(
                raw_action, f"Review draft clip {clip_index} action {action_slot}"
            )
            _exact_fields(
                action,
                {
                    "action",
                    "relative_start_seconds",
                    "relative_end_seconds",
                    "team_side",
                    "note",
                },
                f"Review draft clip {clip_index} action {action_slot}",
            )
            label = action["action"]
            side = action["team_side"]
            start = action["relative_start_seconds"]
            end = action["relative_end_seconds"]
            if label not in ACTION_LABELS:
                raise ActiveLearningError("Review action label is invalid.")
            if side not in {"far", "near"}:
                raise ActiveLearningError(
                    "Review action team_side must be far or near."
                )
            if not isinstance(action["note"], str):
                raise ActiveLearningError("Review action note must be text.")
            if label == "background":
                backgrounds += 1
                if (
                    isinstance(start, bool)
                    or isinstance(end, bool)
                    or not isinstance(start, (int, float))
                    or not isinstance(end, (int, float))
                    or not math.isfinite(float(start))
                    or not math.isfinite(float(end))
                    or start != 0
                    or end != duration
                ):
                    raise ActiveLearningError(
                        "Background must cover the exact normalized clip bounds."
                    )
            elif (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end <= start
                or end > duration
            ):
                raise ActiveLearningError(
                    "Positive review actions must use whole relative seconds within the clip."
                )
            normalized = dict(action)
            normalized_actions.append(normalized)
            if label != "background":
                crop = selection["video"]["crops"][side]
                absolute_actions.append(
                    {
                        "batch_id": selection["batch_id"],
                        "clip_id": draft_clip["clip_id"],
                        "action_slot": action_slot,
                        "action": label,
                        "team_side": side,
                        "relative_start_seconds": start,
                        "relative_end_seconds": end,
                        "start_seconds": round(
                            selection_clip["start_seconds"] + start, 6
                        ),
                        "end_seconds": round(selection_clip["start_seconds"] + end, 6),
                        "crop": list(crop),
                        "note": action["note"],
                    }
                )
        if backgrounds and len(normalized_actions) != 1:
            raise ActiveLearningError(
                "Background cannot be mixed with positive actions."
            )
        audited_clips.append(
            {
                "clip_id": draft_clip["clip_id"],
                "ordinal": draft_clip["ordinal"],
                "source_start_seconds": draft_clip["source_start_seconds"],
                "source_end_seconds": draft_clip["source_end_seconds"],
                "actions": normalized_actions,
            }
        )
    return audited_clips, absolute_actions


def _read_source_table(path: Path) -> tuple[list[str], list[dict[str, str | None]]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ActiveLearningError("Base manifest has no header.")
            return list(reader.fieldnames), list(reader)
    except OSError as exc:
        raise ActiveLearningError(f"Cannot read base manifest: {path}") from exc


def _video_root_audit(effective_video_root: Path, repo_root: Path) -> dict[str, str]:
    try:
        relative = effective_video_root.relative_to(repo_root)
    except ValueError:
        return {"kind": "absolute", "path": effective_video_root.as_posix()}
    return {"kind": "repo_relative", "path": relative.as_posix() or "."}


def _portable_video_path(video_path: Path, effective_video_root: Path) -> str:
    try:
        relative = os.path.relpath(video_path, effective_video_root)
    except ValueError as exc:
        raise ActiveLearningError(
            "video_root must share a filesystem volume with the selected video."
        ) from exc
    return relative.replace(os.sep, "/")


def _write_unique_sibling(output_path: Path, payload: bytes) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{output_path.name}.tmp-",
            dir=output_path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return temporary
    except OSError as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise ActiveLearningError(
            f"Could not write temporary output for: {output_path}"
        ) from exc


def _publish_dual_outputs(
    *,
    manifest_bytes: bytes,
    results_builder: Any,
    output_manifest: Path,
    output_results: Path,
    effective_video_root: Path,
    require_files: bool,
) -> tuple[bytes, dict[str, object]]:
    temporary_manifest: Path | None = None
    temporary_results: Path | None = None
    manifest_created = False
    try:
        temporary_manifest = _write_unique_sibling(output_manifest, manifest_bytes)
        validated_records = load_manifest(
            temporary_manifest,
            video_root=effective_video_root,
            require_files=require_files,
        )
        manifest_hash = _sha256_file(temporary_manifest)
        results = results_builder(manifest_hash, validated_records)
        results_bytes = (
            json.dumps(results, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        temporary_results = _write_unique_sibling(output_results, results_bytes)
        try:
            os.link(temporary_manifest, output_manifest)
            manifest_created = True
            os.link(temporary_results, output_results)
        except OSError as exc:
            if manifest_created:
                try:
                    if os.path.samefile(temporary_manifest, output_manifest):
                        output_manifest.unlink()
                except (FileNotFoundError, OSError):
                    pass
            if isinstance(exc, FileExistsError):
                raise ActiveLearningError(
                    "Review application output already exists."
                ) from exc
            raise
        return manifest_bytes, results
    finally:
        if temporary_manifest is not None:
            temporary_manifest.unlink(missing_ok=True)
        if temporary_results is not None:
            temporary_results.unlink(missing_ok=True)


def _background_settings(
    *,
    background_guard_seconds: object,
    max_background_windows: object,
    background_seed: object,
    selection: dict[str, Any],
    positive_count: int,
) -> tuple[float | int, int, int, int]:
    if (
        isinstance(background_guard_seconds, bool)
        or not isinstance(background_guard_seconds, (int, float))
        or not math.isfinite(float(background_guard_seconds))
        or background_guard_seconds < 0
    ):
        raise ActiveLearningError(
            "background_guard_seconds must be finite and nonnegative."
        )
    if max_background_windows is not None and (
        isinstance(max_background_windows, bool)
        or not isinstance(max_background_windows, int)
        or max_background_windows < 0
    ):
        raise ActiveLearningError(
            "max_background_windows must be a nonnegative integer or None."
        )
    if background_seed is None:
        persisted_seed = selection.get("settings", {}).get("seed", 0)
        actual_seed = persisted_seed if type(persisted_seed) is int else 0
    elif type(background_seed) is int:
        actual_seed = background_seed
    else:
        raise ActiveLearningError("background_seed must be an integer or None.")
    requested_cap = (
        positive_count if max_background_windows is None else max_background_windows
    )
    return (
        background_guard_seconds,
        requested_cap,
        min(requested_cap, positive_count),
        actual_seed,
    )


def _overlaps(
    left_start: float,
    left_end: float,
    right_start: float,
    right_end: float,
) -> bool:
    return left_start < right_end and right_start < left_end


def _rank_tie(seed: int, clip_id: str, side: str, window_index: int) -> str:
    return hashlib.sha256(
        f"{seed}/{clip_id}/{side}/{window_index}".encode()
    ).hexdigest()


def _select_background_windows(
    *,
    selection: dict[str, Any],
    audited_clips: list[dict[str, Any]],
    absolute_actions: list[dict[str, Any]],
    merged: dict[str, Any],
    guard: float,
    cap: int,
    seed: int,
) -> list[dict[str, Any]]:
    if cap == 0:
        return []
    input_runs = _mapping(merged.get("input_runs"), "Merged input_runs")
    candidates: list[dict[str, Any]] = []
    for clip, selection_clip in zip(audited_clips, selection["clips"]):
        reviewed_sides = {action["team_side"] for action in clip["actions"]}
        clip_start = selection_clip["start_seconds"]
        clip_end = selection_clip["end_seconds"]
        for side in sorted(reviewed_sides):
            run = _mapping(input_runs.get(side), f"Merged {side} input run")
            windows = run.get("windows")
            if not isinstance(windows, list):
                raise ActiveLearningError(f"Merged {side} windows must be an array.")
            for raw_window in windows:
                window = _mapping(raw_window, f"Merged {side} window")
                window_index = window.get("window_index")
                start = window.get("start_seconds")
                end = window.get("end_seconds")
                action = window.get("action")
                confidence = window.get("confidence")
                if (
                    type(window_index) is not int
                    or window_index < 0
                    or isinstance(start, bool)
                    or isinstance(end, bool)
                    or not isinstance(start, (int, float))
                    or not isinstance(end, (int, float))
                    or not math.isfinite(float(start))
                    or not math.isfinite(float(end))
                    or end <= start
                    or action not in ACTION_LABELS
                    or isinstance(confidence, bool)
                    or not isinstance(confidence, (int, float))
                    or not math.isfinite(float(confidence))
                    or not 0 <= confidence <= 1
                ):
                    raise ActiveLearningError(f"Merged {side} window is invalid.")
                if start < clip_start or end > clip_end:
                    continue
                if any(
                    positive["team_side"] == side
                    and _overlaps(
                        start,
                        end,
                        positive["start_seconds"] - guard,
                        positive["end_seconds"] + guard,
                    )
                    for positive in absolute_actions
                ):
                    continue
                candidate = {
                    "clip_id": clip["clip_id"],
                    "team_side": side,
                    "window_index": window_index,
                    "start_seconds": start,
                    "end_seconds": end,
                    "source_top1_action": action,
                    "source_top1_confidence": confidence,
                    "crop": list(selection["video"]["crops"][side]),
                }
                candidate["_rank"] = (
                    0 if action != "background" else 1,
                    -confidence if action != "background" else 0,
                    _rank_tie(seed, clip["clip_id"], side, window_index),
                )
                candidates.append(candidate)
    candidates.sort(key=lambda item: item["_rank"])
    selected: list[dict[str, Any]] = []
    for candidate in candidates:
        if any(
            _overlaps(
                candidate["start_seconds"],
                candidate["end_seconds"],
                chosen["start_seconds"],
                chosen["end_seconds"],
            )
            for chosen in selected
        ):
            continue
        candidate.pop("_rank")
        selected.append(candidate)
        if len(selected) == cap:
            break
    return selected


def apply_active_review(
    base_manifest_path: str | Path,
    selection_path: str | Path,
    review_input_path: str | Path,
    output_manifest_path: str | Path,
    output_results_path: str | Path,
    *,
    repo_root: str | Path,
    legacy_base_match_id: str,
    review_match_id: str,
    video_root: str | Path | None = None,
    background_guard_seconds: float = 0.5,
    max_background_windows: int | None = None,
    background_seed: int | None = None,
    require_files: bool = True,
) -> dict[str, object]:
    root = Path(repo_root).expanduser().resolve()
    base_manifest = Path(base_manifest_path).expanduser().resolve()
    selection_file = Path(selection_path).expanduser().resolve()
    review_input = Path(review_input_path).expanduser().resolve()
    output_manifest = Path(output_manifest_path).expanduser().resolve()
    output_results = Path(output_results_path).expanduser().resolve()
    legacy_id, review_id = _validate_match_ids(legacy_base_match_id, review_match_id)
    if output_manifest.exists() or output_results.exists():
        raise ActiveLearningError("Review application output already exists.")
    effective_video_root = (
        Path(video_root).expanduser().resolve()
        if video_root is not None
        else base_manifest.parent.resolve()
    )
    base_records = load_manifest(
        base_manifest,
        video_root=effective_video_root,
        require_files=require_files,
    )
    selection = load_review_selection(
        selection_file, repo_root=root, require_video=require_files
    )
    selection_video = _resolve_repo_path(
        selection["video"]["path"], root, "Selection video path"
    )
    if any(
        record.video_path == selection_video and record.split in {"val", "test"}
        for record in base_records
    ):
        raise ActiveLearningError(
            "The reviewed source video already appears in a val or test split."
        )
    draft = _read_json(review_input, "review draft")
    audited_clips, absolute_actions = _validate_draft(
        draft,
        selection=selection,
        selection_path=selection_file,
        repo_root=root,
    )
    guard, requested_cap, effective_cap, actual_seed = _background_settings(
        background_guard_seconds=background_guard_seconds,
        max_background_windows=max_background_windows,
        background_seed=background_seed,
        selection=selection,
        positive_count=len(absolute_actions),
    )
    merged_path = _resolve_repo_path(
        selection["source"]["merged_json"], root, "Selection merged JSON"
    )
    merged = _read_json(
        merged_path,
        "merged review source",
        expected_sha256=selection["source"]["merged_json_sha256"],
    )
    negatives = _select_background_windows(
        selection=selection,
        audited_clips=audited_clips,
        absolute_actions=absolute_actions,
        merged=merged,
        guard=guard,
        cap=effective_cap,
        seed=actual_seed,
    )
    fieldnames, rows = _read_source_table(base_manifest)
    for column in _CANONICAL_COLUMNS:
        if column not in fieldnames:
            fieldnames.append(column)
    blank_match_videos: set[Path] = set()
    for row in rows:
        if (row.get("match_id") or "").strip():
            continue
        raw_video = Path((row.get("video_path") or "").strip()).expanduser()
        blank_match_videos.add(
            (
                raw_video
                if raw_video.is_absolute()
                else effective_video_root / raw_video
            ).resolve()
        )
    if len(blank_match_videos) > 1:
        raise ActiveLearningError(
            "Blank legacy match IDs resolve to more than one base video."
        )
    for row in rows:
        if not (row.get("match_id") or "").strip():
            row["match_id"] = legacy_id

    portable_video = _portable_video_path(selection_video, effective_video_root)
    for action in absolute_actions:
        x1, y1, x2, y2 = action["crop"]
        note = (
            f"Active review batch={action['batch_id']}; clip={action['clip_id']}; "
            f"action_slot={action['action_slot']}; relative_seconds="
            f"[{action['relative_start_seconds']}, {action['relative_end_seconds']}]."
        )
        if action["note"]:
            note += f" Reviewer note: {action['note']}"
        new_row = {column: "" for column in fieldnames}
        new_row.update(
            {
                "video_path": portable_video,
                "start_seconds": str(action["start_seconds"]),
                "end_seconds": str(action["end_seconds"]),
                "label": action["action"],
                "team_side": action["team_side"],
                "player_number": "",
                "split": "train",
                "crop_x1": str(x1),
                "crop_y1": str(y1),
                "crop_x2": str(x2),
                "crop_y2": str(y2),
                "match_id": review_id,
                "review_status": "reviewed",
                "notes": note,
            }
        )
        rows.append(new_row)
    for negative in negatives:
        x1, y1, x2, y2 = negative["crop"]
        new_row = {column: "" for column in fieldnames}
        new_row.update(
            {
                "video_path": portable_video,
                "start_seconds": str(negative["start_seconds"]),
                "end_seconds": str(negative["end_seconds"]),
                "label": "background",
                "team_side": negative["team_side"],
                "player_number": "",
                "split": "train",
                "crop_x1": str(x1),
                "crop_y1": str(y1),
                "crop_x2": str(x2),
                "crop_y2": str(y2),
                "match_id": review_id,
                "review_status": "reviewed",
                "notes": (
                    f"Active review hard negative batch={selection['batch_id']}; "
                    f"clip={negative['clip_id']}; side={negative['team_side']}; "
                    f"window_index={negative['window_index']}; model_top1="
                    f"{negative['source_top1_action']} "
                    f"({negative['source_top1_confidence']})."
                ),
            }
        )
        rows.append(new_row)

    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    manifest_bytes = csv_buffer.getvalue().encode("utf-8")
    normalized_output_path = (
        _relative_repo_path(output_manifest, root, "output manifest")
        if output_manifest.is_relative_to(root)
        else output_manifest.as_posix()
    )
    settings = {
        "legacy_base_match_id": legacy_id,
        "review_match_id": review_id,
        "effective_video_root": _video_root_audit(effective_video_root, root),
        "background_guard_seconds": guard,
        "requested_max_background_windows": requested_cap,
        "effective_max_background_windows": effective_cap,
        "background_seed": actual_seed,
        "require_files": require_files,
    }
    label_counts = Counter(action["action"] for action in absolute_actions)
    label_counts.update({"background": len(negatives)})
    summary = {
        "positive_action_count": len(absolute_actions),
        "generated_background_count": len(negatives),
        "added_records_by_label": dict(sorted(label_counts.items())),
    }

    def build_results(
        manifest_hash: str, validated_records: object
    ) -> dict[str, object]:
        return {
            "format_version": 1,
            "batch_id": selection["batch_id"],
            "round_id": selection["round_id"],
            "selection_sha256": _sha256_file(selection_file),
            "review_input_sha256": _sha256_file(review_input),
            "base_manifest_sha256": _sha256_file(base_manifest),
            "output_manifest": normalized_output_path,
            "output_manifest_sha256": manifest_hash,
            "settings": settings,
            "clips": audited_clips,
            "absolute_actions": absolute_actions,
            "generated_background_windows": negatives,
            "positive_action_count": len(absolute_actions),
            "summary": {
                **summarize_manifest(validated_records),
                **summary,
            },
        }

    _, results = _publish_dual_outputs(
        manifest_bytes=manifest_bytes,
        results_builder=build_results,
        output_manifest=output_manifest,
        output_results=output_results,
        effective_video_root=effective_video_root,
        require_files=require_files,
    )
    return results
