from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

from .constants import ACTION_LABELS
from .errors import ReviewError
from .manifest import load_manifest
from .timecode import format_video_time

REVIEW_SPEC_FORMAT_VERSION = 1
SUGGESTED_OPERATIONS = ("keep", "relabel", "move_window", "add_window")
REVIEW_QUEUE_FIELDS = (
    "record_index",
    "video_path",
    "current_start_seconds",
    "current_end_seconds",
    "current_start_time",
    "current_end_time",
    "current_action",
    "split",
    "team_side",
    "player_number",
    "crop",
    "source_notes",
    "reviewer_note",
    "review_reason",
    "suggested_operation",
    "suggested_action",
    "suggested_start_seconds",
    "suggested_end_seconds",
    "suggested_start_time",
    "suggested_end_time",
    "confirmed_action",
    "confirmed_start_time",
    "confirmed_end_time",
    "confirmation_note",
)

_SPEC_FIELDS = {"format_version", "manifest", "target_team", "requests"}
_REQUEST_FIELDS = {
    "record_index",
    "reason",
    "suggested_operation",
    "suggested_action",
    "suggested_start_seconds",
    "suggested_end_seconds",
}


def _read_spec(spec_path: str | Path) -> tuple[Path, dict[str, Any]]:
    path = Path(spec_path).expanduser().resolve()
    if not path.is_file():
        raise ReviewError(f"Review specification does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"Could not read review specification: {path}") from exc
    if not isinstance(payload, dict):
        raise ReviewError("Review specification must be a JSON object.")
    unknown = set(payload) - _SPEC_FIELDS
    if unknown:
        raise ReviewError(
            "Review specification has unknown fields: " + ", ".join(sorted(unknown))
        )
    if payload.get("format_version") != REVIEW_SPEC_FORMAT_VERSION:
        raise ReviewError(
            f"Unsupported review specification format: {payload.get('format_version')}"
        )
    if not isinstance(payload.get("manifest"), str) or not payload["manifest"].strip():
        raise ReviewError("Review specification must name its annotation manifest.")
    if (
        not isinstance(payload.get("target_team"), str)
        or not payload["target_team"].strip()
    ):
        raise ReviewError("Review specification must provide target_team.")
    if not isinstance(payload.get("requests"), list) or not payload["requests"]:
        raise ReviewError("Review specification requests must be a non-empty list.")
    return path, payload


def _suggested_time(request: dict[str, Any], field: str, request_number: int) -> float:
    value = request[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReviewError(f"Request {request_number} {field} must be a number.")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise ReviewError(
            f"Request {request_number} {field} must be a non-negative finite number."
        )
    return parsed


def _validate_requests(requests: list[Any], record_count: int) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    seen: set[int] = set()
    for request_number, raw_request in enumerate(requests, start=1):
        if not isinstance(raw_request, dict):
            raise ReviewError(f"Request {request_number} must be a JSON object.")
        unknown = set(raw_request) - _REQUEST_FIELDS
        if unknown:
            raise ReviewError(
                f"Request {request_number} has unknown fields: "
                + ", ".join(sorted(unknown))
            )

        record_index = raw_request.get("record_index")
        if isinstance(record_index, bool) or not isinstance(record_index, int):
            raise ReviewError(
                f"Request {request_number} record_index must be an integer."
            )
        if not 1 <= record_index <= record_count:
            raise ReviewError(
                f"Request {request_number} record_index {record_index} is out of range."
            )
        if record_index in seen:
            raise ReviewError(
                f"Review request has duplicate record_index {record_index}."
            )
        seen.add(record_index)

        reason = raw_request.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ReviewError(f"Request {request_number} reason must be non-empty.")

        operation = raw_request.get("suggested_operation")
        if operation is not None and operation not in SUGGESTED_OPERATIONS:
            raise ReviewError(
                f"Request {request_number} has invalid suggested_operation "
                f"'{operation}'."
            )
        action = raw_request.get("suggested_action")
        if action is not None and action not in ACTION_LABELS:
            raise ReviewError(
                f"Request {request_number} has unknown suggested_action '{action}'."
            )
        has_start = "suggested_start_seconds" in raw_request
        has_end = "suggested_end_seconds" in raw_request
        if (action is not None or has_start or has_end) and operation is None:
            raise ReviewError(
                f"Request {request_number} suggestions require suggested_operation."
            )

        request = dict(raw_request)
        request["reason"] = reason.strip()
        if has_start:
            request["suggested_start_seconds"] = _suggested_time(
                request, "suggested_start_seconds", request_number
            )
        if has_end:
            request["suggested_end_seconds"] = _suggested_time(
                request, "suggested_end_seconds", request_number
            )
        if has_start and has_end:
            start = request["suggested_start_seconds"]
            end = request["suggested_end_seconds"]
            if end <= start:
                raise ReviewError(
                    f"Request {request_number} suggested times must satisfy start < end."
                )
        validated.append(request)
    return sorted(validated, key=lambda request: request["record_index"])


def _read_source_rows(manifest: Path) -> list[dict[str, str | None]]:
    with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _reviewer_note(notes: str) -> str:
    marker = "reviewer note:"
    index = notes.casefold().rfind(marker)
    if index < 0:
        return ""
    return notes[index + len(marker) :].strip()


def _queue_row(
    record_index: int,
    record: Any,
    source_row: dict[str, str | None],
    request: dict[str, Any],
) -> dict[str, object]:
    suggested_start = request.get("suggested_start_seconds")
    suggested_end = request.get("suggested_end_seconds")
    notes = (source_row.get("notes") or "").strip()
    return {
        "record_index": record_index,
        "video_path": (source_row.get("video_path") or "").strip(),
        "current_start_seconds": record.start_seconds,
        "current_end_seconds": record.end_seconds,
        "current_start_time": format_video_time(record.start_seconds),
        "current_end_time": format_video_time(record.end_seconds),
        "current_action": record.label,
        "split": record.split,
        "team_side": record.team_side or "",
        "player_number": record.player_number or "",
        "crop": ",".join(str(value) for value in record.crop) if record.crop else "",
        "source_notes": notes,
        "reviewer_note": _reviewer_note(notes),
        "review_reason": request["reason"],
        "suggested_operation": request.get("suggested_operation", ""),
        "suggested_action": request.get("suggested_action", ""),
        "suggested_start_seconds": suggested_start
        if suggested_start is not None
        else "",
        "suggested_end_seconds": suggested_end if suggested_end is not None else "",
        "suggested_start_time": (
            format_video_time(suggested_start) if suggested_start is not None else ""
        ),
        "suggested_end_time": (
            format_video_time(suggested_end) if suggested_end is not None else ""
        ),
        "confirmed_action": "",
        "confirmed_start_time": "",
        "confirmed_end_time": "",
        "confirmation_note": "",
    }


def prepare_review_queue(
    manifest_path: str | Path,
    spec_path: str | Path,
    output_path: str | Path,
    *,
    video_root: str | Path | None = None,
    require_files: bool = True,
) -> dict[str, object]:
    manifest = Path(manifest_path).expanduser().resolve()
    _spec_path, spec = _read_spec(spec_path)
    if Path(spec["manifest"]).name != manifest.name:
        raise ReviewError(
            f"Review specification manifest '{spec['manifest']}' does not match "
            f"'{manifest.name}'."
        )

    records = load_manifest(
        manifest, video_root=video_root, require_files=require_files
    )
    source_rows = _read_source_rows(manifest)
    if len(source_rows) != len(records):
        raise ReviewError("Manifest rows changed while preparing the review queue.")
    requests = _validate_requests(spec["requests"], len(records))
    rows = [
        _queue_row(
            request["record_index"],
            records[request["record_index"] - 1],
            source_rows[request["record_index"] - 1],
            request,
        )
        for request in requests
    ]

    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_QUEUE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    return {
        "output_csv": str(output),
        "records": len(rows),
        "record_indices": [request["record_index"] for request in requests],
        "target_team": spec["target_team"].strip(),
    }
