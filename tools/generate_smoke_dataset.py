"""Generate artificial clips for checking the pipeline, never model quality."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

LABELS = ("background", "serve", "receive", "set", "attack", "block")
COLORS = {
    "background": (70, 70, 70),
    "serve": (40, 40, 220),
    "receive": (40, 190, 40),
    "set": (210, 80, 40),
    "attack": (30, 210, 220),
    "block": (180, 50, 180),
}


def _draw_frame(cv2, label: str, frame_index: int, total_frames: int, size: int):
    frame = np.full((size, size, 3), 24, dtype=np.uint8)
    color = COLORS[label]
    progress = frame_index / max(total_frames - 1, 1)
    margin = size // 8

    if label == "background":
        frame[:] = color
    elif label == "serve":
        center = (size // 2, int(size * (0.78 - 0.55 * progress)))
        cv2.circle(frame, center, size // 9, color, thickness=-1)
    elif label == "receive":
        y = int(size * (0.25 + 0.5 * progress))
        cv2.rectangle(frame, (margin, y), (size - margin, y + size // 8), color, -1)
    elif label == "set":
        radius = int(size * (0.08 + 0.16 * progress))
        cv2.circle(frame, (size // 2, size // 2), radius, color, thickness=3)
    elif label == "attack":
        offset = int((size - 2 * margin) * progress)
        cv2.line(
            frame,
            (margin + offset, size - margin),
            (size - margin, margin),
            color,
            thickness=max(2, size // 14),
        )
    elif label == "block":
        gap = size // 8
        x = size // 2 - gap
        cv2.rectangle(frame, (x, margin), (x + gap // 2, size - margin), color, -1)
        cv2.rectangle(
            frame,
            (x + 2 * gap, margin),
            (x + 2 * gap + gap // 2, size - margin),
            color,
            -1,
        )
    return frame


def generate_dataset(output_dir: Path, *, fps: int = 8, size: int = 64) -> Path:
    try:
        import cv2
    except ImportError as exc:
        raise SystemExit(
            "OpenCV is required. Install project dependencies first."
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    segment_seconds = 1.0
    frames_per_segment = int(fps * segment_seconds)
    video_specs = (
        ("train_01.avi", "train", LABELS),
        ("train_02.avi", "train", tuple(reversed(LABELS))),
        (
            "val_01.avi",
            "val",
            ("serve", "set", "attack", "block", "receive", "background"),
        ),
    )
    rows: list[dict[str, object]] = []

    for video_name, split, ordered_labels in video_specs:
        video_path = output_dir / video_name
        writer = cv2.VideoWriter(
            str(video_path),
            cv2.VideoWriter_fourcc(*"MJPG"),
            fps,
            (size, size),
        )
        if not writer.isOpened():
            raise SystemExit(f"Could not create smoke video: {video_path}")
        try:
            for segment_index, label in enumerate(ordered_labels):
                for frame_index in range(frames_per_segment):
                    writer.write(
                        _draw_frame(cv2, label, frame_index, frames_per_segment, size)
                    )
                rows.append(
                    {
                        "video_path": video_name,
                        "start_seconds": segment_index * segment_seconds,
                        "end_seconds": (segment_index + 1) * segment_seconds,
                        "label": label,
                        "team_side": "",
                        "player_number": "",
                        "split": split,
                    }
                )
        finally:
            writer.release()

    manifest_path = output_dir / "annotations.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "video_path",
                "start_seconds",
                "end_seconds",
                "label",
                "team_side",
                "player_number",
                "split",
            ),
        )
        writer.writeheader()
        writer.writerows(rows)
    print(manifest_path)
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    generate_dataset(args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
