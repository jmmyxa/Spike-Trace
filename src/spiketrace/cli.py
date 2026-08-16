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


def _time_strata(value: str) -> int:
    parsed = int(value)
    if parsed < 10:
        raise argparse.ArgumentTypeError("value must be at least 10")
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
    train_parser.add_argument(
        "--allow-train-only",
        action="store_true",
        help="Train without validation records; use training metrics for checkpoint selection.",
    )

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

    dual_crop_parser = subparsers.add_parser(
        "build-dual-crop-review",
        help="Build deterministic far/near review artifacts from inference JSON v2.",
    )
    dual_crop_parser.add_argument("far_json", type=Path)
    dual_crop_parser.add_argument("near_json", type=Path)
    dual_crop_parser.add_argument("output_dir", type=Path)
    dual_crop_parser.add_argument("--repo-root", type=Path, required=True)

    verify_dual_crop_parser = subparsers.add_parser(
        "verify-dual-crop-review",
        help="Recompute and verify a deterministic dual-crop review artifact.",
    )
    verify_dual_crop_parser.add_argument("merged_json", type=Path)
    verify_dual_crop_parser.add_argument("--csv", type=Path)

    selection_parser = subparsers.add_parser(
        "select-review-batch",
        help="Select a deterministic 40-clip active-learning review batch.",
    )
    selection_parser.add_argument("merged_json", type=Path)
    selection_parser.add_argument("output_json", type=Path)
    selection_parser.add_argument("--repo-root", type=Path, required=True)
    selection_parser.add_argument("--round-number", type=_positive_int, default=1)
    selection_parser.add_argument("--seed", type=int, default=42)
    selection_parser.add_argument(
        "--preferred-clip-seconds", type=_positive_float, default=15.0
    )
    selection_parser.add_argument(
        "--min-clip-seconds", type=_positive_float, default=5.0
    )
    selection_parser.add_argument(
        "--max-clip-seconds", type=_positive_float, default=30.0
    )
    selection_parser.add_argument(
        "--min-anchor-gap-seconds", type=_positive_float, default=5.0
    )
    selection_parser.add_argument("--time-strata", type=_time_strata, default=10)
    selection_parser.add_argument(
        "--previous-selection", type=Path, action="append", default=[]
    )

    pretrained_parser = subparsers.add_parser(
        "evaluate-pretrained",
        help="Evaluate pretrained YOLO action weights against an annotation CSV.",
    )
    pretrained_parser.add_argument("manifest", type=Path)
    pretrained_parser.add_argument("weights", type=Path)
    pretrained_parser.add_argument("output_dir", type=Path)
    pretrained_parser.add_argument("--video-root", type=Path)
    pretrained_parser.add_argument("--confidence-threshold", type=float, default=0.25)
    pretrained_parser.add_argument("--frames-per-window", type=_positive_int, default=6)
    pretrained_parser.add_argument(
        "--device", choices=("auto", "cpu", "cuda", "mps"), default="auto"
    )

    review_parser = subparsers.add_parser(
        "prepare-review",
        help="Build a focused manual-review CSV from a reviewed manifest.",
    )
    review_parser.add_argument("manifest", type=Path)
    review_parser.add_argument("spec", type=Path)
    review_parser.add_argument("output_csv", type=Path)
    review_parser.add_argument("--video-root", type=Path)
    review_parser.add_argument("--allow-missing-videos", action="store_true")

    apply_review_parser = subparsers.add_parser(
        "apply-review",
        help="Apply completed manual-review results to a new annotation CSV.",
    )
    apply_review_parser.add_argument("manifest", type=Path)
    apply_review_parser.add_argument("spec", type=Path)
    apply_review_parser.add_argument("results", type=Path)
    apply_review_parser.add_argument("output_csv", type=Path)
    apply_review_parser.add_argument("--video-root", type=Path)
    apply_review_parser.add_argument("--allow-missing-videos", action="store_true")
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
            allow_train_only=args.allow_train_only,
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

    if args.command == "build-dual-crop-review":
        from .dual_crop_review import build_dual_crop_review

        return build_dual_crop_review(
            args.far_json,
            args.near_json,
            args.output_dir,
            repo_root=args.repo_root,
        )

    if args.command == "verify-dual-crop-review":
        from .dual_crop_review import verify_dual_crop_review

        return verify_dual_crop_review(args.merged_json, csv_path=args.csv)

    if args.command == "select-review-batch":
        from .active_learning_selection import select_review_batch

        return select_review_batch(
            args.merged_json,
            args.output_json,
            repo_root=args.repo_root,
            round_number=args.round_number,
            seed=args.seed,
            preferred_clip_seconds=args.preferred_clip_seconds,
            min_clip_seconds=args.min_clip_seconds,
            max_clip_seconds=args.max_clip_seconds,
            min_anchor_gap_seconds=args.min_anchor_gap_seconds,
            time_strata=args.time_strata,
            previous_selection_paths=args.previous_selection,
        )

    if args.command == "evaluate-pretrained":
        from .pretrained import evaluate_pretrained_model

        return evaluate_pretrained_model(
            args.manifest,
            args.weights,
            args.output_dir,
            video_root=args.video_root,
            confidence_threshold=args.confidence_threshold,
            frames_per_window=args.frames_per_window,
            device=args.device,
        )

    if args.command == "prepare-review":
        from .review import prepare_review_queue

        return prepare_review_queue(
            args.manifest,
            args.spec,
            args.output_csv,
            video_root=args.video_root,
            require_files=not args.allow_missing_videos,
        )

    if args.command == "apply-review":
        from .review import apply_review_results

        return apply_review_results(
            args.manifest,
            args.spec,
            args.results,
            args.output_csv,
            video_root=args.video_root,
            require_files=not args.allow_missing_videos,
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
