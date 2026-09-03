from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Mapping

from .errors import ValidationError
from .validation_contract import canonical_json_bytes, sha256_file
from .validation_evaluation import ValidationReport
from .validation_inference import ValidationInferenceResult
from .validation_truth import ValidationTruth

_FILES = ("metrics.json", "confusion_matrix.csv", "predicted-events.json", "predicted-events.csv", "run-manifest.json")
_EVENT_FIELDS = ("prediction_id", "segment_id", "set_index", "team_side", "start_seconds", "end_seconds", "action", "confidence", "source_window_indices")


def _write_fsync(path: Path, payload: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _json(value: object) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _events(inference: ValidationInferenceResult) -> list[dict[str, object]]:
    return [asdict(item) for item in inference.predictions]


def _confusion(report: ValidationReport) -> bytes:
    import io
    rows: list[dict[str, object]] = []
    for item in report.confusion_rows:
        rows.append({"scope": "event", "true_label": item.get("truth_label", ""), "predicted_label": item.get("predicted_label", ""), "count": 1})
    # Include the one-second matrix in a deterministic long form.
    matrix = report.window_metrics.get("confusion_matrix", {}) if isinstance(report.window_metrics, dict) else {}
    labels = matrix.get("labels", []) if isinstance(matrix, dict) else []
    values = matrix.get("values", []) if isinstance(matrix, dict) else []
    for true_index, true_label in enumerate(labels):
        for pred_index, predicted_label in enumerate(labels):
            try:
                count = int(values[true_index][pred_index])
            except (IndexError, TypeError, ValueError):
                continue
            if count:
                rows.append({"scope": "window", "true_label": true_label, "predicted_label": predicted_label, "count": count})
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=("scope", "true_label", "predicted_label", "count"), lineterminator="\n")
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
    checkpoint = Path(checkpoint_path).expanduser().resolve()
    # Source reads happen only after destination collision gate.
    checkpoint_hash = sha256_file(checkpoint)
    if inference.video_sha256.lower() != truth.video.sha256.lower():
        raise ValidationError("Inference video SHA-256 does not match locked truth")
    if inference.checkpoint_sha256.lower() != checkpoint_hash.lower():
        raise ValidationError("Inference checkpoint SHA-256 does not match checkpoint")
    events = _events(inference)
    metrics = {
        "format_version": 1,
        **report.to_dict(),
        "match_id": truth.video.match_id,
        "video": {"path": truth.video.repo_video_path, "sha256": truth.video.sha256, "metadata": truth.video.metadata.to_dict()},
        "truth": {"locked": truth.locked, "json_sha256": truth.locked_sha256, "csv_sha256": truth.csv_sha256},
        "checkpoint": {"path": str(checkpoint), "sha256": checkpoint_hash},
        "parameters": dict(parameters),
        "code_sha": code_sha,
        "created_at": created_at,
        "prediction_count": len(events),
    }
    file_payloads: dict[str, bytes] = {
        "metrics.json": _json(metrics),
        "confusion_matrix.csv": _confusion(report),
        "predicted-events.json": _json(events),
        "predicted-events.csv": _events_csv(events),
    }
    file_hashes = {name: {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)} for name, payload in file_payloads.items()}
    manifest = {
        "format_version": 1,
        "match_id": truth.video.match_id,
        "video_path": truth.video.repo_video_path,
        "video_sha256": truth.video.sha256,
        "video_metadata": truth.video.metadata.to_dict(),
        "truth_json_sha256": truth.locked_sha256,
        "truth_csv_sha256": truth.csv_sha256,
        "checkpoint_path": str(checkpoint),
        "checkpoint_sha256": checkpoint_hash,
        "truth_json_path": dict(parameters).get("truth_json_path"),
        "truth_csv_path": dict(parameters).get("truth_csv_path"),
        "code_sha": code_sha,
        "parameters": dict(parameters),
        "created_at": created_at,
        "prediction_count": len(events),
        "output_files": file_hashes,
    }
    manifest_core = dict(manifest)
    manifest_core.pop("output_files", None)
    metrics["manifest_core_sha256"] = hashlib.sha256(canonical_json_bytes(manifest_core)).hexdigest()
    metrics["output_files"] = {name: value for name, value in file_hashes.items() if name != "metrics.json"}
    file_payloads["metrics.json"] = _json(metrics)
    file_hashes["metrics.json"] = {"sha256": hashlib.sha256(file_payloads["metrics.json"]).hexdigest(), "bytes": len(file_payloads["metrics.json"])}
    manifest["output_files"] = {name: value for name, value in file_hashes.items() if name != "metrics.json"}
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
    extras = [item.name for item in output.iterdir() if item.is_file() and item.name not in _FILES]
    if extras:
        raise ValidationError(f"Validation output contains unexpected file: {extras[0]}")
    try:
        metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
        events = json.loads((output / "predicted-events.json").read_text(encoding="utf-8"))
        manifest = json.loads((output / "run-manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Validation output JSON is invalid") from exc
    if not isinstance(events, list) or not isinstance(manifest, dict) or not isinstance(metrics, dict):
        raise ValidationError("Validation output schema is invalid")
    if manifest.get("format_version") != 1 or metrics.get("format_version") != 1:
        raise ValidationError("Validation output format version is invalid")
    truth_info = metrics.get("truth")
    if not isinstance(truth_info, dict) or truth_info.get("locked") is not True:
        raise ValidationError("Validation output requires locked truth")
    video_info = metrics.get("video")
    checkpoint_info = metrics.get("checkpoint")
    if not isinstance(video_info, dict) or not isinstance(checkpoint_info, dict):
        raise ValidationError("Validation output identity is incomplete")
    if manifest.get("match_id") != metrics.get("match_id") or manifest.get("video_path") != video_info.get("path") or manifest.get("video_sha256") != video_info.get("sha256") or manifest.get("video_metadata") != video_info.get("metadata"):
        raise ValidationError("Validation output video binding mismatch")
    if manifest.get("truth_json_sha256") != truth_info.get("json_sha256") or manifest.get("truth_csv_sha256") != truth_info.get("csv_sha256"):
        raise ValidationError("Validation output truth hash mismatch")
    if manifest.get("code_sha") != metrics.get("code_sha") or manifest.get("created_at") != metrics.get("created_at") or manifest.get("prediction_count") != len(events):
        raise ValidationError("Validation output identity mismatch")
    manifest_core = dict(manifest); manifest_core.pop("output_files", None)
    if metrics.get("manifest_core_sha256") != hashlib.sha256(canonical_json_bytes(manifest_core)).hexdigest():
        raise ValidationError("Validation output manifest integrity mismatch")
    manifest_files = manifest.get("output_files", {})
    if metrics.get("output_files") != manifest_files or set(manifest_files) != set(_FILES[1:-1]):
        raise ValidationError("Validation output file manifest mismatch")
    if manifest.get("checkpoint_path") != checkpoint_info.get("path") or manifest.get("checkpoint_sha256") != checkpoint_info.get("sha256") or manifest.get("parameters") != metrics.get("parameters"):
        raise ValidationError("Validation output checkpoint hash mismatch")
    if manifest.get("code_sha") is None or manifest.get("created_at") is None:
        raise ValidationError("Validation output identity is incomplete")
    video_path_value = manifest.get("video_path")
    if not isinstance(video_path_value, str) or Path(video_path_value).is_absolute() or Path(video_path_value).as_posix() != video_path_value or ".." in Path(video_path_value).parts:
        raise ValidationError("Validation output video path is invalid")
    files = manifest.get("output_files")
    if not isinstance(files, dict) or set(files) != set(_FILES[1:-1]):
        raise ValidationError("Validation output manifest is missing file hashes")
    for name in _FILES[1:-1]:
        expected = files.get(name)
        if not isinstance(expected, dict):
            raise ValidationError(f"Validation output manifest is missing {name}")
        actual = (output / name).read_bytes()
        if hashlib.sha256(actual).hexdigest() != expected.get("sha256") or len(actual) != expected.get("bytes"):
            raise ValidationError(f"Validation output hash mismatch: {name}")
    try:
        with (output / "predicted-events.csv").open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(_EVENT_FIELDS):
                raise ValidationError("Predicted events CSV schema is invalid")
            csv_rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValidationError("Predicted events CSV is invalid") from exc
    if len(csv_rows) != len(events) or manifest.get("prediction_count") != len(events):
        raise ValidationError("Prediction output count mismatch")
    for event, row in zip(events, csv_rows):
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
    try:
        with (output / "confusion_matrix.csv").open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != ["scope", "true_label", "predicted_label", "count"]:
                raise ValidationError("Confusion matrix schema is invalid")
            confusion_count = {"event": 0, "window": 0}
            for row in reader:
                if row.get("scope") not in {"event", "window"} or not row.get("true_label") or not row.get("predicted_label"):
                    raise ValidationError("Confusion matrix row is invalid")
                count = int(row.get("count", ""))
                if count < 0:
                    raise ValidationError("Confusion matrix row is invalid")
                confusion_count[row["scope"]] += count
            expected_window = metrics.get("window_metrics", {}).get("samples") if isinstance(metrics.get("window_metrics"), dict) else None
            if expected_window is not None and confusion_count["window"] != int(expected_window):
                raise ValidationError("Confusion matrix count mismatch")
            expected_event = len(metrics.get("confusion_rows", [])) if isinstance(metrics.get("confusion_rows"), list) else None
            if expected_event is not None and confusion_count["event"] != int(expected_event):
                raise ValidationError("Confusion matrix count mismatch")
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError("Confusion matrix is invalid") from exc
    if manifest.get("match_id") != metrics.get("match_id"):
        raise ValidationError("Validation output match binding mismatch")
    root = Path(video_root).expanduser().resolve() if video_root else Path(repo_root).expanduser().resolve()
    source = (root / manifest.get("video_path", "")).resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise ValidationError("Validation output video path escapes video root") from exc
    if require_source_files:
        if not source.is_file():
            raise ValidationError(f"Bound source video is unreadable: {source}")
        if sha256_file(source).lower() != str(manifest.get("video_sha256", "")).lower():
            raise ValidationError("Bound source video SHA-256 mismatch")
        from .video import inspect_video
        try:
            observed = inspect_video(source).to_dict()
        except Exception as exc:
            raise ValidationError("Bound source video metadata is unreadable") from exc
        expected_meta = manifest.get("video_metadata")
        if isinstance(expected_meta, dict):
            if expected_meta.get("path") != str(source):
                raise ValidationError("Bound source video metadata path mismatch")
            for field in ("fps", "frame_count", "width", "height", "duration_seconds"):
                if field in expected_meta and abs(float(observed[field]) - float(expected_meta[field])) > 1e-6:
                    raise ValidationError("Bound source video metadata mismatch")
        checkpoint_path = Path(str(manifest.get("checkpoint_path", ""))).expanduser().resolve()
        if not checkpoint_path.is_file() or sha256_file(checkpoint_path).lower() != str(manifest.get("checkpoint_sha256", "")).lower():
            raise ValidationError("Checkpoint SHA-256 mismatch")
        truth_json_path = manifest.get("truth_json_path")
        truth_csv_path = manifest.get("truth_csv_path")
        if not isinstance(truth_json_path, str) or not isinstance(truth_csv_path, str):
            raise ValidationError("Locked truth paths are required")
        try:
            repo_resolved = Path(repo_root).expanduser().resolve()
            for item in (truth_json_path, truth_csv_path):
                Path(item).expanduser().resolve().relative_to(repo_resolved)
        except ValueError as exc:
            raise ValidationError("Locked truth path is outside repo root") from exc
        if truth_json_path and truth_csv_path:
            from .validation_contract import ValidationVideoBinding
            from .domain import VideoMetadata
            from .validation_truth import verify_truth_bundle
            try:
                truth_data = json.loads(Path(truth_json_path).read_text(encoding="utf-8"))
                info = truth_data["video"]; meta = info["metadata"]
                binding = ValidationVideoBinding(info["match_id"], source, root, info["video_path"], info["sha256"], VideoMetadata(source, float(meta["fps"]), int(meta["frame_count"]), int(meta["width"]), int(meta["height"]), float(meta["duration_seconds"])))
                verify_truth_bundle(truth_json_path, truth_csv_path, binding=binding, repo_root=repo_root, video_root=video_root)
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValidationError("Locked truth files are invalid") from exc
    return {"match_id": manifest.get("match_id"), "prediction_count": len(events), "code_sha": manifest.get("code_sha"), "created_at": manifest.get("created_at"), "output_dir": str(output)}
