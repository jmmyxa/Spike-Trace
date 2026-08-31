from __future__ import annotations

import csv
import ctypes
import errno
import hashlib
import io
import json
import math
import os
import shutil
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from ._active_learning_review_contract import ArtifactBinding, ValidatedReviewInput
from ._active_learning_review_observations import ObservationSet
from ._active_learning_review_projection import TrainingProjection
from .constants import ACTION_LABELS

_AUTHORITY_FILENAME = "round-01-results.json"
_TRAINING_FILENAME = "action_training_round_01.csv"
_OBSERVATIONS_FILENAME = "round-01-observations.csv"
_VISIBILITY_FILENAME = "round-01-visibility-events.csv"
_PARTICIPANTS_FILENAME = "round-01-action-participants.csv"
_EXPORTS_FILENAME = "round-01-exports.manifest.json"
_CANONICAL_COLUMNS = (
    "video_path", "start_seconds", "end_seconds", "label", "team_side",
    "player_number", "split", "crop_x1", "crop_y1", "crop_x2", "crop_y2",
    "match_id", "review_status", "notes",
)
_OBSERVATION_FIELDS = (
    "result_set_id", "selection_sha256", "workbook_sha256", "generator_version",
    "observation_type", "observation_ref", "action_ref", "clip_id",
    "source_action_slot", "review_label", "relative_start_seconds",
    "relative_end_seconds", "start_seconds", "end_seconds", "team_side",
    "visibility", "evidence_basis", "training_decision", "outcome",
    "result_type", "status", "related_action_refs_json", "note",
)
_VISIBILITY_FIELDS = (
    "result_set_id", "selection_sha256", "workbook_sha256", "generator_version",
    "event_kind", "event_ref", "team_side", "start_seconds", "end_seconds",
    "duration_seconds", "interval_scope", "related_action_refs_json",
    "source_refs_json", "note",
)
_PARTICIPANT_FIELDS = (
    "result_set_id", "selection_sha256", "workbook_sha256", "generator_version",
    "action_ref", "track_id", "identity_ref", "player_number", "participation",
    "touch_status", "assignment_status", "assignment_confidence", "evidence_json",
)
_BUNDLE_FILENAMES = (
    _AUTHORITY_FILENAME,
    _TRAINING_FILENAME,
    _OBSERVATIONS_FILENAME,
    _VISIBILITY_FILENAME,
    _PARTICIPANTS_FILENAME,
    _EXPORTS_FILENAME,
)
_AUTHORITY_FIELDS = (
    "format", "format_version", "result_set_id", "content_sha256", "batch_id",
    "round_id", "generator_version", "sources", "source_review_rows", "repairs",
    "action_observations", "outcome_observations", "occlusion_events",
    "off_camera_events", "action_participants", "protected_intervals",
    "training_projection", "summary", "exports",
)
_MANIFEST_FIELDS = (
    "format", "format_version", "result_set_id", "content_sha256",
    "generator_version", "sources", "artifacts",
)
_SOURCE_FIELDS = (
    "selection", "review_input", "workbook", "evidence_overrides",
    "merged_candidates", "base_manifest", "video", "verification",
)
_ARTIFACT_FIELDS = (
    "path", "media_type", "sha256", "bytes", "encoding", "line_ending",
    "data_rows", "entity_counts",
)
_ENTITY_FIELDS = (
    "action_observations", "outcome_observations", "occlusion_events",
    "off_camera_events", "action_participants", "training_rows",
)
_SUMMARY_FIELDS = (
    "action_observations", "outcome_observations", "occlusion_events",
    "off_camera_events", "affected_action_count", "action_participants",
    "positive_training_count", "generated_background_count", "training_rows",
)


@dataclass(frozen=True, slots=True)
class BundleSettings:
    generator_version: str
    legacy_base_match_id: str
    review_match_id: str
    video_root_audit: dict[str, str]
    training_video_path: str
    source_video_file_checked: bool
    background_guard_seconds: float
    max_background_windows: int | None
    background_seed: int


@dataclass(frozen=True, slots=True)
class RenderedReviewBundle:
    authority: dict[str, object]
    artifacts: tuple[tuple[str, bytes], ...]


def render_result_bundle(
    *,
    review: ValidatedReviewInput,
    observations: ObservationSet,
    projection: TrainingProjection,
    base_fieldnames: Sequence[str],
    base_rows: Sequence[Mapping[str, str | None]],
    base_manifest_binding: ArtifactBinding,
    settings: BundleSettings,
) -> RenderedReviewBundle:
    if review.result_set_id != observations.result_set_id:
        raise ValueError("Review and observations result_set_id must match.")

    training_bytes, training_rows, base_training_view = _render_training_csv(
        base_fieldnames, base_rows, projection, settings
    )
    decisions = dict(projection.decisions)
    observation_rows = _observation_rows(review, observations, decisions, settings)
    observation_bytes = _csv_bytes(_OBSERVATION_FIELDS, observation_rows)
    visibility_rows = _visibility_rows(review, observations, settings)
    visibility_bytes = _csv_bytes(_VISIBILITY_FIELDS, visibility_rows)
    participant_rows = _participant_rows(review, observations, settings)
    participant_bytes = _csv_bytes(_PARTICIPANT_FIELDS, participant_rows)

    csv_artifacts = (
        (_TRAINING_FILENAME, training_bytes),
        (_OBSERVATIONS_FILENAME, observation_bytes),
        (_VISIBILITY_FILENAME, visibility_bytes),
        (_PARTICIPANTS_FILENAME, participant_bytes),
    )
    sources = _sources(review, base_manifest_binding, settings)
    semantic_authority = {
        "format": "spiketrace.active-review-observations",
        "format_version": 2,
        "result_set_id": review.result_set_id,
        "batch_id": review.batch_id,
        "round_id": review.round_id,
        "generator_version": settings.generator_version,
        "sources": sources,
        "source_review_rows": _plain(review.source_review_rows),
        "repairs": _plain(review.source_repairs),
        "action_observations": _plain(observations.actions),
        "outcome_observations": _plain(observations.outcomes),
        "occlusion_events": _plain(observations.occlusion_events),
        "off_camera_events": _plain(observations.off_camera_events),
        "action_participants": _plain(observations.participants),
        "protected_intervals": _plain(projection.protected_intervals),
        "training_projection": _training_projection(
            projection, settings, base_training_view
        ),
        "summary": _summary(observations, projection, training_rows),
    }
    content_sha256 = hashlib.sha256(_canonical_json_bytes(semantic_authority)).hexdigest()
    exports = {
        "training_csv": _binding(_TRAINING_FILENAME, training_bytes),
        "observations_csv": _binding(_OBSERVATIONS_FILENAME, observation_bytes),
        "visibility_events_csv": _binding(_VISIBILITY_FILENAME, visibility_bytes),
        "action_participants_csv": _binding(_PARTICIPANTS_FILENAME, participant_bytes),
        "manifest": {"path": _EXPORTS_FILENAME},
    }
    authority = {
        "format": semantic_authority["format"],
        "format_version": semantic_authority["format_version"],
        "result_set_id": semantic_authority["result_set_id"],
        "content_sha256": content_sha256,
        **{
            key: value
            for key, value in semantic_authority.items()
            if key not in {"format", "format_version", "result_set_id"}
        },
        "exports": exports,
    }
    authority_bytes = _presentation_json_bytes(authority)
    manifest = {
        "format": "spiketrace.active-review-exports-manifest",
        "format_version": 1,
        "result_set_id": review.result_set_id,
        "content_sha256": content_sha256,
        "generator_version": settings.generator_version,
        "sources": sources,
        "artifacts": [
            _artifact_entry(
                _AUTHORITY_FILENAME,
                authority_bytes,
                media_type="application/json",
                encoding="utf-8",
                line_ending="lf",
                data_rows=None,
                entity_counts={
                    "action_observations": len(observations.actions),
                    "outcome_observations": len(observations.outcomes),
                    "occlusion_events": len(observations.occlusion_events),
                    "off_camera_events": len(observations.off_camera_events),
                    "action_participants": len(observations.participants),
                    "training_rows": training_rows,
                },
            ),
            _artifact_entry(_TRAINING_FILENAME, training_bytes, data_rows=training_rows),
            _artifact_entry(
                _OBSERVATIONS_FILENAME, observation_bytes, data_rows=len(observation_rows)
            ),
            _artifact_entry(
                _VISIBILITY_FILENAME, visibility_bytes, data_rows=len(visibility_rows)
            ),
            _artifact_entry(
                _PARTICIPANTS_FILENAME, participant_bytes, data_rows=len(participant_rows)
            ),
        ],
    }
    manifest_bytes = _presentation_json_bytes(manifest)
    return RenderedReviewBundle(
        authority,
        (
            (_AUTHORITY_FILENAME, authority_bytes),
            *csv_artifacts,
            (_EXPORTS_FILENAME, manifest_bytes),
        ),
    )


def validate_result_bundle(bundle_dir: str | Path) -> dict[str, object]:
    directory = Path(bundle_dir).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f"Review bundle directory does not exist: {directory}")
    entries = tuple(sorted(entry.name for entry in directory.iterdir()))
    if set(entries) != set(_BUNDLE_FILENAMES) or len(entries) != len(_BUNDLE_FILENAMES):
        raise ValueError("Review bundle must contain exactly six fixed files.")

    raw_by_name = {
        filename: _read_bundle_file(directory / filename) for filename in _BUNDLE_FILENAMES
    }
    authority = _load_json_artifact(raw_by_name[_AUTHORITY_FILENAME], "authority JSON")
    manifest = _load_json_artifact(raw_by_name[_EXPORTS_FILENAME], "exports manifest")
    _exact_fields(authority, _AUTHORITY_FIELDS, "authority JSON")
    _exact_fields(manifest, _MANIFEST_FIELDS, "exports manifest")
    _format_version(
        authority,
        "spiketrace.active-review-observations",
        2,
        "authority JSON",
    )
    _format_version(
        manifest,
        "spiketrace.active-review-exports-manifest",
        1,
        "exports manifest",
    )
    result_set_id = _nonempty_text(authority["result_set_id"], "result_set_id")
    if manifest["result_set_id"] != result_set_id:
        raise ValueError("Exports manifest result_set_id does not match authority JSON.")
    generator_version = _nonempty_text(authority["generator_version"], "generator_version")
    if manifest["generator_version"] != generator_version:
        raise ValueError("Exports manifest generator_version does not match authority JSON.")
    content_sha256 = _hash(authority["content_sha256"], "authority content_sha256")
    if manifest["content_sha256"] != content_sha256:
        raise ValueError("Exports manifest content_sha256 does not match authority JSON.")

    sources = _validate_sources(authority["sources"])
    if manifest["sources"] != sources:
        raise ValueError("Exports manifest sources do not match authority JSON.")
    semantic = dict(authority)
    del semantic["content_sha256"]
    del semantic["exports"]
    recomputed_content = hashlib.sha256(_canonical_json_bytes(semantic)).hexdigest()
    if recomputed_content != content_sha256:
        raise ValueError("Authority content_sha256 does not match semantic authority bytes.")

    training_fields, training_rows = _load_csv_artifact(
        raw_by_name[_TRAINING_FILENAME], None, "training CSV"
    )
    _, observation_rows = _load_csv_artifact(
        raw_by_name[_OBSERVATIONS_FILENAME], _OBSERVATION_FIELDS, "observations CSV"
    )
    _, visibility_rows = _load_csv_artifact(
        raw_by_name[_VISIBILITY_FILENAME], _VISIBILITY_FIELDS, "visibility events CSV"
    )
    _, participant_rows = _load_csv_artifact(
        raw_by_name[_PARTICIPANTS_FILENAME], _PARTICIPANT_FIELDS, "action participants CSV"
    )
    csv_rows = {
        _TRAINING_FILENAME: training_rows,
        _OBSERVATIONS_FILENAME: observation_rows,
        _VISIBILITY_FILENAME: visibility_rows,
        _PARTICIPANTS_FILENAME: participant_rows,
    }
    _validate_manifest_artifacts(manifest, authority, raw_by_name, csv_rows)
    _validate_exports(authority["exports"], raw_by_name)
    _validate_cross_views(
        authority,
        csv_rows,
        result_set_id,
        sources["selection"]["sha256"],
        sources["workbook"]["sha256"],
        generator_version,
        training_fields,
    )
    summary = _validate_summary(authority, len(csv_rows[_TRAINING_FILENAME]))
    return {
        "result_set_id": result_set_id,
        "content_sha256": content_sha256,
        "summary": summary,
    }


class _PublicationIO:
    def create_parent(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def create_staging(self, path: Path) -> None:
        os.mkdir(path)

    def open_exclusive(self, path: Path):
        return path.open("xb")

    def write(self, handle: Any, raw: bytes) -> None:
        written = handle.write(raw)
        if written != len(raw):
            raise OSError(f"Short write for {handle.name}")

    def flush(self, handle: Any) -> None:
        handle.flush()

    def fsync(self, handle: Any) -> None:
        os.fsync(handle.fileno())

    def read(self, path: Path) -> bytes:
        return path.read_bytes()

    def rename(self, source: Path, destination: Path) -> None:
        rename_directory_noreplace(source, destination)


def publish_result_bundle(
    output_dir: str | Path,
    bundle: RenderedReviewBundle,
    *,
    validate: Callable[[Path], dict[str, object]] = validate_result_bundle,
    before_publish: Callable[[], None] | None = None,
    io: object | None = None,
) -> None:
    _require_noreplace_platform()
    artifacts = tuple(bundle.artifacts)
    if tuple(filename for filename, _ in artifacts) != _BUNDLE_FILENAMES:
        raise ValueError("Rendered bundle must contain the exact six fixed artifacts.")
    if any(not isinstance(raw, bytes) for _, raw in artifacts):
        raise TypeError("Rendered bundle artifacts must be bytes.")
    destination = Path(os.path.abspath(os.fspath(Path(output_dir).expanduser())))
    parent = destination.parent
    publication_io = io if io is not None else _PublicationIO()
    publication_io.create_parent(parent)
    staging = parent / f".{destination.name}.staging-{uuid.uuid4().hex}"
    created_staging = False
    try:
        publication_io.create_staging(staging)
        created_staging = True
        for filename, raw in artifacts:
            artifact_path = staging / filename
            with publication_io.open_exclusive(artifact_path) as handle:
                publication_io.write(handle, raw)
                publication_io.flush(handle)
                publication_io.fsync(handle)
        for filename, expected in artifacts:
            actual = publication_io.read(staging / filename)
            if actual != expected:
                raise OSError(f"Staged artifact changed during publication: {filename}")
        validate(staging)
        if before_publish is not None:
            before_publish()
        publication_io.rename(staging, destination)
        created_staging = False
    finally:
        if created_staging:
            _remove_staging(staging)


def rename_directory_noreplace(source: Path, destination: Path) -> None:
    _require_noreplace_platform()
    if sys.platform == "win32":
        _rename_windows_noreplace(source, destination)
    elif sys.platform.startswith("linux"):
        _rename_linux_noreplace(source, destination)
    elif sys.platform == "darwin":
        _rename_macos_noreplace(source, destination)
    else:
        raise RuntimeError(f"Atomic no-replace publication is unsupported on {sys.platform}.")


def _require_noreplace_platform() -> None:
    if sys.platform not in {"win32", "darwin"} and not sys.platform.startswith("linux"):
        raise RuntimeError(f"Atomic no-replace publication is unsupported on {sys.platform}.")
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        if not hasattr(libc, "renameat2"):
            raise RuntimeError("Atomic no-replace publication requires renameat2.")
    elif sys.platform == "darwin":
        _load_macos_renamex()


def _rename_windows_noreplace(source: Path, destination: Path) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    move_file = kernel32.MoveFileExW
    move_file.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
    move_file.restype = ctypes.c_int
    if move_file(str(source), str(destination), 0):
        return
    error = ctypes.get_last_error()
    if os.path.lexists(destination):
        raise FileExistsError(error, "Publication destination already exists", destination)
    raise OSError(error, "MoveFileExW failed", destination)


def _rename_linux_noreplace(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = libc.renameat2
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    if renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1) == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(error, os.strerror(error), destination)
    raise OSError(error, os.strerror(error), destination)


def _rename_macos_noreplace(source: Path, destination: Path) -> None:
    renamex_np = _load_macos_renamex()
    if renamex_np(os.fsencode(source), os.fsencode(destination), 0x00000004) == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(error, os.strerror(error), destination)
    raise OSError(error, os.strerror(error), destination)


def _load_macos_renamex() -> Any:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renamex_np = libc.renamex_np
    except (AttributeError, OSError) as exc:
        raise RuntimeError("Atomic no-replace publication requires renamex_np.") from exc
    renamex_np.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    renamex_np.restype = ctypes.c_int
    return renamex_np


def _remove_staging(staging: Path) -> None:
    if staging.is_symlink():
        staging.unlink(missing_ok=True)
    elif staging.exists():
        shutil.rmtree(staging)


def _read_bundle_file(path: Path) -> bytes:
    if not path.is_file():
        raise ValueError(f"Bundle entry must be a regular file: {path.name}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Could not read bundle artifact: {path.name}") from exc


def _load_json_artifact(raw: bytes, description: str) -> dict[str, Any]:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"{description} must not contain a BOM.")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise ValueError(f"{description} must use LF and exactly one trailing LF.")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {constant}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{description} is not valid UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{description} must contain an object.")  # noqa: TRY004
    return value


def _load_csv_artifact(
    raw: bytes,
    expected_fields: Sequence[str] | None,
    description: str,
) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    if not raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"{description} must start with a UTF-8 BOM.")
    body = raw[3:]
    remaining = body.replace(b"\r\n", b"")
    if not body.endswith(b"\r\n") or b"\r" in remaining or b"\n" in remaining:
        raise ValueError(f"{description} must use only CRLF line endings.")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{description} must be UTF-8.") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fields = tuple(reader.fieldnames or ())
    if expected_fields is not None and fields != tuple(expected_fields):
        raise ValueError(f"{description} header does not match the fixed schema.")
    if expected_fields is None and (
        len(fields) != len(set(fields))
        or not set(_CANONICAL_COLUMNS).issubset(fields)
    ):
        raise ValueError("Training CSV header does not contain the canonical manifest schema.")
    return fields, list(reader)


def _validate_sources(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("sources must be an object.")  # noqa: TRY004
    _exact_fields(value, _SOURCE_FIELDS, "sources")
    for name in _SOURCE_FIELDS[:6]:
        binding = value[name]
        if not isinstance(binding, dict):
            raise ValueError(  # noqa: TRY004
                f"sources.{name} must be an artifact binding."
            )
        _exact_fields(binding, ("path", "sha256"), f"sources.{name}")
        _repo_path_text(binding["path"], f"sources.{name}.path")
        _hash(binding["sha256"], f"sources.{name}.sha256")
    video = value["video"]
    if not isinstance(video, dict):
        raise ValueError("sources.video must be an object.")  # noqa: TRY004
    _exact_fields(
        video,
        (
            "video_id", "path", "sha256", "fps", "frame_count", "width",
            "height", "duration_seconds", "crops",
        ),
        "sources.video",
    )
    _nonempty_text(video["video_id"], "sources.video.video_id")
    _repo_path_text(video["path"], "sources.video.path")
    _hash(video["sha256"], "sources.video.sha256")
    fps = _finite_number(video["fps"], "sources.video.fps")
    duration = _finite_number(
        video["duration_seconds"], "sources.video.duration_seconds"
    )
    frame_count = _whole_number(
        video["frame_count"], "sources.video.frame_count"
    )
    width = _whole_number(video["width"], "sources.video.width")
    height = _whole_number(video["height"], "sources.video.height")
    if fps <= 0 or duration <= 0 or frame_count < 1 or width < 1 or height < 1:
        raise ValueError("sources.video metadata must be positive.")
    crops = video["crops"]
    if not isinstance(crops, dict):
        raise ValueError("sources.video.crops must be an object.")  # noqa: TRY004
    _exact_fields(crops, ("far", "near"), "sources.video.crops")
    for side in ("far", "near"):
        _validate_crop(crops[side], width, height, f"sources.video.crops.{side}")
    verification = value["verification"]
    if not isinstance(verification, dict):
        raise ValueError("sources.verification must be an object.")  # noqa: TRY004
    _exact_fields(
        verification, ("source_video_file_checked",), "sources.verification"
    )
    if type(verification["source_video_file_checked"]) is not bool:
        raise ValueError("source_video_file_checked must be a boolean.")
    return value


def _validate_manifest_artifacts(
    manifest: dict[str, Any],
    authority: dict[str, Any],
    raw_by_name: dict[str, bytes],
    csv_rows: dict[str, list[dict[str, str]]],
) -> None:
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != 5:
        raise ValueError("Exports manifest must list exactly five artifacts.")
    expected_names = _BUNDLE_FILENAMES[:-1]
    if tuple(item.get("path") if isinstance(item, dict) else None for item in artifacts) != expected_names:
        raise ValueError("Exports manifest artifacts are not in the fixed order.")
    for index, entry in enumerate(artifacts):
        if not isinstance(entry, dict):
            raise ValueError(  # noqa: TRY004
                "Exports manifest artifact entry must be an object."
            )
        _exact_fields(entry, _ARTIFACT_FIELDS, "exports manifest artifact")
        filename = expected_names[index]
        raw = raw_by_name[filename]
        expected_json = filename == _AUTHORITY_FILENAME
        if (
            entry["media_type"] != ("application/json" if expected_json else "text/csv")
            or entry["encoding"] != ("utf-8" if expected_json else "utf-8-sig")
            or entry["line_ending"] != ("lf" if expected_json else "crlf")
            or entry["sha256"] != hashlib.sha256(raw).hexdigest()
            or type(entry["bytes"]) is not int
            or entry["bytes"] != len(raw)
        ):
            raise ValueError(f"Exports manifest artifact metadata is invalid for {filename}.")
        if expected_json:
            if entry["data_rows"] is not None:
                raise ValueError("Authority artifact data_rows must be null.")
            expected_entities = {
                "action_observations": _array_length(authority, "action_observations"),
                "outcome_observations": _array_length(authority, "outcome_observations"),
                "occlusion_events": _array_length(authority, "occlusion_events"),
                "off_camera_events": _array_length(authority, "off_camera_events"),
                "action_participants": _array_length(authority, "action_participants"),
                "training_rows": len(csv_rows[_TRAINING_FILENAME]),
            }
            if not isinstance(entry["entity_counts"], dict):
                raise ValueError("Authority artifact entity_counts must be an object.")
            _exact_fields(entry["entity_counts"], _ENTITY_FIELDS, "entity_counts")
            if any(
                type(count) is not int or count < 0
                for count in entry["entity_counts"].values()
            ):
                raise ValueError("Authority artifact entity_counts must be nonnegative integers.")
            if entry["entity_counts"] != expected_entities:
                raise ValueError("Authority artifact entity_counts do not match bundle data.")
        else:
            if (
                type(entry["data_rows"]) is not int
                or entry["data_rows"] != len(csv_rows[filename])
            ):
                raise ValueError(f"Artifact data rows do not match {filename}.")
            if entry["entity_counts"] is not None:
                raise ValueError("CSV artifact entity_counts must be null.")


def _validate_exports(value: object, raw_by_name: dict[str, bytes]) -> None:
    if not isinstance(value, dict):
        raise ValueError("Authority exports must be an object.")  # noqa: TRY004
    expected = (
        ("training_csv", _TRAINING_FILENAME),
        ("observations_csv", _OBSERVATIONS_FILENAME),
        ("visibility_events_csv", _VISIBILITY_FILENAME),
        ("action_participants_csv", _PARTICIPANTS_FILENAME),
    )
    _exact_fields(value, tuple(name for name, _ in expected) + ("manifest",), "authority exports")
    for field, filename in expected:
        if value[field] != _binding(filename, raw_by_name[filename]):
            raise ValueError(f"Authority exports binding is invalid for {filename}.")
    if value["manifest"] != {"path": _EXPORTS_FILENAME}:
        raise ValueError("Authority exports manifest binding is invalid.")


def _validate_cross_views(
    authority: dict[str, Any],
    csv_rows: dict[str, list[dict[str, str]]],
    result_set_id: str,
    selection_sha256: str,
    workbook_sha256: str,
    generator_version: str,
    training_fields: tuple[str, ...],
) -> None:
    common = (result_set_id, selection_sha256, workbook_sha256, generator_version)
    decisions = _validate_projection_contract(authority)
    for filename in (_OBSERVATIONS_FILENAME, _VISIBILITY_FILENAME, _PARTICIPANTS_FILENAME):
        for row in csv_rows[filename]:
            actual = (
                row["result_set_id"], row["selection_sha256"],
                row["workbook_sha256"], row["generator_version"],
            )
            if actual != common:
                raise ValueError(f"{filename} row result_set_id or source identity does not match.")
    expected_observations = _authority_observation_rows(authority, common, decisions)
    if csv_rows[_OBSERVATIONS_FILENAME] != expected_observations:
        raise ValueError("Observations CSV does not match authority observations.")
    expected_events = _authority_visibility_rows(authority, common)
    if csv_rows[_VISIBILITY_FILENAME] != expected_events:
        raise ValueError("Visibility events CSV does not match authority events.")
    expected_participants = _authority_participant_rows(authority, common)
    if csv_rows[_PARTICIPANTS_FILENAME] != expected_participants:
        raise ValueError("Action participants CSV does not match authority participants.")
    _validate_training_view(
        authority, csv_rows[_TRAINING_FILENAME], training_fields
    )


def _validate_projection_contract(authority: dict[str, Any]) -> dict[str, str]:
    projection = authority["training_projection"]
    if not isinstance(projection, dict):
        raise ValueError("Authority training_projection must be an object.")  # noqa: TRY004
    _exact_fields(
        projection,
        (
            "decisions", "human_windows", "generated_background_windows",
            "positive_training_count", "requested_background_cap",
            "effective_background_cap", "training_video_path", "review_match_id",
            "base_training_view",
        ),
        "training_projection",
    )
    actions = authority["action_observations"]
    if not isinstance(actions, list):
        raise ValueError("Authority action_observations must be an array.")  # noqa: TRY004
    action_by_ref: dict[str, dict[str, Any]] = {}
    expected_decisions: list[dict[str, object]] = []
    for action in actions:
        if not isinstance(action, dict):
            raise ValueError("Authority action observation must be an object.")  # noqa: TRY004
        action_ref = _nonempty_text(
            action.get("action_ref"), "training_projection decisions action_ref"
        )
        if action_ref in action_by_ref:
            raise ValueError("training_projection decisions require unique action refs.")
        action_by_ref[action_ref] = action
        expected_decisions.append({
            "action_ref": action_ref,
            **_expected_training_decision(action),
        })
    decisions = projection["decisions"]
    if not isinstance(decisions, list):
        raise ValueError("Authority training_projection decisions must be an array.")  # noqa: TRY004
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError("training_projection decisions must contain objects.")  # noqa: TRY004
        _exact_fields(
            decision,
            ("action_ref", "decision", "training_label", "reason"),
            "training_projection decisions item",
        )
    if decisions != expected_decisions:
        raise ValueError(
            "training_projection decisions must exactly cover authority actions."
        )

    video = authority["sources"]["video"]
    participants = authority["action_participants"]
    expected_players = _projected_player_numbers(participants, set(action_by_ref))
    expected_human: list[dict[str, object]] = []
    for action, decision in zip(actions, expected_decisions, strict=True):
        start = _optional_finite_number(
            action.get("start_seconds"), "training_projection action start_seconds"
        )
        end = _optional_finite_number(
            action.get("end_seconds"), "training_projection action end_seconds"
        )
        if (start is None) != (end is None) or (
            start is not None and (start < 0 or end <= start or end > video["duration_seconds"])
        ):
            raise ValueError("training_projection action interval is invalid.")
        training_label = decision["training_label"]
        if training_label is not None and start is None:
            raise ValueError("training_projection eligible action must be timed.")
        if training_label is None or start is None:
            continue
        action_ref = decision["action_ref"]
        side = _enum_text(
            action.get("team_side"), {"far", "near"},
            "training_projection action team_side",
        )
        expected_human.append({
            "source_ref": action_ref,
            "clip_id": _nonempty_text(
                action.get("clip_id"), "training_projection action clip_id"
            ),
            "start_seconds": action["start_seconds"],
            "end_seconds": action["end_seconds"],
            "training_label": training_label,
            "review_label": action["review_label"],
            "team_side": side,
            "crop": video["crops"][side],
            "player_number": expected_players.get(action_ref),
            "generated": False,
            "window_index": None,
            "source_top1_action": None,
            "source_top1_confidence": None,
            "note": _text_or_empty(
                action.get("note"), "training_projection action note"
            ),
        })
    human = projection["human_windows"]
    if not isinstance(human, list):
        raise ValueError("Authority training_projection human_windows must be an array.")  # noqa: TRY004
    for window in human:
        _validate_window_shape(window, video, generated=False)
    if human != expected_human:
        raise ValueError(
            "training_projection human windows do not match eligible actions or players."
        )

    generated = projection["generated_background_windows"]
    if not isinstance(generated, list):
        raise ValueError(  # noqa: TRY004
            "Authority training_projection generated_background_windows must be an array."
        )
    generated_refs: set[str] = set()
    human_refs = {window["source_ref"] for window in expected_human}
    for window in generated:
        _validate_window_shape(window, video, generated=True)
        source_ref = window["source_ref"]
        if source_ref in generated_refs or source_ref in human_refs:
            raise ValueError("training_projection window source refs must be unique.")
        generated_refs.add(source_ref)
        expected_ref = (
            f"{window['clip_id']}/hard-negative-"
            f"{window['team_side']}-{window['window_index']}"
        )
        if source_ref != expected_ref:
            raise ValueError("training_projection generated source_ref is invalid.")
        donor_actions = [
            action for action in actions if action.get("clip_id") == window["clip_id"]
        ]
        if not (
            len(donor_actions) == 1
            and donor_actions[0].get("review_label") == "background"
            and donor_actions[0].get("background_scope") == "clip_sentinel"
            and donor_actions[0].get("start_seconds") is None
            and donor_actions[0].get("end_seconds") is None
            and donor_actions[0].get("team_side") == window["team_side"]
        ):
            raise ValueError("training_projection generated window has no sentinel donor.")

    positive_count = sum(
        window["training_label"] != "background" for window in expected_human
    )
    positive = _nonnegative_integer(
        projection["positive_training_count"],
        "training_projection positive_training_count",
    )
    requested = _nonnegative_integer(
        projection["requested_background_cap"],
        "training_projection requested_background_cap",
    )
    effective = _nonnegative_integer(
        projection["effective_background_cap"],
        "training_projection effective_background_cap",
    )
    if (
        positive != positive_count
        or effective != min(requested, positive)
        or len(generated) > effective
    ):
        raise ValueError("training_projection caps or counts are inconsistent.")
    return {item["action_ref"]: item["decision"] for item in decisions}


def _expected_training_decision(action: dict[str, Any]) -> dict[str, object]:
    review_label = _enum_text(
        action.get("review_label"),
        {*ACTION_LABELS, "free_ball"},
        "training_projection decisions review_label",
    )
    visibility = _enum_text(
        action.get("visibility"),
        {"direct_clear", "direct_partial", "fully_occluded", "off_camera", "unresolved"},
        "training_projection decisions visibility",
    )
    evidence = _enum_text(
        action.get("evidence_basis"),
        {"direct_video", "referee_signal", "scoreboard", "sequence_context", "mixed"},
        "training_projection decisions evidence_basis",
    )
    background_scope = action.get("background_scope")
    if (
        review_label == "background"
        and background_scope not in {"timed_interval", "clip_sentinel"}
    ) or (review_label != "background" and background_scope is not None):
        raise ValueError("training_projection decisions background_scope is invalid.")
    if visibility not in {"direct_clear", "direct_partial"} or evidence != "direct_video":
        return {
            "decision": "excluded", "training_label": None,
            "reason": "insufficient_visual_evidence",
        }
    if review_label == "free_ball":
        return {
            "decision": "eligible_as_background", "training_label": "background",
            "reason": "free_ball_projects_to_background",
        }
    if review_label == "background" and action.get("background_scope") == "clip_sentinel":
        return {
            "decision": "excluded", "training_label": None,
            "reason": "background_sentinel_only",
        }
    return {
        "decision": "eligible", "training_label": review_label,
        "reason": "direct_visual",
    }


def _projected_player_numbers(
    participants: object, action_refs: set[str]
) -> dict[str, str | None]:
    if not isinstance(participants, list):
        raise ValueError("Authority action_participants must be an array.")  # noqa: TRY004
    confirmed: dict[str, list[str | None]] = {}
    for participant in participants:
        if not isinstance(participant, dict):
            raise ValueError("Authority action participant must be an object.")  # noqa: TRY004
        _exact_fields(
            participant,
            (
                "action_ref", "track_id", "identity_ref", "player_number",
                "participation", "touch_status", "assignment_status",
                "assignment_confidence", "evidence",
            ),
            "training_projection participant",
        )
        action_ref = participant.get("action_ref")
        if action_ref not in action_refs:
            raise ValueError("training_projection participant action_ref is invalid.")
        for field in ("track_id", "identity_ref", "player_number"):
            if participant[field] is not None:
                _nonempty_text(
                    participant[field], f"training_projection participant {field}"
                )
        status = _enum_text(
            participant["assignment_status"],
            {"confirmed", "candidate", "unresolved"},
            "training_projection participant assignment_status",
        )
        confidence = participant["assignment_confidence"]
        if confidence is not None and not _is_unit_interval(confidence):
            raise ValueError("training_projection participant confidence is invalid.")
        if status == "confirmed":
            player = participant["player_number"]
            if (
                participant["identity_ref"] is None
                or player is None
                or confidence is None
            ):
                raise ValueError("training_projection participant player_number is invalid.")
            confirmed.setdefault(action_ref, []).append(player)
        elif status == "candidate" and (
            confidence is None
            or all(
                participant[field] is None
                for field in ("track_id", "identity_ref", "player_number")
            )
        ):
            raise ValueError("training_projection candidate participant is invalid.")
        elif status == "unresolved" and (
            participant["identity_ref"] is not None
            or participant["player_number"] is not None
            or confidence is not None
        ):
            raise ValueError("training_projection unresolved participant is invalid.")
    return {
        action_ref: players[0] if len(players) == 1 else None
        for action_ref, players in confirmed.items()
    }


def _validate_window_shape(
    value: object, video: dict[str, Any], *, generated: bool
) -> None:
    description = "training_projection generated window" if generated else "training_projection human window"
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be an object.")  # noqa: TRY004
    fields = (
        "source_ref", "clip_id", "start_seconds", "end_seconds", "training_label",
        "review_label", "team_side", "crop", "player_number", "generated",
        "window_index", "source_top1_action", "source_top1_confidence", "note",
    )
    _exact_fields(value, fields, description)
    _nonempty_text(value["source_ref"], f"{description} source_ref")
    _nonempty_text(value["clip_id"], f"{description} clip_id")
    start = _finite_number(value["start_seconds"], f"{description} start_seconds")
    end = _finite_number(value["end_seconds"], f"{description} end_seconds")
    if start < 0 or end <= start or end > video["duration_seconds"]:
        raise ValueError(f"{description} interval is invalid.")
    side = _enum_text(value["team_side"], {"far", "near"}, f"{description} team_side")
    crop = _validate_crop(
        value["crop"], video["width"], video["height"], f"{description} crop"
    )
    if crop != video["crops"][side]:
        raise ValueError(f"{description} crop does not match the video side.")
    if type(value["generated"]) is not bool or value["generated"] is not generated:
        raise ValueError(f"{description} generated flag is invalid.")
    if generated:
        if (
            value["training_label"] != "background"
            or value["review_label"] != "background"
            or value["player_number"] is not None
            or type(value["window_index"]) is not int
            or value["window_index"] < 0
            or value["source_top1_action"] not in ACTION_LABELS
            or not _is_unit_interval(value["source_top1_confidence"])
            or value["note"] != ""
        ):
            raise ValueError(f"{description} metadata is invalid.")
    elif (
        value["training_label"] not in ACTION_LABELS
        or value["review_label"] not in {*ACTION_LABELS, "free_ball"}
        or (value["player_number"] is not None and not isinstance(value["player_number"], str))
        or value["window_index"] is not None
        or value["source_top1_action"] is not None
        or value["source_top1_confidence"] is not None
        or not isinstance(value["note"], str)
    ):
        raise ValueError(f"{description} metadata is invalid.")


def _validate_training_view(
    authority: dict[str, Any],
    training_rows: list[dict[str, str]],
    training_fields: tuple[str, ...],
) -> None:
    projection = authority["training_projection"]
    if not isinstance(projection, dict):
        raise ValueError("Authority training_projection must be an object.")  # noqa: TRY004
    expected_fields = (
        "decisions", "human_windows", "generated_background_windows",
        "positive_training_count", "requested_background_cap", "effective_background_cap",
        "training_video_path", "review_match_id", "base_training_view",
    )
    _exact_fields(projection, expected_fields, "training_projection")
    human = projection["human_windows"]
    generated = projection["generated_background_windows"]
    if not isinstance(human, list) or not isinstance(generated, list):
        raise ValueError("Authority training_projection windows must be arrays.")  # noqa: TRY004
    windows = [*human, *generated]
    base_rows = _validate_base_training_view(
        projection["base_training_view"], training_rows, training_fields
    )
    if len(training_rows) != base_rows + len(windows):
        raise ValueError(
            "Training CSV row count does not match base training view and projection."
        )
    video_path = _nonempty_text(
        projection["training_video_path"], "training_projection training_video_path"
    )
    match_id = _nonempty_text(
        projection["review_match_id"], "training_projection review_match_id"
    )
    if not windows:
        return
    projected_rows = training_rows[base_rows:]
    fieldnames = tuple(projected_rows[0])
    if not set(_CANONICAL_COLUMNS).issubset(fieldnames):
        raise ValueError("Training CSV is missing canonical fields.")
    expected_rows = [
        _authority_training_row(window, fieldnames, video_path, match_id)
        for window in windows
    ]
    if projected_rows != expected_rows:
        raise ValueError("Training CSV does not match authority training_projection.")


def _validate_base_training_view(
    value: object,
    training_rows: list[dict[str, str]],
    training_fields: tuple[str, ...],
) -> int:
    if not isinstance(value, dict):
        raise ValueError("Authority base training view must be an object.")  # noqa: TRY004
    _exact_fields(
        value,
        ("fieldnames", "data_rows", "content_sha256"),
        "base training view",
    )
    fieldnames = value["fieldnames"]
    if (
        not isinstance(fieldnames, list)
        or not fieldnames
        or any(not isinstance(field, str) or not field for field in fieldnames)
        or len(fieldnames) != len(set(fieldnames))
        or not set(_CANONICAL_COLUMNS).issubset(fieldnames)
    ):
        raise ValueError("Authority base training fieldnames are invalid.")
    if fieldnames != list(training_fields):
        raise ValueError("Training CSV fields do not match authority base training view.")
    data_rows = value["data_rows"]
    if type(data_rows) is not int or data_rows < 0 or data_rows > len(training_rows):
        raise ValueError("Authority base training data_rows must be a valid nonnegative integer.")
    content_sha256 = _hash(
        value["content_sha256"], "base training view content_sha256"
    )
    semantic = {
        "fieldnames": fieldnames,
        "rows": training_rows[:data_rows],
    }
    if hashlib.sha256(_canonical_json_bytes(semantic)).hexdigest() != content_sha256:
        raise ValueError("Training CSV base training prefix does not match authority.")
    return data_rows


def _authority_training_row(
    window: object,
    fieldnames: Sequence[str],
    video_path: str,
    match_id: str,
) -> dict[str, str]:
    if not isinstance(window, dict):
        raise ValueError("Authority training_projection window must be an object.")  # noqa: TRY004
    expected_fields = (
        "source_ref", "clip_id", "start_seconds", "end_seconds", "training_label",
        "review_label", "team_side", "crop", "player_number", "generated",
        "window_index", "source_top1_action", "source_top1_confidence", "note",
    )
    _exact_fields(window, expected_fields, "training_projection window")
    crop = window["crop"]
    if not isinstance(crop, list) or len(crop) != 4:
        raise ValueError("Authority training_projection crop must contain four values.")
    row: dict[str, object] = {field: "" for field in fieldnames}
    row.update({
        "video_path": video_path,
        "start_seconds": window["start_seconds"],
        "end_seconds": window["end_seconds"],
        "label": window["training_label"],
        "team_side": window["team_side"],
        "player_number": window["player_number"],
        "split": "train",
        "crop_x1": crop[0],
        "crop_y1": crop[1],
        "crop_x2": crop[2],
        "crop_y2": crop[3],
        "match_id": match_id,
        "review_status": "reviewed",
        "notes": _authority_training_note(window),
    })
    return _csv_text_row(fieldnames, row)


def _authority_training_note(window: dict[str, Any]) -> str:
    if window["generated"] is True:
        return (
            f"Active review hard negative source_ref={window['source_ref']}; "
            f"model_top1={window['source_top1_action']} "
            f"({window['source_top1_confidence']})."
        )
    note = (
        f"Evidence-aware active review source_ref={window['source_ref']}; "
        f"review_label={window['review_label']}."
    )
    return f"{note} Reviewer note: {window['note']}" if window["note"] else note


def _validate_summary(
    authority: dict[str, Any], training_rows: int
) -> dict[str, Any]:
    summary = authority["summary"]
    if not isinstance(summary, dict):
        raise ValueError("Authority summary must be an object.")  # noqa: TRY004
    _exact_fields(summary, _SUMMARY_FIELDS, "summary")
    projection = authority["training_projection"]
    if not isinstance(projection, dict):
        raise ValueError("Authority training_projection must be an object.")  # noqa: TRY004
    human = projection.get("human_windows")
    generated = projection.get("generated_background_windows")
    if not isinstance(human, list) or not isinstance(generated, list):
        raise ValueError("Authority training_projection windows must be arrays.")  # noqa: TRY004
    positive_count = sum(
        isinstance(window, dict) and window.get("training_label") != "background"
        for window in human
    )
    if projection.get("positive_training_count") != positive_count:
        raise ValueError("Authority training_projection positive count is invalid.")
    affected_refs: set[str] = set()
    for field in ("occlusion_events", "off_camera_events"):
        events = authority[field]
        if not isinstance(events, list):
            raise ValueError(f"Authority {field} must be an array.")  # noqa: TRY004
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("related_action_refs"), list):
                raise ValueError(  # noqa: TRY004
                    "Authority visibility related_action_refs must be an array."
                )
            if any(not isinstance(action_ref, str) for action_ref in event["related_action_refs"]):
                raise ValueError("Authority visibility action refs must be text.")
            affected_refs.update(event["related_action_refs"])
    expected = {
        "action_observations": _array_length(authority, "action_observations"),
        "outcome_observations": _array_length(authority, "outcome_observations"),
        "occlusion_events": _array_length(authority, "occlusion_events"),
        "off_camera_events": _array_length(authority, "off_camera_events"),
        "affected_action_count": len(affected_refs),
        "action_participants": _array_length(authority, "action_participants"),
        "positive_training_count": positive_count,
        "generated_background_count": len(generated),
        "training_rows": training_rows,
    }
    if any(type(count) is not int or count < 0 for count in summary.values()):
        raise ValueError("Authority summary must contain nonnegative integers.")
    if summary != expected:
        raise ValueError("Authority summary does not match authority entities.")
    return summary


def _authority_observation_rows(
    authority: dict[str, Any],
    common: tuple[str, str, str, str],
    decisions: dict[str, str],
) -> list[dict[str, str]]:
    common_row = dict(zip(_OBSERVATION_FIELDS[:4], common, strict=True))
    actions = authority["action_observations"]
    outcomes = authority["outcome_observations"]
    if not isinstance(actions, list) or not isinstance(outcomes, list):
        raise ValueError("Authority observations must be arrays.")  # noqa: TRY004
    rows: list[dict[str, object]] = []
    for action in actions:
        if not isinstance(action, dict) or action.get("action_ref") not in decisions:
            raise ValueError("Authority action observation has no training decision.")
        rows.append({
            **common_row,
            "observation_type": "action",
            "observation_ref": action.get("action_ref"),
            "action_ref": action.get("action_ref"),
            "clip_id": action.get("clip_id"),
            "source_action_slot": action.get("source_action_slot"),
            "review_label": action.get("review_label"),
            "relative_start_seconds": action.get("relative_start_seconds"),
            "relative_end_seconds": action.get("relative_end_seconds"),
            "start_seconds": action.get("start_seconds"),
            "end_seconds": action.get("end_seconds"),
            "team_side": action.get("team_side"),
            "visibility": action.get("visibility"),
            "evidence_basis": action.get("evidence_basis"),
            "training_decision": decisions[action["action_ref"]],
            "outcome": "",
            "result_type": "",
            "status": "",
            "related_action_refs_json": "[]",
            "note": action.get("note"),
        })
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            raise ValueError("Authority outcome observation must be an object.")  # noqa: TRY004
        rows.append({
            **common_row,
            "observation_type": "outcome",
            "observation_ref": outcome.get("outcome_ref"),
            "action_ref": "",
            "clip_id": "",
            "source_action_slot": "",
            "review_label": "",
            "relative_start_seconds": "",
            "relative_end_seconds": "",
            "start_seconds": "",
            "end_seconds": "",
            "team_side": "",
            "visibility": "",
            "evidence_basis": outcome.get("evidence_basis"),
            "training_decision": "",
            "outcome": outcome.get("outcome"),
            "result_type": outcome.get("result_type"),
            "status": outcome.get("status"),
            "related_action_refs_json": _compact_json(outcome.get("related_action_refs")),
            "note": outcome.get("note"),
        })
    rows.sort(key=lambda row: (
        float(row["start_seconds"])
        if row["start_seconds"] not in {"", None}
        else float("inf"),
        str(row["observation_type"]),
        str(row["observation_ref"]),
    ))
    return [_csv_text_row(_OBSERVATION_FIELDS, row) for row in rows]


def _authority_visibility_rows(
    authority: dict[str, Any], common: tuple[str, str, str, str]
) -> list[dict[str, str]]:
    common_row = dict(zip(_VISIBILITY_FIELDS[:4], common, strict=True))
    rows: list[dict[str, object]] = []
    for field in ("occlusion_events", "off_camera_events"):
        events = authority[field]
        if not isinstance(events, list):
            raise ValueError(f"Authority {field} must be an array.")  # noqa: TRY004
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("Authority visibility event must be an object.")  # noqa: TRY004
            rows.append({
                **common_row,
                "event_kind": event.get("event_kind"),
                "event_ref": event.get("event_ref"),
                "team_side": event.get("team_side"),
                "start_seconds": event.get("start_seconds"),
                "end_seconds": event.get("end_seconds"),
                "duration_seconds": event.get("duration_seconds"),
                "interval_scope": event.get("interval_scope"),
                "related_action_refs_json": _compact_json(event.get("related_action_refs")),
                "source_refs_json": _compact_json(event.get("source_refs")),
                "note": event.get("note"),
            })
    rows.sort(key=lambda row: (
        float(row["start_seconds"]), str(row["event_kind"]), str(row["event_ref"])
    ))
    return [_csv_text_row(_VISIBILITY_FIELDS, row) for row in rows]


def _authority_participant_rows(
    authority: dict[str, Any], common: tuple[str, str, str, str]
) -> list[dict[str, str]]:
    common_row = dict(zip(_PARTICIPANT_FIELDS[:4], common, strict=True))
    participants = authority["action_participants"]
    if not isinstance(participants, list):
        raise ValueError("Authority action_participants must be an array.")  # noqa: TRY004
    rows: list[dict[str, object]] = []
    for participant in participants:
        if not isinstance(participant, dict):
            raise ValueError("Authority participant must be an object.")  # noqa: TRY004
        rows.append({
            **common_row,
            "action_ref": participant.get("action_ref"),
            "track_id": participant.get("track_id"),
            "identity_ref": participant.get("identity_ref"),
            "player_number": participant.get("player_number"),
            "participation": participant.get("participation"),
            "touch_status": participant.get("touch_status"),
            "assignment_status": participant.get("assignment_status"),
            "assignment_confidence": participant.get("assignment_confidence"),
            "evidence_json": _compact_json(participant.get("evidence")),
        })
    rows.sort(key=lambda row: (
        str(row["action_ref"]), str(row["track_id"] or ""),
        str(row["identity_ref"] or ""),
    ))
    return [_csv_text_row(_PARTICIPANT_FIELDS, row) for row in rows]


def _csv_text_row(
    fieldnames: Sequence[str], row: Mapping[str, object]
) -> dict[str, str]:
    return {field: _csv_cell(row.get(field)) for field in fieldnames}


def _array_length(value: dict[str, Any], field: str) -> int:
    items = value[field]
    if not isinstance(items, list):
        raise ValueError(f"Authority {field} must be an array.")  # noqa: TRY004
    return len(items)


def _exact_fields(value: dict[str, Any], fields: Sequence[str], description: str) -> None:
    if set(value) != set(fields):
        raise ValueError(f"{description} must contain exact fields.")


def _format_version(
    value: dict[str, Any], expected_format: str, expected_version: int, description: str
) -> None:
    if value["format"] != expected_format or type(value["format_version"]) is not int or value["format_version"] != expected_version:
        raise ValueError(f"{description} format or format_version is invalid.")


def _hash(value: object, description: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{description} must be a lowercase SHA-256 digest.")
    return value


def _repo_path_text(value: object, description: str) -> str:
    text = _nonempty_text(value, description)
    posix = PurePosixPath(text)
    windows = PureWindowsPath(text)
    if (
        windows.drive
        or posix.is_absolute()
        or "\\" in text
        or text.startswith("/")
        or ".." in posix.parts
    ):
        raise ValueError(f"{description} must be a repository-relative POSIX path.")
    return posix.as_posix()


def _finite_number(value: object, description: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(value):
        raise ValueError(f"{description} must be a finite number.")
    return float(value)


def _optional_finite_number(value: object, description: str) -> float | None:
    if value is None:
        return None
    return _finite_number(value, description)


def _whole_number(value: object, description: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{description} must be an integer.")
    return value


def _nonnegative_integer(value: object, description: str) -> int:
    result = _whole_number(value, description)
    if result < 0:
        raise ValueError(f"{description} must be nonnegative.")
    return result


def _enum_text(value: object, choices: set[str], description: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{description} is invalid.")
    return value


def _text_or_empty(value: object, description: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{description} must be text.")  # noqa: TRY004
    return value


def _validate_crop(
    value: object, width: int, height: int, description: str
) -> list[int]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{description} must contain four integer coordinates.")
    coordinates = [_whole_number(item, description) for item in value]
    x1, y1, x2, y2 = coordinates
    if x1 < 0 or y1 < 0 or x1 >= x2 or y1 >= y2 or x2 > width or y2 > height:
        raise ValueError(f"{description} is outside video bounds.")
    return coordinates


def _is_unit_interval(value: object) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(value)
        and 0 <= value <= 1
    )


def _nonempty_text(value: object, description: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{description} must be nonempty text.")
    return value


def _render_training_csv(
    base_fieldnames: Sequence[str],
    base_rows: Sequence[Mapping[str, str | None]],
    projection: TrainingProjection,
    settings: BundleSettings,
) -> tuple[bytes, int, dict[str, object]]:
    fieldnames = list(base_fieldnames)
    for column in _CANONICAL_COLUMNS:
        if column not in fieldnames:
            fieldnames.append(column)
    rows = [dict(row) for row in base_rows]
    for row in rows:
        if not (row.get("match_id") or "").strip():
            row["match_id"] = settings.legacy_base_match_id
    base_text_rows = [_csv_text_row(fieldnames, row) for row in rows]
    base_training_view = {
        "fieldnames": fieldnames,
        "data_rows": len(base_text_rows),
        "content_sha256": hashlib.sha256(_canonical_json_bytes({
            "fieldnames": fieldnames,
            "rows": base_text_rows,
        })).hexdigest(),
    }
    for window in projection.human_windows + projection.generated_background_windows:
        x1, y1, x2, y2 = window.crop
        row = {field: "" for field in fieldnames}
        row.update({
            "video_path": settings.training_video_path,
            "start_seconds": str(window.start_seconds),
            "end_seconds": str(window.end_seconds),
            "label": window.training_label,
            "team_side": window.team_side,
            "player_number": window.player_number or "",
            "split": "train",
            "crop_x1": str(x1),
            "crop_y1": str(y1),
            "crop_x2": str(x2),
            "crop_y2": str(y2),
            "match_id": settings.review_match_id,
            "review_status": "reviewed",
            "notes": _training_note(window),
        })
        rows.append(row)
    return _csv_bytes(fieldnames, rows), len(rows), base_training_view


def _training_note(window: Any) -> str:
    if window.generated:
        return (
            f"Active review hard negative source_ref={window.source_ref}; "
            f"model_top1={window.source_top1_action} ({window.source_top1_confidence})."
        )
    note = (
        f"Evidence-aware active review source_ref={window.source_ref}; "
        f"review_label={window.review_label}."
    )
    return f"{note} Reviewer note: {window.note}" if window.note else note


def _observation_rows(
    review: ValidatedReviewInput,
    observations: ObservationSet,
    decisions: dict[str, Any],
    settings: BundleSettings,
) -> list[dict[str, object]]:
    common = _view_common(review, settings)
    rows: list[dict[str, object]] = []
    for action in observations.actions:
        rows.append({
            **common,
            "observation_type": "action",
            "observation_ref": action.action_ref,
            "action_ref": action.action_ref,
            "clip_id": action.clip_id,
            "source_action_slot": action.source_action_slot,
            "review_label": action.review_label,
            "relative_start_seconds": action.relative_start_seconds,
            "relative_end_seconds": action.relative_end_seconds,
            "start_seconds": action.start_seconds,
            "end_seconds": action.end_seconds,
            "team_side": action.team_side,
            "visibility": action.visibility,
            "evidence_basis": action.evidence_basis,
            "training_decision": decisions[action.action_ref].decision,
            "outcome": "",
            "result_type": "",
            "status": "",
            "related_action_refs_json": "[]",
            "note": action.note,
        })
    for outcome in observations.outcomes:
        rows.append({
            **common,
            "observation_type": "outcome",
            "observation_ref": outcome.outcome_ref,
            "action_ref": "",
            "clip_id": "",
            "source_action_slot": "",
            "review_label": "",
            "relative_start_seconds": "",
            "relative_end_seconds": "",
            "start_seconds": "",
            "end_seconds": "",
            "team_side": "",
            "visibility": "",
            "evidence_basis": outcome.evidence_basis,
            "training_decision": "",
            "outcome": outcome.outcome,
            "result_type": outcome.result_type,
            "status": outcome.status,
            "related_action_refs_json": _compact_json(outcome.related_action_refs),
            "note": outcome.note,
        })
    return sorted(
        rows,
        key=lambda row: (
            float(row["start_seconds"])
            if row["start_seconds"] not in {"", None}
            else float("inf"),
            str(row["observation_type"]),
            str(row["observation_ref"]),
        ),
    )


def _visibility_rows(
    review: ValidatedReviewInput,
    observations: ObservationSet,
    settings: BundleSettings,
) -> list[dict[str, object]]:
    common = _view_common(review, settings)
    rows = [
        {
            **common,
            "event_kind": event.event_kind,
            "event_ref": event.event_ref,
            "team_side": event.team_side,
            "start_seconds": event.start_seconds,
            "end_seconds": event.end_seconds,
            "duration_seconds": event.duration_seconds,
            "interval_scope": event.interval_scope,
            "related_action_refs_json": _compact_json(event.related_action_refs),
            "source_refs_json": _compact_json(event.source_refs),
            "note": event.note,
        }
        for event in observations.occlusion_events + observations.off_camera_events
    ]
    return sorted(
        rows,
        key=lambda row: (float(row["start_seconds"]), str(row["event_kind"]), str(row["event_ref"])),
    )


def _participant_rows(
    review: ValidatedReviewInput,
    observations: ObservationSet,
    settings: BundleSettings,
) -> list[dict[str, object]]:
    common = _view_common(review, settings)
    rows = [
        {
            **common,
            "action_ref": participant.action_ref,
            "track_id": participant.track_id,
            "identity_ref": participant.identity_ref,
            "player_number": participant.player_number,
            "participation": participant.participation,
            "touch_status": participant.touch_status,
            "assignment_status": participant.assignment_status,
            "assignment_confidence": participant.assignment_confidence,
            "evidence_json": _compact_json(participant.evidence),
        }
        for participant in observations.participants
    ]
    return sorted(rows, key=lambda row: (
        str(row["action_ref"]), str(row["track_id"] or ""), str(row["identity_ref"] or "")
    ))


def _view_common(
    review: ValidatedReviewInput, settings: BundleSettings
) -> dict[str, object]:
    return {
        "result_set_id": review.result_set_id,
        "selection_sha256": review.source_hashes.selection_sha256,
        "workbook_sha256": review.source_hashes.workbook_sha256,
        "generator_version": settings.generator_version,
    }


def _sources(
    review: ValidatedReviewInput,
    base_manifest_binding: ArtifactBinding,
    settings: BundleSettings,
) -> dict[str, object]:
    return {
        "selection": _plain(review.selection_binding),
        "review_input": _plain(review.review_input_binding),
        "workbook": _plain(review.workbook_binding),
        "evidence_overrides": _plain(review.evidence_overrides_binding),
        "merged_candidates": _plain(review.merged_candidates_binding),
        "base_manifest": _plain(base_manifest_binding),
        "video": _plain(review.video_binding),
        "verification": {"source_video_file_checked": settings.source_video_file_checked},
    }


def _training_projection(
    projection: TrainingProjection,
    settings: BundleSettings,
    base_training_view: dict[str, object],
) -> dict[str, object]:
    return {
        "decisions": [
            {"action_ref": action_ref, **_plain(decision)}
            for action_ref, decision in projection.decisions
        ],
        "human_windows": _plain(projection.human_windows),
        "generated_background_windows": _plain(projection.generated_background_windows),
        "positive_training_count": projection.positive_training_count,
        "requested_background_cap": projection.requested_background_cap,
        "effective_background_cap": projection.effective_background_cap,
        "training_video_path": settings.training_video_path,
        "review_match_id": settings.review_match_id,
        "base_training_view": base_training_view,
    }


def _summary(
    observations: ObservationSet,
    projection: TrainingProjection,
    training_rows: int,
) -> dict[str, object]:
    return {
        "action_observations": len(observations.actions),
        "outcome_observations": len(observations.outcomes),
        "occlusion_events": len(observations.occlusion_events),
        "off_camera_events": len(observations.off_camera_events),
        "affected_action_count": observations.affected_action_count,
        "action_participants": len(observations.participants),
        "positive_training_count": projection.positive_training_count,
        "generated_background_count": len(projection.generated_background_windows),
        "training_rows": training_rows,
    }


def _binding(filename: str, raw: bytes) -> dict[str, str]:
    return {"path": filename, "sha256": hashlib.sha256(raw).hexdigest()}


def _artifact_entry(
    filename: str,
    raw: bytes,
    *,
    media_type: str = "text/csv",
    encoding: str = "utf-8-sig",
    line_ending: str = "crlf",
    data_rows: int | None,
    entity_counts: dict[str, int] | None = None,
) -> dict[str, object]:
    return {
        "path": filename,
        "media_type": media_type,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "encoding": encoding,
        "line_ending": line_ending,
        "data_rows": data_rows,
        "entity_counts": entity_counts,
    }


def _csv_bytes(
    fieldnames: Sequence[str], rows: Sequence[Mapping[str, object]]
) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=fieldnames, lineterminator="\r\n", extrasaction="ignore"
    )
    writer.writeheader()
    writer.writerows(
        {
            key: _csv_cell(value)
            for key, value in row.items()
        }
        for row in rows
    )
    return buffer.getvalue().encode("utf-8-sig")


def _csv_cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _presentation_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _compact_json(value: object) -> str:
    return json.dumps(_plain(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _plain(asdict(value))
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value
