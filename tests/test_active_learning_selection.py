from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stderr
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from spiketrace import active_learning_selection
from spiketrace._active_learning_review_contract import load_review_selection_bytes
from spiketrace.active_learning_selection import (
    _relative_posix_path,
    load_review_selection,
    validate_merged_review_source,
    write_review_selection,
)
from spiketrace.dual_crop_review import build_dual_crop_review, verify_dual_crop_review
from spiketrace.errors import ActiveLearningError

ROOT = Path(__file__).resolve().parents[1]
DUAL_CROP_FIXTURES = ROOT / "tests" / "fixtures" / "dual_crop_review"


LITERAL_CLIPS = (
    {
        "clip_id": "clip-001",
        "ordinal": 1,
        "start_seconds": 0.0,
        "end_seconds": 1.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-002",
        "ordinal": 2,
        "start_seconds": 2.0,
        "end_seconds": 3.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-003",
        "ordinal": 3,
        "start_seconds": 4.0,
        "end_seconds": 5.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-004",
        "ordinal": 4,
        "start_seconds": 6.0,
        "end_seconds": 7.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-005",
        "ordinal": 5,
        "start_seconds": 8.0,
        "end_seconds": 9.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-006",
        "ordinal": 6,
        "start_seconds": 10.0,
        "end_seconds": 11.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-007",
        "ordinal": 7,
        "start_seconds": 12.0,
        "end_seconds": 13.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-008",
        "ordinal": 8,
        "start_seconds": 14.0,
        "end_seconds": 15.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-009",
        "ordinal": 9,
        "start_seconds": 16.0,
        "end_seconds": 17.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-010",
        "ordinal": 10,
        "start_seconds": 18.0,
        "end_seconds": 19.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-011",
        "ordinal": 11,
        "start_seconds": 20.0,
        "end_seconds": 21.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-012",
        "ordinal": 12,
        "start_seconds": 22.0,
        "end_seconds": 23.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-013",
        "ordinal": 13,
        "start_seconds": 24.0,
        "end_seconds": 25.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-014",
        "ordinal": 14,
        "start_seconds": 26.0,
        "end_seconds": 27.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-015",
        "ordinal": 15,
        "start_seconds": 28.0,
        "end_seconds": 29.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-016",
        "ordinal": 16,
        "start_seconds": 30.0,
        "end_seconds": 31.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-017",
        "ordinal": 17,
        "start_seconds": 32.0,
        "end_seconds": 33.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-018",
        "ordinal": 18,
        "start_seconds": 34.0,
        "end_seconds": 35.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-019",
        "ordinal": 19,
        "start_seconds": 36.0,
        "end_seconds": 37.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-020",
        "ordinal": 20,
        "start_seconds": 38.0,
        "end_seconds": 39.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-021",
        "ordinal": 21,
        "start_seconds": 40.0,
        "end_seconds": 41.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-022",
        "ordinal": 22,
        "start_seconds": 42.0,
        "end_seconds": 43.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-023",
        "ordinal": 23,
        "start_seconds": 44.0,
        "end_seconds": 45.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-024",
        "ordinal": 24,
        "start_seconds": 46.0,
        "end_seconds": 47.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-025",
        "ordinal": 25,
        "start_seconds": 48.0,
        "end_seconds": 49.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-026",
        "ordinal": 26,
        "start_seconds": 50.0,
        "end_seconds": 51.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-027",
        "ordinal": 27,
        "start_seconds": 52.0,
        "end_seconds": 53.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-028",
        "ordinal": 28,
        "start_seconds": 54.0,
        "end_seconds": 55.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-029",
        "ordinal": 29,
        "start_seconds": 56.0,
        "end_seconds": 57.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-030",
        "ordinal": 30,
        "start_seconds": 58.0,
        "end_seconds": 59.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-031",
        "ordinal": 31,
        "start_seconds": 60.0,
        "end_seconds": 61.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-032",
        "ordinal": 32,
        "start_seconds": 62.0,
        "end_seconds": 63.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-033",
        "ordinal": 33,
        "start_seconds": 64.0,
        "end_seconds": 65.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-034",
        "ordinal": 34,
        "start_seconds": 66.0,
        "end_seconds": 67.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-035",
        "ordinal": 35,
        "start_seconds": 68.0,
        "end_seconds": 69.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-036",
        "ordinal": 36,
        "start_seconds": 70.0,
        "end_seconds": 71.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-037",
        "ordinal": 37,
        "start_seconds": 72.0,
        "end_seconds": 73.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-038",
        "ordinal": 38,
        "start_seconds": 74.0,
        "end_seconds": 75.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-039",
        "ordinal": 39,
        "start_seconds": 76.0,
        "end_seconds": 77.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-040",
        "ordinal": 40,
        "start_seconds": 78.0,
        "end_seconds": 79.0,
        "duration_seconds": 1.0,
    },
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_directory_link(link: Path, target: Path) -> str:
    try:
        link.symlink_to(target, target_is_directory=True)
        return "symlink"
    except OSError as symlink_error:
        if os.name != "nt":
            raise
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise OSError(
                "Could not create a directory symlink or junction: "
                f"{completed.stderr or completed.stdout}"
            ) from symlink_error
        return "junction"


def make_valid_selection_payload(source, clip_count=40):
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
        "clips": copy.deepcopy(list(LITERAL_CLIPS[:clip_count])),
    }


def make_compact_merged_payload(root: Path, video_path: Path, checkpoint_path: Path):
    video_sha256 = sha256_file(video_path)
    checkpoint_sha256 = sha256_file(checkpoint_path)
    audit = {
        "source_file": "outputs/inference/events.json",
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
            "frame_count": 2500,
            "width": 1920,
            "height": 1080,
            "duration_seconds": 100.0,
        },
        "model_version": "rangitoto-test-v1",
        "settings": {
            "input_runs": {
                "far": copy.deepcopy(audit),
                "near": {
                    **copy.deepcopy(audit),
                    "source_file": "outputs/inference-near/events.json",
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


def make_selection_merged_payload(video_path: Path, checkpoint_path: Path):
    video_sha256 = sha256_file(video_path)
    checkpoint_sha256 = sha256_file(checkpoint_path)
    video = {
        "path": "data/video.mp4",
        "fps": 25.0,
        "frame_count": 30000,
        "width": 1920,
        "height": 1080,
        "duration_seconds": 1200.0,
    }
    shared_settings = {
        "checkpoint": "runs/model/best.pt",
        "checkpoint_sha256": checkpoint_sha256,
        "video_sha256": video_sha256,
    }
    events = []

    def add_event(
        event_id,
        slot,
        action,
        confidence,
        *,
        observed_sides=("far",),
        duplicate_group_id=None,
        conflict_group_id=None,
        offset_ms=0,
    ):
        center_ms = 7500 + slot * 15000 + offset_ms
        source_ids = [f"{side}:{event_id}" for side in observed_sides]
        events.append(
            {
                "video_id": "video",
                "event_id": event_id,
                "start_ms": center_ms - 500,
                "end_ms": center_ms + 500,
                "action": action,
                "confidence": confidence,
                "team_side": None,
                "player_number": None,
                "status": "needs_review" if conflict_group_id else "predicted",
                "model_version": "rangitoto-test-v1",
                "source": "dual_crop_merge",
                "side": observed_sides[0],
                "observed_sides": list(observed_sides),
                "source_event_refs": [],
                "duplicate_group_id": duplicate_group_id,
                "conflict_group_id": conflict_group_id,
                "merge_decision": (
                    "same_action_cross_side_deduped"
                    if duplicate_group_id
                    else "single_source"
                ),
                "source_event_ids": source_ids,
                "source_window_count": len(source_ids),
                "source_window_max_confidence": confidence,
                "primary_source_event_id": source_ids[0],
                "review_reason": (
                    "different_action_cross_side_conflict"
                    if conflict_group_id
                    else "single_source_candidate"
                ),
            }
        )

    minority_slots = {
        "receive": (0, 16, 32, 48, 64),
        "block": (8, 24, 40, 56, 72),
        "dig": (0, 16, 68, 74, 78),
    }
    for action, slots in minority_slots.items():
        for index, slot in enumerate(slots, start=1):
            paired = action in ("receive", "dig") and index <= 2
            add_event(
                f"minority-{action}-{index:02d}",
                slot,
                action,
                0.72,
                observed_sides=("far",) if action != "dig" else ("near",),
                conflict_group_id=f"cg-minority-pair-{index:02d}" if paired else None,
                offset_ms=0,
            )

    for index, slot in enumerate((2, 18, 34, 50, 66, 70, 76), start=1):
        add_event(
            f"conflict-{index:02d}",
            slot,
            "attack",
            0.88,
            observed_sides=("near",),
            conflict_group_id=f"cg-filler-{index:02d}",
        )
    for index, slot in enumerate((4, 12, 20, 28, 36, 44, 52, 60), start=1):
        add_event(
            f"tail-{index:02d}",
            slot,
            ("set", "attack", "serve")[(index - 1) % 3],
            0.91 - index / 100,
            observed_sides=("far",) if index % 2 else ("near",),
        )
    for index, slot in enumerate((6, 22, 38, 54), start=1):
        add_event(
            f"dual-{index:02d}",
            slot,
            "set",
            0.3,
            observed_sides=("far", "near"),
            duplicate_group_id=f"dg-{index:02d}",
        )
    for index, slot in enumerate((10, 26, 46, 62), start=1):
        add_event(
            f"random-{index:02d}",
            slot,
            "tip",
            0.25,
            observed_sides=("far",) if index % 2 else ("near",),
        )

    extra_slots = (
        1,
        3,
        5,
        7,
        9,
        11,
        13,
        15,
        17,
        19,
        21,
        23,
        25,
        27,
        29,
        31,
        33,
        35,
        37,
        39,
        41,
        43,
        45,
        47,
        49,
        51,
        53,
        55,
        57,
        59,
        61,
        63,
        65,
        67,
        69,
        71,
        73,
        75,
        77,
        79,
    )
    for index, slot in enumerate(extra_slots[:20], start=1):
        add_event(
            f"tail-reserve-{index:02d}",
            slot,
            ("set", "attack", "serve")[(index - 1) % 3],
            0.41 + index / 1000,
            observed_sides=("far",) if index % 2 else ("near",),
        )
    for index, slot in enumerate(extra_slots[20:32], start=1):
        add_event(
            f"dual-reserve-{index:02d}",
            slot,
            "set",
            0.3,
            observed_sides=("far", "near"),
            duplicate_group_id=f"dg-reserve-{index:02d}",
        )
    for index, slot in enumerate(extra_slots[32:36], start=1):
        add_event(
            f"random-reserve-{index:02d}",
            slot,
            "tip",
            0.2,
            observed_sides=("far",) if index % 2 else ("near",),
        )

    windows = {"far": [], "near": []}
    for side in ("far", "near"):
        for index, slot in enumerate((14, 30, 42, 58, *extra_slots[36:])):
            center_seconds = 7.5 + slot * 15
            windows[side].append(
                {
                    "window_index": index,
                    "start_seconds": center_seconds - 3,
                    "end_seconds": center_seconds + 3,
                    "action": "background",
                    "confidence": 0.99,
                }
            )

    audit = {
        "source_file": "outputs/inference/far.json",
        "source_file_sha256": "1" * 64,
        "normalized_payload_sha256": "2" * 64,
    }
    events.sort(key=lambda item: (item["start_ms"], item["end_ms"], item["event_id"]))
    return {
        "format_version": 2,
        "merge_format_version": 2,
        "video": video,
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
            "far": {
                "settings": {**shared_settings, "crop": [0, 0, 1920, 645]},
                "windows": windows["far"],
            },
            "near": {
                "settings": {**shared_settings, "crop": [0, 255, 1920, 1080]},
                "windows": windows["near"],
            },
        },
        "events": events,
        "duplicate_groups": [],
        "conflict_groups": [],
    }


class FiveBucketSelectionTests(unittest.TestCase):
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
            json.dumps(
                make_selection_merged_payload(self.video_path, self.checkpoint_path),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def select_fixture(
        self,
        *,
        seed=42,
        filename="selection.json",
        rewrite_event_ids=False,
        merged_mutator=None,
        **kwargs,
    ):
        selector = getattr(active_learning_selection, "select_review_batch", None)
        self.assertIsNotNone(selector, "select_review_batch API is missing")
        output_path = self.root / "selections" / filename
        setattr(self, filename.replace(".json", "_path").replace("-", "_"), output_path)
        merged_json = self.merged_json
        if rewrite_event_ids or merged_mutator is not None:
            merged = json.loads(self.merged_json.read_text(encoding="utf-8"))
            if rewrite_event_ids:
                for event in merged["events"]:
                    event["event_id"] = f"rewritten-{event['event_id']}"
                    event["source_event_ids"] = [
                        f"rewritten-{source_id}"
                        for source_id in event["source_event_ids"]
                    ]
            if merged_mutator is not None:
                merged_mutator(merged)
            merged_json = self.root / "outputs" / "review" / f"merged-{filename}"
            merged_json.write_text(
                json.dumps(merged, indent=2) + "\n", encoding="utf-8"
            )
        with patch(
            "spiketrace.active_learning_selection.verify_dual_crop_review",
            return_value={"verified": True},
        ):
            return selector(
                merged_json,
                output_path,
                repo_root=self.root,
                seed=seed,
                **kwargs,
            )

    def test_selects_exact_quotas_and_all_available_minority_candidates_first(self):
        payload = self.select_fixture(seed=42)
        counts = Counter(clip["selection_bucket"] for clip in payload["clips"])
        self.assertEqual(
            counts,
            {
                "conflict_or_minority": 20,
                "high_confidence_tail": 8,
                "dual_view_agreement": 4,
                "random_candidate_control": 4,
                "dual_background_control": 4,
            },
        )
        expected_minority_ids = {
            f"minority-{action}-{index:02d}"
            for action in ("receive", "block", "dig")
            for index in range(1, 6)
        }
        covered_minority_ids = {
            hint["canonical_event_id"]
            for clip in payload["clips"]
            for hint in clip["candidate_hints"]
            if hint["canonical_event_id"] in expected_minority_ids
        }
        self.assertEqual(covered_minority_ids, expected_minority_ids)
        self.assertEqual(len(payload["clips"]), 40)

    def test_same_seed_produces_byte_identical_json(self):
        first = self.select_fixture(seed=42, filename="first.json")
        second = self.select_fixture(seed=42, filename="second.json")
        self.assertEqual(first, second)
        self.assertEqual(self.first_path.read_bytes(), self.second_path.read_bytes())

    def test_enforces_time_and_source_uniqueness(self):
        payload = self.select_fixture(seed=42)
        clips = payload["clips"]
        self.assertTrue(
            all(5.0 <= clip["duration_seconds"] <= 30.0 for clip in clips)
        )
        self.assertEqual(len({clip["clip_id"] for clip in clips}), 40)
        self.assertGreaterEqual(len({clip["time_stratum"] for clip in clips}), 10)
        self.assertFalse(
            any(
                left["start_seconds"] < right["end_seconds"]
                and right["start_seconds"] < left["end_seconds"]
                for index, left in enumerate(clips)
                for right in clips[index + 1 :]
            )
        )
        source_ids = [
            source_id
            for clip in clips
            for source_id in clip["reserved_source_event_ids"]
        ]
        self.assertEqual(len(source_ids), len(set(source_ids)))

    def test_excludes_prior_clip_time_even_when_new_event_ids_differ(self):
        previous = self.select_fixture(filename="round-01.json")
        current = self.select_fixture(
            filename="round-02.json",
            round_number=2,
            previous_selection_paths=[self.round_01_path],
            rewrite_event_ids=True,
        )
        self.assertTrue(
            all(
                not (
                    old["start_seconds"] < new["end_seconds"]
                    and new["start_seconds"] < old["end_seconds"]
                )
                for old in previous["clips"]
                for new in current["clips"]
            )
        )
        self.assertEqual(
            current["previous_selections"],
            [
                {
                    "path": "selections/round-01.json",
                    "sha256": sha256_file(self.round_01_path),
                    "batch_id": previous["batch_id"],
                    "round_id": "round-01",
                }
            ],
        )

    def test_rejects_a_previous_selection_for_a_different_video(self):
        self.select_fixture(filename="round-01.json")
        other_video = self.root / "data" / "other.mp4"
        other_video.write_bytes(b"other video")

        def use_other_video(merged):
            merged["video"]["path"] = "data/other.mp4"
            for run in merged["input_runs"].values():
                run["settings"]["video_sha256"] = sha256_file(other_video)

        with self.assertRaisesRegex(ActiveLearningError, "same source video"):
            self.select_fixture(
                filename="cross-video-round-02.json",
                round_number=2,
                previous_selection_paths=[self.round_01_path],
                merged_mutator=use_other_video,
            )

    def test_loader_rejects_a_clip_overlapping_a_persisted_previous_selection(self):
        previous_path = self.root / "selections" / "task-1-round-01.json"
        previous = self.select_fixture(filename=previous_path.name)
        current = self.select_fixture(filename="round-02.json", round_number=2)

        current["previous_selections"] = [
            {
                "path": "selections/task-1-round-01.json",
                "sha256": sha256_file(previous_path),
                "batch_id": previous["batch_id"],
                "round_id": previous["round_id"],
            }
        ]
        self.round_02_path.write_text(
            json.dumps(current, indent=2) + "\n", encoding="utf-8"
        )
        with (
            patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            self.assertRaisesRegex(ActiveLearningError, "previous selection"),
        ):
            load_review_selection(self.round_02_path, repo_root=self.root)

    def test_serializes_exact_clip_surface_and_retains_coalesced_hints(self):
        payload = self.select_fixture()
        self.assertEqual(
            tuple(payload["clips"][0]),
            (
                "clip_id",
                "ordinal",
                "start_seconds",
                "end_seconds",
                "start_time",
                "end_time",
                "duration_seconds",
                "time_stratum",
                "selection_bucket",
                "selection_reasons",
                "proxy_filename",
                "anchor",
                "candidate_hints",
                "reserved_source_event_ids",
                "reserved_duplicate_group_ids",
                "reserved_conflict_group_ids",
            ),
        )
        paired = next(
            clip
            for clip in payload["clips"]
            if {
                "minority-receive-01",
                "minority-dig-01",
            }.issubset(
                {
                    hint["canonical_event_id"]
                    for hint in clip["candidate_hints"]
                }
            )
        )
        self.assertEqual(
            [hint["canonical_event_id"] for hint in paired["candidate_hints"]],
            ["minority-receive-01", "minority-dig-01"],
        )
        self.assertEqual(
            paired["reserved_conflict_group_ids"], ["cg-minority-pair-01"]
        )
        self.assertEqual(
            paired["reserved_source_event_ids"],
            ["far:minority-receive-01", "near:minority-dig-01"],
        )

    def test_loader_rejects_malformed_task_two_business_surfaces(self):
        mutations = {
            "settings": lambda payload: payload["settings"].update(time_strata=9),
            "previous selection": lambda payload: payload[
                "previous_selections"
            ].append({"path": "selections/round-00.json"}),
            "quota summary": lambda payload: payload["quota_summary"][0].update(
                selected=19
            ),
            "coverage": lambda payload: payload["coverage"].update(
                represented_time_strata_count=9
            ),
            "clip surface": lambda payload: payload["clips"][0].pop("anchor"),
        }
        for index, (name, mutate) in enumerate(mutations.items(), start=1):
            with self.subTest(name=name):
                path = self.root / "selections" / f"malformed-{index}.json"
                self.select_fixture(filename=path.name)
                payload = json.loads(path.read_text(encoding="utf-8"))
                mutate(payload)
                path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                with (
                    patch(
                        "spiketrace.active_learning_selection.verify_dual_crop_review",
                        return_value={"verified": True},
                    ),
                    self.assertRaises(ActiveLearningError),
                ):
                    load_review_selection(path, repo_root=self.root)

    def test_loader_rejects_selection_v1_with_all_task_two_surfaces_stripped(self):
        path = self.root / "selections" / "stripped-task-two-surfaces.json"
        self.select_fixture(filename=path.name)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["settings"] = {}
        payload["quota_summary"] = []
        payload["coverage"] = {}
        base_clip_fields = {
            "clip_id",
            "ordinal",
            "start_seconds",
            "end_seconds",
            "duration_seconds",
        }
        payload["clips"] = [
            {key: value for key, value in clip.items() if key in base_clip_fields}
            for clip in payload["clips"]
        ]
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        with (
            patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            self.assertRaisesRegex(ActiveLearningError, "selection settings"),
        ):
            load_review_selection(path, repo_root=self.root)

    def test_loader_enforces_persisted_clip_duration_bounds(self):
        mutations = (
            ("min", "min_clip_seconds", 16.0),
            ("max", "max_clip_seconds", 14.0),
        )
        for name, field, value in mutations:
            with self.subTest(bound=name):
                path = self.root / "selections" / f"duration-{name}.json"
                self.select_fixture(filename=path.name)
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["settings"][field] = value
                path.write_text(
                    json.dumps(payload, indent=2) + "\n", encoding="utf-8"
                )
                with (
                    patch(
                        "spiketrace.active_learning_selection.verify_dual_crop_review",
                        return_value={"verified": True},
                    ),
                    self.assertRaisesRegex(ActiveLearningError, "duration bounds"),
                ):
                    load_review_selection(path, repo_root=self.root)

    def test_loader_enforces_the_persisted_anchor_gap(self):
        path = self.root / "selections" / "persisted-anchor-gap.json"
        self.select_fixture(filename=path.name)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["settings"]["min_anchor_gap_seconds"] = 30.0
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        with (
            patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            self.assertRaisesRegex(ActiveLearningError, "anchor gap"),
        ):
            load_review_selection(path, repo_root=self.root)

    def test_loader_requires_canonical_hint_ids_to_be_globally_unique(self):
        path = self.root / "selections" / "duplicate-hint.json"
        payload = self.select_fixture(filename=path.name)
        target_index = next(
            index
            for index, clip in enumerate(payload["clips"])
            if index > 0 and len(clip["candidate_hints"]) > 1
        )
        earlier_id = next(
            hint["canonical_event_id"]
            for clip in payload["clips"][:target_index]
            for hint in clip["candidate_hints"]
        )
        target = payload["clips"][target_index]
        target_hint = next(
            hint
            for hint in target["candidate_hints"]
            if hint["canonical_event_id"] != target["anchor"]["canonical_event_id"]
        )
        target_hint["canonical_event_id"] = earlier_id
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        with (
            patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            self.assertRaisesRegex(ActiveLearningError, "globally unique"),
        ):
            load_review_selection(path, repo_root=self.root)

    def test_loader_matches_each_hint_to_its_merged_canonical_event(self):
        path = self.root / "selections" / "hint-semantics.json"
        payload = self.select_fixture(filename=path.name)
        hint = next(
            hint
            for clip in payload["clips"]
            for hint in clip["candidate_hints"]
        )
        hint["action"] = "tampered-action"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        with (
            patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            self.assertRaisesRegex(ActiveLearningError, "merged canonical event"),
        ):
            load_review_selection(path, repo_root=self.root)

    def test_loader_reconstructs_minority_coverage_from_the_merged_source(self):
        path = self.root / "selections" / "minority-coverage.json"
        payload = self.select_fixture(filename=path.name)
        payload["coverage"]["available_minority_candidate_ids"].pop()
        payload["coverage"]["covered_minority_candidate_ids"].pop()
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        with (
            patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            self.assertRaisesRegex(ActiveLearningError, "merged minority"),
        ):
            load_review_selection(path, repo_root=self.root)

    def test_loader_reconstructs_scene_coverage_from_source_and_hints(self):
        path = self.root / "selections" / "scene-coverage.json"
        payload = self.select_fixture(filename=path.name)
        payload["coverage"]["available_crop_scenes"] = ["far"]
        payload["coverage"]["represented_crop_scenes"] = ["far"]
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        with (
            patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            self.assertRaisesRegex(ActiveLearningError, "scene coverage"),
        ):
            load_review_selection(path, repo_root=self.root)

    def test_transfers_a_dual_view_shortfall_to_random_control(self):
        def leave_three_dual_candidates(merged):
            dual_ids = {
                event["event_id"]
                for event in merged["events"]
                if event["duplicate_group_id"] is not None
                and event["conflict_group_id"] is None
            }
            keep = set(sorted(dual_ids)[:3])
            merged["events"] = [
                event
                for event in merged["events"]
                if event["event_id"] not in dual_ids or event["event_id"] in keep
            ]

        payload = self.select_fixture(
            filename="quota-transfer.json",
            merged_mutator=leave_three_dual_candidates,
        )
        counts = Counter(clip["selection_bucket"] for clip in payload["clips"])
        self.assertEqual(counts["dual_view_agreement"], 3)
        self.assertEqual(counts["random_candidate_control"], 5)
        self.assertEqual(
            payload["quota_summary"][2],
            {
                "bucket": "dual_view_agreement",
                "planned": 4,
                "selected": 3,
                "transferred_out": 1,
                "transferred_to": "random_candidate_control",
                "reason": "eligible_pool_exhausted",
            },
        )

    def test_random_control_draws_from_every_remaining_canonical_candidate(self):
        def remove_tip_specific_controls(merged):
            merged["events"] = [
                event for event in merged["events"] if event["action"] != "tip"
            ]

        payload = self.select_fixture(
            filename="all-remaining-control.json",
            merged_mutator=remove_tip_specific_controls,
        )
        counts = Counter(clip["selection_bucket"] for clip in payload["clips"])
        self.assertEqual(counts["random_candidate_control"], 4)
        self.assertEqual(counts["dual_background_control"], 4)

    def test_tail_bucket_uses_descending_confidence_after_primary_actions(self):
        def leave_three_primary_tail_candidates(merged):
            merged["events"] = [
                event
                for event in merged["events"]
                if not event["event_id"].startswith("tail-")
                or event["event_id"] in {"tail-01", "tail-02", "tail-03"}
            ]

        payload = self.select_fixture(
            filename="tail-fallback.json",
            merged_mutator=leave_three_primary_tail_candidates,
        )
        tail_clips = [
            clip
            for clip in payload["clips"]
            if clip["selection_bucket"] == "high_confidence_tail"
        ]
        self.assertEqual(len(tail_clips), 8)
        tail_hint_ids = {
            hint["canonical_event_id"]
            for clip in tail_clips
            for hint in clip["candidate_hints"]
        }
        self.assertTrue({"tail-01", "tail-02", "tail-03"}.issubset(tail_hint_ids))

    def test_fails_instead_of_returning_fewer_than_forty_clips(self):
        def remove_last_legal_candidates(merged):
            merged["events"] = [
                event
                for event in merged["events"]
                if "-reserve-" not in event["event_id"]
                and event["event_id"] != "random-04"
            ]
            for run in merged["input_runs"].values():
                run["windows"] = run["windows"][:4]

        with self.assertRaisesRegex(ActiveLearningError, "exactly 40"):
            self.select_fixture(
                filename="too-few.json",
                merged_mutator=remove_last_legal_candidates,
            )

    def test_rejects_a_batch_with_fewer_than_ten_represented_strata(self):
        def double_video_duration(merged):
            merged["video"]["duration_seconds"] = 2400.0
            merged["video"]["frame_count"] = 60000

        with self.assertRaisesRegex(ActiveLearningError, "at least 10 time strata"):
            self.select_fixture(
                filename="too-few-strata.json",
                merged_mutator=double_video_duration,
            )

    def test_rejects_an_unfittable_minority_cluster_without_losing_ids(self):
        def link_distant_minority_events(merged):
            for event in merged["events"]:
                if event["event_id"] in {
                    "minority-receive-05",
                    "minority-dig-05",
                }:
                    event["conflict_group_id"] = "cg-distant-minority"

        with self.assertRaisesRegex(
            ActiveLearningError,
            "minority-receive-05.*minority-dig-05",
        ):
            self.select_fixture(
                filename="unfittable-minority.json",
                merged_mutator=link_distant_minority_events,
            )

    def test_names_a_single_unfittable_minority_candidate(self):
        def lengthen_one_minority_event(merged):
            event = next(
                event
                for event in merged["events"]
                if event["event_id"] == "minority-block-01"
            )
            event["end_ms"] = event["start_ms"] + 31000

        with self.assertRaisesRegex(
            ActiveLearningError,
            "Required minority cluster.*minority-block-01",
        ):
            self.select_fixture(
                filename="unfittable-single-minority.json",
                merged_mutator=lengthen_one_minority_event,
            )

    def test_names_every_unfittable_minority_candidate_in_one_error(self):
        def lengthen_two_minority_events(merged):
            bounds = {
                "minority-receive-05": (900000, 940000),
                "minority-block-05": (1000000, 1040000),
            }
            for event in merged["events"]:
                if event["event_id"] in bounds:
                    event["start_ms"], event["end_ms"] = bounds[event["event_id"]]

        with self.assertRaisesRegex(
            ActiveLearningError,
            "minority-receive-05.*minority-block-05",
        ):
            self.select_fixture(
                filename="multiple-unfittable-minority.json",
                merged_mutator=lengthen_two_minority_events,
            )

    def test_skips_an_unfittable_nonminority_candidate(self):
        def lengthen_nonminority_event(merged):
            event = next(
                event
                for event in merged["events"]
                if event["event_id"] == "tail-reserve-20"
            )
            event["start_ms"] = 570000
            event["end_ms"] = 610000

        payload = self.select_fixture(
            filename="unfittable-nonminority.json",
            merged_mutator=lengthen_nonminority_event,
        )
        hint_ids = {
            hint["canonical_event_id"]
            for clip in payload["clips"]
            for hint in clip["candidate_hints"]
        }
        self.assertEqual(len(payload["clips"]), 40)
        self.assertNotIn("tail-reserve-20", hint_ids)

    def test_rejects_when_both_available_scenes_are_not_in_candidate_hints(self):
        def leave_unselected_near_evidence(merged):
            for event in merged["events"]:
                event["observed_sides"] = ["far"]
                event["source_event_ids"] = [f"far:{event['event_id']}"]
            reserve = next(
                event
                for event in merged["events"]
                if event["event_id"] == "tail-reserve-01"
            )
            reserve["observed_sides"] = ["near"]
            reserve["source_event_ids"] = ["near:tail-reserve-01"]

        with self.assertRaisesRegex(ActiveLearningError, "both available crop scenes"):
            self.select_fixture(
                filename="missing-near.json",
                merged_mutator=leave_unselected_near_evidence,
            )

    def test_allows_selection_when_only_one_crop_scene_is_available(self):
        def make_far_only(merged):
            for event in merged["events"]:
                event["observed_sides"] = ["far"]
                event["source_event_ids"] = [f"far:{event['event_id']}"]

        payload = self.select_fixture(
            filename="far-only.json", merged_mutator=make_far_only
        )
        self.assertEqual(payload["coverage"]["available_crop_scenes"], ["far"])
        self.assertEqual(payload["coverage"]["represented_crop_scenes"], ["far"])

    def test_enforces_the_minimum_anchor_gap_in_integer_milliseconds(self):
        payload = self.select_fixture(filename="anchor-gap.json")
        anchors = [clip["anchor"] for clip in payload["clips"]]
        for index, left in enumerate(anchors):
            left_start = round(left["start_seconds"] * 1000)
            left_end = round(left["end_seconds"] * 1000)
            for right in anchors[index + 1 :]:
                right_start = round(right["start_seconds"] * 1000)
                right_end = round(right["end_seconds"] * 1000)
                gap = max(left_start, right_start) - min(left_end, right_end)
                self.assertGreaterEqual(gap, 5000)

    def test_stratified_controls_visit_distinct_strata_before_repeating(self):
        payload = self.select_fixture(filename="stratified-controls.json")
        for bucket in (
            "random_candidate_control",
            "dual_background_control",
        ):
            strata = [
                clip["time_stratum"]
                for clip in payload["clips"]
                if clip["selection_bucket"] == bucket
            ]
            self.assertEqual(len(strata), 4)
            self.assertEqual(len(set(strata)), 4)

    def test_bucket_priority_is_stable_and_exclusive(self):
        payload = self.select_fixture(filename="bucket-priority.json")
        buckets_by_hint = {
            hint["canonical_event_id"]: clip["selection_bucket"]
            for clip in payload["clips"]
            for hint in clip["candidate_hints"]
        }
        self.assertEqual(
            {buckets_by_hint[f"tail-{index:02d}"] for index in range(1, 9)},
            {"high_confidence_tail"},
        )
        self.assertEqual(
            {
                event_id: buckets_by_hint[event_id]
                for event_id in (
                    "dual-01",
                    "dual-02",
                    "dual-03",
                    "dual-reserve-01",
                )
            },
            {
                "dual-01": "dual_view_agreement",
                "dual-02": "dual_view_agreement",
                "dual-03": "dual_view_agreement",
                "dual-reserve-01": "dual_view_agreement",
            },
        )
        self.assertEqual(len(buckets_by_hint), len(set(buckets_by_hint)))

    def test_agreement_bucket_accepts_high_confidence_duplicates_left_by_tail(self):
        def make_agreement_candidates_high_confidence(merged):
            for event in merged["events"]:
                if (
                    event["duplicate_group_id"] is not None
                    and event["conflict_group_id"] is None
                ):
                    event["action"] = "set"
                    event["confidence"] = 0.8

        payload = self.select_fixture(
            filename="high-confidence-agreement.json",
            merged_mutator=make_agreement_candidates_high_confidence,
        )
        agreement_clips = [
            clip
            for clip in payload["clips"]
            if clip["selection_bucket"] == "dual_view_agreement"
        ]
        self.assertEqual(len(agreement_clips), 4)
        self.assertTrue(
            all(
                hint["duplicate_group_id"] is not None
                and hint["action"] == "set"
                and hint["confidence"] == 0.8
                for clip in agreement_clips
                for hint in clip["candidate_hints"]
            )
        )

    def test_dual_background_controls_use_five_second_continuous_intervals(self):
        payload = self.select_fixture(filename="background-intervals.json")
        background_clips = [
            clip
            for clip in payload["clips"]
            if clip["selection_bucket"] == "dual_background_control"
        ]
        self.assertEqual(len(background_clips), 4)
        for clip in background_clips:
            anchor = clip["anchor"]
            self.assertGreaterEqual(
                anchor["end_seconds"] - anchor["start_seconds"], 5.0
            )
            self.assertEqual(anchor["observed_sides"], ["far", "near"])
            self.assertEqual(clip["candidate_hints"], [])

    def test_splits_a_long_continuous_background_run_into_multiple_controls(self):
        def use_one_long_background_run(merged):
            for run in merged["input_runs"].values():
                run["windows"] = [
                    {
                        "window_index": 0,
                        "start_seconds": 0.0,
                        "end_seconds": 1200.0,
                        "action": "background",
                        "confidence": 0.99,
                    }
                ]

        payload = self.select_fixture(
            filename="long-background-run.json",
            merged_mutator=use_one_long_background_run,
        )
        background_clips = [
            clip
            for clip in payload["clips"]
            if clip["selection_bucket"] == "dual_background_control"
        ]
        self.assertEqual(len(background_clips), 4)
        self.assertEqual(len({clip["time_stratum"] for clip in background_clips}), 4)
        self.assertTrue(
            all(5.0 <= clip["duration_seconds"] <= 30.0 for clip in background_clips)
        )

    def test_merges_overlapping_nonrequired_clips_across_a_stratum_boundary(self):
        def overlap_tail_candidates(merged):
            bounds = {
                "tail-01": (214000, 215000),
                "tail-02": (222000, 223000),
            }
            for event in merged["events"]:
                if event["event_id"] in bounds:
                    event["start_ms"], event["end_ms"] = bounds[event["event_id"]]

        payload = self.select_fixture(
            filename="overlap-across-strata.json",
            merged_mutator=overlap_tail_candidates,
            time_strata=11,
        )
        merged_clip = next(
            clip
            for clip in payload["clips"]
            if {"tail-01", "tail-02"}.issubset(
                {
                    hint["canonical_event_id"]
                    for hint in clip["candidate_hints"]
                }
            )
        )
        self.assertLessEqual(merged_clip["duration_seconds"], 30.0)
        self.assertEqual(len(payload["clips"]), 40)

    def test_merges_three_overlapping_candidates_without_duplicate_reasons(self):
        def overlap_three_tail_candidates(merged):
            bounds = {
                "tail-01": (214000, 215000),
                "tail-02": (222000, 223000),
                "tail-03": (229000, 230000),
            }
            for event in merged["events"]:
                if event["event_id"] in bounds:
                    event["start_ms"], event["end_ms"] = bounds[event["event_id"]]

        payload = self.select_fixture(
            filename="three-overlapping-tail-candidates.json",
            merged_mutator=overlap_three_tail_candidates,
        )
        merged_clip = next(
            clip
            for clip in payload["clips"]
            if {"tail-01", "tail-02", "tail-03"}.issubset(
                {
                    hint["canonical_event_id"]
                    for hint in clip["candidate_hints"]
                }
            )
        )
        self.assertEqual(
            len(merged_clip["selection_reasons"]),
            len(set(merged_clip["selection_reasons"])),
        )


class SelectionCliTests(unittest.TestCase):
    def test_dispatches_every_selection_argument_and_preserves_prior_order(self):
        from spiketrace.cli import build_parser, run_command

        args = build_parser().parse_args(
            [
                "select-review-batch",
                "outputs/review/merged.json",
                "selections/round-02.json",
                "--repo-root",
                ".",
                "--round-number",
                "2",
                "--seed",
                "99",
                "--preferred-clip-seconds",
                "16",
                "--min-clip-seconds",
                "6",
                "--max-clip-seconds",
                "29",
                "--min-anchor-gap-seconds",
                "7",
                "--time-strata",
                "12",
                "--previous-selection",
                "selections/round-00.json",
                "--previous-selection",
                "selections/round-01.json",
            ]
        )
        expected = {"batch_id": "video-round-02"}
        with patch(
            "spiketrace.active_learning_selection.select_review_batch",
            return_value=expected,
        ) as selector:
            self.assertEqual(run_command(args), expected)
        selector.assert_called_once_with(
            Path("outputs/review/merged.json"),
            Path("selections/round-02.json"),
            repo_root=Path("."),
            round_number=2,
            seed=99,
            preferred_clip_seconds=16.0,
            min_clip_seconds=6.0,
            max_clip_seconds=29.0,
            min_anchor_gap_seconds=7.0,
            time_strata=12,
            previous_selection_paths=[
                Path("selections/round-00.json"),
                Path("selections/round-01.json"),
            ],
        )

    def test_argparse_rejects_invalid_selection_ranges_before_dispatch(self):
        from spiketrace.cli import build_parser

        invalid_options = (
            ("--preferred-clip-seconds", "0"),
            ("--min-clip-seconds", "-1"),
            ("--max-clip-seconds", "0"),
            ("--min-anchor-gap-seconds", "0"),
            ("--time-strata", "9"),
        )
        for option, value in invalid_options:
            with self.subTest(option=option):
                stderr = io.StringIO()
                with redirect_stderr(stderr), self.assertRaises(SystemExit):
                    build_parser().parse_args(
                        [
                            "select-review-batch",
                            "merged.json",
                            "selection.json",
                            "--repo-root",
                            ".",
                            option,
                            value,
                        ]
                    )
                self.assertNotIn("invalid choice: 'select-review-batch'", stderr.getvalue())

    def test_argparse_rejects_non_finite_positive_floats(self):
        from spiketrace.cli import build_parser

        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value):
                stderr = io.StringIO()
                with redirect_stderr(stderr), self.assertRaises(SystemExit):
                    build_parser().parse_args(
                        [
                            "select-review-batch",
                            "merged.json",
                            "selection.json",
                            "--repo-root",
                            ".",
                            f"--preferred-clip-seconds={value}",
                        ]
                    )
                self.assertIn("positive finite number", stderr.getvalue())

        parsed = build_parser().parse_args(
            [
                "select-review-batch",
                "merged.json",
                "selection.json",
                "--repo-root",
                ".",
                "--preferred-clip-seconds",
                "12.5",
            ]
        )
        self.assertEqual(parsed.preferred_clip_seconds, 12.5)


class SelectionContractTests(unittest.TestCase):
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
            json.dumps(
                make_selection_merged_payload(self.video_path, self.checkpoint_path),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.output_json = self.root / "selections" / "round-01.json"
        with patch(
            "spiketrace.active_learning_selection.verify_dual_crop_review",
            return_value={"verified": True},
        ):
            self.source = validate_merged_review_source(
                self.merged_json,
                repo_root=self.root,
            )
            seed_output = self.root / "selections" / "seed.json"
            self.selection_payload = active_learning_selection.select_review_batch(
                self.merged_json,
                seed_output,
                repo_root=self.root,
            )
            seed_output.unlink()

    def _write_selection(self, payload=None):
        with patch(
            "spiketrace.active_learning_selection.verify_dual_crop_review",
            return_value={"verified": True},
        ):
            return write_review_selection(
                self.selection_payload if payload is None else payload,
                self.output_json,
                repo_root=self.root,
            )

    def _load_selection(self):
        with patch(
            "spiketrace.active_learning_selection.verify_dual_crop_review",
            return_value={"verified": True},
        ):
            return load_review_selection(self.output_json, repo_root=self.root)

    def _write_raw_selection(self, payload):
        self.output_json.parent.mkdir(parents=True, exist_ok=True)
        self.output_json.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )

    def test_writes_source_and_video_hashes_without_a_generated_timestamp(self):
        source = self.source
        payload = copy.deepcopy(self.selection_payload)
        self._write_selection(payload)
        self.assertEqual(payload["format_version"], 1)
        self.assertEqual(
            payload["selection_algorithm_version"],
            "active-learning-selection-v1",
        )
        self.assertEqual(
            payload["source"]["merged_json_sha256"], sha256_file(self.merged_json)
        )
        self.assertEqual(
            payload["source"]["checkpoint_sha256"],
            source["merged"]["input_runs"]["far"]["settings"]["checkpoint_sha256"],
        )
        self.assertIs(source["verification"]["checkpoint_file_checked"], True)
        self.assertNotIn("checkpoint_file_checked", payload["source"])
        self.assertEqual(payload["video"]["sha256"], sha256_file(self.video_path))
        self.assertNotIn("generated_at", payload)
        self.assertEqual(self._load_selection(), payload)

    def test_rejects_an_existing_output_without_changing_its_bytes(self):
        self.output_json.parent.mkdir(parents=True, exist_ok=True)
        self.output_json.write_bytes(b"keep")
        with self.assertRaisesRegex(ActiveLearningError, "already exists"):
            self._write_selection()
        self.assertEqual(self.output_json.read_bytes(), b"keep")

    def test_losing_output_race_preserves_the_winner_and_removes_temporary(self):
        real_link = os.link

        def publish_competitor_first(source, destination):
            Path(destination).write_bytes(b"winner")
            return real_link(source, destination)

        with (
            patch(
                "spiketrace.active_learning_selection.os.link",
                side_effect=publish_competitor_first,
            ),
            self.assertRaisesRegex(ActiveLearningError, "already exists"),
        ):
            self._write_selection()
        self.assertEqual(self.output_json.read_bytes(), b"winner")
        self.assertEqual(
            list(self.output_json.parent.glob(f".{self.output_json.name}.*.tmp")), []
        )

    def test_fsync_failure_leaves_no_output_or_temporary_sibling(self):
        with (
            patch(
                "spiketrace.active_learning_selection.os.fsync",
                side_effect=OSError("sync failed"),
            ),
            self.assertRaisesRegex(OSError, "sync failed"),
        ):
            self._write_selection()
        self.assertFalse(self.output_json.exists())
        self.assertEqual(
            list(self.output_json.parent.glob(f".{self.output_json.name}.*.tmp")), []
        )

    def test_rejects_duplicate_json_keys(self):
        self._write_selection()
        text = self.output_json.read_text(encoding="utf-8")
        duplicate = text.replace(
            '  "format_version": 1,',
            '  "format_version": 1,\n  "format_version": 1,',
            1,
        )
        self.output_json.write_text(duplicate, encoding="utf-8")
        with self.assertRaisesRegex(ActiveLearningError, "duplicate key"):
            self._load_selection()

    def test_rejects_non_finite_numbers(self):
        self._write_selection()
        text = self.output_json.read_text(encoding="utf-8")
        non_finite = text.replace(
            '    "preferred_clip_seconds": 15.0',
            '    "preferred_clip_seconds": NaN',
            1,
        )
        self.output_json.write_text(non_finite, encoding="utf-8")
        with self.assertRaisesRegex(ActiveLearningError, "non-finite"):
            self._load_selection()

    def test_rejects_merged_and_video_hash_tampering(self):
        for target, replacement in (
            ("merged", b" \n"),
            ("video", b"tampered-video"),
        ):
            with self.subTest(target=target):
                self.output_json.unlink(missing_ok=True)
                self.merged_json.write_text(
                    json.dumps(
                        make_selection_merged_payload(
                            self.video_path, self.checkpoint_path
                        ),
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                self.video_path.write_bytes(b"video")
                with patch(
                    "spiketrace.active_learning_selection.verify_dual_crop_review",
                    return_value={"verified": True},
                ):
                    validate_merged_review_source(self.merged_json, repo_root=self.root)
                payload = copy.deepcopy(self.selection_payload)
                self._write_selection(payload)
                if target == "merged":
                    self.merged_json.write_bytes(
                        self.merged_json.read_bytes() + replacement
                    )
                else:
                    self.video_path.write_bytes(replacement)
                with self.assertRaisesRegex(ActiveLearningError, "SHA-256"):
                    self._load_selection()

    def test_rejects_path_escape_and_invalid_format(self):
        escaped = copy.deepcopy(self.selection_payload)
        escaped["source"]["merged_json"] = "../outside.json"
        self._write_raw_selection(escaped)
        with self.assertRaisesRegex(ActiveLearningError, "escapes repository root"):
            self._load_selection()

        self.output_json.unlink()
        invalid_format = copy.deepcopy(self.selection_payload)
        invalid_format["format_version"] = 2
        with self.assertRaisesRegex(ActiveLearningError, "format version 1"):
            self._write_selection(invalid_format)

    def test_rejects_boolean_format_and_ordinal_values(self):
        for field in ("format", "ordinal"):
            with self.subTest(field=field):
                self.output_json.unlink(missing_ok=True)
                payload = copy.deepcopy(self.selection_payload)
                if field == "format":
                    payload["format_version"] = True
                else:
                    payload["clips"][0]["ordinal"] = True
                with self.assertRaises(ActiveLearningError):
                    self._write_selection(payload)

    def test_reports_a_missing_pinned_merged_file_as_active_learning_error(self):
        missing = copy.deepcopy(self.selection_payload)
        missing["source"]["merged_json"] = "outputs/review/missing.json"
        self._write_raw_selection(missing)
        with self.assertRaisesRegex(ActiveLearningError, "Cannot hash"):
            self._load_selection()

    def test_rejects_extra_root_source_and_video_fields(self):
        for section, field in (
            (None, "generated_at"),
            ("source", "checkpoint_file_checked"),
            ("video", "local_path"),
        ):
            with self.subTest(section=section, field=field):
                payload = copy.deepcopy(self.selection_payload)
                target = payload if section is None else payload[section]
                target[field] = True
                with self.assertRaisesRegex(
                    ActiveLearningError, "fields must be exactly"
                ):
                    self._write_selection(payload)

    def test_requires_exactly_40_unique_ordered_non_overlapping_clips(self):
        mutations = {
            "39 clips": lambda clips: clips.pop(),
            "duplicate ID": lambda clips: clips[1].update(clip_id="clip-001"),
            "wrong ordinal": lambda clips: clips[1].update(ordinal=3),
            "overlap": lambda clips: clips[1].update(start_seconds=0.5),
            "outside video": lambda clips: clips[-1].update(end_seconds=101.0),
            "wrong duration": lambda clips: clips[0].update(duration_seconds=2.0),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                payload = copy.deepcopy(self.selection_payload)
                mutate(payload["clips"])
                with self.assertRaises(ActiveLearningError):
                    self._write_selection(payload)

    def test_allows_absent_checkpoint_but_rejects_mismatched_local_checkpoint(self):
        self.checkpoint_path.unlink()
        with patch(
            "spiketrace.active_learning_selection.verify_dual_crop_review",
            return_value={"verified": True},
        ):
            result = validate_merged_review_source(
                self.merged_json, repo_root=self.root
            )
        self.assertIs(result["verification"]["checkpoint_file_checked"], False)
        self.assertEqual(result["source"], self.source["source"])

        self.checkpoint_path.write_bytes(b"wrong checkpoint")
        with (
            patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            self.assertRaisesRegex(ActiveLearningError, "Checkpoint SHA-256"),
        ):
            validate_merged_review_source(self.merged_json, repo_root=self.root)

    def test_rejects_far_and_near_checkpoint_pin_mismatch(self):
        merged = json.loads(self.merged_json.read_text(encoding="utf-8"))
        merged["input_runs"]["near"]["settings"]["checkpoint"] = "runs/other/best.pt"
        self.merged_json.write_text(
            json.dumps(merged, indent=2) + "\n", encoding="utf-8"
        )
        with (
            patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            self.assertRaisesRegex(ActiveLearningError, "same checkpoint"),
        ):
            validate_merged_review_source(self.merged_json, repo_root=self.root)

    def test_rejects_windows_drive_paths_under_posix_host_semantics(self):
        for value in ("C:secret.json", "C:/outside.json"):
            with (
                self.subTest(value=value),
                patch("spiketrace.active_learning_selection.Path", PurePosixPath),
                self.assertRaisesRegex(ActiveLearningError, "relative POSIX path"),
            ):
                _relative_posix_path(value, "source_file")

    def test_rejects_inference_audit_link_escape_during_validate_and_load(self):
        self._write_selection()
        with tempfile.TemporaryDirectory() as outside_temporary:
            outside = Path(outside_temporary).resolve()
            (outside / "events.json").write_text("{}\n", encoding="utf-8")
            inference_link = self.root / "outputs" / "inference"
            link_kind = create_directory_link(inference_link, outside)
            self.assertIn(link_kind, ("symlink", "junction"))

            with (
                patch(
                    "spiketrace.active_learning_selection.verify_dual_crop_review",
                    return_value={"verified": True},
                ),
                self.assertRaisesRegex(ActiveLearningError, "escapes repository root"),
            ):
                validate_merged_review_source(self.merged_json, repo_root=self.root)
            with self.assertRaisesRegex(
                ActiveLearningError, "escapes repository root"
            ):
                self._load_selection()

    def test_rejects_selection_pins_that_differ_from_the_merged_source(self):
        mutations = {
            "checkpoint hash": lambda payload: payload["source"].update(
                checkpoint_sha256="f" * 64
            ),
            "inference hash": lambda payload: payload["source"]["inference_runs"][
                "near"
            ].update(normalized_payload_sha256="e" * 64),
            "crop": lambda payload: payload["video"]["crops"].update(
                near=[0, 0, 1920, 1080]
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                payload = copy.deepcopy(self.selection_payload)
                mutate(payload)
                self._write_raw_selection(payload)
                with self.assertRaisesRegex(
                    ActiveLearningError, "do not match the verified merged JSON"
                ):
                    self._load_selection()
                self.output_json.unlink()

    def test_requires_task_two_container_types_and_exact_clip_fields(self):
        container_mutations = {
            "settings": [],
            "previous_selections": {},
            "quota_summary": {},
            "coverage": [],
        }
        for field, wrong_value in container_mutations.items():
            with self.subTest(field=field):
                payload = copy.deepcopy(self.selection_payload)
                payload[field] = wrong_value
                with self.assertRaisesRegex(ActiveLearningError, field):
                    self._write_selection(payload)

        extra_clip_field = copy.deepcopy(self.selection_payload)
        extra_clip_field["clips"][0]["future_evidence"] = {
            "score": 0.75,
            "labels": ["serve"],
        }
        with self.assertRaisesRegex(ActiveLearningError, "fields must be exactly"):
            self._write_selection(extra_clip_field)

    def test_task_one_style_selection_v1_payload_is_not_a_compatibility_contract(self):
        payload = copy.deepcopy(self.selection_payload)
        payload["settings"] = {"clip_duration_ms": 1000}
        payload["quota_summary"] = []
        payload["coverage"] = {"start_seconds": 0.0, "end_seconds": 79.0}
        payload["clips"] = [
            {
                key: clip[key]
                for key in (
                    "clip_id",
                    "ordinal",
                    "start_seconds",
                    "end_seconds",
                    "duration_seconds",
                )
            }
            for clip in payload["clips"]
        ]
        with self.assertRaisesRegex(ActiveLearningError, "selection settings"):
            self._write_selection(payload)


class SelectionVerifierIntegrationTests(unittest.TestCase):
    def test_byte_selection_loader_matches_path_loader_for_identical_bytes(self):
        selection_path = ROOT / "data/active-learning/rangitoto/round-01-selection.json"
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        merged_path = ROOT / selection["source"]["merged_json"]
        self.assertEqual(
            load_review_selection(selection_path, repo_root=ROOT, require_video=False),
            load_review_selection_bytes(
                selection_path.read_bytes(),
                merged_bytes=merged_path.read_bytes(),
                merged_repo_path=selection["source"]["merged_json"],
                repo_root=ROOT,
                require_video=False,
            ),
        )

    def test_real_merged_fixture_flows_through_verifier_and_selector(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            video_path = directory / "selector-video.mp4"
            video_path.write_bytes(b"selector integration video")
            video_sha256 = sha256_file(video_path)
            video = {
                "path": str(video_path),
                "fps": 25.0,
                "frame_count": 250000,
                "width": 1920,
                "height": 1080,
                "duration_seconds": 10000.0,
            }
            runs = {
                side: json.loads(
                    (DUAL_CROP_FIXTURES / f"{side}.json").read_text(
                        encoding="utf-8"
                    )
                )
                for side in ("far", "near")
            }
            for run in runs.values():
                run["video"] = copy.deepcopy(video)
                run["settings"]["video"] = copy.deepcopy(video)
                run["settings"]["video_sha256"] = video_sha256
                run["events"] = []
                run["windows"] = []

            def add_event(side, start_seconds, action, confidence):
                run = runs[side]
                confidence = round(confidence, 6)
                window_index = len(run["windows"])
                event_id = f"evt_{side}_{window_index:03d}"
                run["windows"].append(
                    {
                        "window_index": window_index,
                        "start_seconds": start_seconds,
                        "end_seconds": start_seconds + 1.0,
                        "action": action,
                        "confidence": confidence,
                    }
                )
                run["events"].append(
                    {
                        "video_id": video_path.stem,
                        "event_id": event_id,
                        "start_ms": int(start_seconds * 1000),
                        "end_ms": int((start_seconds + 1.0) * 1000),
                        "action": action,
                        "confidence": confidence,
                        "team_side": None,
                        "player_number": None,
                        "status": "predicted",
                        "model_version": "rangitoto-test-v1",
                        "source": "sliding_window",
                        "source_window_indices": [window_index],
                    }
                )

            positions = [100.0 + index * 250.0 for index in range(36)]
            for index, start_seconds in enumerate(positions[:20]):
                add_event(
                    ("far", "near")[index % 2],
                    start_seconds,
                    ("receive", "block", "dig")[index % 3],
                    0.72,
                )
            for index, start_seconds in enumerate(positions[20:28]):
                add_event(
                    "far",
                    start_seconds,
                    ("set", "attack", "serve")[index % 3],
                    0.9 - index / 100,
                )
            for start_seconds in positions[28:32]:
                add_event("far", start_seconds, "set", 0.3)
                add_event("near", start_seconds, "set", 0.3)
            for start_seconds in positions[32:36]:
                add_event("near", start_seconds, "tip", 0.2)
            for start_seconds in (9100.0, 9350.0, 9600.0, 9850.0):
                for side in ("far", "near"):
                    run = runs[side]
                    run["windows"].append(
                        {
                            "window_index": len(run["windows"]),
                            "start_seconds": start_seconds,
                            "end_seconds": start_seconds + 15.0,
                            "action": "background",
                            "confidence": 0.99,
                        }
                    )

            far_path = directory / "far.json"
            near_path = directory / "near.json"
            far_path.write_text(
                json.dumps(runs["far"], indent=2) + "\n", encoding="utf-8"
            )
            near_path.write_text(
                json.dumps(runs["near"], indent=2) + "\n", encoding="utf-8"
            )
            output_dir = directory / "review"
            build_dual_crop_review(
                far_path,
                near_path,
                output_dir,
                repo_root=ROOT,
            )
            merged_path = output_dir / "merged_candidates.json"
            verification = verify_dual_crop_review(
                merged_path,
                csv_path=output_dir / "merged_candidates.csv",
            )
            selection_path = directory / "selection.json"
            selection = active_learning_selection.select_review_batch(
                merged_path,
                selection_path,
                repo_root=ROOT,
            )

            self.assertTrue(verification["verified"])
            self.assertEqual(len(selection["clips"]), 40)
            self.assertEqual(
                selection["source"]["merged_json_sha256"],
                verification["hashes"]["merged_json_sha256"],
            )
            merged_ids = {
                event["event_id"]
                for event in json.loads(merged_path.read_text(encoding="utf-8"))[
                    "events"
                ]
            }
            selected_hint_ids = {
                hint["canonical_event_id"]
                for clip in selection["clips"]
                for hint in clip["candidate_hints"]
            }
            self.assertTrue(selected_hint_ids)
            self.assertLessEqual(selected_hint_ids, merged_ids)

    def test_real_dual_crop_verifier_accepts_source_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            output_dir = directory / "review"
            build_dual_crop_review(
                DUAL_CROP_FIXTURES / "far.json",
                DUAL_CROP_FIXTURES / "near.json",
                output_dir,
                repo_root=ROOT,
            )
            merged_path = output_dir / "merged_candidates.json"
            result = validate_merged_review_source(
                merged_path,
                repo_root=ROOT,
                require_video=False,
            )
            self.assertEqual(result["source"]["format_version"], 2)
            self.assertIs(result["verification"]["checkpoint_file_checked"], False)

            tampered = json.loads(merged_path.read_text(encoding="utf-8"))
            tampered["events"][0]["confidence"] = 0.1
            merged_path.write_text(
                json.dumps(tampered, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ActiveLearningError, "independent recomputation"
            ):
                validate_merged_review_source(
                    merged_path,
                    repo_root=ROOT,
                    require_video=False,
                )


if __name__ == "__main__":
    unittest.main()
