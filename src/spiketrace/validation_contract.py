from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .domain import VideoMetadata
from .errors import ValidationError, VideoError
from .video import inspect_video


def sha256_file(path: str | Path) -> str:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValidationError(f"Video does not exist: {source}")
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def write_new_bytes(path: Path, payload: bytes, *, error_type: type[Exception] = ValidationError) -> None:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise error_type(f"Destination already exists: {destination}")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise error_type(f"Destination already exists: {destination}") from exc
    except OSError as exc:
        raise error_type(f"Could not publish {destination}: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class ValidationVideoBinding:
    match_id: str
    video_path: Path
    video_root: Path
    repo_video_path: str
    sha256: str
    metadata: VideoMetadata


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValidationError(f"Video path is outside video_root: {path}") from exc


def freeze_video_binding(video_path: str | Path, *, match_id: str, expected_sha256: str, repo_root: str | Path, video_root: str | Path | None = None, expected_metadata: Mapping[str, int | float] | None = None) -> ValidationVideoBinding:
    if not isinstance(match_id, str) or not match_id.strip() or any(ch.isspace() for ch in match_id):
        raise ValidationError("match_id must be non-empty and contain no whitespace")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in expected_sha256):
        raise ValidationError("expected SHA-256 must be a 64-character hexadecimal hash")
    repo = Path(repo_root).expanduser().resolve()
    root = Path(video_root).expanduser().resolve() if video_root else repo
    source = Path(video_path).expanduser().resolve()
    if not source.is_file():
        raise ValidationError(f"Video does not exist: {source}")
    relative = _relative(source, root)
    actual_hash = sha256_file(source)
    if actual_hash.lower() != expected_sha256.lower():
        raise ValidationError("Video SHA-256 does not match expected SHA-256")
    try:
        metadata = inspect_video(source)
    except VideoError as exc:
        raise ValidationError(str(exc)) from exc
    if expected_metadata:
        for key, expected in expected_metadata.items():
            actual = getattr(metadata, key)
            if isinstance(expected, float):
                if abs(float(actual) - expected) > 1e-6:
                    raise ValidationError(f"Video metadata mismatch for {key}")
            elif actual != expected:
                raise ValidationError(f"Video metadata mismatch for {key}")
    return ValidationVideoBinding(match_id=match_id, video_path=source, video_root=root, repo_video_path=relative, sha256=actual_hash, metadata=metadata)


def write_video_binding(path: str | Path, binding: ValidationVideoBinding, *, repo_root: str | Path) -> Path:
    relative = Path(binding.repo_video_path)
    if relative.is_absolute() or relative.as_posix() != binding.repo_video_path or ".." in relative.parts:
        raise ValidationError("Binding video_path must be a relative POSIX path")
    try:
        resolved = (binding.video_root / relative).resolve()
        resolved.relative_to(binding.video_root.resolve())
    except ValueError as exc:
        raise ValidationError("Binding video_path is outside video_root") from exc
    payload = {"format_version": 1, "match_id": binding.match_id, "video_path": binding.repo_video_path, "sha256": binding.sha256, "metadata": {"fps": binding.metadata.fps, "frame_count": binding.metadata.frame_count, "width": binding.metadata.width, "height": binding.metadata.height, "duration_seconds": binding.metadata.duration_seconds}}
    destination = Path(path).expanduser().resolve()
    write_new_bytes(destination, canonical_json_bytes(payload), error_type=ValidationError)
    return destination


def load_video_binding(path: str | Path, *, repo_root: str | Path, video_root: str | Path | None = None) -> ValidationVideoBinding:
    source = Path(path).expanduser().resolve()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        root = Path(video_root).expanduser().resolve() if video_root else Path(repo_root).expanduser().resolve()
        video = (root / data["video_path"]).resolve()
        return freeze_video_binding(video, match_id=data["match_id"], expected_sha256=data["sha256"], repo_root=repo_root, video_root=root, expected_metadata=data.get("metadata"))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Invalid validation binding: {source}") from exc


def assert_no_content_overlap(binding: ValidationVideoBinding, *, manifest_paths: Sequence[str | Path], selection_paths: Sequence[str | Path], repo_root: str | Path, video_root: str | Path | None = None) -> None:
    validation_path = binding.video_path.resolve()
    root = Path(video_root).expanduser().resolve() if video_root else binding.video_root
    for manifest_path in manifest_paths:
        path = Path(manifest_path).expanduser().resolve()
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    split = (row.get("split") or "").strip().lower()
                    if split not in {"train", "val", "test"}:
                        raise ValidationError(f"Manifest row has invalid split: {split or '<empty>'}")
                    if row.get("match_id", "").strip() == binding.match_id:
                        raise ValidationError("Manifest overlaps validation match_id")
                    if row.get("video_sha256", "").strip().lower() == binding.sha256.lower():
                        raise ValidationError("Manifest overlaps validation SHA-256")
                    raw = (row.get("video_path") or "").strip()
                    if raw:
                        candidate = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
                        if candidate == validation_path or (candidate.is_file() and sha256_file(candidate) == binding.sha256):
                            raise ValidationError("Manifest overlaps validation video")
        except ValidationError:
            raise
        except OSError as exc:
            raise ValidationError(f"Could not read explicit source: {path}") from exc
    for selection_path in selection_paths:
        path = Path(selection_path).expanduser().resolve()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(f"Could not read explicit source: {path}") from exc
        def walk(value: object) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "match_id" and item == binding.match_id:
                        raise ValidationError("Selection overlaps validation match_id")
                    if key in {"video_sha256", "sha256"} and isinstance(item, str) and item.lower() == binding.sha256.lower():
                        raise ValidationError("Selection overlaps validation SHA-256")
                    if key == "video" and isinstance(item, dict) and isinstance(item.get("path"), str):
                        candidate = Path(item["path"]).expanduser()
                        candidate = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
                        if not candidate.is_file():
                            raise ValidationError(f"Selection video source is unreadable: {candidate}")
                        if candidate == validation_path or sha256_file(candidate) == binding.sha256:
                            raise ValidationError("Selection overlaps validation video")
                    if key == "path" and isinstance(item, str):
                        candidate = Path(item).expanduser()
                        candidate = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
                        if candidate == validation_path or (candidate.is_file() and sha256_file(candidate) == binding.sha256):
                            raise ValidationError("Selection overlaps validation video")
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(data)
