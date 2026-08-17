from __future__ import annotations

import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from math import floor, isfinite
from pathlib import Path

import numpy as np

from .domain import VideoMetadata
from .errors import VideoError


@dataclass
class _PendingClip:
    times: tuple[float, float]
    frame_indices: tuple[int, ...]
    frames: list[np.ndarray | None]
    next_frame_slot: int = 0


def clip_sample_frame_indices(
    start_seconds: float,
    end_seconds: float,
    *,
    num_frames: int,
    fps: float,
    frame_count: int,
) -> tuple[int, ...]:
    if (
        not isfinite(start_seconds)
        or not isfinite(end_seconds)
        or not isfinite(fps)
        or start_seconds < 0
        or end_seconds <= start_seconds
        or num_frames <= 0
        or fps <= 0
        or frame_count <= 0
    ):
        raise VideoError("Sampling parameters are invalid.")
    duration = end_seconds - start_seconds
    return tuple(
        min(
            frame_count - 1,
            max(
                0,
                floor(
                    (start_seconds + (index + 0.5) * duration / num_frames) * fps
                    + 0.5
                ),
            ),
        )
        for index in range(num_frames)
    )


def _cv2():
    try:
        import cv2
    except ImportError as exc:
        raise VideoError(
            "OpenCV is required for video decoding. Install the project dependencies first."
        ) from exc
    return cv2


def inspect_video(video_path: str | Path) -> VideoMetadata:
    cv2 = _cv2()
    path = Path(video_path).expanduser().resolve()
    if not path.is_file():
        raise VideoError(f"Video does not exist: {path}")

    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise VideoError(f"OpenCV could not open video: {path}")
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if fps <= 0 or frame_count <= 0 or width <= 0 or height <= 0:
            raise VideoError(f"Video metadata is invalid or incomplete: {path}")
        return VideoMetadata(
            path=path,
            fps=fps,
            frame_count=frame_count,
            width=width,
            height=height,
            duration_seconds=frame_count / fps,
        )
    finally:
        capture.release()


def write_proxy_video(
    video_path: str | Path,
    output_path: str | Path,
    start_seconds: float,
    end_seconds: float,
    *,
    output_fps: float = 15.0,
    max_width: int = 960,
    codec: str = "mp4v",
) -> VideoMetadata:
    """Write a sampled, resized, silent MP4 clip and return its metadata."""
    try:
        start_seconds = float(start_seconds)
        end_seconds = float(end_seconds)
        output_fps = float(output_fps)
    except (TypeError, ValueError) as exc:
        raise VideoError("Proxy video parameters must be numeric.") from exc
    if (
        not isfinite(start_seconds)
        or not isfinite(end_seconds)
        or start_seconds < 0
        or end_seconds <= start_seconds
    ):
        raise VideoError("Clip must satisfy 0 <= start_seconds < end_seconds.")
    if not isfinite(output_fps) or output_fps <= 0:
        raise VideoError("output_fps must be finite and positive.")
    if isinstance(max_width, bool) or not isinstance(max_width, int) or max_width < 2:
        raise VideoError("max_width must be an integer of at least 2.")
    if not isinstance(codec, str) or len(codec) != 4 or not codec.isascii():
        raise VideoError("codec must contain exactly four ASCII characters.")

    source = inspect_video(video_path)
    if end_seconds > source.duration_seconds + 1e-9:
        raise VideoError("Clip end exceeds the source video duration.")

    destination = Path(output_path).expanduser().resolve()
    if destination.exists():
        raise VideoError(f"Proxy video destination already exists: {destination}")

    target_width = min(source.width, max_width)
    target_height = max(2, round(source.height * target_width / source.width))
    target_width -= target_width % 2
    target_height -= target_height % 2
    frame_total = max(1, floor((end_seconds - start_seconds) * output_fps + 0.5))
    frame_indices = clip_sample_frame_indices(
        start_seconds,
        end_seconds,
        num_frames=frame_total,
        fps=source.fps,
        frame_count=source.frame_count,
    )

    cv2 = _cv2()
    temporary_path: Path | None = None
    capture = None
    writer = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".mp4",
            prefix=f".{destination.stem}.",
            dir=destination.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

        capture = cv2.VideoCapture(str(source.path))
        if not capture.isOpened():
            raise VideoError(f"OpenCV could not open video: {source.path}")
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_indices[0])

        writer = cv2.VideoWriter(
            str(temporary_path),
            cv2.VideoWriter_fourcc(*codec),
            output_fps,
            (target_width, target_height),
        )
        if not writer.isOpened():
            raise VideoError(f"OpenCV could not create proxy video: {destination}")

        decoded_frame_index = frame_indices[0] - 1
        source_frame: np.ndarray | None = None
        for frame_index in frame_indices:
            while decoded_frame_index < frame_index:
                ok, source_frame = capture.read()
                decoded_frame_index += 1
                if not ok or source_frame is None:
                    raise VideoError(
                        f"Could not decode frame {frame_index} from {source.path}"
                    )
            resized_frame = cv2.resize(
                source_frame,
                (target_width, target_height),
                interpolation=cv2.INTER_AREA,
            )
            writer.write(resized_frame)

        writer.release()
        writer = None
        proxy = inspect_video(temporary_path)
        expected_duration = frame_total / output_fps
        if (
            proxy.frame_count != frame_total
            or abs(proxy.fps - output_fps) > 0.01
            or (proxy.width, proxy.height) != (target_width, target_height)
            or abs(proxy.duration_seconds - expected_duration) > 0.11
        ):
            raise VideoError("Written proxy video did not match the requested metadata.")

        temporary_path.replace(destination)
        temporary_path = None
        return VideoMetadata(
            path=destination,
            fps=proxy.fps,
            frame_count=proxy.frame_count,
            width=proxy.width,
            height=proxy.height,
            duration_seconds=proxy.duration_seconds,
        )
    except VideoError:
        raise
    except Exception as exc:
        raise VideoError(f"Could not write proxy video: {destination}") from exc
    finally:
        if capture is not None:
            capture.release()
        if writer is not None:
            writer.release()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def sample_video_frames(
    video_path: str | Path,
    start_seconds: float,
    end_seconds: float,
    *,
    num_frames: int,
    crop: tuple[int, int, int, int] | None = None,
) -> np.ndarray:
    """Return BGR uint8 frames with shape [T, H, W, C] at source resolution."""
    cv2 = _cv2()
    path = Path(video_path).expanduser().resolve()
    if num_frames <= 0:
        raise VideoError("num_frames must be positive.")
    if start_seconds < 0 or end_seconds <= start_seconds:
        raise VideoError("Clip must satisfy 0 <= start_seconds < end_seconds.")

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise VideoError(f"OpenCV could not open video: {path}")

    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_indices = clip_sample_frame_indices(
            start_seconds,
            end_seconds,
            num_frames=num_frames,
            fps=fps,
            frame_count=frame_count,
        )
        frames: list[np.ndarray] = []
        for frame_index in frame_indices:
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok or frame is None:
                if frames:
                    frames.append(frames[-1].copy())
                    continue
                raise VideoError(
                    f"Could not decode frame {frame_index} from {path}"
                )
            if crop is not None:
                x1, y1, x2, y2 = crop
                frame_height, frame_width = frame.shape[:2]
                if (
                    min(crop) < 0
                    or x2 <= x1
                    or y2 <= y1
                    or x2 > frame_width
                    or y2 > frame_height
                ):
                    raise VideoError(
                        f"Crop {crop} exceeds video frame {frame_width}x{frame_height}."
                    )
                frame = frame[y1:y2, x1:x2]
            frames.append(frame)
    finally:
        capture.release()

    return np.stack(frames, axis=0)


def sample_video_clip(
    video_path: str | Path,
    start_seconds: float,
    end_seconds: float,
    *,
    num_frames: int,
    image_size: int,
    crop: tuple[int, int, int, int] | None = None,
) -> np.ndarray:
    """Return an RGB uint8 array with shape [T, H, W, C]."""
    cv2 = _cv2()
    if num_frames <= 0 or image_size <= 0:
        raise VideoError("num_frames and image_size must be positive.")
    source_frames = sample_video_frames(
        video_path,
        start_seconds,
        end_seconds,
        num_frames=num_frames,
        crop=crop,
    )
    frames = [
        cv2.resize(
            cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
            (image_size, image_size),
            interpolation=cv2.INTER_AREA,
        )
        for frame in source_frames
    ]

    return np.stack(frames, axis=0)


def iter_sequential_video_clip_batches(
    video_path: str | Path,
    windows: Iterable[tuple[float, float]],
    *,
    num_frames: int,
    image_size: int,
    batch_size: int,
    crop: tuple[int, int, int, int] | None = None,
) -> Iterator[tuple[list[tuple[float, float]], np.ndarray]]:
    """Yield ordered RGB clip batches after decoding a video once in frame order.

    Each yielded value contains the source window times followed by uint8 clips
    shaped ``[B, T, H, W, C]``. Windows must have nondecreasing starts and ends.
    """
    if num_frames <= 0 or image_size <= 0:
        raise VideoError("num_frames and image_size must be positive.")
    if batch_size <= 0:
        raise VideoError("batch_size must be positive.")

    cv2 = _cv2()
    path = Path(video_path).expanduser().resolve()
    if not path.is_file():
        raise VideoError(f"Video does not exist: {path}")

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        raise VideoError(f"OpenCV could not open video: {path}")

    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if fps <= 0 or frame_count <= 0 or frame_width <= 0 or frame_height <= 0:
            raise VideoError(f"Video metadata is invalid or incomplete: {path}")
        if crop is not None:
            x1, y1, x2, y2 = crop
            if (
                min(crop) < 0
                or x2 <= x1
                or y2 <= y1
                or x2 > frame_width
                or y2 > frame_height
            ):
                raise VideoError(
                    f"Crop {crop} exceeds video frame {frame_width}x{frame_height}."
                )

        duration_seconds = frame_count / fps
        window_iterator = iter(windows)
        previous_start: float | None = None
        previous_end: float | None = None

        def next_clip() -> _PendingClip | None:
            nonlocal previous_start, previous_end
            try:
                requested_window = next(window_iterator)
            except StopIteration:
                return None
            try:
                start_seconds, end_seconds = requested_window
                start_seconds = float(start_seconds)
                end_seconds = float(end_seconds)
            except (TypeError, ValueError) as exc:
                raise VideoError("Each window must contain numeric start and end seconds.") from exc
            if (
                not isfinite(start_seconds)
                or not isfinite(end_seconds)
                or start_seconds < 0
                or end_seconds <= start_seconds
                or end_seconds > duration_seconds + 1e-9
            ):
                raise VideoError(
                    "Each window must satisfy 0 <= start_seconds < end_seconds <= video duration."
                )
            if (
                previous_start is not None
                and (start_seconds < previous_start or end_seconds < previous_end)
            ):
                raise VideoError("Windows must be ordered by nondecreasing start and end time.")
            previous_start = start_seconds
            previous_end = end_seconds
            frame_indices = clip_sample_frame_indices(
                start_seconds,
                end_seconds,
                num_frames=num_frames,
                fps=fps,
                frame_count=frame_count,
            )
            return _PendingClip(
                times=(start_seconds, end_seconds),
                frame_indices=frame_indices,
                frames=[None] * num_frames,
            )

        upcoming = next_clip()
        active_clips: list[_PendingClip] = []
        batch_times: list[tuple[float, float]] = []
        batch_clips: list[np.ndarray] = []
        decoded_frame_index = -1

        while active_clips or upcoming is not None:
            if not active_clips:
                active_clips.append(upcoming)
                upcoming = next_clip()

            next_required_index = min(
                clip.frame_indices[clip.next_frame_slot] for clip in active_clips
            )
            while (
                upcoming is not None
                and upcoming.frame_indices[0] <= next_required_index
            ):
                active_clips.append(upcoming)
                upcoming = next_clip()

            next_required_index = min(
                clip.frame_indices[clip.next_frame_slot] for clip in active_clips
            )
            while decoded_frame_index < next_required_index:
                if not capture.grab():
                    raise VideoError(
                        f"Video ended before frame {next_required_index} could be decoded: {path}"
                    )
                decoded_frame_index += 1
            ok, source_frame = capture.retrieve()
            if not ok or source_frame is None:
                raise VideoError(
                    f"Could not decode frame {next_required_index} from {path}"
                )
            if crop is not None:
                x1, y1, x2, y2 = crop
                source_frame = source_frame[y1:y2, x1:x2]
            resized_frame = cv2.resize(
                cv2.cvtColor(source_frame, cv2.COLOR_BGR2RGB),
                (image_size, image_size),
                interpolation=cv2.INTER_AREA,
            )
            for clip in active_clips:
                while (
                    clip.next_frame_slot < len(clip.frame_indices)
                    and clip.frame_indices[clip.next_frame_slot] == next_required_index
                ):
                    clip.frames[clip.next_frame_slot] = resized_frame
                    clip.next_frame_slot += 1

            while (
                active_clips
                and active_clips[0].next_frame_slot == len(active_clips[0].frame_indices)
            ):
                completed_clip = active_clips.pop(0)
                batch_times.append(completed_clip.times)
                batch_clips.append(np.stack(completed_clip.frames, axis=0))
                if len(batch_clips) == batch_size:
                    yield batch_times, np.stack(batch_clips, axis=0)
                    batch_times = []
                    batch_clips = []

        if batch_clips:
            yield batch_times, np.stack(batch_clips, axis=0)
    finally:
        capture.release()


def iter_window_times(
    duration_seconds: float,
    *,
    window_seconds: float,
    stride_seconds: float,
) -> Iterator[tuple[float, float]]:
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive.")
    if window_seconds <= 0 or stride_seconds <= 0:
        raise ValueError("window_seconds and stride_seconds must be positive.")
    if stride_seconds > window_seconds:
        raise ValueError("stride_seconds cannot exceed window_seconds.")

    if duration_seconds <= window_seconds:
        yield 0.0, duration_seconds
        return

    last_start = duration_seconds - window_seconds
    start = 0.0
    yielded_last = False
    while start <= last_start + 1e-9:
        yield start, start + window_seconds
        yielded_last = abs(start - last_start) <= 1e-9
        start += stride_seconds
    if not yielded_last and last_start > 0:
        yield last_start, duration_seconds
