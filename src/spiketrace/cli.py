from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .errors import SpikeTraceError
from .manifest import load_manifest, summarize_manifest
from .video import inspect_video


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _crop(value: str) -> tuple[int, int, int, int]:
    try:
        coordinates = tuple(int(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "crop must contain integer x1,y1,x2,y2 coordinates"
        ) from exc
    if len(coordinates) != 4:
        raise argparse.ArgumentTypeError("crop must contain x1,y1,x2,y2")
    x1, y1, x2, y2 = coordinates
    if min(coordinates) < 0 or x2 <= x1 or y2 <= y1:
        raise argparse.ArgumentTypeError("crop coordinates are invalid")
    return x1, y1, x2, y2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spiketrace",
        description="Train and run Spike-Trace volleyball action models.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest_parser = subparsers.add_parser(
        "validate-manifest", help="Validate and summarize an annotation CSV."
    )
    manifest_parser.add_argument("manifest", type=Path)
    manifest_parser.add_argument("--video-root", type=Path)
    manifest_parser.add_argument("--allow-missing-videos", action="store_true")

    video_parser = subparsers.add_parser(
        "inspect-video", help="Read video metadata and verify decoding support."
    )
    video_parser.add_argument("video", type=Path)

    train_parser = subparsers.add_parser("train", help="Train an action classifier.")
    train_parser.add_argument("manifest", type=Path)
    train_parser.add_argument("output_dir", type=Path)
    train_parser.add_argument("--video-root", type=Path)
    train_parser.add_argument("--model", choices=("r3d18", "tiny3d"), default="r3d18")
    train_parser.add_argument("--model-version", default="action-r3d18-v0.1")
    train_parser.add_argument("--pretrained", action="store_true")
    train_parser.add_argument("--epochs", type=_positive_int, default=10)
    train_parser.add_argument("--batch-size", type=_positive_int, default=4)
    train_parser.add_argument("--learning-rate", type=_positive_float, default=1e-4)
    train_parser.add_argument("--weight-decay", type=float, default=1e-4)
    train_parser.add_argument("--num-frames", type=_positive_int, default=16)
    train_parser.add_argument("--image-size", type=_positive_int, default=112)
    train_parser.add_argument("--window-seconds", type=_positive_float, default=1.0)
    train_parser.add_argument(
        "--device", choices=("auto", "cpu", "cuda", "mps"), default="auto"
    )
    train_parser.add_argument("--seed", type=int, default=42)
    train_parser.add_argument("--num-workers", type=int, default=0)

    infer_parser = subparsers.add_parser(
        "infer", help="Run sliding-window inference on a complete video."
    )
    infer_parser.add_argument("video", type=Path)
    infer_parser.add_argument("checkpoint", type=Path)
    infer_parser.add_argument("output_dir", type=Path)
    infer_parser.add_argument("--stride-seconds", type=_positive_float, default=0.4)
    infer_parser.add_argument("--confidence-threshold", type=float, default=0.5)
    infer_parser.add_argument("--merge-gap-seconds", type=float, default=0.25)
    infer_parser.add_argument("--min-event-seconds", type=float, default=0.2)
    infer_parser.add_argument("--batch-size", type=_positive_int, default=8)
    infer_parser.add_argument(
        "--crop",
        type=_crop,
        help="Optional x1,y1,x2,y2 region used for every inference window.",
    )
    infer_parser.add_argument(
        "--device", choices=("auto", "cpu", "cuda", "mps"), default="auto"
    )
    return parser


def run_command(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "validate-manifest":
        records = load_manifest(
            args.manifest,
            video_root=args.video_root,
            require_files=not args.allow_missing_videos,
        )
        return summarize_manifest(records)

    if args.command == "inspect-video":
        return inspect_video(args.video).to_dict()

    if args.command == "train":
        from .training import train_action_model

        return train_action_model(
            args.manifest,
            args.output_dir,
            video_root=args.video_root,
            model_name=args.model,
            model_version=args.model_version,
            pretrained=args.pretrained,
            epochs=args.epochs,
            batch_size=args.batch_size,
            learning_rate=args.learning_rate,
            weight_decay=args.weight_decay,
            num_frames=args.num_frames,
            image_size=args.image_size,
            window_seconds=args.window_seconds,
            device=args.device,
            seed=args.seed,
            num_workers=args.num_workers,
        )

    if args.command == "infer":
        from .inference import infer_video

        return infer_video(
            args.video,
            args.checkpoint,
            args.output_dir,
            stride_seconds=args.stride_seconds,
            confidence_threshold=args.confidence_threshold,
            merge_gap_seconds=args.merge_gap_seconds,
            min_event_seconds=args.min_event_seconds,
            batch_size=args.batch_size,
            device=args.device,
            crop=args.crop,
        )

    raise ValueError(f"Unknown command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run_command(args)
    except (SpikeTraceError, ValueError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
