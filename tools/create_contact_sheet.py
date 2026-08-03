"""Create a contact sheet from selected or evenly spaced video timestamps."""

from __future__ import annotations

import argparse
import math
from pathlib import Path


def _parse_times(raw: str | None) -> list[float] | None:
    if not raw:
        return None
    values = [float(item.strip()) for item in raw.split(",") if item.strip()]
    if not values or any(value < 0 for value in values):
        raise argparse.ArgumentTypeError("times must be non-negative seconds")
    return values


def _parse_crop(raw: str | None) -> tuple[int, int, int, int] | None:
    if not raw:
        return None
    try:
        values = tuple(int(item.strip()) for item in raw.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "crop must contain integer x1,y1,x2,y2 coordinates"
        ) from exc
    if len(values) != 4:
        raise argparse.ArgumentTypeError("crop must contain x1,y1,x2,y2")
    x1, y1, x2, y2 = values
    if min(values) < 0 or x2 <= x1 or y2 <= y1:
        raise argparse.ArgumentTypeError("crop coordinates are invalid")
    return x1, y1, x2, y2


def create_contact_sheet(
    video_path: Path,
    output_path: Path,
    *,
    times: list[float] | None,
    samples: int,
    columns: int,
    thumbnail_width: int,
    crop: tuple[int, int, int, int] | None,
) -> Path:
    import cv2
    from PIL import Image, ImageDraw, ImageFont

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps <= 0 or frame_count <= 0:
        capture.release()
        raise RuntimeError(f"Invalid video metadata: {video_path}")
    duration = frame_count / fps

    if times is None:
        step = duration / (samples + 1)
        times = [step * (index + 1) for index in range(samples)]
    times = [min(timestamp, max(duration - 1 / fps, 0)) for timestamp in times]

    thumbnails: list[tuple[Image.Image, float]] = []
    try:
        for timestamp in times:
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            if crop is not None:
                x1, y1, x2, y2 = crop
                frame_height, frame_width = frame.shape[:2]
                if x2 > frame_width or y2 > frame_height:
                    raise RuntimeError(
                        f"Crop {crop} exceeds video frame {frame_width}x{frame_height}."
                    )
                frame = frame[y1:y2, x1:x2]
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            height, width = frame.shape[:2]
            thumbnail_height = round(height * thumbnail_width / width)
            frame = cv2.resize(frame, (thumbnail_width, thumbnail_height))
            thumbnails.append((Image.fromarray(frame), timestamp))
    finally:
        capture.release()

    if not thumbnails:
        raise RuntimeError("No frames could be decoded.")

    label_height = 28
    cell_width = thumbnail_width
    cell_height = thumbnails[0][0].height + label_height
    rows = math.ceil(len(thumbnails) / columns)
    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=16)

    for index, (thumbnail, timestamp) in enumerate(thumbnails):
        x = (index % columns) * cell_width
        y = (index // columns) * cell_height
        sheet.paste(thumbnail, (x, y))
        minutes, seconds = divmod(timestamp, 60)
        draw.text(
            (x + 8, y + thumbnail.height + 4),
            f"{int(minutes):02d}:{seconds:05.2f}",
            fill="black",
            font=font,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=92)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--times", type=_parse_times)
    parser.add_argument("--samples", type=int, default=16)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--thumbnail-width", type=int, default=400)
    parser.add_argument("--crop", type=_parse_crop)
    args = parser.parse_args()
    output = create_contact_sheet(
        args.video.expanduser().resolve(),
        args.output.expanduser().resolve(),
        times=args.times,
        samples=args.samples,
        columns=args.columns,
        thumbnail_width=args.thumbnail_width,
        crop=args.crop,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
