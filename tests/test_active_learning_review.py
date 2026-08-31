from __future__ import annotations

import copy
import csv
import hashlib
import importlib
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from spiketrace import active_learning_selection
from spiketrace.active_learning_review import (
    apply_active_review,
    apply_active_review_v2,
)
from spiketrace.cli import build_parser, run_command
from spiketrace.errors import ActiveLearningError
from spiketrace.manifest import load_manifest
from tests.test_active_learning_review_contract import (
    _json_bytes as contract_json_bytes,
)
from tests.test_active_learning_review_contract import _valid_review as valid_v2_review
from tests.test_active_learning_review_contract import (
    _valid_selection as valid_v2_selection,
)

_FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED_V1_MANIFEST_BYTES = (
    _FIXTURES / "active_review_v1_expected_manifest.csv"
).read_bytes()
EXPECTED_V1_RESULTS_BYTES = (
    _FIXTURES / "active_review_v1_expected_results.json"
).read_bytes()
EXPECTED_V1_RESULT = json.loads(EXPECTED_V1_RESULTS_BYTES)


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
                "frame_count": 25000,
                "width": 1920,
                "height": 1080,
                "duration_seconds": 1000,
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
        events = []

        def add_event(index, start_seconds, action, confidence, side, duplicate=None):
            event_id = f"event-{index:03d}"
            events.append(
                {
                    "event_id": event_id,
                    "start_ms": int(start_seconds * 1000),
                    "end_ms": int((start_seconds + 1) * 1000),
                    "action": action,
                    "confidence": confidence,
                    "observed_sides": [side] if duplicate is None else ["far", "near"],
                    "source_event_ids": [f"{side}:{event_id}"],
                    "duplicate_group_id": duplicate,
                    "conflict_group_id": None,
                }
            )

        for index in range(20):
            add_event(index, 102 + index * 20, ("receive", "block", "dig")[index % 3], 0.72, "far")
        for index in range(8):
            add_event(20 + index, 520 + index * 20, ("set", "attack", "serve")[index % 3], 0.9, "far")
        for index in range(4):
            start_seconds = 700 + index * 20
            add_event(28 + index, start_seconds, "set", 0.3, "far", f"duplicate-{index:02d}")
        for index in range(4):
            add_event(32 + index, 800 + index * 20, "tip", 0.2, "near")
        merged["events"] = events
        for side in ("far", "near"):
            merged["input_runs"][side]["windows"].extend(
                [
                {
                    "window_index": 4 + index,
                    "start_seconds": start_seconds,
                    "end_seconds": start_seconds + 15,
                    "action": "background",
                    "confidence": 0.99,
                }
                for index, start_seconds in enumerate((0, 120, 900, 925, 950))
                ]
            )
        merged_path.write_text(json.dumps(merged) + "\n", encoding="utf-8")
        with mock.patch(
            "spiketrace.active_learning_selection.verify_dual_crop_review",
            return_value={"verified": True},
        ):
            selection = active_learning_selection.select_review_batch(
                merged_path,
                self.selection,
                repo_root=self.root,
                preferred_clip_seconds=5,
                min_clip_seconds=5,
                max_clip_seconds=5,
            )
        draft_clips = []
        receive_count = 0
        for clip in selection["clips"]:
            action = (
                {
                    "action": "receive",
                    "relative_start_seconds": 1,
                    "relative_end_seconds": 2,
                    "team_side": "far",
                    "note": "clean receive",
                }
                if clip["anchor"]["action"] == "receive" and receive_count < 1
                else {
                    "action": "background",
                    "relative_start_seconds": 0,
                    "relative_end_seconds": clip["duration_seconds"],
                    "team_side": "near",
                    "note": "",
                }
            )
            if action["action"] == "receive":
                receive_count += 1
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

    def test_persists_validated_time_precision_in_returned_and_saved_results(self):
        result = self.apply(max_background_windows=0)
        persisted = json.loads(self.output_results.read_text(encoding="utf-8"))

        self.assertEqual(result["time_precision_seconds"], 1)
        self.assertEqual(persisted["time_precision_seconds"], 1)

    def test_v1_result_and_bytes_do_not_drift_when_v2_output_module_is_imported(self):
        actual_before_import = self.apply(max_background_windows=0)

        self.assertEqual(actual_before_import, EXPECTED_V1_RESULT)
        self.assertEqual(self.output_manifest.read_bytes(), EXPECTED_V1_MANIFEST_BYTES)
        self.assertEqual(self.output_results.read_bytes(), EXPECTED_V1_RESULTS_BYTES)
        self.output_manifest.unlink()
        self.output_results.unlink()

        from spiketrace import _active_learning_review_outputs

        importlib.reload(_active_learning_review_outputs)
        actual = self.apply(max_background_windows=0)

        self.assertEqual(actual, EXPECTED_V1_RESULT)
        self.assertEqual(self.output_manifest.read_bytes(), EXPECTED_V1_MANIFEST_BYTES)
        self.assertEqual(self.output_results.read_bytes(), EXPECTED_V1_RESULTS_BYTES)


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
        self.assertEqual(result["settings"]["effective_max_background_windows"], 2)
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
        expected_input_hashes = {
            "selection_sha256": sha256_file(self.selection),
            "review_input_sha256": sha256_file(self.review_draft),
            "base_manifest_sha256": sha256_file(self.base_manifest),
        }
        result = self.apply(background_seed=42)
        persisted = json.loads(self.output_results.read_text(encoding="utf-8"))
        self.assertEqual(result, persisted)
        self.assertEqual(
            {
                field: persisted[field]
                for field in (
                    "selection_sha256",
                    "review_input_sha256",
                    "base_manifest_sha256",
                )
            },
            expected_input_hashes,
        )
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

    def test_rollback_surfaces_identity_and_unlink_failures(self):
        real_link = __import__("os").link
        real_unlink = Path.unlink

        def fail_second(source, destination):
            if Path(destination) == self.output_results:
                raise OSError("injected second-link failure")
            return real_link(source, destination)

        def fail_identity_check(*_args, **_kwargs):
            raise PermissionError("injected rollback identity failure")

        def fail_manifest_unlink(path, *args, **kwargs):
            if path == self.output_manifest:
                raise PermissionError("injected rollback unlink failure")
            return real_unlink(path, *args, **kwargs)

        cases = (
            (
                "identity check",
                mock.patch(
                    "spiketrace.active_learning_review.os.path.samefile",
                    side_effect=fail_identity_check,
                ),
                "verify manifest ownership during rollback",
            ),
            (
                "unlink",
                mock.patch.object(Path, "unlink", new=fail_manifest_unlink),
                "remove manifest during rollback",
            ),
        )
        for name, rollback_failure, message in cases:
            with self.subTest(name=name):
                try:
                    with (
                        mock.patch(
                            "spiketrace.active_learning_selection.verify_dual_crop_review",
                            return_value={"verified": True},
                        ),
                        mock.patch(
                            "spiketrace.active_learning_review.os.link",
                            side_effect=fail_second,
                        ),
                        rollback_failure,
                        self.assertRaisesRegex(ActiveLearningError, message) as caught,
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
                    self.assertIsInstance(caught.exception.__cause__, PermissionError)
                    self.assertTrue(self.output_manifest.exists())
                    self.assertFalse(self.output_results.exists())
                    self.assertEqual(self.temporary_siblings(), [])
                finally:
                    real_unlink(self.output_manifest, missing_ok=True)
                    real_unlink(self.output_results, missing_ok=True)

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

    def test_rejects_invalid_source_bounds_before_comparing_them(self):
        original_selection = json.loads(self.selection.read_text(encoding="utf-8"))
        original_draft = json.loads(self.review_draft.read_text(encoding="utf-8"))
        cases = (
            ("boolean start", "source_start_seconds", False, "preserve exact"),
            ("boolean end", "source_end_seconds", True, "preserve exact"),
            ("text start", "source_start_seconds", "0", "preserve exact"),
            ("null end", "source_end_seconds", None, "preserve exact"),
            (
                "oversized integer start",
                "source_start_seconds",
                10**400,
                "preserve exact",
            ),
            ("NaN start", "source_start_seconds", float("nan"), "non-finite"),
            ("infinite end", "source_end_seconds", float("inf"), "non-finite"),
            (
                "negative infinite start",
                "source_start_seconds",
                float("-inf"),
                "non-finite",
            ),
        )

        for name, field, value, message in cases:
            with self.subTest(name=name):
                selection = copy.deepcopy(original_selection)
                self.selection.write_text(
                    json.dumps(selection) + "\n", encoding="utf-8"
                )
                draft = copy.deepcopy(original_draft)
                draft["selection_sha256"] = sha256_file(self.selection)
                draft["clips"][0]["source_start_seconds"] = selection["clips"][0][
                    "start_seconds"
                ]
                draft["clips"][0]["source_end_seconds"] = selection["clips"][0][
                    "end_seconds"
                ]
                draft["clips"][0][field] = value
                self.review_draft.write_text(
                    json.dumps(draft) + "\n", encoding="utf-8"
                )

                with self.assertRaisesRegex(ActiveLearningError, message):
                    self.apply()
                self.assertFalse(self.output_manifest.exists())
                self.assertFalse(self.output_results.exists())

    def test_rejects_concurrent_replacement_of_each_audited_input(self):
        from spiketrace import active_learning_review

        inputs = {
            "selection": self.selection,
            "review input": self.review_draft,
            "base manifest": self.base_manifest,
        }
        originals = {name: path.read_bytes() for name, path in inputs.items()}
        real_read_source_table = active_learning_review._read_source_table

        for name, target in inputs.items():
            with self.subTest(name=name):
                for original_name, path in inputs.items():
                    path.write_bytes(originals[original_name])
                self.output_manifest.unlink(missing_ok=True)
                self.output_results.unlink(missing_ok=True)

                def read_then_replace(path, *, replacement=target, input_name=name):
                    table = real_read_source_table(path)
                    replacement.write_bytes(originals[input_name] + b" ")
                    return table

                try:
                    with (
                        mock.patch(
                            "spiketrace.active_learning_review._read_source_table",
                            side_effect=read_then_replace,
                        ),
                        self.assertRaisesRegex(
                            ActiveLearningError, f"{name.title()} changed"
                        ),
                    ):
                        self.apply(max_background_windows=0)
                    self.assertFalse(self.output_manifest.exists())
                    self.assertFalse(self.output_results.exists())
                finally:
                    self.output_manifest.unlink(missing_ok=True)
                    self.output_results.unlink(missing_ok=True)

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


class ApplyActiveReviewV2Tests(unittest.TestCase):
    def setUp(self):
        repository = Path(__file__).resolve().parents[1]
        self.repository = repository
        self.temporary = tempfile.TemporaryDirectory(dir=repository / "tests")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.video = self.directory / (
            "YTDown.com_YouTube_Rangitoto-vs-Taka-National-Final-Sets-1-_"
            "Media_k3PdQgm2jVs_001_1080p.mp4"
        )
        self.video.write_bytes(b"small-v2-video")
        video_sha256 = sha256_file(self.video)

        merged = json.loads(
            (repository / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_text(
                encoding="utf-8"
            )
        )
        merged["video"]["path"] = self.video.relative_to(repository).as_posix()
        for side in ("far", "near"):
            merged["input_runs"][side]["settings"]["video_sha256"] = video_sha256
        merged_bytes = contract_json_bytes(merged)
        self.merged = self.directory / "merged.json"
        self.merged.write_bytes(merged_bytes)

        selection = valid_v2_selection(self.directory, merged_bytes)
        selection["video"]["path"] = self.video.relative_to(repository).as_posix()
        selection["video"]["sha256"] = video_sha256
        selection_bytes = contract_json_bytes(selection)
        self.selection = self.directory / "selection.json"
        self.selection.write_bytes(selection_bytes)
        workbook_bytes = b"v2 workbook"
        (self.directory / "review.xlsx").write_bytes(workbook_bytes)
        overrides_bytes = b"{}\n"
        (self.directory / "overrides.json").write_bytes(overrides_bytes)
        review = valid_v2_review(
            selection, selection_bytes, workbook_bytes, overrides_bytes
        )
        self.review = self.directory / "review-v2.json"
        self.review.write_bytes(contract_json_bytes(review))

        self.base_manifest = self.directory / "base.csv"
        with self.base_manifest.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=(
                    "video_path", "start_seconds", "end_seconds", "label", "split",
                    "match_id",
                ),
            )
            writer.writeheader()
            writer.writerow({
                "video_path": self.video.name, "start_seconds": "1", "end_seconds": "2",
                "label": "serve", "split": "train", "match_id": "",
            })
        self.output_dir = self.directory / "bundle"
        self.verifier_patches = (
            mock.patch(
                "spiketrace._active_learning_review_contract._verify_merged_bytes",
                return_value={"verified": True},
            ),
            mock.patch(
                "spiketrace._active_learning_review_projection.verify_dual_crop_review_bytes",
                return_value={"verified": True},
            ),
        )
        for patcher in self.verifier_patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def apply(self, **kwargs):
        return apply_active_review_v2(
            self.base_manifest,
            self.selection,
            self.review,
            self.output_dir,
            repo_root=self.repository,
            legacy_base_match_id="legacy-match",
            review_match_id="review-match",
            video_root=self.directory,
            max_background_windows=0,
            **kwargs,
        )

    def test_publishes_v2_bundle_from_frozen_merged_artifact_and_returns_authority(self):
        from spiketrace import _active_learning_review_contract as contract_module
        from spiketrace import _active_learning_review_projection as projection_module
        from spiketrace._active_learning_review_contract import FrozenArtifact

        with mock.patch.object(
            contract_module,
            "snapshot_review_sources_v2",
            wraps=contract_module.snapshot_review_sources_v2,
        ) as snapshot, mock.patch.object(
            projection_module,
            "build_training_projection",
            wraps=projection_module.build_training_projection,
        ) as project:
            result = self.apply()

        self.assertEqual(snapshot.call_count, 1)
        self.assertIsInstance(project.call_args.args[2], FrozenArtifact)
        self.assertEqual(project.call_args.args[2].absolute_path, self.merged.resolve())
        self.assertEqual(result, json.loads((self.output_dir / "round-01-results.json").read_bytes()))
        self.assertTrue(result["sources"]["verification"]["source_video_file_checked"])
        records = load_manifest(
            self.output_dir / "action_training_round_01.csv",
            video_root=self.directory,
            require_files=True,
        )
        self.assertEqual(records[0].split, "train")

    def test_missing_video_is_allowed_only_without_file_requirement(self):
        original_exists = Path.exists
        video_key = os.path.normcase(os.path.abspath(self.video))

        def selective_exists(path):
            if os.path.normcase(os.path.abspath(path)) == video_key:
                return False
            return original_exists(path)

        with mock.patch.object(Path, "exists", selective_exists):
            result = self.apply(require_files=False)
        self.assertFalse(result["sources"]["verification"]["source_video_file_checked"])

        self.output_dir = self.directory / "required-bundle"
        with (
            mock.patch.object(Path, "exists", selective_exists),
            self.assertRaisesRegex(ValueError, "does not exist"),
        ):
            self.apply(require_files=True)
        self.assertFalse(self.output_dir.exists())

    def test_every_bound_source_mutation_in_publication_callback_prevents_final_directory(self):
        from spiketrace import _active_learning_review_outputs as output_module

        original_publish = output_module.publish_result_bundle

        def mutation_publisher(bound_source: Path, bound_bytes: bytes):
            def mutate_before_callback(output_dir, bundle, **kwargs):
                callback = kwargs["before_publish"]

                def wrapped_callback():
                    bound_source.write_bytes(bound_bytes + b"changed")
                    callback()

                kwargs["before_publish"] = wrapped_callback
                return original_publish(output_dir, bundle, **kwargs)

            return mutate_before_callback

        sources = (
            self.selection,
            self.review,
            self.directory / "review.xlsx",
            self.directory / "overrides.json",
            self.merged,
            self.base_manifest,
            self.video,
        )
        for index, source in enumerate(sources):
            with self.subTest(source=source.name):
                self.output_dir = self.directory / f"bundle-{index}"
                original_bytes = source.read_bytes()

                try:
                    with (
                        mock.patch(
                            "spiketrace._active_learning_review_outputs.publish_result_bundle",
                            side_effect=mutation_publisher(source, original_bytes),
                        ),
                        self.assertRaisesRegex(ValueError, "changed"),
                    ):
                        self.apply()
                finally:
                    source.write_bytes(original_bytes)
                self.assertFalse(self.output_dir.exists())
                self.assertEqual(
                    list(self.directory.glob(f".{self.output_dir.name}.staging-*")), []
                )

    def test_rejects_review_video_already_present_in_validation_split(self):
        rows = read_csv_rows(self.base_manifest)
        rows[0]["split"] = "val"
        with self.base_manifest.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

        with self.assertRaisesRegex(ActiveLearningError, "val or test"):
            self.apply()
        self.assertFalse(self.output_dir.exists())

    def test_rejects_invalid_guard_even_when_background_cap_is_zero(self):
        with self.assertRaisesRegex(ActiveLearningError, "finite and nonnegative"):
            self.apply(background_guard_seconds=float("nan"))

        self.assertFalse(self.output_dir.exists())

    def test_output_collision_preserves_first_complete_bundle(self):
        self.apply()
        expected = {
            path.name: path.read_bytes() for path in self.output_dir.iterdir()
        }

        with self.assertRaises(OSError):
            self.apply()

        self.assertEqual(
            {path.name: path.read_bytes() for path in self.output_dir.iterdir()},
            expected,
        )
        self.assertEqual(list(self.directory.glob(".bundle.staging-*")), [])


class ActiveReviewV2CliTests(unittest.TestCase):
    def test_apply_active_review_v2_parser_and_forwarding(self):
        args = build_parser().parse_args([
            "apply-active-review-v2", "base.csv", "selection.json", "review.json",
            "bundle", "--repo-root", ".", "--legacy-base-match-id", "legacy",
            "--review-match-id", "review", "--video-root", "videos",
            "--background-guard-seconds", "0.75", "--max-background-windows", "3",
            "--background-seed", "11", "--allow-missing-videos",
        ])

        with mock.patch(
            "spiketrace.active_learning_review.apply_active_review_v2",
            return_value={"ok": True},
        ) as apply_v2:
            self.assertEqual(run_command(args), {"ok": True})

        apply_v2.assert_called_once_with(
            Path("base.csv"), Path("selection.json"), Path("review.json"), Path("bundle"),
            repo_root=Path("."), legacy_base_match_id="legacy", review_match_id="review",
            video_root=Path("videos"), background_guard_seconds=0.75,
            max_background_windows=3, background_seed=11, require_files=False,
        )

    def test_verify_bundle_parser_and_forwarding(self):
        args = build_parser().parse_args([
            "verify-active-review-bundle", "bundle",
        ])

        with mock.patch(
            "spiketrace._active_learning_review_outputs.validate_result_bundle",
            return_value={"summary": {"training_rows": 3}},
        ) as validate:
            result = run_command(args)

        validate.assert_called_once_with(Path("bundle"))
        self.assertEqual(result, {"summary": {"training_rows": 3}})


if __name__ == "__main__":
    unittest.main()
