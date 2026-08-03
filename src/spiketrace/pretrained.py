from __future__ import annotations

import csv
import hashlib
import importlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .constants import ACTION_LABELS, BACKGROUND_LABEL
from .errors import SpikeTraceError
from .manifest import load_manifest, summarize_manifest
from .metrics import classification_metrics
from .ml import resolve_device
from .video import sample_video_frames

EXTERNAL_ACTION_LABELS: dict[str, str | None] = {
    "ball": None,
    "block": "block",
    "receive": "receive",
    "set": "set",
    "spike": "attack",
    "serve": "serve",
}

REVIEW_FIELDS = (
    "record_index",
    "video_path",
    "start_seconds",
    "end_seconds",
    "start_time",
    "end_time",
    "split",
    "team_side",
    "player_number",
    "expected_action",
    "predicted_action",
    "confidence",
    "correct",
    "crop",
    "evidence",
)


@dataclass(frozen=True, slots=True)
class DetectionEvidence:
    frame_index: int
    source_label: str
    action: str
    confidence: float
    box_xyxy: tuple[float, float, float, float]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WindowPrediction:
    action: str
    confidence: float
    evidence: tuple[DetectionEvidence, ...]


def normalize_external_label(label: str) -> str | None:
    return EXTERNAL_ACTION_LABELS.get(label.strip().lower())


def format_video_time(seconds: float) -> str:
    total_centiseconds = round(seconds * 100)
    hours, remainder = divmod(total_centiseconds, 60 * 60 * 100)
    minutes, remainder = divmod(remainder, 60 * 100)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"


def aggregate_action_detections(
    detections: list[DetectionEvidence], *, confidence_threshold: float
) -> WindowPrediction:
    if not 0 <= confidence_threshold <= 1:
        raise ValueError("confidence_threshold must be between 0 and 1.")

    best_by_action: dict[str, DetectionEvidence] = {}
    for detection in detections:
        if detection.confidence < confidence_threshold:
            continue
        current = best_by_action.get(detection.action)
        if current is None or detection.confidence > current.confidence:
            best_by_action[detection.action] = detection

    winner: DetectionEvidence | None = None
    for action in ACTION_LABELS:
        candidate = best_by_action.get(action)
        if candidate is not None and (
            winner is None or candidate.confidence > winner.confidence
        ):
            winner = candidate
    if winner is None:
        return WindowPrediction(
            action=BACKGROUND_LABEL,
            confidence=0.0,
            evidence=tuple(detections),
        )
    return WindowPrediction(
        action=winner.action,
        confidence=round(winner.confidence, 6),
        evidence=tuple(detections),
    )


def _to_list(value: Any) -> list[Any]:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        return value.tolist()
    return list(value)


def _class_name(names: Any, class_id: int) -> str | None:
    if isinstance(names, dict):
        value = names.get(class_id, names.get(str(class_id)))
    else:
        try:
            value = names[class_id]
        except (IndexError, KeyError, TypeError):
            return None
    return str(value) if value is not None else None


def _model_labels(names: Any) -> tuple[str, ...]:
    if isinstance(names, dict):
        try:
            keys = sorted(names, key=lambda value: int(value))
        except (TypeError, ValueError):
            keys = list(names)
        return tuple(str(names[key]) for key in keys)
    return tuple(str(value) for value in names)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_action_detections(results: Any) -> list[DetectionEvidence]:
    detections: list[DetectionEvidence] = []
    for frame_index, result in enumerate(results):
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            continue
        names = getattr(result, "names", {})
        classes = _to_list(getattr(boxes, "cls", ()))
        confidences = _to_list(getattr(boxes, "conf", ()))
        coordinates = _to_list(getattr(boxes, "xyxy", ()))
        for class_value, confidence_value, box_value in zip(
            classes, confidences, coordinates
        ):
            source_label = _class_name(names, int(class_value))
            if source_label is None:
                continue
            action = normalize_external_label(source_label)
            if action is None:
                continue
            box = tuple(float(value) for value in box_value)
            if len(box) != 4:
                continue
            detections.append(
                DetectionEvidence(
                    frame_index=frame_index,
                    source_label=source_label,
                    action=action,
                    confidence=round(float(confidence_value), 6),
                    box_xyxy=box,
                )
            )
    return detections


def _require_yolo_class() -> tuple[Any, str]:
    try:
        module = importlib.import_module("ultralytics")
    except (ImportError, ModuleNotFoundError) as exc:
        raise SpikeTraceError(
            "Pretrained YOLO support requires Ultralytics. "
            'Install it with: python -m pip install -e ".[pretrained]"'
        ) from exc
    yolo_class = getattr(module, "YOLO", None)
    if yolo_class is None:
        raise SpikeTraceError("The installed ultralytics package does not expose YOLO.")
    return yolo_class, str(getattr(module, "__version__", "unknown"))


class PretrainedActionDetector:
    def __init__(
        self,
        weights: str | Path,
        *,
        device: str = "auto",
        confidence_threshold: float = 0.25,
    ) -> None:
        if not 0 <= confidence_threshold <= 1:
            raise ValueError("confidence_threshold must be between 0 and 1.")
        self.weights = Path(weights).expanduser().resolve()
        if not self.weights.is_file():
            raise SpikeTraceError(f"Pretrained weights do not exist: {self.weights}")
        self.device = resolve_device(device)
        self.confidence_threshold = confidence_threshold
        self.weights_sha256 = _file_sha256(self.weights)
        yolo_class, self.ultralytics_version = _require_yolo_class()
        try:
            self.model = yolo_class(str(self.weights))
        except Exception as exc:
            raise SpikeTraceError(
                f"Could not load pretrained YOLO weights: {self.weights}"
            ) from exc
        self.model_labels = _model_labels(getattr(self.model, "names", ()))
        required_labels = set(EXTERNAL_ACTION_LABELS) - {"ball"}
        available_labels = {label.strip().lower() for label in self.model_labels}
        missing_labels = required_labels - available_labels
        if missing_labels:
            raise SpikeTraceError(
                "The YOLO weights are not a compatible volleyball action model. "
                "Missing labels: " + ", ".join(sorted(missing_labels))
            )

    def predict_window(self, frames: np.ndarray) -> WindowPrediction:
        if frames.ndim != 4 or frames.shape[-1] != 3 or len(frames) == 0:
            raise ValueError("Expected BGR frames with shape [T, H, W, 3].")
        try:
            results = self.model.predict(
                source=list(frames),
                conf=self.confidence_threshold,
                device=self.device,
                verbose=False,
            )
        except Exception as exc:
            raise SpikeTraceError("Pretrained YOLO inference failed.") from exc
        detections = extract_action_detections(results)
        return aggregate_action_detections(
            detections,
            confidence_threshold=self.confidence_threshold,
        )


def _review_row(
    record_index: int, record: Any, prediction: WindowPrediction
) -> dict[str, object]:
    return {
        "record_index": record_index,
        "video_path": str(record.video_path),
        "start_seconds": record.start_seconds,
        "end_seconds": record.end_seconds,
        "start_time": format_video_time(record.start_seconds),
        "end_time": format_video_time(record.end_seconds),
        "split": record.split,
        "team_side": record.team_side or "",
        "player_number": record.player_number or "",
        "expected_action": record.label,
        "predicted_action": prediction.action,
        "confidence": prediction.confidence,
        "correct": record.label == prediction.action,
        "crop": ",".join(str(value) for value in record.crop) if record.crop else "",
        "evidence": [item.to_dict() for item in prediction.evidence],
    }


def evaluate_pretrained_model(
    manifest_path: str | Path,
    weights: str | Path,
    output_dir: str | Path,
    *,
    video_root: str | Path | None = None,
    confidence_threshold: float = 0.25,
    frames_per_window: int = 6,
    device: str = "auto",
) -> dict[str, object]:
    if frames_per_window <= 0:
        raise ValueError("frames_per_window must be positive.")
    records = load_manifest(manifest_path, video_root=video_root)
    detector = PretrainedActionDetector(
        weights,
        device=device,
        confidence_threshold=confidence_threshold,
    )

    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    review_rows: list[dict[str, object]] = []
    targets: list[int] = []
    predictions: list[int] = []
    label_to_index = {label: index for index, label in enumerate(ACTION_LABELS)}

    for record_index, record in enumerate(records, start=1):
        print(
            f"Evaluating annotation {record_index}/{len(records)}: "
            f"{record.video_path.name} {record.start_seconds:.3f}s",
            file=sys.stderr,
        )
        frames = sample_video_frames(
            record.video_path,
            record.start_seconds,
            record.end_seconds,
            num_frames=frames_per_window,
            crop=record.crop,
        )
        prediction = detector.predict_window(frames)
        review_rows.append(_review_row(record_index, record, prediction))
        targets.append(label_to_index[record.label])
        predictions.append(label_to_index[prediction.action])

    metrics = classification_metrics(targets, predictions, ACTION_LABELS)
    report_path = destination / "pretrained_evaluation.json"
    review_path = destination / "pretrained_review.csv"
    report = {
        "format_version": 1,
        "model_type": "ultralytics-yolo-action-detector",
        "weights": str(detector.weights),
        "weights_sha256": detector.weights_sha256,
        "ultralytics_version": detector.ultralytics_version,
        "model_labels": list(detector.model_labels),
        "settings": {
            "device": detector.device,
            "confidence_threshold": confidence_threshold,
            "frames_per_window": frames_per_window,
            "external_label_mapping": EXTERNAL_ACTION_LABELS,
        },
        "manifest": str(Path(manifest_path).expanduser().resolve()),
        "manifest_summary": summarize_manifest(records),
        "metrics": metrics,
        "records": review_rows,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with review_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        for row in review_rows:
            csv_row = dict(row)
            csv_row["evidence"] = json.dumps(row["evidence"], ensure_ascii=False)
            writer.writerow(csv_row)

    return {
        "report": str(report_path),
        "review_csv": str(review_path),
        "metrics": metrics,
    }
