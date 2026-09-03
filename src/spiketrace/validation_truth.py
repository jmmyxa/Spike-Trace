from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Mapping

from .constants import ACTION_LABELS
from .errors import ValidationError
from .validation_contract import ValidationVideoBinding, canonical_json_bytes, sha256_file, write_new_bytes
from .validation_rallies import RallySegment

CSV_HEADER = "video_path,start_seconds,end_seconds,label,team_side,player_number,crop_x1,crop_y1,crop_x2,crop_y2,split,match_id,rally_id"
_CSV_FIELDS = tuple(CSV_HEADER.split(","))
_ROOT_FIELDS = {"format_version", "state", "video", "set_intervals", "side_intervals", "coverage", "actions", "visibility_events", "annotation", "integrity"}
_VIDEO_FIELDS = {"match_id", "video_path", "sha256", "metadata"}
_ACTION_FIELDS = {"action_ref", "match_id", "rally_id", "label", "projected_label", "start_seconds", "end_seconds", "visibility", "evidence", "player_number", "notes"}
_VIS_FIELDS = {"event_ref", "rally_id", "kind", "start_seconds", "end_seconds", "notes"}
_COVERAGE_FIELDS = {f.name for f in RallySegment.__dataclass_fields__.values()}
_SET_FIELDS = {"set_index", "start_seconds", "end_seconds"}
_SIDE_FIELDS = {"segment_id", "set_index", "start_seconds", "end_seconds", "team_side", "crop"}


@dataclass(frozen=True, slots=True)
class GroundTruthAction:
    action_ref: str
    match_id: str
    rally_id: str
    label: str
    projected_label: str
    start_seconds: float
    end_seconds: float
    visibility: Literal["visible", "fully_occluded", "off_camera", "unresolved"]
    evidence: str
    player_number: str | None
    notes: str


@dataclass(frozen=True, slots=True)
class VisibilityInterval:
    event_ref: str
    rally_id: str | None
    kind: Literal["fully_occluded", "off_camera", "unresolved"]
    start_seconds: float
    end_seconds: float
    notes: str


@dataclass(frozen=True, slots=True)
class ValidationTruth:
    video: ValidationVideoBinding
    set_intervals: tuple[Mapping[str, object], ...]
    side_intervals: tuple[Mapping[str, object], ...]
    coverage: tuple[RallySegment, ...]
    actions: tuple[GroundTruthAction, ...]
    visibility_events: tuple[VisibilityInterval, ...]
    annotation_version: str
    locked: bool
    locked_sha256: str | None
    csv_sha256: str | None


def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _read_json(path: str | Path) -> dict[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_strict_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError(f"Invalid truth JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValidationError("Truth root must be an object")
    return value


def _check_fields(value: Mapping[str, object], allowed: set[str], name: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValidationError(f"Unknown {name} field: {sorted(unknown)[0]}")


def _binding_dict(binding: ValidationVideoBinding) -> dict[str, object]:
    return {"match_id": binding.match_id, "video_path": binding.repo_video_path, "sha256": binding.sha256, "metadata": binding.metadata.to_dict()}


def _check_binding(video: Mapping[str, object], binding: ValidationVideoBinding) -> None:
    _check_fields(video, _VIDEO_FIELDS, "video")
    if video.get("match_id") != binding.match_id or video.get("video_path") != binding.repo_video_path or str(video.get("sha256", "")).lower() != binding.sha256.lower():
        raise ValidationError("Truth binding mismatch")
    metadata = video.get("metadata")
    if not isinstance(metadata, dict):
        raise ValidationError("Truth video metadata is invalid")
    _check_fields(metadata, {"path", "fps", "frame_count", "width", "height", "duration_seconds"}, "metadata")
    for key, expected in binding.metadata.to_dict().items():
        if key == "path":
            continue
        try:
            if abs(float(metadata[key]) - float(expected)) > 1e-6:
                raise ValidationError("Truth video metadata mismatch")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("Truth video metadata is invalid") from exc


def _resolve_bound_source(binding: ValidationVideoBinding, *, repo_root: str | Path, video_root: str | Path | None = None) -> Path:
    repository = Path(repo_root).expanduser().resolve()
    if not repository.is_dir():
        raise ValidationError("Repository root is invalid")
    relative = Path(binding.repo_video_path)
    if relative.is_absolute() or relative.as_posix() != binding.repo_video_path or ".." in relative.parts:
        raise ValidationError("Binding video_path must be relative POSIX")
    root = Path(video_root).expanduser().resolve() if video_root is not None else binding.video_root.resolve()
    source = (root / relative).resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise ValidationError("Binding video_path escapes video root") from exc
    if source != binding.video_path.resolve() or not source.is_file():
        raise ValidationError("Bound source video does not match explicit video_root")
    if sha256_file(source).lower() != binding.sha256.lower():
        raise ValidationError("Bound source video SHA-256 mismatch")
    return source


def _coverage_from_payload(items: object) -> tuple[RallySegment, ...]:
    if not isinstance(items, list):
        raise ValidationError("coverage must be a list")
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValidationError("coverage record is invalid")
        _check_fields(item, _COVERAGE_FIELDS, "coverage")
        try:
            crop = item.get("crop")
            if crop is not None:
                if not isinstance(crop, (list, tuple)) or len(crop) != 4 or any(isinstance(v, bool) or not isinstance(v, int) for v in crop):
                    raise ValidationError("coverage crop is invalid")
                crop = tuple(crop)
            if item.get("status") not in {"pending", "rally", "non_rally", "unusable"}:
                raise ValidationError("coverage status is invalid")
            if not isinstance(item.get("segment_id"), str) or not isinstance(item.get("rally_id"), str):
                raise ValidationError("coverage identifiers are invalid")
            if item.get("boundary_source") not in {"motion", "manual"} or item.get("team_side") not in {None, "near", "far"}:
                raise ValidationError("coverage enum is invalid")
            if item.get("set_index") is not None and (isinstance(item.get("set_index"), bool) or not isinstance(item.get("set_index"), int)):
                raise ValidationError("coverage set_index is invalid")
            for bound in ("start_seconds", "end_seconds", "buffer_before_seconds", "buffer_after_seconds"):
                _num(item.get(bound), f"coverage {bound}")
            for flag in ("coverage_confirmed", "all_c2_actions_checked"):
                if not isinstance(item.get(flag), bool):
                    raise ValidationError("coverage confirmation is invalid")
            result.append(RallySegment(**{**item, "crop": crop}))
        except (TypeError, ValueError) as exc:
            raise ValidationError("coverage record is invalid") from exc
    return tuple(result)


def _num(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValidationError(f"{label} is invalid")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} is invalid") from exc
    if not math.isfinite(number):
        raise ValidationError(f"{label} is invalid")
    return number


def _validate_records(data: Mapping[str, object], binding: ValidationVideoBinding, *, locked: bool) -> ValidationTruth:
    _check_fields(data, _ROOT_FIELDS, "root")
    if data.get("format_version") != 1 or data.get("state") != ("locked" if locked else "draft"):
        raise ValidationError("Truth state or format is invalid")
    video = data.get("video")
    if not isinstance(video, dict):
        raise ValidationError("Truth video is invalid")
    _check_binding(video, binding)
    coverage = _coverage_from_payload(data.get("coverage"))
    if any(segment.status == "pending" for segment in coverage):
        raise ValidationError("rally coverage is pending")
    set_intervals = data.get("set_intervals"); side_intervals = data.get("side_intervals")
    if not isinstance(set_intervals, list) or not isinstance(side_intervals, list):
        raise ValidationError("Intervals must be lists")
    for interval in set_intervals:
        if not isinstance(interval, dict): raise ValidationError("Interval record is invalid")
        _check_fields(interval, _SET_FIELDS, "set interval")
        set_index = interval.get("set_index")
        if isinstance(set_index, bool) or not isinstance(set_index, int): raise ValidationError("set interval set_index is invalid")
        start, end = _num(interval.get("start_seconds"), "interval start"), _num(interval.get("end_seconds"), "interval end")
        if end <= start or start < 0 or end > binding.metadata.duration_seconds: raise ValidationError("Interval bounds are invalid")
    for interval in side_intervals:
        if not isinstance(interval, dict):
            raise ValidationError("Interval record is invalid")
        _check_fields(interval, _SIDE_FIELDS, "side interval")
        if "start_seconds" not in interval or "end_seconds" not in interval:
            raise ValidationError("Interval record is missing bounds")
        start, end = _num(interval.get("start_seconds"), "interval start"), _num(interval.get("end_seconds"), "interval end")
        if end <= start or start < 0 or end > binding.metadata.duration_seconds:
            raise ValidationError("Interval bounds are invalid")
        if interval.get("team_side") not in {"near", "far"}: raise ValidationError("side team_side is invalid")
        set_index = interval.get("set_index")
        if isinstance(set_index, bool) or not isinstance(set_index, int): raise ValidationError("side set_index is invalid")
        crop = interval.get("crop")
        if not isinstance(crop, (list, tuple)) or len(crop) != 4 or any(isinstance(v, bool) or not isinstance(v, int) for v in crop): raise ValidationError("side crop is invalid")
        if crop[0] < 0 or crop[1] < 0 or crop[2] <= crop[0] or crop[3] <= crop[1] or crop[2] > binding.metadata.width or crop[3] > binding.metadata.height: raise ValidationError("side crop geometry is invalid")
    actions_raw = data.get("actions")
    if not isinstance(actions_raw, list):
        raise ValidationError("actions must be a list")
    rally_segments: dict[str, list[RallySegment]] = {}
    for segment in coverage:
        if segment.status == "rally":
            rally_segments.setdefault(segment.rally_id, []).append(segment)
    actions: list[GroundTruthAction] = []; refs: set[str] = set()
    by_rally: dict[str, list[GroundTruthAction]] = {}
    for raw in actions_raw:
        if not isinstance(raw, dict):
            raise ValidationError("action record is invalid")
        _check_fields(raw, _ACTION_FIELDS, "action")
        required_action = {"action_ref", "rally_id", "label", "start_seconds", "end_seconds", "visibility", "evidence", "notes"}
        if locked:
            required_action |= {"match_id", "projected_label", "player_number"}
        if not required_action.issubset(raw):
            raise ValidationError("action record is missing fields")
        if set(raw) - {"action_ref", "rally_id", "label", "projected_label", "start_seconds", "end_seconds", "visibility", "evidence", "player_number", "notes", "match_id"}:
            raise ValidationError("action record is invalid")
        ref = raw.get("action_ref")
        if not isinstance(ref, str) or not ref or ref in refs:
            raise ValidationError("duplicate or invalid action_ref")
        refs.add(ref)
        rally_id = raw.get("rally_id"); rally_parts = rally_segments.get(rally_id, [])
        if not isinstance(rally_id, str) or not rally_parts or raw.get("match_id", binding.match_id) != binding.match_id:
            raise ValidationError("action rally or match is invalid")
        rally_start = min(s.start_seconds for s in rally_parts)
        rally_end = max(s.end_seconds for s in rally_parts)
        label = raw.get("label")
        if label not in set(ACTION_LABELS) | {"free_ball"}:
            raise ValidationError("invalid action label")
        visibility = raw.get("visibility", "visible")
        if visibility not in {"visible", "fully_occluded", "off_camera", "unresolved"}:
            raise ValidationError("invalid action visibility")
        start, end = _num(raw.get("start_seconds"), "start_seconds"), _num(raw.get("end_seconds"), "end_seconds")
        if start != math.floor(start) or end != math.floor(end) or end <= start or start < rally_start or end > rally_end:
            raise ValidationError("action times must be whole seconds within rally")
        player = raw.get("player_number")
        if player not in (None, ""):
            raise ValidationError("player_number must be empty")
        if not isinstance(raw.get("evidence"), str) or not isinstance(raw.get("notes"), str):
            raise ValidationError("action evidence and notes must be strings")
        action = GroundTruthAction(ref, binding.match_id, rally_id, str(label), "background" if label == "free_ball" else str(label), start, end, visibility, raw["evidence"], None, raw["notes"])
        if "projected_label" in raw and raw.get("projected_label") != action.projected_label:
            raise ValidationError("projected label mismatch")
        actions.append(action); by_rally.setdefault(rally_id, []).append(action)
    for rally_id, rally_parts in rally_segments.items():
        has = bool(by_rally.get(rally_id))
        flags = {part.no_c2_action for part in rally_parts}
        if len(flags) != 1 or any(not part.coverage_confirmed or not part.all_c2_actions_checked for part in rally_parts):
            raise ValidationError("rally coverage is pending or inconsistent")
        flag = next(iter(flags))
        if flag is None or bool(flag) != (not has):
            raise ValidationError("no_c2_action does not match actions")
        for action in by_rally.get(rally_id, ()):
            if not any(part.start_seconds <= action.start_seconds and action.end_seconds <= part.end_seconds for part in rally_parts):
                raise ValidationError("action must fit one rally segment")
    for segment in coverage:
        if segment.status in {"non_rally", "unusable"} and by_rally.get(segment.rally_id):
            raise ValidationError("actions cannot be in non-rally coverage")
    for rally_id, items in by_rally.items():
        ordered = sorted(items, key=lambda a: (a.start_seconds, a.end_seconds, a.action_ref))
        for left, right in zip(ordered, ordered[1:]):
            if right.start_seconds < left.end_seconds:
                raise ValidationError("overlapping duplicate action")
    vis_raw = data.get("visibility_events")
    if not isinstance(vis_raw, list):
        raise ValidationError("visibility_events must be a list")
    visibility_events: list[VisibilityInterval] = []; vis_refs: set[str] = set()
    for raw in vis_raw:
        if not isinstance(raw, dict): raise ValidationError("visibility record is invalid")
        _check_fields(raw, _VIS_FIELDS, "visibility")
        if not {"event_ref", "kind", "start_seconds", "end_seconds", "notes"}.issubset(raw):
            raise ValidationError("visibility record is missing fields")
        ref = raw.get("event_ref"); kind = raw.get("kind")
        if not isinstance(ref, str) or not ref or ref in vis_refs or kind not in {"fully_occluded", "off_camera", "unresolved"}:
            raise ValidationError("visibility record is invalid")
        start, end = _num(raw.get("start_seconds"), "visibility start"), _num(raw.get("end_seconds"), "visibility end")
        if end <= start or start < 0 or end > binding.metadata.duration_seconds:
            raise ValidationError("visibility interval is out of bounds")
        rally_id = raw.get("rally_id")
        if rally_id is not None and rally_id not in rally_segments: raise ValidationError("visibility rally is invalid")
        if not isinstance(raw.get("notes"), str): raise ValidationError("visibility notes must be a string")
        vis_refs.add(ref); visibility_events.append(VisibilityInterval(ref, rally_id, kind, start, end, raw["notes"]))
    annotation = data.get("annotation")
    if not isinstance(annotation, dict): raise ValidationError("annotation is invalid")
    _check_fields(annotation, {"annotation_version", "code_sha", "created_at"}, "annotation")
    if "annotation_version" not in annotation:
        raise ValidationError("annotation_version is required")
    if locked and (not annotation.get("code_sha") or not annotation.get("created_at")):
        raise ValidationError("locked annotation metadata is incomplete")
    version = annotation.get("annotation_version", "truth-v1")
    integrity = data.get("integrity")
    if not isinstance(integrity, dict):
        raise ValidationError("integrity is invalid")
    _check_fields(integrity, {"locked_sha256", "csv_sha256"}, "integrity")
    if locked and (not isinstance(integrity.get("locked_sha256"), str) or not integrity.get("locked_sha256") or not isinstance(integrity.get("csv_sha256"), str) or not integrity.get("csv_sha256")):
        raise ValidationError("Locked truth hashes are incomplete")
    return ValidationTruth(binding, tuple(set_intervals), tuple(side_intervals), coverage, tuple(actions), tuple(visibility_events), str(version), locked, integrity.get("locked_sha256"), integrity.get("csv_sha256"))


def init_truth_draft(queue_json: str | Path, output_json: str | Path, *, code_sha: str) -> Path:
    queue = _read_json(queue_json)
    if queue.get("format_version") != 1 or not isinstance(queue.get("binding"), dict):
        raise ValidationError("Invalid rally queue")
    binding = queue["binding"]
    segments = queue.get("segments", [])
    payload = {"format_version": 1, "state": "draft", "video": binding, "set_intervals": queue.get("set_intervals", []), "side_intervals": queue.get("side_intervals", []), "coverage": segments, "actions": [], "visibility_events": [], "annotation": {"annotation_version": "truth-v1", "code_sha": code_sha}, "integrity": {"locked_sha256": None, "csv_sha256": None}}
    destination = Path(output_json).expanduser().resolve(); write_new_bytes(destination, canonical_json_bytes(payload)); return destination


def validate_truth_draft(draft_json: str | Path, *, binding: ValidationVideoBinding) -> ValidationTruth:
    data = _read_json(draft_json)
    return _validate_records(data, binding, locked=False)


def _csv_bytes(truth: ValidationTruth) -> bytes:
    import io
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(_CSV_FIELDS)
    for action in truth.actions:
        if action.visibility != "visible": continue
        segment = next((s for s in truth.coverage if s.rally_id == action.rally_id and s.status == "rally" and s.start_seconds <= action.start_seconds and s.end_seconds >= action.end_seconds), None)
        if segment is None: raise ValidationError("action has no mapped rally segment")
        side = next((s for s in truth.side_intervals if _side_set_index(s, segment.set_index) == segment.set_index and float(s.get("start_seconds", -1)) <= action.start_seconds and float(s.get("end_seconds", -1)) >= action.end_seconds), None)
        if side is None: raise ValidationError("action has no mapped side interval")
        crop = side.get("crop")
        if not isinstance(crop, (list, tuple)) or len(crop) != 4: raise ValidationError("action crop is missing")
        row = [truth.video.repo_video_path, str(int(action.start_seconds)), str(int(action.end_seconds)), action.projected_label, str(side.get("team_side", "")), "", str(crop[0]), str(crop[1]), str(crop[2]), str(crop[3]), "val", truth.video.match_id, action.rally_id]
        writer.writerow(row)
    return ("\ufeff" + out.getvalue()).encode("utf-8")


def _lock_digest(authority: Mapping[str, object], csv_digest: str) -> str:
    basis = dict(authority)
    basis["integrity"] = {"csv_sha256": csv_digest}
    return hashlib.sha256(canonical_json_bytes(basis)).hexdigest()


def _side_set_index(side: Mapping[str, object], fallback: int | None) -> int | None:
    value = side.get("set_index", fallback)
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def lock_truth_bundle(draft_json: str | Path, csv_path: str | Path, json_path: str | Path, *, binding: ValidationVideoBinding, repo_root: str | Path, code_sha: str, created_at: str) -> dict[str, Path]:
    if not isinstance(code_sha, str) or not code_sha.strip() or not isinstance(created_at, str) or not created_at.strip():
        raise ValidationError("code_sha and created_at must be non-empty strings")
    truth = validate_truth_draft(draft_json, binding=binding)
    _resolve_bound_source(binding, repo_root=repo_root)
    csv_bytes = _csv_bytes(truth); csv_digest = hashlib.sha256(csv_bytes).hexdigest()
    authority = {"format_version": 1, "state": "locked", "video": _binding_dict(binding), "set_intervals": list(truth.set_intervals), "side_intervals": list(truth.side_intervals), "coverage": [asdict(s) for s in truth.coverage], "actions": [asdict(a) for a in truth.actions], "visibility_events": [asdict(v) for v in truth.visibility_events], "annotation": {"annotation_version": truth.annotation_version, "code_sha": code_sha, "created_at": created_at}}
    locked_digest = _lock_digest(authority, csv_digest)
    final = {**authority, "integrity": {"locked_sha256": locked_digest, "csv_sha256": csv_digest}}
    csv_dest, json_dest = Path(csv_path).expanduser().resolve(), Path(json_path).expanduser().resolve()
    if csv_dest.exists() or json_dest.exists(): raise ValidationError("Destination already exists")
    published_csv = False
    try:
        write_new_bytes(csv_dest, csv_bytes); published_csv = True
        write_new_bytes(json_dest, canonical_json_bytes(final))
    except Exception:
        if published_csv and csv_dest.exists() and not json_dest.exists():
            csv_dest.unlink(missing_ok=True)
        raise
    return {"csv": csv_dest, "json": json_dest}


def load_locked_truth(json_path: str | Path, csv_path: str | Path, *, binding: ValidationVideoBinding) -> ValidationTruth:
    data = _read_json(json_path)
    if data.get("state") != "locked" or not isinstance(data.get("integrity"), dict) or not data["integrity"].get("locked_sha256"):
        raise ValidationError("Locked truth is missing lock hash")
    truth = _validate_records(data, binding, locked=True)
    authority = dict(data); authority.pop("integrity", None)
    if _lock_digest(authority, truth.csv_sha256 or "") != data["integrity"].get("locked_sha256"):
        raise ValidationError("Locked truth hash mismatch")
    if truth.csv_sha256:
        try:
            actual_csv_hash = hashlib.sha256(Path(csv_path).read_bytes()).hexdigest()
        except OSError as exc:
            raise ValidationError("CSV is unreadable") from exc
        if actual_csv_hash != truth.csv_sha256:
            raise ValidationError("CSV hash mismatch")
    return truth


def verify_truth_bundle(json_path: str | Path, csv_path: str | Path, *, binding: ValidationVideoBinding, repo_root: str | Path, video_root: str | Path | None = None) -> dict[str, object]:
    truth = load_locked_truth(json_path, csv_path, binding=binding)
    _resolve_bound_source(binding, repo_root=repo_root, video_root=video_root)
    data = _read_json(json_path); integrity = data["integrity"]
    try:
        if Path(json_path).read_bytes() != canonical_json_bytes(data):
            raise ValidationError("Locked truth JSON bytes were modified")
    except OSError as exc:
        raise ValidationError("Locked truth JSON is unreadable") from exc
    authority = dict(data); authority.pop("integrity", None)
    if _lock_digest(authority, truth.csv_sha256 or "") != integrity.get("locked_sha256"): raise ValidationError("Locked truth hash mismatch")
    try:
        with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle); header = next(reader, None)
            if header != list(_CSV_FIELDS): raise ValidationError("CSV header mismatch")
            rows = list(reader)
    except ValidationError:
        raise
    except (OSError, csv.Error) as exc:
        raise ValidationError("CSV is unreadable") from exc
    try:
        import io
        generated = csv.reader(io.StringIO(_csv_bytes(truth).decode("utf-8-sig"), newline=""))
        next(generated, None)
        expected_rows = list(generated)
    except csv.Error as exc:
        raise ValidationError("CSV projection is invalid") from exc
    if rows != expected_rows: raise ValidationError("CSV projection mismatch")
    return {"coverage_segments": len(truth.coverage), "visible_actions": sum(a.visibility == "visible" for a in truth.actions), "no_action_rallies": len({s.rally_id for s in truth.coverage if s.status == "rally" and s.no_c2_action is True}), "visibility_intervals": len(truth.visibility_events)}
