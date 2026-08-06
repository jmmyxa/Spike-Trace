from __future__ import annotations

import csv
import json
import math
import tempfile
from pathlib import Path
from typing import Any

from .constants import ACTION_LABELS
from .errors import ReviewError
from .manifest import load_manifest, summarize_manifest
from .timecode import format_video_time

REVIEW_SPEC_FORMAT_VERSION = 1
REVIEW_RESULTS_FORMAT_VERSION = 1
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
_RESULT_FIELDS = {
    "format_version",
    "manifest",
    "spec",
    "time_precision_seconds",
    "confirmations",
}
_CONFIRMATION_FIELDS = {
    "record_index",
    "source_video_path",
    "source_start_seconds",
    "source_end_seconds",
    "source_action",
    "source_split",
    "source_team_side",
    "source_player_number",
    "source_crop",
    "source_notes",
    "operation",
    "confirmed_action",
    "confirmed_start_seconds",
    "confirmed_end_seconds",
    "confirmation_note",
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


def _read_source_table(
    manifest: Path,
) -> tuple[list[str], list[dict[str, str | None]]]:
    with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or ())
        rows: list[dict[str, str | None]] = []
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise ReviewError(
                    f"Manifest row {row_number} has extra cells beyond the header."
                )
            rows.append({field: row.get(field) for field in fieldnames})
    return fieldnames, rows


def _read_source_rows(manifest: Path) -> list[dict[str, str | None]]:
    _fieldnames, rows = _read_source_table(manifest)
    return rows


def _read_review_results(results_path: str | Path) -> tuple[Path, dict[str, Any]]:
    path = Path(results_path).expanduser().resolve()
    if not path.is_file():
        raise ReviewError(f"Review results do not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"Could not read review results: {path}") from exc
    if not isinstance(payload, dict):
        raise ReviewError("Review results must be a JSON object.")
    unknown = set(payload) - _RESULT_FIELDS
    if unknown:
        raise ReviewError(
            "Review results have unknown fields: " + ", ".join(sorted(unknown))
        )
    if payload.get("format_version") != REVIEW_RESULTS_FORMAT_VERSION:
        raise ReviewError(
            f"Unsupported review results format: {payload.get('format_version')}"
        )
    precision = payload.get("time_precision_seconds")
    if (
        isinstance(precision, bool)
        or not isinstance(precision, (int, float))
        or not math.isfinite(float(precision))
        or float(precision) <= 0
    ):
        raise ReviewError(
            "Review results time_precision_seconds must be a positive finite number."
        )
    if not isinstance(payload.get("confirmations"), list):
        raise ReviewError("Review results confirmations must be a list.")
    return path, payload


def _result_number(value: Any, field: str, record_index: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReviewError(f"Confirmation {record_index} {field} must be a number.")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise ReviewError(
            f"Confirmation {record_index} {field} must be non-negative and finite."
        )
    return parsed


def _seconds_text(value: float) -> str:
    return f"{value:.10f}".rstrip("0").rstrip(".")


def _append_review_note(
    row: dict[str, str | None],
    *,
    record_index: int,
    operation: str,
    prior_action: str,
    prior_start: float,
    prior_end: float,
    confirmed_action: str,
    confirmed_start: float,
    confirmed_end: float,
    confirmation_note: str,
) -> None:
    if operation == "add_window":
        audit = (
            f"Second-pass human-reviewed; added from source record {record_index}; "
            f"confirmed={confirmed_action}@{_seconds_text(confirmed_start)}-"
            f"{_seconds_text(confirmed_end)}"
        )
    else:
        audit = (
            f"Second-pass human-reviewed; operation={operation}; "
            f"prior={prior_action}@{_seconds_text(prior_start)}-"
            f"{_seconds_text(prior_end)}; confirmed={confirmed_action}@"
            f"{_seconds_text(confirmed_start)}-{_seconds_text(confirmed_end)}"
        )
    if confirmation_note:
        audit += f"; reviewer note: {confirmation_note}"
    existing = (row.get("notes") or "").strip()
    row["notes"] = f"{existing}; {audit}" if existing else audit


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
    spec_input = Path(spec_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if output in {manifest, spec_input}:
        raise ReviewError("Review output must be different from every input file.")
    _spec_path, spec = _read_spec(spec_input)
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


def apply_review_results(
    manifest_path: str | Path,
    spec_path: str | Path,
    results_path: str | Path,
    output_path: str | Path,
    *,
    video_root: str | Path | None = None,
    require_files: bool = True,
) -> dict[str, object]:
    manifest = Path(manifest_path).expanduser().resolve()
    spec_input = Path(spec_path).expanduser().resolve()
    results_input = Path(results_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if output in {manifest, spec_input, results_input}:
        raise ReviewError("Review output must be different from every input file.")
    spec_file, spec = _read_spec(spec_input)
    _results_file, results = _read_review_results(results_input)
    if Path(spec["manifest"]).name != manifest.name:
        raise ReviewError(
            f"Review specification manifest '{spec['manifest']}' does not match "
            f"'{manifest.name}'."
        )
    if Path(str(results.get("manifest", ""))).name != manifest.name:
        raise ReviewError("Review results do not match the source manifest.")
    if Path(str(results.get("spec", ""))).name != spec_file.name:
        raise ReviewError("Review results do not match the review specification.")

    effective_video_root = (
        Path(video_root).expanduser().resolve()
        if video_root is not None
        else manifest.parent
    )
    records = load_manifest(
        manifest, video_root=effective_video_root, require_files=require_files
    )
    fieldnames, rows = _read_source_table(manifest)
    for field in ("review_status", "notes"):
        if field not in fieldnames:
            fieldnames.append(field)

    requests = _validate_requests(spec["requests"], len(records))
    requests_by_index = {request["record_index"]: request for request in requests}
    confirmations = results["confirmations"]
    if len(confirmations) != len(requests):
        raise ReviewError("Review results must confirm every requested record.")

    updated = 0
    added_rows: list[dict[str, str | None]] = []
    applied_indices: list[int] = []
    for raw_confirmation in confirmations:
        if not isinstance(raw_confirmation, dict):
            raise ReviewError("Each review confirmation must be a JSON object.")
        unknown = set(raw_confirmation) - _CONFIRMATION_FIELDS
        if unknown:
            raise ReviewError(
                "Review confirmation has unknown fields: " + ", ".join(sorted(unknown))
            )
        record_index = raw_confirmation.get("record_index")
        if isinstance(record_index, bool) or not isinstance(record_index, int):
            raise ReviewError("Review confirmation record_index must be an integer.")
        if record_index not in requests_by_index:
            raise ReviewError(
                f"Review confirmation record_index {record_index} was not requested."
            )
        if record_index in applied_indices:
            raise ReviewError(
                f"Review results have duplicate record_index {record_index}."
            )

        request = requests_by_index[record_index]
        operation = raw_confirmation.get("operation")
        if operation not in SUGGESTED_OPERATIONS:
            raise ReviewError(
                f"Confirmation {record_index} must provide a supported operation."
            )
        if operation != request.get("suggested_operation"):
            raise ReviewError(
                f"Confirmation {record_index} operation does not match the review spec."
            )
        action = raw_confirmation.get("confirmed_action")
        if action not in ACTION_LABELS:
            raise ReviewError(
                f"Confirmation {record_index} has unknown action '{action}'."
            )
        start = _result_number(
            raw_confirmation.get("confirmed_start_seconds"),
            "confirmed_start_seconds",
            record_index,
        )
        end = _result_number(
            raw_confirmation.get("confirmed_end_seconds"),
            "confirmed_end_seconds",
            record_index,
        )
        if end <= start:
            raise ReviewError(f"Confirmation {record_index} must satisfy start < end.")
        note = raw_confirmation.get("confirmation_note", "")
        if not isinstance(note, str):
            raise ReviewError(
                f"Confirmation {record_index} confirmation_note must be text."
            )

        source = rows[record_index - 1]
        source_start = _result_number(
            raw_confirmation.get("source_start_seconds"),
            "source_start_seconds",
            record_index,
        )
        source_end = _result_number(
            raw_confirmation.get("source_end_seconds"),
            "source_end_seconds",
            record_index,
        )
        source_action = raw_confirmation.get("source_action")
        source_video = raw_confirmation.get("source_video_path")
        source_crop = ",".join(
            (source.get(field) or "").strip()
            for field in ("crop_x1", "crop_y1", "crop_x2", "crop_y2")
            if (source.get(field) or "").strip()
        )
        if (
            source_video != (source.get("video_path") or "").strip()
            or source_action != (source.get("label") or "").strip()
            or raw_confirmation.get("source_split")
            != (source.get("split") or "").strip()
            or raw_confirmation.get("source_team_side")
            != (source.get("team_side") or "").strip()
            or raw_confirmation.get("source_player_number")
            != (source.get("player_number") or "").strip()
            or raw_confirmation.get("source_crop") != source_crop
            or raw_confirmation.get("source_notes")
            != (source.get("notes") or "").strip()
            or source_start != float(source.get("start_seconds") or "")
            or source_end != float(source.get("end_seconds") or "")
        ):
            raise ReviewError(
                f"Confirmation {record_index} source snapshot does not match manifest."
            )

        target = dict(source) if operation == "add_window" else source
        target["start_seconds"] = _seconds_text(start)
        target["end_seconds"] = _seconds_text(end)
        target["label"] = action
        target["review_status"] = "reviewed"
        _append_review_note(
            target,
            record_index=record_index,
            operation=operation,
            prior_action=str(source_action),
            prior_start=source_start,
            prior_end=source_end,
            confirmed_action=action,
            confirmed_start=start,
            confirmed_end=end,
            confirmation_note=note.strip(),
        )
        if operation == "add_window":
            added_rows.append(target)
        else:
            updated += 1
        applied_indices.append(record_index)

    if applied_indices != [request["record_index"] for request in requests]:
        raise ReviewError(
            "Review confirmations must follow the review specification order."
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8-sig",
            newline="",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_output = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows([*rows, *added_rows])
        output_records = load_manifest(
            temporary_output,
            video_root=effective_video_root,
            require_files=require_files,
        )
        manifest_summary = summarize_manifest(output_records)
        temporary_output.replace(output)
    finally:
        if temporary_output is not None:
            temporary_output.unlink(missing_ok=True)
    return {
        "output_csv": str(output),
        "source_records": len(rows),
        "updated_records": updated,
        "added_records": len(added_rows),
        "output_records": len(output_records),
        "record_indices": applied_indices,
        **manifest_summary,
    }
