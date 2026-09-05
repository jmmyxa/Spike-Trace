from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path

from .constants import ACTION_LABELS
from .errors import ValidationError
from .validation_contract import canonical_json_bytes, sha256_file
from .validation_evaluation import ValidationReport
from .validation_inference import ValidationInferenceResult
from .validation_truth import ValidationTruth

_FILES = ("metrics.json", "confusion_matrix.csv", "predicted-events.json", "predicted-events.csv", "run-manifest.json")
_EVENT_FIELDS = ("prediction_id", "segment_id", "set_index", "team_side", "start_seconds", "end_seconds", "action", "confidence", "source_window_indices")
_PAYLOAD_FILES = _FILES[1:-1]
_CONFUSION_FIELDS = ("scope", "true_label", "predicted_label", "count")


def _write_fsync(path: Path, payload: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _json(value: object) -> bytes:
    try:
        return canonical_json_bytes(value) + b"\n"
    except (TypeError, ValueError) as exc:
        raise ValidationError("Validation output JSON contains unsupported or non-finite values") from exc


def _manifest_core(manifest: Mapping[str, object]) -> dict[str, object]:
    core = dict(manifest)
    core.pop("output_files", None)
    core.pop("metrics_file", None)
    return core


def _metrics_content_hash(metrics: Mapping[str, object]) -> str:
    content = dict(metrics)
    content.pop("manifest_core_sha256", None)
    try:
        payload = canonical_json_bytes(content)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Validation output JSON contains unsupported or non-finite values") from exc
    return hashlib.sha256(payload).hexdigest()


def _file_digest(payload: bytes) -> dict[str, object]:
    return {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )


def _is_format_version(value: object, expected: int = 1) -> bool:
    return type(value) is int and value == expected


def _require_sha256(value: object, label: str) -> str:
    if not _is_sha256(value):
        raise ValidationError(f"{label} SHA-256 is invalid")
    return str(value)


def _strict_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValidationError(f"non-finite JSON value: {value}")


def _validate_digest_record(value: object, label: str) -> None:
    if not isinstance(value, dict) or set(value) != {"sha256", "bytes"}:
        raise ValidationError(f"{label} digest is invalid")
    if not _is_sha256(value.get("sha256")):
        raise ValidationError(f"{label} digest is invalid")
    size = value.get("bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValidationError(f"{label} digest is invalid")


def _validate_video_metadata(value: object, source: Path) -> None:
    fields = {"path", "fps", "frame_count", "width", "height", "duration_seconds"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValidationError("Validation output video metadata is invalid")
    path_value = value.get("path")
    if not isinstance(path_value, str) or Path(path_value).expanduser().resolve() != source.resolve():
        raise ValidationError("Bound source video metadata path mismatch")
    for field in ("frame_count", "width", "height"):
        number = value.get(field)
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise ValidationError("Validation output video metadata is invalid")
    for field in ("fps", "duration_seconds"):
        number = value.get(field)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(float(number)) or float(number) < 0:
            raise ValidationError("Validation output video metadata is invalid")


def _compare_video_metadata(observed: Mapping[str, object], expected: Mapping[str, object]) -> None:
    for field in ("fps", "duration_seconds"):
        if abs(float(observed[field]) - float(expected[field])) > 1e-6:
            raise ValidationError("Bound source video metadata mismatch")
    for field in ("frame_count", "width", "height"):
        if int(observed[field]) != int(expected[field]):
            raise ValidationError("Bound source video metadata mismatch")


def _validate_predicted_events(events: object) -> None:
    if not isinstance(events, list):
        raise ValidationError("Predicted events JSON schema is invalid")
    for event in events:
        if not isinstance(event, dict) or set(event) != set(_EVENT_FIELDS):
            raise ValidationError("Predicted events JSON schema is invalid")
        for field in ("prediction_id", "segment_id"):
            if not isinstance(event.get(field), str) or not event[field]:
                raise ValidationError("Predicted events JSON schema is invalid")
        if isinstance(event.get("set_index"), bool) or not isinstance(event.get("set_index"), int):
            raise ValidationError("Predicted events JSON schema is invalid")
        if event.get("team_side") not in {"near", "far"} or event.get("action") not in ACTION_LABELS:
            raise ValidationError("Predicted events JSON schema is invalid")
        for field in ("start_seconds", "end_seconds", "confidence"):
            value = event.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValidationError("Predicted events JSON schema is invalid")
        if float(event["end_seconds"]) <= float(event["start_seconds"]) or not 0 <= float(event["confidence"]) <= 1:
            raise ValidationError("Predicted events JSON schema is invalid")
        indices = event.get("source_window_indices")
        if not isinstance(indices, list) or any(isinstance(index, bool) or not isinstance(index, int) or index < 0 for index in indices):
            raise ValidationError("Predicted events JSON schema is invalid")


def _validate_predicted_events_csv(path: Path, events: list[object]) -> None:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(_EVENT_FIELDS):
                raise ValidationError("Predicted events CSV schema is invalid")
            rows = list(reader)
    except ValidationError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValidationError("Predicted events CSV is invalid") from exc
    if len(rows) != len(events):
        raise ValidationError("Prediction output count mismatch")
    for event, row in zip(events, rows):
        if None in row:
            raise ValidationError("Predicted events CSV schema is invalid")
        for field in _EVENT_FIELDS:
            expected = event.get(field)
            observed = row.get(field)
            if field == "source_window_indices":
                try:
                    if json.loads(observed or "[]") != list(expected or []):
                        raise ValidationError("Predicted events CSV content mismatch")
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ValidationError("Predicted events CSV content mismatch") from exc
            elif ("" if expected is None else str(expected)) != observed:
                raise ValidationError("Predicted events CSV content mismatch")


def _event_confusion_rows(metrics: Mapping[str, object]) -> list[dict[str, object]]:
    raw = metrics.get("confusion_rows", [])
    if not isinstance(raw, list):
        raise ValidationError("Confusion report schema is invalid")
    rows: list[dict[str, object]] = []
    fields = {"prediction_id", "truth_ref", "predicted_label", "truth_label", "center_error_seconds", "confidence"}
    for item in raw:
        if not isinstance(item, dict) or set(item) != fields:
            raise ValidationError("Confusion report schema is invalid")
        if not isinstance(item["prediction_id"], str) or not isinstance(item["truth_ref"], str):
            raise ValidationError("Confusion report schema is invalid")
        if item["predicted_label"] not in ACTION_LABELS or item["truth_label"] not in ACTION_LABELS:
            raise ValidationError("Confusion report schema is invalid")
        for field in ("center_error_seconds", "confidence"):
            value = item[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
                raise ValidationError("Confusion report schema is invalid")
        if not 0 <= float(item["confidence"]) <= 1:
            raise ValidationError("Confusion report schema is invalid")
        rows.append({"scope": "event", "true_label": item["truth_label"], "predicted_label": item["predicted_label"], "count": 1})
    return rows


def _window_confusion_rows(metrics: Mapping[str, object]) -> list[dict[str, object]]:
    window = metrics.get("window_metrics")
    if not isinstance(window, dict):
        return []
    matrix = window.get("confusion_matrix")
    if matrix in (None, {}):
        samples = window.get("samples", 0)
        if isinstance(samples, bool) or not isinstance(samples, int) or samples != 0:
            raise ValidationError("Window confusion report schema is invalid")
        return []
    if not isinstance(matrix, dict) or matrix.get("labels") != list(ACTION_LABELS):
        raise ValidationError("Window confusion report schema is invalid")
    values = matrix.get("values")
    if not isinstance(values, list) or len(values) != len(ACTION_LABELS):
        raise ValidationError("Window confusion report schema is invalid")
    rows: list[dict[str, object]] = []
    total = 0
    for true_index, true_label in enumerate(ACTION_LABELS):
        row_values = values[true_index]
        if not isinstance(row_values, list) or len(row_values) != len(ACTION_LABELS):
            raise ValidationError("Window confusion report schema is invalid")
        for predicted_index, predicted_label in enumerate(ACTION_LABELS):
            count = row_values[predicted_index]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValidationError("Window confusion report schema is invalid")
            total += count
            rows.append({"scope": "window", "true_label": true_label, "predicted_label": predicted_label, "count": count})
    samples = window.get("samples")
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 0 or samples != total:
        raise ValidationError("Window confusion report count mismatch")
    return rows


def _validate_confusion_csv(path: Path, metrics: Mapping[str, object]) -> None:
    expected = _event_confusion_rows(metrics) + _window_confusion_rows(metrics)
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(_CONFUSION_FIELDS):
                raise ValidationError("Confusion matrix schema is invalid")
            rows = list(reader)
    except ValidationError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValidationError("Confusion matrix is invalid") from exc
    normalized: list[dict[str, object]] = []
    for row in rows:
        if None in row or row.get("scope") not in {"event", "window"} or row.get("true_label") not in ACTION_LABELS or row.get("predicted_label") not in ACTION_LABELS:
            raise ValidationError("Confusion matrix row is invalid")
        raw_count = row.get("count")
        if raw_count is None or not raw_count.isdecimal():
            raise ValidationError("Confusion matrix row is invalid")
        normalized.append({"scope": row["scope"], "true_label": row["true_label"], "predicted_label": row["predicted_label"], "count": int(raw_count)})
    if normalized != expected:
        raise ValidationError("Confusion matrix content mismatch")


def _events(inference: ValidationInferenceResult) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for item in inference.predictions:
        event = asdict(item)
        indices = event.get("source_window_indices")
        if isinstance(indices, tuple):
            event["source_window_indices"] = list(indices)
        events.append(event)
    return events


def _confusion(report: ValidationReport) -> bytes:
    import io
    rows: list[dict[str, object]] = []
    for item in report.confusion_rows:
        rows.append(
            {
                "scope": "event",
                "true_label": item.get("truth_label", ""),
                "predicted_label": item.get("predicted_label", ""),
                "count": 1,
            }
        )
    # Include the one-second matrix in a deterministic long form.
    matrix = report.window_metrics.get("confusion_matrix", {}) if isinstance(report.window_metrics, dict) else {}
    labels = matrix.get("labels", []) if isinstance(matrix, dict) else []
    values = matrix.get("values", []) if isinstance(matrix, dict) else []
    for true_index, true_label in enumerate(labels):
        for pred_index, predicted_label in enumerate(labels):
            try:
                count = int(values[true_index][pred_index])
            except (IndexError, TypeError, ValueError):
                count = 0
            rows.append(
                {
                    "scope": "window",
                    "true_label": true_label,
                    "predicted_label": predicted_label,
                    "count": count,
                }
            )
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=_CONFUSION_FIELDS, lineterminator="\n")
    writer.writeheader(); writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _events_csv(events: list[dict[str, object]]) -> bytes:
    import io
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=_EVENT_FIELDS, lineterminator="\n")
    writer.writeheader()
    for event in events:
        row = {field: event.get(field) for field in _EVENT_FIELDS}
        row["source_window_indices"] = json.dumps(row["source_window_indices"] or [], separators=(",", ":"))
        writer.writerow(row)
    return stream.getvalue().encode("utf-8")


def write_validation_outputs(
    output_dir: str | Path,
    *,
    truth: ValidationTruth,
    inference: ValidationInferenceResult,
    report: ValidationReport,
    checkpoint_path: str | Path,
    code_sha: str,
    parameters: Mapping[str, object],
    created_at: str,
) -> dict[str, Path]:
    if not truth.locked:
        raise ValidationError("validation truth must be locked")
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists():
        raise ValidationError(f"Output directory already exists: {destination}")
    parameter_values = dict(parameters)
    truth_json_value = parameter_values.get("truth_json_path")
    truth_csv_value = parameter_values.get("truth_csv_path")
    if (
        not isinstance(truth_json_value, (str, Path))
        or not str(truth_json_value).strip()
        or not isinstance(truth_csv_value, (str, Path))
        or not str(truth_csv_value).strip()
    ):
        raise ValidationError("Both locked truth paths are required")
    truth_json_path = Path(truth_json_value).expanduser().resolve()
    truth_csv_path = Path(truth_csv_value).expanduser().resolve()
    if truth_json_path == truth_csv_path:
        raise ValidationError("Locked truth JSON and CSV paths must differ")
    parameter_values["truth_json_path"] = str(truth_json_path)
    parameter_values["truth_csv_path"] = str(truth_csv_path)
    checkpoint = Path(checkpoint_path).expanduser().resolve()
    truth_video_sha256 = _require_sha256(truth.video.sha256, "Bound source video")
    inference_video_sha256 = _require_sha256(inference.video_sha256, "Inference video")
    inference_checkpoint_sha256 = _require_sha256(inference.checkpoint_sha256, "Inference checkpoint")
    truth_locked_sha256 = _require_sha256(truth.locked_sha256, "Locked truth JSON")
    truth_csv_sha256 = _require_sha256(truth.csv_sha256, "Locked truth CSV")
    # Source reads happen only after destination collision gate.
    checkpoint_hash = sha256_file(checkpoint)
    video_hash = sha256_file(truth.video.video_path)
    if video_hash.lower() != truth_video_sha256.lower():
        raise ValidationError("Bound source video SHA-256 mismatch")
    if inference_video_sha256.lower() != truth_video_sha256.lower():
        raise ValidationError("Inference video SHA-256 does not match locked truth")
    if inference_checkpoint_sha256.lower() != checkpoint_hash.lower():
        raise ValidationError("Inference checkpoint SHA-256 does not match checkpoint")
    truth_json_file_hash = sha256_file(truth_json_path)
    truth_csv_file_hash = sha256_file(truth_csv_path)
    if truth_csv_file_hash.lower() != truth_csv_sha256.lower():
        raise ValidationError("Locked truth CSV hash mismatch")
    from .validation_truth import verify_truth_artifacts

    try:
        loaded_truth = verify_truth_artifacts(truth_json_path, truth_csv_path, binding=truth.video)
    except ValidationError:
        raise
    except (OSError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Locked truth files are invalid") from exc
    if loaded_truth != truth:
        raise ValidationError("Locked truth object does not match truth files")
    events = _events(inference)
    _validate_predicted_events(events)
    metrics = {
        "format_version": 1,
        **report.to_dict(),
        "match_id": truth.video.match_id,
        "video": {"path": truth.video.repo_video_path, "sha256": truth_video_sha256, "metadata": truth.video.metadata.to_dict()},
        "truth": {
            "locked": truth.locked,
            "json_sha256": truth_locked_sha256,
            "csv_sha256": truth_csv_sha256,
            "json_file_sha256": truth_json_file_hash,
            "csv_file_sha256": truth_csv_file_hash,
        },
        "checkpoint": {"path": str(checkpoint), "sha256": checkpoint_hash},
        "parameters": parameter_values,
        "code_sha": code_sha,
        "created_at": created_at,
        "prediction_count": len(events),
    }
    _event_confusion_rows(metrics)
    _window_confusion_rows(metrics)
    file_payloads: dict[str, bytes] = {
        "metrics.json": _json(metrics),
        "confusion_matrix.csv": _confusion(report),
        "predicted-events.json": _json(events),
        "predicted-events.csv": _events_csv(events),
    }
    file_hashes = {name: _file_digest(payload) for name, payload in file_payloads.items()}
    manifest = {
        "format_version": 1,
        "match_id": truth.video.match_id,
        "video_path": truth.video.repo_video_path,
        "video_sha256": truth_video_sha256,
        "video_metadata": truth.video.metadata.to_dict(),
        "truth_json_sha256": truth_locked_sha256,
        "truth_csv_sha256": truth_csv_sha256,
        "truth_json_file_sha256": truth_json_file_hash,
        "truth_csv_file_sha256": truth_csv_file_hash,
        "checkpoint_path": str(checkpoint),
        "checkpoint_sha256": checkpoint_hash,
        "truth_json_path": str(truth_json_path),
        "truth_csv_path": str(truth_csv_path),
        "code_sha": code_sha,
        "parameters": parameter_values,
        "created_at": created_at,
        "prediction_count": len(events),
        "output_files": {name: value for name, value in file_hashes.items() if name in _PAYLOAD_FILES},
    }
    metrics["output_files"] = {name: value for name, value in file_hashes.items() if name in _PAYLOAD_FILES}
    manifest["metrics_content_sha256"] = _metrics_content_hash(metrics)
    manifest_core = _manifest_core(manifest)
    try:
        manifest_core_bytes = canonical_json_bytes(manifest_core)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Validation output JSON contains unsupported or non-finite values") from exc
    metrics["manifest_core_sha256"] = hashlib.sha256(manifest_core_bytes).hexdigest()
    file_payloads["metrics.json"] = _json(metrics)
    file_hashes["metrics.json"] = _file_digest(file_payloads["metrics.json"])
    manifest["metrics_file"] = file_hashes["metrics.json"]
    file_payloads["run-manifest.json"] = _json(manifest)
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging: Path | None = None
    try:
        staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=str(parent)))
        for name in _FILES:
            _write_fsync(staging / name, file_payloads[name])
        # Cross-file checks before publication.
        if len(json.loads((staging / "predicted-events.json").read_text())) != len(events):
            raise ValidationError("prediction output count mismatch")
        for name, expected in file_hashes.items():
            if sha256_file(staging / name) != expected["sha256"]:
                raise ValidationError("output hash mismatch")
        if sha256_file(truth.video.video_path).lower() != truth.video.sha256.lower():
            raise ValidationError("Bound source video SHA-256 mismatch")
        if sha256_file(checkpoint).lower() != checkpoint_hash.lower():
            raise ValidationError("Bound checkpoint SHA-256 changed during publication")
        if (
            sha256_file(truth_json_path).lower() != truth_json_file_hash.lower()
            or sha256_file(truth_csv_path).lower() != truth_csv_file_hash.lower()
        ):
            raise ValidationError("Locked truth files changed during publication")
        try:
            from ._active_learning_review_outputs import rename_directory_noreplace
            rename_directory_noreplace(staging, destination)
        except FileExistsError as exc:
            raise ValidationError(f"Output directory already exists: {destination}") from exc
        staging = None
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(f"Could not publish validation outputs: {exc}") from exc
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
    return {name: destination / name for name in _FILES}


def verify_validation_outputs(
    output_dir: str | Path,
    *,
    repo_root: str | Path,
    video_root: str | Path | None = None,
    require_source_files: bool = True,
) -> dict[str, object]:
    output = Path(output_dir).expanduser().resolve()
    if not output.is_dir():
        raise ValidationError(f"Validation output directory does not exist: {output}")
    missing = [name for name in _FILES if not (output / name).is_file()]
    if missing:
        raise ValidationError(f"Validation output is incomplete: missing {missing[0]}")
    extras = [item.name for item in output.iterdir() if item.name not in _FILES]
    if extras:
        raise ValidationError(f"Validation output contains unexpected file: {extras[0]}")

    def read_json(name: str) -> object:
        try:
            return json.loads(
                (output / name).read_text(encoding="utf-8"),
                object_pairs_hook=_strict_json_pairs,
                parse_constant=_reject_json_constant,
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
            raise ValidationError(f"Validation output JSON is invalid: {name}") from exc

    metrics = read_json("metrics.json")
    events = read_json("predicted-events.json")
    manifest = read_json("run-manifest.json")
    if not isinstance(events, list) or not isinstance(manifest, dict) or not isinstance(metrics, dict):
        raise ValidationError("Validation output schema is invalid")
    if not _is_format_version(manifest.get("format_version")) or not _is_format_version(metrics.get("format_version")):
        raise ValidationError("Validation output format version is invalid")

    # Validate the externally anchored file hashes before consuming projections.
    manifest_files = manifest.get("output_files")
    if not isinstance(manifest_files, dict) or set(manifest_files) != set(_PAYLOAD_FILES):
        raise ValidationError("Validation output manifest is missing file hashes")
    metrics_files = metrics.get("output_files")
    if metrics_files != manifest_files or not isinstance(metrics_files, dict) or set(metrics_files) != set(_PAYLOAD_FILES):
        raise ValidationError("Validation output file manifest mismatch")
    metrics_file = manifest.get("metrics_file")
    if not isinstance(metrics_file, dict) or set(metrics_file) != {"sha256", "bytes"}:
        raise ValidationError("Validation output metrics hash is missing")
    if not _is_sha256(manifest.get("metrics_content_sha256")):
        raise ValidationError("Validation output metrics content hash is invalid")
    for name in _PAYLOAD_FILES:
        expected = manifest_files.get(name)
        _validate_digest_record(expected, f"output {name}")
        try:
            actual = (output / name).read_bytes()
        except OSError as exc:
            raise ValidationError(f"Validation output is unreadable: {name}") from exc
        if hashlib.sha256(actual).hexdigest() != expected["sha256"] or len(actual) != expected["bytes"]:
            raise ValidationError(f"Validation output hash mismatch: {name}")
    _validate_digest_record(metrics_file, "metrics.json")
    try:
        metrics_bytes = (output / "metrics.json").read_bytes()
    except OSError as exc:
        raise ValidationError("Validation output is unreadable: metrics.json") from exc
    if metrics_bytes != _json(metrics):
        raise ValidationError("Validation output metrics bytes were modified")
    if hashlib.sha256(metrics_bytes).hexdigest() != metrics_file["sha256"] or len(metrics_bytes) != metrics_file["bytes"]:
        raise ValidationError("Validation output hash mismatch: metrics.json")
    if _metrics_content_hash(metrics) != manifest["metrics_content_sha256"]:
        raise ValidationError("Validation output metrics content mismatch")
    try:
        if (output / "run-manifest.json").read_bytes() != _json(manifest):
            raise ValidationError("Validation output manifest bytes were modified")
    except OSError as exc:
        raise ValidationError("Validation output is unreadable: run-manifest.json") from exc

    truth_info = metrics.get("truth")
    video_info = metrics.get("video")
    checkpoint_info = metrics.get("checkpoint")
    if not isinstance(truth_info, dict) or truth_info.get("locked") is not True:
        raise ValidationError("Validation output requires locked truth")
    if not isinstance(video_info, dict) or not isinstance(checkpoint_info, dict):
        raise ValidationError("Validation output identity is incomplete")
    if not isinstance(manifest.get("match_id"), str) or not manifest["match_id"] or any(character.isspace() for character in manifest["match_id"]):
        raise ValidationError("Validation output match binding is invalid")
    for value, label in (
        (manifest.get("video_sha256"), "video"),
        (manifest.get("checkpoint_sha256"), "checkpoint"),
        (manifest.get("truth_json_sha256"), "truth"),
        (manifest.get("truth_csv_sha256"), "truth"),
    ):
        if not _is_sha256(value):
            raise ValidationError(f"Validation output {label} hash is invalid")
    if (
        manifest.get("match_id") != metrics.get("match_id")
        or manifest.get("video_path") != video_info.get("path")
        or manifest.get("video_sha256") != video_info.get("sha256")
        or manifest.get("video_metadata") != video_info.get("metadata")
    ):
        raise ValidationError("Validation output video binding mismatch")
    if (
        manifest.get("truth_json_sha256") != truth_info.get("json_sha256")
        or manifest.get("truth_csv_sha256") != truth_info.get("csv_sha256")
        or manifest.get("truth_json_file_sha256") != truth_info.get("json_file_sha256")
        or manifest.get("truth_csv_file_sha256") != truth_info.get("csv_file_sha256")
    ):
        raise ValidationError("Validation output truth hash mismatch")
    if (
        manifest.get("code_sha") != metrics.get("code_sha")
        or manifest.get("created_at") != metrics.get("created_at")
        or manifest.get("prediction_count") != len(events)
        or manifest.get("parameters") != metrics.get("parameters")
    ):
        raise ValidationError("Validation output identity mismatch")
    try:
        manifest_core_hash = hashlib.sha256(canonical_json_bytes(_manifest_core(manifest))).hexdigest()
    except (TypeError, ValueError) as exc:
        raise ValidationError("Validation output JSON contains unsupported or non-finite values") from exc
    if metrics.get("manifest_core_sha256") != manifest_core_hash:
        raise ValidationError("Validation output manifest integrity mismatch")
    if manifest.get("checkpoint_path") != checkpoint_info.get("path") or manifest.get("checkpoint_sha256") != checkpoint_info.get("sha256"):
        raise ValidationError("Validation output checkpoint hash mismatch")
    if not isinstance(manifest.get("code_sha"), str) or not manifest.get("code_sha") or not isinstance(manifest.get("created_at"), str) or not manifest.get("created_at"):
        raise ValidationError("Validation output identity is incomplete")

    video_path_value = manifest.get("video_path")
    if not isinstance(video_path_value, str) or Path(video_path_value).is_absolute() or Path(video_path_value).as_posix() != video_path_value or ".." in Path(video_path_value).parts:
        raise ValidationError("Validation output video path is invalid")
    metadata = manifest.get("video_metadata")
    source_root = Path(video_root).expanduser().resolve() if video_root else Path(repo_root).expanduser().resolve()
    source = (source_root / video_path_value).resolve()
    try:
        source.relative_to(source_root)
    except ValueError as exc:
        raise ValidationError("Validation output video path escapes video root") from exc
    _validate_video_metadata(metadata, source)

    # Checkpoint and locked truth are always independently verified. The flag
    # only permits an absent video source for offline structural inspection.
    checkpoint_path_value = manifest.get("checkpoint_path")
    if not isinstance(checkpoint_path_value, str) or not checkpoint_path_value:
        raise ValidationError("Checkpoint path is missing")
    checkpoint_path = Path(checkpoint_path_value).expanduser().resolve()
    if not checkpoint_path.is_file() or sha256_file(checkpoint_path).lower() != str(manifest.get("checkpoint_sha256", "")).lower():
        raise ValidationError("Checkpoint SHA-256 mismatch")

    truth_json_path_value = manifest.get("truth_json_path")
    truth_csv_path_value = manifest.get("truth_csv_path")
    if not isinstance(truth_json_path_value, str) or not isinstance(truth_csv_path_value, str) or not truth_json_path_value or not truth_csv_path_value:
        raise ValidationError("Locked truth paths are required")
    repository = Path(repo_root).expanduser().resolve()
    if not repository.is_dir():
        raise ValidationError("Repository root is invalid")
    truth_json_path = Path(truth_json_path_value).expanduser().resolve()
    truth_csv_path = Path(truth_csv_path_value).expanduser().resolve()
    for path in (truth_json_path, truth_csv_path):
        try:
            path.relative_to(repository)
        except ValueError as exc:
            raise ValidationError("Locked truth path is outside repo root") from exc
        if not path.is_file():
            raise ValidationError(f"Locked truth file is unreadable: {path}")
    if not _is_sha256(manifest.get("truth_json_file_sha256")) or not _is_sha256(manifest.get("truth_csv_file_sha256")):
        raise ValidationError("Locked truth file hashes are missing")
    if truth_info.get("json_file_sha256") != manifest.get("truth_json_file_sha256") or truth_info.get("csv_file_sha256") != manifest.get("truth_csv_file_sha256"):
        raise ValidationError("Validation output truth file hash mismatch")
    if sha256_file(truth_json_path).lower() != str(manifest["truth_json_file_sha256"]).lower() or sha256_file(truth_csv_path).lower() != str(manifest["truth_csv_file_sha256"]).lower():
        raise ValidationError("Locked truth file hash mismatch")

    from .domain import VideoMetadata
    from .validation_contract import ValidationVideoBinding
    from .validation_truth import verify_truth_artifacts, verify_truth_bundle

    try:
        info = json.loads(truth_json_path.read_text(encoding="utf-8"), object_pairs_hook=_strict_json_pairs)
        if not isinstance(info, dict) or not isinstance(info.get("video"), dict) or not isinstance(info["video"].get("metadata"), dict):
            raise ValidationError("Locked truth files are invalid")
        truth_video = info["video"]
        truth_meta = truth_video["metadata"]
        truth_binding = ValidationVideoBinding(
            str(truth_video.get("match_id", "")),
            source,
            source_root,
            str(truth_video.get("video_path", "")),
            str(truth_video.get("sha256", "")),
            VideoMetadata(
                source,
                float(truth_meta["fps"]),
                int(truth_meta["frame_count"]),
                int(truth_meta["width"]),
                int(truth_meta["height"]),
                float(truth_meta["duration_seconds"]),
            ),
        )
        loaded_truth = verify_truth_artifacts(truth_json_path, truth_csv_path, binding=truth_binding)
        if (
            loaded_truth.video.match_id != manifest.get("match_id")
            or loaded_truth.video.repo_video_path != manifest.get("video_path")
            or loaded_truth.video.sha256.lower() != str(manifest.get("video_sha256", "")).lower()
            or loaded_truth.video.metadata.to_dict() != metadata
            or loaded_truth.locked_sha256 != manifest.get("truth_json_sha256")
            or loaded_truth.csv_sha256 != manifest.get("truth_csv_sha256")
        ):
            raise ValidationError("Locked truth binding does not match validation output")
        # The complete truth verifier also inspects the source.  Defer that
        # call until the source check below so `require_source_files=False`
        # can only skip an absent/unreadable video, never the truth files.
    except ValidationError:
        raise
    except (OSError, KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Locked truth files are invalid") from exc

    source_verified = False
    if source.is_file():
        try:
            actual_source_hash = sha256_file(source)
        except ValidationError as exc:
            raise ValidationError("Bound source video hash is unreadable") from exc
        if actual_source_hash.lower() != str(manifest.get("video_sha256", "")).lower():
            raise ValidationError("Bound source video SHA-256 mismatch")
        from .video import inspect_video
        try:
            observed = inspect_video(source).to_dict()
        except Exception as exc:
            if require_source_files:
                raise ValidationError("Bound source video metadata is unreadable") from exc
        else:
            _compare_video_metadata(observed, metadata)
            source_verified = True
    elif require_source_files:
        raise ValidationError(f"Bound source video is unreadable: {source}")

    if source_verified:
        verify_truth_bundle(
            truth_json_path,
            truth_csv_path,
            binding=truth_binding,
            repo_root=repo_root,
            video_root=video_root,
        )

    _validate_predicted_events(events)
    _validate_predicted_events_csv(output / "predicted-events.csv", events)
    _validate_confusion_csv(output / "confusion_matrix.csv", metrics)
    return {
        "match_id": manifest.get("match_id"),
        "prediction_count": len(events),
        "code_sha": manifest.get("code_sha"),
        "created_at": manifest.get("created_at"),
        "output_dir": str(output),
    }
