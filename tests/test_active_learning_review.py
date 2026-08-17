from __future__ import annotations

import copy
import csv
import hashlib
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from spiketrace.active_learning_review import apply_active_review
from spiketrace.cli import build_parser, run_command
from spiketrace.errors import ActiveLearningError
from spiketrace.manifest import load_manifest


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class ApplyActiveReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "data" / "annotations").mkdir(parents=True)
        (self.root / "outputs").mkdir()
        self.video = self.root / "data" / "rangitoto.mp4"
        self.video.write_bytes(b"synthetic-rangitoto-video")
        self.selection = self.root / "selection.json"
        self.review_draft = self.root / "review-draft.json"
        self.base_manifest = self.root / "data" / "annotations" / "base.csv"
        self.output_manifest = self.root / "data" / "annotations" / "round-01.csv"
        self.output_results = self.root / "round-01-results.json"
        self._write_selection_and_draft()
        self.base_rows = [
            {
                "video_path": "usa-germany.mp4",
                "start_seconds": "1",
                "end_seconds": "2",
                "label": "serve",
                "team_side": "ours",
                "player_number": "8",
                "split": "train",
                "crop_x1": "",
                "crop_y1": "",
                "crop_x2": "",
                "crop_y2": "",
                "match_id": "",
                "review_status": "reviewed",
                "notes": "keep this exact note",
                "source_tag": "legacy-import",
            },
            {
                "video_path": "usa-germany.mp4",
                "start_seconds": "4.000",
                "end_seconds": "5.500",
                "label": "attack",
                "team_side": "ours",
                "player_number": "12",
                "split": "train",
                "crop_x1": "0",
                "crop_y1": "0",
                "crop_x2": "1920",
                "crop_y2": "645",
                "match_id": "",
                "review_status": "reviewed",
                "notes": "",
                "source_tag": "legacy-import",
            },
        ]
        with self.base_manifest.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self.base_rows[0]))
            writer.writeheader()
            writer.writerows(self.base_rows)

    def _write_selection_and_draft(self) -> None:
        video_hash = sha256_file(self.video)
        audit_far = {
            "source_file": "outputs/far.json",
            "source_file_sha256": "1" * 64,
            "normalized_payload_sha256": "2" * 64,
        }
        audit_near = {
            "source_file": "outputs/near.json",
            "source_file_sha256": "3" * 64,
            "normalized_payload_sha256": "4" * 64,
        }
        shared_settings = {
            "checkpoint": "runs/best.pt",
            "checkpoint_sha256": "a" * 64,
            "video_sha256": video_hash,
        }
        merged = {
            "format_version": 2,
            "merge_format_version": 2,
            "model_version": "synthetic-v1",
            "video": {
                "path": "data/rangitoto.mp4",
                "fps": 25,
                "frame_count": 12500,
                "width": 1920,
                "height": 1080,
                "duration_seconds": 500,
            },
            "settings": {"input_runs": {"far": audit_far, "near": audit_near}},
            "input_runs": {
                "far": {
                    "settings": {**shared_settings, "crop": [0, 0, 1920, 645]},
                    "windows": [
                        {
                            "window_index": 0,
                            "start_seconds": 100.75,
                            "end_seconds": 101.25,
                            "action": "attack",
                            "confidence": 0.999,
                        },
                        {
                            "window_index": 1,
                            "start_seconds": 103,
                            "end_seconds": 104,
                            "action": "attack",
                            "confidence": 0.97,
                        },
                        {
                            "window_index": 2,
                            "start_seconds": 110.5,
                            "end_seconds": 111.5,
                            "action": "set",
                            "confidence": 0.999,
                        },
                        {
                            "window_index": 3,
                            "start_seconds": 113,
                            "end_seconds": 114,
                            "action": "background",
                            "confidence": 0.99,
                        },
                    ],
                },
                "near": {
                    "settings": {**shared_settings, "crop": [0, 255, 1920, 1080]},
                    "windows": [
                        {
                            "window_index": 0,
                            "start_seconds": 106.5,
                            "end_seconds": 107.5,
                            "action": "attack",
                            "confidence": 0.999,
                        },
                        {
                            "window_index": 1,
                            "start_seconds": 108,
                            "end_seconds": 109,
                            "action": "set",
                            "confidence": 0.96,
                        },
                        {
                            "window_index": 2,
                            "start_seconds": 115,
                            "end_seconds": 116,
                            "action": "serve",
                            "confidence": 0.8,
                        },
                    ],
                },
            },
            "events": [],
            "duplicate_groups": [],
            "conflict_groups": [],
        }
        merged_path = self.root / "outputs" / "merged.json"
        merged_path.write_text(json.dumps(merged) + "\n", encoding="utf-8")
        clips = [
            {
                "clip_id": f"round-01-clip-{ordinal:03d}",
                "ordinal": ordinal,
                "start_seconds": 100 + (ordinal - 1) * 5,
                "end_seconds": 104 + (ordinal - 1) * 5,
                "duration_seconds": 4,
            }
            for ordinal in range(1, 41)
        ]
        selection = {
            "format_version": 1,
            "selection_algorithm_version": "active-learning-selection-v1",
            "batch_id": "rangitoto-round-01",
            "round_id": "round-01",
            "round_number": 1,
            "source": {
                "merged_json": "outputs/merged.json",
                "merged_json_sha256": sha256_file(merged_path),
                "checkpoint": "runs/best.pt",
                "checkpoint_sha256": "a" * 64,
                "inference_runs": {"far": audit_far, "near": audit_near},
                "format_version": 2,
                "merge_format_version": 2,
                "model_version": "synthetic-v1",
            },
            "video": {
                "video_id": "rangitoto",
                "path": "data/rangitoto.mp4",
                "sha256": video_hash,
                "fps": 25,
                "frame_count": 12500,
                "width": 1920,
                "height": 1080,
                "duration_seconds": 500,
                "crops": {
                    "far": [0, 0, 1920, 645],
                    "near": [0, 255, 1920, 1080],
                },
            },
            "settings": {},
            "previous_selections": [],
            "quota_summary": [],
            "coverage": {},
            "clips": clips,
        }
        self.selection.write_text(json.dumps(selection) + "\n", encoding="utf-8")
        draft_clips = []
        for clip in clips:
            action = (
                {
                    "action": "receive",
                    "relative_start_seconds": 1,
                    "relative_end_seconds": 2,
                    "team_side": "far",
                    "note": "clean receive",
                }
                if clip["ordinal"] == 1
                else {
                    "action": "background",
                    "relative_start_seconds": 0,
                    "relative_end_seconds": 4,
                    "team_side": "near",
                    "note": "",
                }
            )
            draft_clips.append(
                {
                    "clip_id": clip["clip_id"],
                    "ordinal": clip["ordinal"],
                    "source_start_seconds": clip["start_seconds"],
                    "source_end_seconds": clip["end_seconds"],
                    "actions": [action],
                }
            )
        draft = {
            "format_version": 1,
            "batch_id": selection["batch_id"],
            "round_id": selection["round_id"],
            "selection": "selection.json",
            "selection_sha256": sha256_file(self.selection),
            "workbook": {"path": "review.xlsx", "sha256": "b" * 64},
            "video": {"path": selection["video"]["path"], "sha256": video_hash},
            "time_precision_seconds": 1,
            "clips": draft_clips,
        }
        self.review_draft.write_text(json.dumps(draft) + "\n", encoding="utf-8")

    def apply(self, **kwargs):
        with mock.patch(
            "spiketrace.active_learning_selection.verify_dual_crop_review",
            return_value={"verified": True},
        ):
            return apply_active_review(
                self.base_manifest,
                self.selection,
                self.review_draft,
                self.output_manifest,
                self.output_results,
                repo_root=self.root,
                legacy_base_match_id="usa-germany-2024-olympics",
                review_match_id="rangitoto-taka-national-final",
                require_files=False,
                **kwargs,
            )

    def test_converts_relative_seconds_and_uses_the_selected_crop(self):
        result = self.apply()
        records = load_manifest(self.output_manifest, require_files=False)
        added = records[len(self.base_rows) :]
        self.assertEqual((added[0].start_seconds, added[0].end_seconds), (101.0, 102.0))
        self.assertEqual(added[0].label, "receive")
        self.assertEqual(added[0].team_side, "far")
        self.assertEqual(added[0].crop, (0, 0, 1920, 645))
        self.assertEqual(result["positive_action_count"], 1)
        self.assertEqual(
            result["settings"]["legacy_base_match_id"],
            "usa-germany-2024-olympics",
        )
        self.assertEqual(
            result["settings"]["review_match_id"],
            "rangitoto-taka-national-final",
        )
        self.assertEqual(
            result["settings"]["effective_video_root"],
            {"kind": "repo_relative", "path": "data/annotations"},
        )
        self.assertEqual(
            read_csv_rows(self.output_manifest)[len(self.base_rows)]["video_path"],
            "../rangitoto.mp4",
        )

    def test_does_not_modify_base_rows_or_source_files(self):
        before = self.base_manifest.read_bytes()
        self.apply()
        self.assertEqual(self.base_manifest.read_bytes(), before)
        output_rows = read_csv_rows(self.output_manifest)
        for source, migrated in zip(self.base_rows, output_rows[: len(self.base_rows)]):
            self.assertEqual(
                {key: value for key, value in migrated.items() if key != "match_id"},
                {key: value for key, value in source.items() if key != "match_id"},
            )
            self.assertEqual(migrated["match_id"], "usa-germany-2024-olympics")
        self.assertTrue(
            all(
                row["match_id"] == "rangitoto-taka-national-final"
                for row in output_rows[len(self.base_rows) :]
            )
        )


class HardNegativeTests(unittest.TestCase):
    _write_selection_and_draft = ApplyActiveReviewTests._write_selection_and_draft
    apply = ApplyActiveReviewTests.apply

    def setUp(self):
        ApplyActiveReviewTests.setUp(self)
        draft = json.loads(self.review_draft.read_text(encoding="utf-8"))
        draft["clips"][1]["actions"] = [
            {
                "action": "set",
                "relative_start_seconds": 1,
                "relative_end_seconds": 2,
                "team_side": "near",
                "note": "",
            }
        ]
        draft["clips"][2]["actions"] = [
            {
                "action": "dig",
                "relative_start_seconds": 1,
                "relative_end_seconds": 2,
                "team_side": "far",
                "note": "",
            }
        ]
        self.review_draft.write_text(json.dumps(draft) + "\n", encoding="utf-8")
        self.actions = [
            {"start_seconds": 101, "end_seconds": 102, "team_side": "far"},
            {"start_seconds": 106, "end_seconds": 107, "team_side": "near"},
            {"start_seconds": 111, "end_seconds": 112, "team_side": "far"},
        ]

    @staticmethod
    def overlaps(left, right):
        return (
            left["start_seconds"] < right["end_seconds"]
            and right["start_seconds"] < left["end_seconds"]
        )

    def overlaps_guard(self, window, action, guard):
        return (
            window["team_side"] == action["team_side"]
            and window["start_seconds"] < action["end_seconds"] + guard
            and action["start_seconds"] - guard < window["end_seconds"]
        )

    def test_uses_hard_negatives_first_and_respects_guard_and_cap(self):
        result = self.apply(
            background_guard_seconds=0.5,
            max_background_windows=9,
            background_seed=42,
        )
        negatives = result["generated_background_windows"]
        self.assertLessEqual(len(negatives), result["positive_action_count"])
        self.assertTrue(negatives)
        self.assertNotEqual(negatives[0]["source_top1_action"], "background")
        self.assertTrue(
            all(
                not self.overlaps_guard(window, action, 0.5)
                for window in negatives
                for action in self.actions
            )
        )
        self.assertTrue(
            all(
                not self.overlaps(left, right)
                for index, left in enumerate(negatives)
                for right in negatives[index + 1 :]
            )
        )
        self.assertEqual(result["settings"]["requested_max_background_windows"], 9)
        self.assertEqual(result["settings"]["effective_max_background_windows"], 3)
        self.assertEqual(result["settings"]["background_seed"], 42)

    def test_zero_cap_is_valid(self):
        result = self.apply(max_background_windows=0)
        self.assertEqual(result["generated_background_windows"], [])
        self.assertEqual(result["settings"]["effective_max_background_windows"], 0)


class ActiveReviewCommandTests(unittest.TestCase):
    def test_dispatches_all_settings_and_allow_missing_videos(self):
        args = build_parser().parse_args(
            [
                "apply-active-review",
                "base.csv",
                "selection.json",
                "draft.json",
                "out.csv",
                "results.json",
                "--repo-root",
                ".",
                "--legacy-base-match-id",
                "legacy",
                "--review-match-id",
                "review",
                "--video-root",
                "videos",
                "--background-guard-seconds",
                "0.75",
                "--max-background-windows",
                "9",
                "--background-seed",
                "42",
                "--allow-missing-videos",
            ]
        )
        with mock.patch(
            "spiketrace.active_learning_review.apply_active_review",
            return_value={"ok": True},
        ) as apply:
            self.assertEqual(run_command(args), {"ok": True})
        self.assertEqual(
            apply.call_args.args,
            (
                Path("base.csv"),
                Path("selection.json"),
                Path("draft.json"),
                Path("out.csv"),
                Path("results.json"),
            ),
        )
        self.assertEqual(apply.call_args.kwargs["repo_root"], Path("."))
        self.assertEqual(apply.call_args.kwargs["video_root"], Path("videos"))
        self.assertEqual(apply.call_args.kwargs["background_guard_seconds"], 0.75)
        self.assertEqual(apply.call_args.kwargs["max_background_windows"], 9)
        self.assertEqual(apply.call_args.kwargs["background_seed"], 42)
        self.assertFalse(apply.call_args.kwargs["require_files"])


class AtomicPublicationTests(unittest.TestCase):
    _write_selection_and_draft = ApplyActiveReviewTests._write_selection_and_draft
    apply = ApplyActiveReviewTests.apply

    def setUp(self):
        ApplyActiveReviewTests.setUp(self)

    def temporary_siblings(self):
        return [
            *self.output_manifest.parent.glob(f".{self.output_manifest.name}.tmp-*"),
            *self.output_results.parent.glob(f".{self.output_results.name}.tmp-*"),
        ]

    def test_second_link_io_failure_rolls_back_first_link_and_temps(self):
        real_link = __import__("os").link

        def fail_second(source, destination):
            if Path(destination) == self.output_results:
                raise OSError("injected second-link failure")
            return real_link(source, destination)

        with (
            mock.patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            mock.patch(
                "spiketrace.active_learning_review.os.link", side_effect=fail_second
            ),
            self.assertRaisesRegex(OSError, "second-link failure"),
        ):
            apply_active_review(
                self.base_manifest,
                self.selection,
                self.review_draft,
                self.output_manifest,
                self.output_results,
                repo_root=self.root,
                legacy_base_match_id="legacy",
                review_match_id="review",
                require_files=False,
            )

        self.assertFalse(self.output_manifest.exists())
        self.assertFalse(self.output_results.exists())
        self.assertEqual(self.temporary_siblings(), [])

    def test_fsync_failure_leaves_no_outputs_or_temps(self):
        with (
            mock.patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            mock.patch(
                "spiketrace.active_learning_review.os.fsync",
                side_effect=OSError("injected fsync failure"),
            ),
            self.assertRaisesRegex(ActiveLearningError, "temporary output"),
        ):
            apply_active_review(
                self.base_manifest,
                self.selection,
                self.review_draft,
                self.output_manifest,
                self.output_results,
                repo_root=self.root,
                legacy_base_match_id="legacy",
                review_match_id="review",
                require_files=False,
            )
        self.assertFalse(self.output_manifest.exists())
        self.assertFalse(self.output_results.exists())
        self.assertEqual(self.temporary_siblings(), [])

    def test_first_link_collision_preserves_competing_bytes(self):
        competitor = b"competing manifest"

        def collide_first(_source, destination):
            Path(destination).write_bytes(competitor)
            raise FileExistsError("injected race")

        with (
            mock.patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            mock.patch(
                "spiketrace.active_learning_review.os.link", side_effect=collide_first
            ),
            self.assertRaisesRegex(ActiveLearningError, "already exists"),
        ):
            apply_active_review(
                self.base_manifest,
                self.selection,
                self.review_draft,
                self.output_manifest,
                self.output_results,
                repo_root=self.root,
                legacy_base_match_id="legacy",
                review_match_id="review",
                require_files=False,
            )
        self.assertEqual(self.output_manifest.read_bytes(), competitor)
        self.assertFalse(self.output_results.exists())
        self.assertEqual(self.temporary_siblings(), [])

    def test_second_link_collision_rolls_back_manifest_and_preserves_results(self):
        competitor = b"competing results"
        real_link = __import__("os").link

        def collide_second(source, destination):
            if Path(destination) == self.output_results:
                self.output_results.write_bytes(competitor)
            return real_link(source, destination)

        with (
            mock.patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            mock.patch(
                "spiketrace.active_learning_review.os.link", side_effect=collide_second
            ),
            self.assertRaisesRegex(ActiveLearningError, "already exists"),
        ):
            apply_active_review(
                self.base_manifest,
                self.selection,
                self.review_draft,
                self.output_manifest,
                self.output_results,
                repo_root=self.root,
                legacy_base_match_id="legacy",
                review_match_id="review",
                require_files=False,
            )
        self.assertFalse(self.output_manifest.exists())
        self.assertEqual(self.output_results.read_bytes(), competitor)
        self.assertEqual(self.temporary_siblings(), [])

    def test_results_hashes_exact_published_manifest_bytes(self):
        result = self.apply(background_seed=42)
        persisted = json.loads(self.output_results.read_text(encoding="utf-8"))
        self.assertEqual(result, persisted)
        self.assertEqual(
            persisted["output_manifest_sha256"], sha256_file(self.output_manifest)
        )
        self.assertEqual(self.temporary_siblings(), [])

    def test_results_temp_write_failure_leaves_no_outputs_or_temps(self):
        from spiketrace import active_learning_review

        real_write = active_learning_review._write_unique_sibling
        calls = 0

        def fail_results_write(path, payload):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected results temp write")
            return real_write(path, payload)

        with (
            mock.patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            mock.patch(
                "spiketrace.active_learning_review._write_unique_sibling",
                side_effect=fail_results_write,
            ),
            self.assertRaisesRegex(OSError, "results temp write"),
        ):
            apply_active_review(
                self.base_manifest,
                self.selection,
                self.review_draft,
                self.output_manifest,
                self.output_results,
                repo_root=self.root,
                legacy_base_match_id="legacy",
                review_match_id="review",
                require_files=False,
            )
        self.assertFalse(self.output_manifest.exists())
        self.assertFalse(self.output_results.exists())
        self.assertEqual(self.temporary_siblings(), [])

    def test_identity_checked_rollback_does_not_delete_competing_manifest(self):
        competitor = b"replacement from another writer"
        real_link = __import__("os").link

        def replace_before_second(source, destination):
            if Path(destination) == self.output_results:
                self.output_manifest.unlink()
                self.output_manifest.write_bytes(competitor)
                raise OSError("injected post-link replacement")
            return real_link(source, destination)

        with (
            mock.patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            mock.patch(
                "spiketrace.active_learning_review.os.link",
                side_effect=replace_before_second,
            ),
            self.assertRaisesRegex(OSError, "post-link replacement"),
        ):
            apply_active_review(
                self.base_manifest,
                self.selection,
                self.review_draft,
                self.output_manifest,
                self.output_results,
                repo_root=self.root,
                legacy_base_match_id="legacy",
                review_match_id="review",
                require_files=False,
            )
        self.assertEqual(self.output_manifest.read_bytes(), competitor)
        self.assertFalse(self.output_results.exists())
        self.assertEqual(self.temporary_siblings(), [])

    def test_concurrent_writers_publish_one_complete_pair_without_temp_leaks(self):
        real_link = __import__("os").link
        manifest_barrier = threading.Barrier(2)

        def synchronized_link(source, destination):
            if Path(destination) == self.output_manifest:
                manifest_barrier.wait(timeout=5)
            return real_link(source, destination)

        def apply_once():
            try:
                return apply_active_review(
                    self.base_manifest,
                    self.selection,
                    self.review_draft,
                    self.output_manifest,
                    self.output_results,
                    repo_root=self.root,
                    legacy_base_match_id="legacy",
                    review_match_id="review",
                    require_files=False,
                )
            except ActiveLearningError as exc:
                return exc

        with (
            mock.patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            mock.patch(
                "spiketrace.active_learning_review.os.link",
                side_effect=synchronized_link,
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            outcomes = list(executor.map(lambda _index: apply_once(), range(2)))

        self.assertEqual(sum(isinstance(item, dict) for item in outcomes), 1)
        self.assertEqual(
            sum(isinstance(item, ActiveLearningError) for item in outcomes), 1
        )
        persisted = json.loads(self.output_results.read_text(encoding="utf-8"))
        self.assertEqual(
            persisted["output_manifest_sha256"], sha256_file(self.output_manifest)
        )
        self.assertEqual(self.temporary_siblings(), [])


class ActiveReviewValidationTests(unittest.TestCase):
    _write_selection_and_draft = ApplyActiveReviewTests._write_selection_and_draft
    apply = ApplyActiveReviewTests.apply

    def setUp(self):
        ApplyActiveReviewTests.setUp(self)

    def rewrite_draft(self, mutate):
        draft = json.loads(self.review_draft.read_text(encoding="utf-8"))
        mutate(draft)
        self.review_draft.write_text(json.dumps(draft) + "\n", encoding="utf-8")

    def call_with_ids(self, legacy, review):
        with mock.patch(
            "spiketrace.active_learning_selection.verify_dual_crop_review",
            return_value={"verified": True},
        ):
            return apply_active_review(
                self.base_manifest,
                self.selection,
                self.review_draft,
                self.output_manifest,
                self.output_results,
                repo_root=self.root,
                legacy_base_match_id=legacy,
                review_match_id=review,
                require_files=False,
            )

    def test_rejects_missing_reordered_duplicate_and_invalid_draft_actions(self):
        original = self.review_draft.read_bytes()

        def duplicate_clip(draft):
            draft["clips"][1] = copy.deepcopy(draft["clips"][0])

        corruptions = {
            "missing clip": lambda draft: draft["clips"].pop(),
            "reordered clips": lambda draft: draft["clips"].__setitem__(
                slice(0, 2), reversed(draft["clips"][:2])
            ),
            "duplicate clip": duplicate_clip,
            "invalid action": lambda draft: draft["clips"][0]["actions"][0].__setitem__(
                "action", "tip"
            ),
            "invalid side": lambda draft: draft["clips"][0]["actions"][0].__setitem__(
                "team_side", "ours"
            ),
            "fractional positive time": lambda draft: draft["clips"][0]["actions"][
                0
            ].__setitem__("relative_start_seconds", 0.5),
            "partial background": lambda draft: draft["clips"][1]["actions"][
                0
            ].__setitem__("relative_start_seconds", 1),
            "mixed background": lambda draft: draft["clips"][1]["actions"].append(
                copy.deepcopy(draft["clips"][0]["actions"][0])
            ),
        }
        for name, mutate in corruptions.items():
            with self.subTest(name=name):
                self.review_draft.write_bytes(original)
                self.rewrite_draft(mutate)
                with self.assertRaises(ActiveLearningError):
                    self.apply()
                self.assertFalse(self.output_manifest.exists())
                self.assertFalse(self.output_results.exists())

    def test_rejects_nonfinite_draft_time(self):
        text = self.review_draft.read_text(encoding="utf-8")
        text = text.replace(
            '"relative_start_seconds": 1', '"relative_start_seconds": NaN', 1
        )
        self.review_draft.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ActiveLearningError, "non-finite"):
            self.apply()

    def test_rejects_draft_hash_path_and_video_mismatches(self):
        original = self.review_draft.read_bytes()
        corruptions = {
            "selection hash": lambda draft: draft.__setitem__(
                "selection_sha256", "c" * 64
            ),
            "selection path": lambda draft: draft.__setitem__(
                "selection", "data/selection.json"
            ),
            "workbook hash shape": lambda draft: draft["workbook"].__setitem__(
                "sha256", "BAD"
            ),
            "video path": lambda draft: draft["video"].__setitem__(
                "path", "data/other.mp4"
            ),
            "video hash": lambda draft: draft["video"].__setitem__("sha256", "c" * 64),
        }
        for name, mutate in corruptions.items():
            with self.subTest(name=name):
                self.review_draft.write_bytes(original)
                self.rewrite_draft(mutate)
                with self.assertRaises(ActiveLearningError):
                    self.apply()

    def test_rejects_invalid_or_equal_match_ids(self):
        for legacy, review in (
            (None, "review"),
            ("", "review"),
            ("legacy\nchanged", "review"),
            ("legacy\x7fchanged", "review"),
            ("legacy\u0085changed", "review"),
            ("legacy", None),
            ("legacy", "review\nchanged"),
            ("legacy", "review\x7fchanged"),
            ("legacy", "review\u0085changed"),
            ("same", "same"),
        ):
            with self.subTest(legacy=legacy, review=review):
                try:
                    with self.assertRaises(ActiveLearningError):
                        self.call_with_ids(legacy, review)
                finally:
                    self.output_manifest.unlink(missing_ok=True)
                    self.output_results.unlink(missing_ok=True)

    def test_rejects_all_invalid_guards_and_caps(self):
        for guard in (-1, float("nan"), float("inf"), float("-inf"), True):
            with (
                self.subTest(guard=guard),
                self.assertRaisesRegex(ActiveLearningError, "background_guard_seconds"),
            ):
                self.apply(background_guard_seconds=guard)
        for cap in (True, 1.5, -1):
            with (
                self.subTest(cap=cap),
                self.assertRaisesRegex(ActiveLearningError, "max_background_windows"),
            ):
                self.apply(max_background_windows=cap)

    def test_external_video_root_uses_absolute_audit_and_portable_rows(self):
        with tempfile.TemporaryDirectory() as external:
            result = self.apply(video_root=Path(external))
            self.assertEqual(
                result["settings"]["effective_video_root"],
                {"kind": "absolute", "path": Path(external).resolve().as_posix()},
            )
            first_added = read_csv_rows(self.output_manifest)[len(self.base_rows)]
            self.assertFalse(Path(first_added["video_path"]).is_absolute())

    def test_rejects_multiple_blank_legacy_base_videos(self):
        rows = copy.deepcopy(self.base_rows)
        rows[1]["video_path"] = "second-video.mp4"
        with self.base_manifest.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        with self.assertRaisesRegex(ActiveLearningError, "more than one base video"):
            self.apply()

    def test_rejects_rangitoto_in_validation_split_before_publication(self):
        rows = copy.deepcopy(self.base_rows)
        for row in rows:
            row["video_path"] = "../rangitoto.mp4"
            row["split"] = "val"
        with self.base_manifest.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        with self.assertRaisesRegex(ActiveLearningError, "val|test"):
            self.apply()
        self.assertFalse(self.output_manifest.exists())
        self.assertFalse(self.output_results.exists())

    def test_allows_existing_rangitoto_train_rows(self):
        rows = copy.deepcopy(self.base_rows)
        for row in rows:
            row["video_path"] = "../rangitoto.mp4"
            row["split"] = "train"
        with self.base_manifest.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        result = self.apply(max_background_windows=0)
        self.assertEqual(result["positive_action_count"], 1)

    def test_treats_lexical_paths_to_same_legacy_video_as_one_video(self):
        rows = copy.deepcopy(self.base_rows)
        rows[1]["video_path"] = "./usa-germany.mp4"
        with self.base_manifest.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        result = self.apply(max_background_windows=0)
        self.assertEqual(
            result["settings"]["legacy_base_match_id"], "usa-germany-2024-olympics"
        )

    def test_rejects_boolean_background_bounds(self):
        self.rewrite_draft(
            lambda draft: draft["clips"][1]["actions"][0].__setitem__(
                "relative_start_seconds", False
            )
        )
        with self.assertRaisesRegex(ActiveLearningError, "Background"):
            self.apply()

    def test_adds_only_missing_canonical_columns_after_source_header(self):
        source_fields = [
            "video_path",
            "start_seconds",
            "end_seconds",
            "label",
            "split",
            "source_tag",
        ]
        with self.base_manifest.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=source_fields)
            writer.writeheader()
            writer.writerow(
                {
                    "video_path": "usa-germany.mp4",
                    "start_seconds": "1.000",
                    "end_seconds": "2.500",
                    "label": "serve",
                    "split": "train",
                    "source_tag": "preserve-me",
                }
            )
        self.apply(max_background_windows=0)
        with self.output_manifest.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            header = reader.fieldnames
        self.assertEqual(header[: len(source_fields)], source_fields)
        self.assertEqual(rows[0]["start_seconds"], "1.000")
        self.assertEqual(rows[0]["source_tag"], "preserve-me")
        self.assertEqual(rows[0]["match_id"], "usa-germany-2024-olympics")

    def test_rejects_merged_source_changed_after_selection_verification(self):
        from spiketrace import active_learning_review

        real_load = active_learning_review.load_review_selection
        merged_path = self.root / "outputs" / "merged.json"

        def load_then_change(*args, **kwargs):
            selection = real_load(*args, **kwargs)
            merged_path.write_bytes(merged_path.read_bytes() + b" ")
            return selection

        with (
            mock.patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            mock.patch(
                "spiketrace.active_learning_review.load_review_selection",
                side_effect=load_then_change,
            ),
            self.assertRaisesRegex(ActiveLearningError, "Merged JSON SHA-256"),
        ):
            apply_active_review(
                self.base_manifest,
                self.selection,
                self.review_draft,
                self.output_manifest,
                self.output_results,
                repo_root=self.root,
                legacy_base_match_id="legacy",
                review_match_id="review",
                require_files=False,
            )

    def test_rejects_boolean_integer_draft_metadata(self):
        self.rewrite_draft(
            lambda draft: (
                draft.__setitem__("format_version", True),
                draft.__setitem__("time_precision_seconds", True),
                draft["clips"][0].__setitem__("ordinal", True),
            )
        )
        with self.assertRaises(ActiveLearningError):
            self.apply()

    @unittest.skipUnless(os.name == "nt", "Windows drive semantics")
    def test_rejects_video_root_on_another_drive_with_active_learning_error(self):
        other_drive = "E:" if self.root.drive.upper() != "E:" else "C:"
        with self.assertRaisesRegex(ActiveLearningError, "volume"):
            self.apply(video_root=Path(f"{other_drive}/spiketrace-external-video-root"))


if __name__ == "__main__":
    unittest.main()
