from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np

from .domain import VideoMetadata
from .errors import VideoError


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
    path = Path(video_path).expanduser().resolve()
    if num_frames <= 0 or image_size <= 0:
        raise VideoError("num_frames and image_size must be positive.")
    if start_seconds < 0 or end_seconds <= start_seconds:
        raise VideoError("Clip must satisfy 0 <= start_seconds < end_seconds.")

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise VideoError(f"OpenCV could not open video: {path}")

    frames: list[np.ndarray] = []
    sample_times = np.linspace(
        start_seconds,
        end_seconds,
        num=num_frames,
        endpoint=False,
        dtype=np.float64,
    )
    sample_times += (end_seconds - start_seconds) / (2 * num_frames)

    try:
        for timestamp in sample_times:
            capture.set(cv2.CAP_PROP_POS_MSEC, float(timestamp * 1000.0))
            ok, frame = capture.read()
            if not ok or frame is None:
                if frames:
                    frames.append(frames[-1].copy())
                    continue
                raise VideoError(
                    f"Could not decode frame at {timestamp:.3f}s from {path}"
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
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame = cv2.resize(
                frame,
                (image_size, image_size),
                interpolation=cv2.INTER_AREA,
            )
            frames.append(frame)
    finally:
        capture.release()

    return np.stack(frames, axis=0)


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
