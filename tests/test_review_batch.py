from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from spiketrace.active_learning_selection import (
    validate_merged_review_source,
    write_review_selection,
)
from spiketrace.cli import build_parser, run_command
from spiketrace.domain import VideoMetadata
from spiketrace.errors import ActiveLearningError, VideoError
from spiketrace.review_batch import build_review_proxies


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_merged_payload(video_path: Path, checkpoint_path: Path) -> dict[str, object]:
    video_sha256 = sha256_file(video_path)
    checkpoint_sha256 = sha256_file(checkpoint_path)
    audit = {
        "source_file": "outputs/inference/far.json",
        "source_file_sha256": "1" * 64,
        "normalized_payload_sha256": "2" * 64,
    }
    settings = {
        "checkpoint": "runs/model/best.pt",
        "checkpoint_sha256": checkpoint_sha256,
        "video_sha256": video_sha256,
    }
    return {
        "format_version": 2,
        "merge_format_version": 2,
        "video": {
            "path": "data/video.mp4",
            "fps": 25.0,
            "frame_count": 3000,
            "width": 1920,
            "height": 1080,
            "duration_seconds": 120.0,
        },
        "model_version": "rangitoto-test-v1",
        "settings": {
            "input_runs": {
                "far": audit,
                "near": {
                    **audit,
                    "source_file": "outputs/inference/near.json",
                    "source_file_sha256": "3" * 64,
                    "normalized_payload_sha256": "4" * 64,
                },
            }
        },
        "input_runs": {
            "far": {"settings": {**settings, "crop": [0, 0, 1920, 645]}},
            "near": {"settings": {**settings, "crop": [0, 255, 1920, 1080]}},
        },
    }


def make_selection_payload(source: dict[str, object]) -> dict[str, object]:
    return {
        "format_version": 1,
        "selection_algorithm_version": "active-learning-selection-v1",
        "batch_id": "rangitoto-active-learning-round-01",
        "round_id": "round-01",
        "round_number": 1,
        "source": copy.deepcopy(source["source"]),
        "video": copy.deepcopy(source["video"]),
        "settings": {"clip_duration_ms": 1000},
        "previous_selections": [],
        "quota_summary": [],
        "coverage": {"start_seconds": 0.0, "end_seconds": 79.0},
        "clips": [
            {
                "clip_id": f"round-01-clip-{ordinal:03d}",
                "ordinal": ordinal,
                "start_seconds": float((ordinal - 1) * 2),
                "end_seconds": float((ordinal - 1) * 2 + 1),
                "duration_seconds": 1.0,
            }
            for ordinal in range(1, 41)
        ],
    }


class ReviewProxyBatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.video_path = self.root / "data" / "video.mp4"
        self.video_path.parent.mkdir(parents=True)
        self.video_path.write_bytes(b"video")
        self.checkpoint_path = self.root / "runs" / "model" / "best.pt"
        self.checkpoint_path.parent.mkdir(parents=True)
        self.checkpoint_path.write_bytes(b"checkpoint")
        self.merged_json = self.root / "outputs" / "review" / "merged.json"
        self.merged_json.parent.mkdir(parents=True)
        self.merged_json.write_text(
            json.dumps(make_merged_payload(self.video_path, self.checkpoint_path))
            + "\n",
            encoding="utf-8",
        )
        with mock.patch(
            "spiketrace.active_learning_selection.verify_dual_crop_review",
            return_value={"verified": True},
        ):
            source = validate_merged_review_source(self.merged_json, repo_root=self.root)
            self.selection_payload = make_selection_payload(source)
            self.selection = self.root / "selections" / "round-01.json"
            write_review_selection(
                self.selection_payload,
                self.selection,
                repo_root=self.root,
            )
        self.output_dir = self.root / "review-batch"

    def _write_proxy_side_effect(self, _video, output_path, _start, _end, **_kwargs):
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"proxy:{path.name}".encode("ascii"))
        return VideoMetadata(
            path=path,
            fps=12.0,
            frame_count=12,
            width=800,
            height=450,
            duration_seconds=1.0,
        )

    def test_builds_exact_ordered_proxy_manifest(self):
        with (
            mock.patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            mock.patch(
                "spiketrace.review_batch.write_proxy_video",
                side_effect=self._write_proxy_side_effect,
            ),
        ):
            result = build_review_proxies(
                self.selection,
                self.output_dir,
                repo_root=self.root,
                output_fps=15.0,
                max_width=960,
            )
        manifest = json.loads(
            (self.output_dir / "proxy-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["format_version"], 1)
        self.assertEqual(manifest["selection_sha256"], sha256_file(self.selection))
        self.assertEqual(
            [item["clip_id"] for item in manifest["clips"]],
            [item["clip_id"] for item in self.selection_payload["clips"]],
        )
        self.assertTrue(all(len(item["sha256"]) == 64 for item in manifest["clips"]))
        self.assertEqual(result["clip_count"], 40)
        self.assertEqual(manifest["selection"], "selections/round-01.json")
        self.assertEqual(
            manifest["settings"],
            {"codec": "mp4v", "fps": 15.0, "max_width": 960, "audio": False},
        )
        self.assertTrue(
            all((self.output_dir / item["path"]).is_file() for item in manifest["clips"])
        )

    def test_missing_selection_leaves_no_output_or_staging(self):
        missing = self.root / "selections" / "missing.json"
        with self.assertRaises(ActiveLearningError):
            build_review_proxies(missing, self.output_dir, repo_root=self.root)

        self.assertFalse(self.output_dir.exists())
        self.assertEqual(list(self.root.glob(".review-batch.tmp-*")), [])

    def test_tampered_video_between_proxy_writes_aborts_before_next_build(self):
        calls = 0

        def tamper_after_first_proxy(video, output_path, start, end, **kwargs):
            nonlocal calls
            calls += 1
            result = self._write_proxy_side_effect(video, output_path, start, end, **kwargs)
            self.video_path.write_bytes(b"tampered")
            return result

        with (
            mock.patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            mock.patch(
                "spiketrace.review_batch.write_proxy_video",
                side_effect=tamper_after_first_proxy,
            ),
            self.assertRaisesRegex(ActiveLearningError, "SHA-256"),
        ):
            build_review_proxies(self.selection, self.output_dir, repo_root=self.root)

        self.assertEqual(calls, 1)
        self.assertFalse(self.output_dir.exists())
        self.assertEqual(list(self.root.glob(".review-batch.tmp-*")), [])

    def test_failed_proxy_write_removes_staging_directory(self):
        def fail_on_second(video, output_path, start, end, **kwargs):
            if Path(output_path).name == "round-01-clip-002.mp4":
                raise VideoError("proxy failed")
            return self._write_proxy_side_effect(video, output_path, start, end, **kwargs)

        with (
            mock.patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            mock.patch(
                "spiketrace.review_batch.write_proxy_video",
                side_effect=fail_on_second,
            ),
            self.assertRaisesRegex(VideoError, "proxy failed"),
        ):
            build_review_proxies(self.selection, self.output_dir, repo_root=self.root)

        self.assertFalse(self.output_dir.exists())
        self.assertEqual(list(self.root.glob(".review-batch.tmp-*")), [])

    def test_refuses_existing_output_directory_without_changing_it(self):
        self.output_dir.mkdir()
        kept = self.output_dir / "keep.txt"
        kept.write_bytes(b"keep")
        with self.assertRaisesRegex(ActiveLearningError, "already exists"):
            build_review_proxies(self.selection, self.output_dir, repo_root=self.root)

        self.assertEqual(kept.read_bytes(), b"keep")
        self.assertEqual(list(self.root.glob(".review-batch.tmp-*")), [])


class ReviewProxyCommandTests(unittest.TestCase):
    def test_dispatches_all_proxy_settings(self):
        args = build_parser().parse_args(
            [
                "build-review-clips",
                "selection.json",
                "batch",
                "--repo-root",
                ".",
                "--proxy-fps",
                "12",
                "--max-width",
                "800",
            ]
        )
        with mock.patch(
            "spiketrace.review_batch.build_review_proxies", return_value={"ok": True}
        ) as build:
            self.assertEqual(run_command(args), {"ok": True})
        self.assertEqual(build.call_args.kwargs["output_fps"], 12.0)
        self.assertEqual(build.call_args.kwargs["max_width"], 800)
        self.assertEqual(build.call_args.kwargs["repo_root"], Path("."))


if __name__ == "__main__":
    unittest.main()
