from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from . import _active_learning_selection_artifact as _selection_artifact
from . import active_learning_selection as _selection_api
from .errors import ActiveLearningError
from .video import write_proxy_video


def build_review_proxies(
    selection_path: str | Path,
    output_dir: str | Path,
    *,
    repo_root: str | Path,
    output_fps: float = 15.0,
    max_width: int = 960,
    codec: str = "mp4v",
) -> dict[str, object]:
    root = Path(repo_root).expanduser().resolve()
    selection_file = _resolved_path(selection_path, root, "selection JSON")
    output = _resolved_path(output_dir, root, "proxy batch output")
    if output.exists():
        raise ActiveLearningError(f"Output directory already exists: {output}")

    staging = output.parent / f".{output.name}.tmp-{os.getpid()}"
    if staging.exists():
        raise ActiveLearningError(f"Staging directory already exists: {staging}")

    snapshot = _load_selection_snapshot(selection_file, root)
    selection = snapshot["selection"]
    selection_sha256 = snapshot["sha256"]
    normalized_selection_path = _normalized_path(selection_file, root, "selection JSON")
    video = _mapping(selection["video"], "selection video")
    video_path = _resolved_path(video["path"], root, "source video")
    clips = list(selection["clips"])
    if len(clips) != 40:
        raise ActiveLearningError("Selection must contain exactly 40 clips.")
    planned_clips = _plan_clip_outputs(clips, staging)

    clip_metadata: list[dict[str, object]] = []
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        (staging / "clips").mkdir(parents=True)
        for clip, relative_proxy_path, proxy_path in planned_clips:
            clip_id = _safe_clip_id(clip["clip_id"])
            _revalidate_selection_snapshot(
                selection_file,
                root,
                selection_sha256=selection_sha256,
            )
            metadata = write_proxy_video(
                video_path,
                proxy_path,
                _number(clip["start_seconds"], "clip start_seconds"),
                _number(clip["end_seconds"], "clip end_seconds"),
                output_fps=output_fps,
                max_width=max_width,
                codec=codec,
            )
            if not proxy_path.is_file():
                raise ActiveLearningError(f"Proxy video was not written: {proxy_path}")
            clip_metadata.append(
                {
                    "clip_id": clip_id,
                    "ordinal": clip["ordinal"],
                    "path": relative_proxy_path,
                    "sha256": _sha256_file(proxy_path),
                    "source_start_seconds": clip["start_seconds"],
                    "source_end_seconds": clip["end_seconds"],
                    "frame_count": metadata.frame_count,
                    "fps": metadata.fps,
                    "width": metadata.width,
                    "height": metadata.height,
                    "duration_seconds": metadata.duration_seconds,
                }
            )

        _validate_proxy_entries(staging, clip_metadata)
        manifest = {
            "format_version": 1,
            "batch_id": selection["batch_id"],
            "round_id": selection["round_id"],
            "selection": normalized_selection_path,
            "selection_sha256": selection_sha256,
            "video": {
                "path": video["path"],
                "sha256": video["sha256"],
            },
            "settings": {
                "codec": codec,
                "fps": output_fps,
                "max_width": max_width,
                "audio": False,
            },
            "clips": clip_metadata,
        }
        _write_json_atomic(staging / "proxy-manifest.json", manifest)
        staging.rename(output)
        staging = None
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)

    return {
        "batch_id": selection["batch_id"],
        "round_id": selection["round_id"],
        "clip_count": len(clip_metadata),
        "output_dir": _normalized_path(output, root, "proxy batch output"),
        "manifest": _normalized_path(
            output / "proxy-manifest.json", root, "proxy manifest"
        ),
    }


def _load_selection_snapshot(selection_file: Path, repo_root: Path) -> dict[str, Any]:
    try:
        raw = selection_file.read_bytes()
    except OSError as exc:
        raise ActiveLearningError(f"Cannot read selection JSON: {selection_file}") from exc
    selection = _selection_artifact._validate_selection_payload(
        _selection_artifact._load_json_bytes(raw, description="selection JSON"),
        repo_root,
        require_video=True,
        verifier=_selection_api.verify_dual_crop_review,
    )
    return {"selection": selection, "sha256": _sha256_bytes(raw)}


def _revalidate_selection_snapshot(
    selection_file: Path,
    repo_root: Path,
    *,
    selection_sha256: str,
) -> None:
    current = _load_selection_snapshot(selection_file, repo_root)
    if current["sha256"] != selection_sha256:
        raise ActiveLearningError("Selection SHA-256 changed during proxy build.")


def _plan_clip_outputs(
    clips: list[object], staging: Path
) -> list[tuple[dict[str, Any], str, Path]]:
    clip_directory = staging / "clips"
    planned: list[tuple[dict[str, Any], str, Path]] = []
    for raw_clip in clips:
        clip = _mapping(raw_clip, "selection clip")
        clip_id = _safe_clip_id(clip["clip_id"])
        relative_proxy_path = f"clips/{clip_id}.mp4"
        proxy_path = (staging / relative_proxy_path).resolve()
        try:
            proxy_path.relative_to(clip_directory.resolve())
        except ValueError as exc:
            raise ActiveLearningError(
                f"clip_id would write outside the proxy staging directory: {clip_id}"
            ) from exc
        planned.append((clip, relative_proxy_path, proxy_path))
    return planned


def _safe_clip_id(value: object) -> str:
    clip_id = _nonempty_string(value, "clip_id")
    posix = PurePosixPath(clip_id)
    windows = PureWindowsPath(clip_id)
    if (
        clip_id in (".", "..")
        or posix.is_absolute()
        or windows.drive
        or "/" in clip_id
        or "\\" in clip_id
        or len(posix.parts) != 1
    ):
        raise ActiveLearningError(
            f"clip_id must be a filename-safe identifier: {clip_id}"
        )
    return clip_id


def _validate_proxy_entries(
    staging: Path, clip_metadata: list[dict[str, object]]
) -> None:
    if len(clip_metadata) != 40:
        raise ActiveLearningError("Proxy batch must contain exactly 40 clips.")
    for clip in clip_metadata:
        path = _relative_posix_path(clip["path"], "proxy path")
        proxy_path = staging / path
        if not proxy_path.is_file():
            raise ActiveLearningError(f"Proxy video is missing: {proxy_path}")
        if _sha256_file(proxy_path) != clip["sha256"]:
            raise ActiveLearningError("Proxy SHA-256 does not match the written file.")


def _write_json_atomic(path: Path, payload: object) -> None:
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ActiveLearningError(f"Cannot hash file: {path}") from exc
    return digest.hexdigest()


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _resolved_path(value: str | Path | object, root: Path, description: str) -> Path:
    path = Path(_path_text(value, description)).expanduser()
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ActiveLearningError(
            f"{description} escapes repository root: {value}"
        ) from exc
    return resolved


def _normalized_path(path: Path, root: Path, description: str) -> str:
    return _resolved_path(path, root, description).relative_to(root).as_posix()


def _relative_posix_path(value: object, description: str) -> Path:
    text = _path_text(value, description)
    candidate = PurePosixPath(text)
    windows_candidate = PureWindowsPath(text)
    if (
        windows_candidate.drive
        or candidate.is_absolute()
        or "\\" in text
        or text.startswith("/")
        or ".." in candidate.parts
    ):
        raise ActiveLearningError(
            f"{description} must be a repository-relative POSIX path."
        )
    return Path(candidate.as_posix())


def _path_text(value: object, description: str) -> str:
    if isinstance(value, Path):
        return str(value)
    return _nonempty_string(value, description)


def _mapping(value: object, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ActiveLearningError(f"{description} must be an object.")
    return value


def _nonempty_string(value: object, description: str) -> str:
    if not isinstance(value, str) or not value:
        raise ActiveLearningError(f"{description} must be a non-empty string.")
    return value


def _number(value: object, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ActiveLearningError(f"{description} must be numeric.")
    return float(value)
