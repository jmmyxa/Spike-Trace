from __future__ import annotations

import argparse
import json
import math
import sys
import subprocess
from datetime import datetime, timezone
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .errors import SpikeTraceError, ValidationError
from .manifest import load_manifest, summarize_manifest
from .video import inspect_video


def _path(value: object) -> str:
    return str(Path(value).expanduser().resolve())


def _queue_binding(path: Path):
    from .domain import VideoMetadata
    from .validation_contract import ValidationVideoBinding
    data = json.loads(path.read_text(encoding="utf-8"))
    item = data["binding"]
    metadata = item["metadata"]
    video = Path(metadata["path"]).expanduser().resolve()
    return ValidationVideoBinding(item["match_id"], video, video.parent, item["video_path"], item["sha256"], VideoMetadata(video, float(metadata["fps"]), int(metadata["frame_count"]), int(metadata["width"]), int(metadata["height"]), float(metadata["duration_seconds"])))


def _git_sha(repo_root: Path) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(repo_root), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive finite number")
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

    review_clips_parser = subparsers.add_parser(
        "build-review-clips",
        help="Build silent proxy clips and a manifest for an active-learning batch.",
    )
    review_clips_parser.add_argument("selection_json", type=Path)
    review_clips_parser.add_argument("output_dir", type=Path)
    review_clips_parser.add_argument("--repo-root", type=Path, required=True)
    review_clips_parser.add_argument("--proxy-fps", type=_positive_float, default=15.0)
    review_clips_parser.add_argument("--max-width", type=_positive_int, default=960)

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

    active_review_parser = subparsers.add_parser(
        "apply-active-review",
        help="Apply an extracted active-learning review draft to a cumulative manifest.",
    )
    active_review_parser.add_argument("base_manifest", type=Path)
    active_review_parser.add_argument("selection", type=Path)
    active_review_parser.add_argument("review_input", type=Path)
    active_review_parser.add_argument("output_manifest", type=Path)
    active_review_parser.add_argument("output_results", type=Path)
    active_review_parser.add_argument("--repo-root", type=Path, required=True)
    active_review_parser.add_argument("--legacy-base-match-id", required=True)
    active_review_parser.add_argument("--review-match-id", required=True)
    active_review_parser.add_argument("--video-root", type=Path)
    active_review_parser.add_argument(
        "--background-guard-seconds", type=float, default=0.5
    )
    active_review_parser.add_argument("--max-background-windows", type=int)
    active_review_parser.add_argument("--background-seed", type=int)
    active_review_parser.add_argument("--allow-missing-videos", action="store_true")

    active_review_v2_parser = subparsers.add_parser(
        "apply-active-review-v2",
        help="Apply evidence-aware review input and publish a synchronized bundle.",
    )
    active_review_v2_parser.add_argument("base_manifest", type=Path)
    active_review_v2_parser.add_argument("selection", type=Path)
    active_review_v2_parser.add_argument("review_input", type=Path)
    active_review_v2_parser.add_argument("output_dir", type=Path)
    active_review_v2_parser.add_argument("--repo-root", type=Path, required=True)
    active_review_v2_parser.add_argument("--legacy-base-match-id", required=True)
    active_review_v2_parser.add_argument("--review-match-id", required=True)
    active_review_v2_parser.add_argument("--video-root", type=Path)
    active_review_v2_parser.add_argument(
        "--background-guard-seconds", type=float, default=0.5
    )
    active_review_v2_parser.add_argument("--max-background-windows", type=int)
    active_review_v2_parser.add_argument("--background-seed", type=int)
    active_review_v2_parser.add_argument("--allow-missing-videos", action="store_true")

    verify_review_bundle_parser = subparsers.add_parser(
        "verify-active-review-bundle",
        help="Verify a synchronized evidence-aware active-review bundle.",
    )
    verify_review_bundle_parser.add_argument("output_dir", type=Path)
    verify_review_bundle_parser.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )

    freeze = subparsers.add_parser("freeze-validation-video")
    freeze.add_argument("video", type=Path); freeze.add_argument("binding_json", type=Path)
    freeze.add_argument("--repo-root", type=Path, required=True); freeze.add_argument("--video-root", type=Path, required=True)
    freeze.add_argument("--match-id", required=True); freeze.add_argument("--expected-sha256", required=True)

    prepare = subparsers.add_parser("prepare-validation-rallies")
    prepare.add_argument("binding_json", type=Path); prepare.add_argument("queue_json", type=Path); prepare.add_argument("proxy_dir", type=Path)
    prepare.add_argument("--repo-root", type=Path, required=True); prepare.add_argument("--video-root", type=Path, required=True); prepare.add_argument("--side-map", type=Path, required=True)

    init_truth = subparsers.add_parser("init-validation-truth")
    init_truth.add_argument("queue_json", type=Path); init_truth.add_argument("draft_json", type=Path); init_truth.add_argument("--code-sha", required=True)

    val_truth = subparsers.add_parser("validate-validation-truth")
    val_truth.add_argument("binding_json", type=Path); val_truth.add_argument("draft_json", type=Path); val_truth.add_argument("--repo-root", type=Path, required=True); val_truth.add_argument("--video-root", type=Path, required=True)

    lock_truth = subparsers.add_parser("lock-validation-truth")
    lock_truth.add_argument("binding_json", type=Path); lock_truth.add_argument("draft_json", type=Path); lock_truth.add_argument("truth_json", type=Path); lock_truth.add_argument("truth_csv", type=Path)
    lock_truth.add_argument("--repo-root", type=Path, required=True); lock_truth.add_argument("--video-root", type=Path, required=True); lock_truth.add_argument("--code-sha", required=True); lock_truth.add_argument("--created-at", required=True)

    verify_truth = subparsers.add_parser("verify-validation-truth")
    verify_truth.add_argument("binding_json", type=Path); verify_truth.add_argument("truth_json", type=Path); verify_truth.add_argument("truth_csv", type=Path); verify_truth.add_argument("--repo-root", type=Path, required=True); verify_truth.add_argument("--video-root", type=Path, required=True)

    isolation = subparsers.add_parser("verify-validation-isolation")
    isolation.add_argument("binding_json", type=Path); isolation.add_argument("--repo-root", type=Path, required=True); isolation.add_argument("--video-root", type=Path, required=True); isolation.add_argument("--manifest", type=Path, action="append", required=True); isolation.add_argument("--selection-source", type=Path, action="append", default=[])

    evaluate = subparsers.add_parser("evaluate-validation")
    evaluate.add_argument("video", type=Path); evaluate.add_argument("truth_json", type=Path); evaluate.add_argument("checkpoint", type=Path); evaluate.add_argument("output_dir", type=Path)
    evaluate.add_argument("--repo-root", type=Path, required=True); evaluate.add_argument("--video-root", type=Path, required=True); evaluate.add_argument("--manifest", type=Path, action="append", required=True); evaluate.add_argument("--selection-source", type=Path, action="append", default=[])
    evaluate.add_argument("--stride-seconds", type=_positive_float, default=0.4); evaluate.add_argument("--confidence-threshold", type=float, default=0.5); evaluate.add_argument("--merge-gap-seconds", type=float, default=0.25); evaluate.add_argument("--min-event-seconds", type=float, default=0.2); evaluate.add_argument("--batch-size", type=_positive_int, default=8); evaluate.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")

    verify_validation = subparsers.add_parser("verify-validation")
    verify_validation.add_argument("output_dir", type=Path); verify_validation.add_argument("--repo-root", type=Path, required=True); verify_validation.add_argument("--video-root", type=Path, required=True)
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

    if args.command == "build-review-clips":
        from .review_batch import build_review_proxies

        return build_review_proxies(
            args.selection_json,
            args.output_dir,
            repo_root=args.repo_root,
            output_fps=args.proxy_fps,
            max_width=args.max_width,
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

    if args.command == "apply-active-review":
        from .active_learning_review import apply_active_review

        return apply_active_review(
            args.base_manifest,
            args.selection,
            args.review_input,
            args.output_manifest,
            args.output_results,
            repo_root=args.repo_root,
            legacy_base_match_id=args.legacy_base_match_id,
            review_match_id=args.review_match_id,
            video_root=args.video_root,
            background_guard_seconds=args.background_guard_seconds,
            max_background_windows=args.max_background_windows,
            background_seed=args.background_seed,
            require_files=not args.allow_missing_videos,
        )

    if args.command == "apply-active-review-v2":
        from .active_learning_review import apply_active_review_v2

        return apply_active_review_v2(
            args.base_manifest,
            args.selection,
            args.review_input,
            args.output_dir,
            repo_root=args.repo_root,
            legacy_base_match_id=args.legacy_base_match_id,
            review_match_id=args.review_match_id,
            video_root=args.video_root,
            background_guard_seconds=args.background_guard_seconds,
            max_background_windows=args.max_background_windows,
            background_seed=args.background_seed,
            require_files=not args.allow_missing_videos,
        )

    if args.command == "verify-active-review-bundle":
        from ._active_learning_review_outputs import validate_result_bundle

        return validate_result_bundle(args.output_dir, repo_root=args.repo_root)

    if args.command == "freeze-validation-video":
        from .validation_contract import freeze_video_binding, write_video_binding
        binding = freeze_video_binding(args.video, match_id=args.match_id, expected_sha256=args.expected_sha256, repo_root=args.repo_root, video_root=args.video_root)
        write_video_binding(args.binding_json, binding, repo_root=args.repo_root)
        return {"binding_json": str(args.binding_json.resolve()), "match_id": binding.match_id, "video_sha256": binding.sha256}

    if args.command == "prepare-validation-rallies":
        from .validation_contract import load_video_binding
        from .validation_rallies import RallyDetectionSettings, apply_side_map, complete_coverage, detect_rally_candidates, write_rally_proxies, write_rally_queue
        binding = load_video_binding(args.binding_json, repo_root=args.repo_root, video_root=args.video_root)
        side_payload = json.loads(args.side_map.read_text(encoding="utf-8"))
        set_intervals = side_payload.get("set_intervals", [])
        side_intervals = side_payload.get("side_intervals", [])
        candidates = detect_rally_candidates(binding.video_path, settings=RallyDetectionSettings())
        segments = apply_side_map(complete_coverage(candidates, duration_seconds=binding.metadata.duration_seconds, binding=binding), set_intervals=set_intervals, side_intervals=side_intervals, metadata=binding.metadata)
        write_rally_queue(args.queue_json, binding=binding, segments=segments, set_intervals=set_intervals, side_intervals=side_intervals, settings=RallyDetectionSettings(), code_sha="cli")
        proxy = write_rally_proxies(segments, args.proxy_dir, video_root=args.video_root, repo_root=args.repo_root, binding=binding)
        return {"queue_json": str(args.queue_json.resolve()), "proxy_dir": str(args.proxy_dir.resolve()), "segments": len(segments), "proxies": proxy.get("proxies", [])}

    if args.command == "init-validation-truth":
        from .validation_truth import init_truth_draft
        path = init_truth_draft(args.queue_json, args.draft_json, code_sha=args.code_sha)
        return {"draft_json": str(path)}

    if args.command == "validate-validation-truth":
        from .validation_contract import load_video_binding
        from .validation_truth import validate_truth_draft
        binding = load_video_binding(args.binding_json, repo_root=args.repo_root, video_root=args.video_root)
        truth = validate_truth_draft(args.draft_json, binding=binding)
        return {"locked": truth.locked, "coverage_segments": len(truth.coverage), "visible_actions": sum(a.visibility == "visible" for a in truth.actions)}

    if args.command == "lock-validation-truth":
        from .validation_contract import load_video_binding
        from .validation_truth import lock_truth_bundle
        binding = load_video_binding(args.binding_json, repo_root=args.repo_root, video_root=args.video_root)
        result = lock_truth_bundle(args.draft_json, args.truth_csv, args.truth_json, binding=binding, repo_root=args.repo_root, code_sha=args.code_sha, created_at=args.created_at)
        return {key: str(value) for key, value in result.items()}

    if args.command == "verify-validation-truth":
        from .validation_contract import load_video_binding
        from .validation_truth import verify_truth_bundle
        binding = load_video_binding(args.binding_json, repo_root=args.repo_root, video_root=args.video_root)
        return verify_truth_bundle(args.truth_json, args.truth_csv, binding=binding, repo_root=args.repo_root, video_root=args.video_root)

    if args.command == "verify-validation-isolation":
        from .validation_contract import assert_no_content_overlap, load_video_binding
        binding = load_video_binding(args.binding_json, repo_root=args.repo_root, video_root=args.video_root)
        assert_no_content_overlap(binding, manifest_paths=args.manifest, selection_paths=args.selection_source, repo_root=args.repo_root, video_root=args.video_root)
        return {"ok": True, "manifests": len(args.manifest), "selection_sources": len(args.selection_source)}

    if args.command == "evaluate-validation":
        from .validation_contract import assert_no_content_overlap, load_video_binding
        from .validation_truth import load_locked_truth
        from .validation_inference import infer_locked_validation
        from .validation_evaluation import evaluate_validation
        from .validation_outputs import write_validation_outputs
        from .validation_contract import freeze_video_binding, ValidationVideoBinding
        from .domain import VideoMetadata
        truth_data = json.loads(args.truth_json.read_text(encoding="utf-8"))
        video_info = truth_data.get("video", {})
        if not isinstance(video_info, dict):
            raise ValidationError("Locked truth video binding is invalid")
        video_root = args.video_root.resolve()
        source = (video_root / str(video_info.get("video_path", ""))).resolve()
        expected_metadata = video_info.get("metadata")
        if isinstance(expected_metadata, dict):
            expected_metadata = {key: value for key, value in expected_metadata.items() if key != "path"}
        csv_path = args.truth_json.with_suffix(".csv")
        metadata_info = video_info.get("metadata") if isinstance(video_info.get("metadata"), dict) else {}
        metadata = VideoMetadata(source, float(metadata_info.get("fps", 0)), int(metadata_info.get("frame_count", 0)), int(metadata_info.get("width", 0)), int(metadata_info.get("height", 0)), float(metadata_info.get("duration_seconds", 0)))
        binding = ValidationVideoBinding(str(video_info.get("match_id", "")), source, video_root, str(video_info.get("video_path", "")), str(video_info.get("sha256", "")), metadata)
        truth = load_locked_truth(args.truth_json, csv_path, binding=binding)
        binding = freeze_video_binding(source, match_id=binding.match_id, expected_sha256=binding.sha256, repo_root=args.repo_root, video_root=video_root, expected_metadata=expected_metadata)
        from .validation_truth import verify_truth_bundle
        verify_truth_bundle(args.truth_json, csv_path, binding=binding, repo_root=args.repo_root, video_root=args.video_root)
        assert_no_content_overlap(binding, manifest_paths=args.manifest, selection_paths=args.selection_source, repo_root=args.repo_root, video_root=args.video_root)
        inference = infer_locked_validation(args.video, args.checkpoint, truth, stride_seconds=args.stride_seconds, confidence_threshold=args.confidence_threshold, merge_gap_seconds=args.merge_gap_seconds, min_event_seconds=args.min_event_seconds, batch_size=args.batch_size, device=args.device)
        report = evaluate_validation(truth, inference)
        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        paths = write_validation_outputs(args.output_dir, truth=truth, inference=inference, report=report, checkpoint_path=args.checkpoint, code_sha=_git_sha(args.repo_root), parameters={"stride_seconds": args.stride_seconds, "confidence_threshold": args.confidence_threshold, "merge_gap_seconds": args.merge_gap_seconds, "min_event_seconds": args.min_event_seconds, "batch_size": args.batch_size, "device": args.device, "truth_json_path": str(args.truth_json), "truth_csv_path": str(csv_path)}, created_at=created_at)
        return {key: str(value) for key, value in paths.items()}

    if args.command == "verify-validation":
        from .validation_outputs import verify_validation_outputs
        return verify_validation_outputs(args.output_dir, repo_root=args.repo_root, video_root=args.video_root)

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
