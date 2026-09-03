from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from typing import Sequence

from .constants import ACTION_LABELS
from .errors import ValidationError
from .metrics import classification_metrics
from .validation_inference import ValidationInferenceResult, ValidationPrediction
from .validation_rallies import RallySegment
from .validation_truth import GroundTruthAction, ValidationTruth


@dataclass(frozen=True, slots=True)
class EventMatch:
    prediction_id: str
    truth_ref: str
    predicted_label: str
    truth_label: str
    center_error_seconds: float
    confidence: float


@dataclass(frozen=True, slots=True)
class EventMatchResult:
    matches: tuple[EventMatch, ...]
    false_positive_ids: tuple[str, ...]
    false_negative_refs: tuple[str, ...]
    diagnostic_confusion: tuple[EventMatch, ...]


@dataclass(frozen=True, slots=True)
class WindowSeries:
    starts: tuple[float, ...]
    targets: tuple[str, ...]
    predictions: tuple[str, ...]
    segment_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValidationReport:
    event_metrics: dict[str, object]
    window_metrics: dict[str, object]
    coverage_metrics: dict[str, object]
    visibility_metrics: dict[str, object]
    confusion_rows: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "event_metrics": self.event_metrics,
            "window_metrics": self.window_metrics,
            "coverage_metrics": self.coverage_metrics,
            "visibility_metrics": self.visibility_metrics,
            "confusion_rows": list(self.confusion_rows),
        }


def _center(item: object) -> float:
    return (float(item.start_seconds) + float(item.end_seconds)) / 2.0


def _project(label: str) -> str:
    return "background" if label == "free_ball" else label


def _valid_number(value: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite and non-negative")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return number


def _match_group(predictions: Sequence[ValidationPrediction], truths: Sequence[GroundTruthAction], tolerance: float, *, ignore_labels: bool = False, enforce_rally: bool = True) -> tuple[tuple[int, int], ...]:
    preds = sorted(enumerate(predictions), key=lambda item: (_center(item[1]), item[1].prediction_id))
    refs = sorted(enumerate(truths), key=lambda item: (_center(item[1]), item[1].action_ref))

    def score(pairs: tuple[tuple[int, int], ...]) -> tuple[int, int, int, tuple[tuple[str, str], ...]]:
        error = sum(round(abs(_center(predictions[p]) - _center(truths[t])) * 1_000_000) for p, t in pairs)
        confidence = sum(round(float(predictions[p].confidence) * 1_000_000) for p, _ in pairs)
        signature = tuple(sorted((predictions[p].prediction_id, truths[t].action_ref) for p, t in pairs))
        return (len(pairs), -error, confidence, tuple((-ord(a[0]) if a else 0, -ord(b[0]) if b else 0) for a, b in signature))

    def better(left: tuple[tuple[int, int], ...] | None, right: tuple[tuple[int, int], ...] | None) -> tuple[tuple[int, int], ...] | None:
        if left is None:
            return right
        if right is None:
            return left
        ls = score(left); rs = score(right)
        if ls[:3] != rs[:3]:
            return left if ls[:3] > rs[:3] else right
        # Stable IDs resolve exact score ties; lower IDs win.
        lsig = tuple(sorted((predictions[p].prediction_id, truths[t].action_ref) for p, t in left))
        rsig = tuple(sorted((predictions[p].prediction_id, truths[t].action_ref) for p, t in right))
        return left if lsig <= rsig else right

    grid: list[list[tuple[tuple[int, int], ...] | None]] = [[() for _ in range(len(refs) + 1)] for _ in range(len(preds) + 1)]
    for i in range(1, len(preds) + 1):
        for j in range(1, len(refs) + 1):
            best = better(grid[i - 1][j], grid[i][j - 1])
            pred = preds[i - 1][1]; truth = refs[j - 1][1]
            edge = abs(_center(pred) - _center(truth)) <= tolerance and (ignore_labels or _project(pred.action) == truth.projected_label)
            if enforce_rally:
                edge = edge and pred.segment_id == truth.rally_id
            if edge:
                candidate = grid[i - 1][j - 1] + ((preds[i - 1][0], refs[j - 1][0]),)
                best = better(best, candidate)
            grid[i][j] = best
    return grid[-1][-1] or ()


def match_events(predictions: Sequence[ValidationPrediction], truth_actions: Sequence[GroundTruthAction], *, tolerance_seconds: float = 1.0) -> EventMatchResult:
    tolerance = _valid_number(tolerance_seconds, "tolerance_seconds")
    visible = tuple(action for action in truth_actions if action.visibility == "visible")
    matches: list[EventMatch] = []
    matched_pred: set[int] = set(); matched_truth: set[int] = set()
    labels = sorted({_project(action.projected_label) for action in visible} | {_project(pred.action) for pred in predictions})
    enforce_rally = True
    for label in labels:
        pidx = [i for i, pred in enumerate(predictions) if _project(pred.action) == label]
        tidx = [i for i, action in enumerate(visible) if _project(action.projected_label) == label]
        pairs = _match_group([predictions[i] for i in pidx], [visible[i] for i in tidx], tolerance, enforce_rally=enforce_rally)
        for pi, ti in pairs:
            original_p, original_t = pidx[pi], tidx[ti]
            matched_pred.add(original_p); matched_truth.add(original_t)
            pred, action = predictions[original_p], visible[original_t]
            matches.append(EventMatch(pred.prediction_id, action.action_ref, pred.action, action.projected_label, abs(_center(pred) - _center(action)), float(pred.confidence)))
    diagnostics: list[EventMatch] = []
    remaining_p = [predictions[i] for i in range(len(predictions)) if i not in matched_pred]
    remaining_t = [visible[i] for i in range(len(visible)) if i not in matched_truth]
    for pi, ti in _match_group(remaining_p, remaining_t, tolerance, ignore_labels=True, enforce_rally=enforce_rally):
        pred, action = remaining_p[pi], remaining_t[ti]
        diagnostics.append(EventMatch(pred.prediction_id, action.action_ref, pred.action, action.projected_label, abs(_center(pred) - _center(action)), float(pred.confidence)))
    matches.sort(key=lambda item: (item.truth_ref, item.prediction_id))
    diagnostics.sort(key=lambda item: (item.truth_ref, item.prediction_id))
    return EventMatchResult(tuple(matches), tuple(predictions[i].prediction_id for i in range(len(predictions)) if i not in matched_pred), tuple(visible[i].action_ref for i in range(len(visible)) if i not in matched_truth), tuple(diagnostics))


def _in_visibility(center: float, truth: ValidationTruth, rally_id: str | None = None) -> bool:
    return any(interval.start_seconds <= center < interval.end_seconds and (interval.rally_id is None or interval.rally_id == rally_id) for interval in truth.visibility_events)


def _overlaps_visibility(start: float, end: float, truth: ValidationTruth, rally_id: str | None = None) -> bool:
    return any(start < interval.end_seconds and end > interval.start_seconds and (interval.rally_id is None or interval.rally_id == rally_id) for interval in truth.visibility_events)


def expand_one_second_windows(truth: ValidationTruth, inference: ValidationInferenceResult) -> WindowSeries:
    if not truth.locked:
        raise ValidationError("validation truth must be locked")
    starts: list[float] = []; targets: list[str] = []; outputs: list[str] = []; segments: list[str] = []
    visible = tuple(action for action in truth.actions if action.visibility == "visible")
    for segment in truth.coverage:
        if segment.status != "rally" or not segment.coverage_confirmed:
            continue
        first = math.floor(segment.start_seconds)
        last = math.ceil(segment.end_seconds)
        for index in range(first, last):
            center = index + 0.5
            if center < segment.start_seconds or center >= segment.end_seconds or _in_visibility(center, truth, segment.rally_id):
                continue
            candidates = [action for action in visible if action.rally_id == segment.rally_id and index <= _center(action) < index + 1]
            if candidates:
                target = min(candidates, key=lambda action: (abs(_center(action) - center), action.action_ref))
                target_label = target.projected_label
            elif segment.no_c2_action is True:
                target_label = "background"
            else:
                continue
            overlapping = [pred for pred in inference.predictions if pred.segment_id == segment.segment_id and pred.start_seconds < index + 1 and pred.end_seconds > index]
            chosen = min(overlapping, key=lambda pred: (-float(pred.confidence), pred.prediction_id)) if overlapping else None
            starts.append(float(index)); targets.append(_project(target_label)); outputs.append(_project(chosen.action) if chosen else "background"); segments.append(segment.segment_id)
    order = sorted(range(len(starts)), key=lambda i: (starts[i], segments[i]))
    return WindowSeries(tuple(starts[i] for i in order), tuple(targets[i] for i in order), tuple(outputs[i] for i in order), tuple(segments[i] for i in order))


def _event_metrics(predictions: Sequence[ValidationPrediction], actions: Sequence[GroundTruthAction], result: EventMatchResult, coverage: Sequence[RallySegment]) -> dict[str, object]:
    active = ACTION_LABELS[1:]
    support = {label: sum(_project(a.projected_label) == label for a in actions) for label in active}
    matched = {label: sum(m.truth_label == label for m in result.matches) for label in active}
    fp = {label: sum(_project(p.action) == label for p in predictions if p.prediction_id in result.false_positive_ids) for label in active}
    per_class: dict[str, dict[str, object]] = {}
    for label in active:
        precision = matched[label] / (matched[label] + fp[label]) if matched[label] + fp[label] else 0.0
        recall = matched[label] / support[label] if support[label] else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"precision": round(precision, 6), "recall": round(recall, 6), "f1": round(f1, 6), "support": support[label]}
    rally_seconds = sum(max(0.0, s.end_seconds - s.start_seconds) for s in coverage if s.status == "rally" and s.coverage_confirmed)
    by_set: dict[str, dict[str, int]] = {}
    seen_set_rallies: set[str] = set()
    for segment in coverage:
        if segment.status == "rally" and segment.coverage_confirmed:
            if segment.rally_id in seen_set_rallies:
                continue
            seen_set_rallies.add(segment.rally_id)
            row = by_set.setdefault(str(segment.set_index), {"matched": 0, "false_positive": 0, "false_negative": 0})
            refs = {a.action_ref for a in actions if a.rally_id == segment.rally_id}; row["matched"] += sum(m.truth_ref in refs for m in result.matches); row["false_negative"] += sum(ref in result.false_negative_refs for ref in refs)
    for label in result.false_positive_ids:
        pred = next((p for p in predictions if p.prediction_id == label), None)
        if pred is not None: by_set.setdefault(str(pred.set_index), {"matched": 0, "false_positive": 0, "false_negative": 0})["false_positive"] += 1
    def side_at(center: float, rally_id: str | None = None) -> str | None:
        candidates = [s for s in coverage if s.status == "rally" and s.coverage_confirmed and (rally_id is None or s.rally_id == rally_id) and s.start_seconds <= center < s.end_seconds and s.team_side]
        return candidates[0].team_side if candidates else None

    action_by_ref = {a.action_ref: a for a in actions}
    prediction_by_id = {p.prediction_id: p for p in predictions}
    per_side: dict[str, dict[str, int]] = {}
    for match in result.matches:
        action = action_by_ref.get(match.truth_ref)
        side = side_at(_center(action), action.rally_id) if action else None
        if side:
            per_side.setdefault(side, {"matched": 0, "false_positive": 0, "false_negative": 0})["matched"] += 1
    for ref in result.false_negative_refs:
        action = action_by_ref.get(ref)
        side = side_at(_center(action), action.rally_id) if action else None
        if side:
            per_side.setdefault(side, {"matched": 0, "false_positive": 0, "false_negative": 0})["false_negative"] += 1
    for prediction_id in result.false_positive_ids:
        pred = prediction_by_id.get(prediction_id)
        side = side_at(_center(pred), None) if pred else None
        if side:
            per_side.setdefault(side, {"matched": 0, "false_positive": 0, "false_negative": 0})["false_positive"] += 1
    return {"classes": list(active), "per_class": per_class, "support": support, "macro_f1": round(sum(v["f1"] for v in per_class.values()) / len(active), 6), "matched_count": len(result.matches), "false_positive_count": len(result.false_positive_ids), "false_negative_count": len(result.false_negative_refs), "false_positives_per_minute": round(len(result.false_positive_ids) / rally_seconds * 60, 6) if rally_seconds else 0.0, "per_set": by_set, "per_side": per_side, "rally_seconds": rally_seconds}


def evaluate_validation(truth: ValidationTruth, inference: ValidationInferenceResult) -> ValidationReport:
    if not truth.locked:
        raise ValidationError("validation truth must be locked")
    confirmed = tuple(s for s in truth.coverage if s.status == "rally" and s.coverage_confirmed)
    covered_ids = {s.segment_id for s in confirmed}; rally_ids = {s.rally_id for s in confirmed}
    visible_actions = tuple(a for a in truth.actions if a.visibility == "visible" and a.rally_id in rally_ids)
    segment_rallies = {s.segment_id: s.rally_id for s in confirmed}
    settings_segments = inference.settings.get("segments", ()) if isinstance(inference.settings, dict) else ()
    settings_map = {item.get("segment_id"): item for item in settings_segments if isinstance(item, dict) and isinstance(item.get("segment_id"), str)}

    def context(prediction: ValidationPrediction) -> tuple[str | None, str | None]:
        known_ids = set(segment_rallies) | set(settings_map) | {s.segment_id for s in truth.coverage}
        if prediction.segment_id not in known_ids:
            return None, None
        rally_id = segment_rallies.get(prediction.segment_id)
        status = "rally" if rally_id is not None else None
        setting = settings_map.get(prediction.segment_id)
        if setting is not None:
            status = str(setting.get("status"))
        candidates = [s for s in truth.coverage if s.start_seconds <= _center(prediction) < s.end_seconds and s.status in {"rally", "non_rally", "unusable"}]
        if candidates:
            candidate = min(candidates, key=lambda s: (abs(_center(prediction) - _center(s)), s.segment_id))
            if status is None or prediction.segment_id not in segment_rallies:
                status = candidate.status
            if rally_id is None and candidate.status == "rally":
                rally_id = candidate.rally_id
        return rally_id, status

    prediction_context = {p.prediction_id: context(p) for p in inference.predictions}
    predictions = tuple(p for p in inference.predictions if prediction_context[p.prediction_id][0] in rally_ids and prediction_context[p.prediction_id][1] == "rally" and not _overlaps_visibility(p.start_seconds, p.end_seconds, truth, prediction_context[p.prediction_id][0]))
    grouped_matches: list[EventMatch] = []; grouped_diagnostics: list[EventMatch] = []; grouped_fp: list[str] = []; grouped_fn: list[str] = []
    for rally_id in sorted(rally_ids):
        rally_actions = tuple(a for a in visible_actions if a.rally_id == rally_id)
        rally_predictions = tuple(replace(p, segment_id=rally_id) for p in predictions if prediction_context[p.prediction_id][0] == rally_id)
        result = match_events(rally_predictions, rally_actions)
        grouped_matches.extend(result.matches); grouped_diagnostics.extend(result.diagnostic_confusion); grouped_fp.extend(result.false_positive_ids); grouped_fn.extend(result.false_negative_refs)
    event_result = EventMatchResult(tuple(grouped_matches), tuple(grouped_fp), tuple(grouped_fn), tuple(grouped_diagnostics))
    series = expand_one_second_windows(truth, inference)
    labels = {label: index for index, label in enumerate(ACTION_LABELS)}
    window_metrics = classification_metrics([labels[t] for t in series.targets], [labels.get(p, 0) for p in series.predictions], ACTION_LABELS)
    rally_groups = {s.rally_id: [part for part in truth.coverage if part.rally_id == s.rally_id] for s in truth.coverage if s.status == "rally"}
    confirmed_groups = {rally_id: parts for rally_id, parts in rally_groups.items() if all(part.coverage_confirmed for part in parts)}
    coverage_metrics = {"total_rallies": len(rally_groups), "confirmed_rallies": len(confirmed_groups), "complete_action_check_count": sum(all(part.all_c2_actions_checked for part in parts) for parts in confirmed_groups.values()), "no_action_count": sum(all(part.no_c2_action is True for part in parts) for parts in confirmed_groups.values()), "coverage_seconds": sum(max(0.0, s.end_seconds - s.start_seconds) for s in confirmed)}
    visibility_metrics: dict[str, object] = {}
    for kind in ("fully_occluded", "off_camera", "unresolved"):
        intervals = tuple(v for v in truth.visibility_events if v.kind == kind)
        affected: set[str] = set()
        for interval in intervals:
            if interval.rally_id is not None:
                affected.add(interval.rally_id)
            else:
                affected.update(s.rally_id for s in confirmed if s.start_seconds < interval.end_seconds and s.end_seconds > interval.start_seconds)
        visibility_metrics[kind] = {"interval_count": len(intervals), "seconds": sum(max(0.0, v.end_seconds - v.start_seconds) for v in intervals), "affected_rally_count": len(affected)}
    event = _event_metrics(predictions, visible_actions, event_result, confirmed)
    event["non_rally_prediction_count"] = sum(prediction_context[p.prediction_id][1] == "non_rally" for p in inference.predictions)
    rows = tuple(asdict(item) for item in event_result.diagnostic_confusion)
    return ValidationReport(event, window_metrics, coverage_metrics, visibility_metrics, rows)
