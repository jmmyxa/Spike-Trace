import copy
import csv
import hashlib
import io
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from spiketrace.cli import build_parser, run_command
from spiketrace.dual_crop_review import (
    _find_cross_side_links,
    build_dual_crop_review,
    verify_dual_crop_review,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "dual_crop_review"
FAR_FIXTURE = FIXTURE_DIR / "far.json"
NEAR_FIXTURE = FIXTURE_DIR / "near.json"
CSV_HEADER = (
    "video_id,event_id,start_ms,end_ms,action,confidence,team_side,"
    "player_number,status,model_version,source,side,observed_sides,"
    "duplicate_group_id,conflict_group_id,merge_decision,source_event_ids,"
    "source_event_refs,source_window_count,source_window_max_confidence,"
    "primary_source_event_id,review_reason"
)


def _load_fixtures():
    return (
        json.loads(FAR_FIXTURE.read_text(encoding="utf-8")),
        json.loads(NEAR_FIXTURE.read_text(encoding="utf-8")),
    )


def _write_json(path, payload):
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
        handle.write("\n")


def _write_inputs(directory, far_payload, near_payload):
    far_path = directory / "far.json"
    near_path = directory / "near.json"
    _write_json(far_path, far_payload)
    _write_json(near_path, near_payload)
    return far_path, near_path


class DualCropReviewBuildTests(unittest.TestCase):
    def test_normalizes_runtime_repo_checkpoint_to_repo_relative_path(self):
        far_payload, near_payload = _load_fixtures()
        runtime_checkpoint = str(ROOT / "runs" / "rangitoto-test" / "best.pt")
        far_payload["settings"]["checkpoint"] = runtime_checkpoint
        near_payload["settings"]["checkpoint"] = runtime_checkpoint

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            far_path, near_path = _write_inputs(
                directory, far_payload, near_payload
            )
            payload = build_dual_crop_review(
                far_path,
                near_path,
                directory / "review",
                repo_root=ROOT,
            )

        self.assertEqual(
            payload["input_runs"]["far"]["settings"]["checkpoint"],
            "runs/rangitoto-test/best.pt",
        )
        self.assertEqual(
            payload["input_runs"]["near"]["settings"]["checkpoint"],
            "runs/rangitoto-test/best.pt",
        )

    def test_builds_exact_deterministic_merge_and_csv(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory) / "review"
            payload = build_dual_crop_review(
                FAR_FIXTURE,
                NEAR_FIXTURE,
                output_dir,
                repo_root=ROOT,
            )
            json_path = output_dir / "merged_candidates.json"
            csv_path = output_dir / "merged_candidates.csv"
            json_bytes = json_path.read_bytes()
            json_text = json_bytes.decode("utf-8")
            csv_bytes = csv_path.read_bytes()
            csv_text = csv_bytes.decode("utf-8-sig")
            csv_rows = list(csv.DictReader(io.StringIO(csv_text)))

            self.assertEqual(
                list(payload),
                [
                    "format_version",
                    "merge_format_version",
                    "video",
                    "model_version",
                    "settings",
                    "input_runs",
                    "events",
                    "duplicate_groups",
                    "conflict_groups",
                ],
            )
            self.assertEqual(payload["format_version"], 2)
            self.assertEqual(payload["merge_format_version"], 2)
            self.assertEqual(
                payload["settings"]["algorithm_version"], "dual-crop-merge-v2"
            )
            self.assertEqual(payload["settings"]["time_unit"], "ms")
            self.assertEqual(payload["settings"]["interval_semantics"], "half_open")
            self.assertEqual(set(payload["input_runs"]), {"far", "near"})
            self.assertEqual(len(payload["input_runs"]["far"]["windows"]), 4)
            self.assertEqual(len(payload["input_runs"]["near"]["windows"]), 4)
            self.assertEqual(len(payload["duplicate_groups"]), 1)
            self.assertEqual(len(payload["conflict_groups"]), 1)
            self.assertEqual(len(payload["events"]), 4)
            self.assertNotIn("E:\\\\", json_text)
            self.assertNotIn("external-video", json_text)
            self.assertEqual(len(csv_rows), len(payload["events"]))
            self.assertEqual(json.loads(json_text), payload)
            self.assertTrue(json_bytes.endswith(b"\n"))
            self.assertNotIn(b"\r", json_bytes)
            self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))
            self.assertTrue(csv_bytes.endswith(b"\n"))
            self.assertNotIn(b"\r", csv_bytes)
            self.assertEqual(csv_text.splitlines()[0], CSV_HEADER)

            self.assertEqual(payload["video"]["path"], "rangitoto.mp4")
            self.assertEqual(
                payload["input_runs"]["far"]["settings"]["checkpoint"],
                "runs/rangitoto-test/best.pt",
            )
            far_audit = payload["settings"]["input_runs"]["far"]
            near_audit = payload["settings"]["input_runs"]["near"]
            self.assertEqual(
                far_audit["source_file"], "tests/fixtures/dual_crop_review/far.json"
            )
            self.assertEqual(
                near_audit["source_file"], "tests/fixtures/dual_crop_review/near.json"
            )
            self.assertEqual(
                far_audit["source_file_sha256"],
                hashlib.sha256(FAR_FIXTURE.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                near_audit["source_file_sha256"],
                hashlib.sha256(NEAR_FIXTURE.read_bytes()).hexdigest(),
            )

            event_1, event_2, event_3, event_4 = payload["events"]
            self.assertEqual(
                list(event_1),
                [
                    "video_id",
                    "event_id",
                    "start_ms",
                    "end_ms",
                    "action",
                    "confidence",
                    "team_side",
                    "player_number",
                    "status",
                    "model_version",
                    "source",
                    "side",
                    "observed_sides",
                    "source_event_refs",
                    "duplicate_group_id",
                    "conflict_group_id",
                    "merge_decision",
                    "source_event_ids",
                    "source_window_count",
                    "source_window_max_confidence",
                    "primary_source_event_id",
                    "review_reason",
                ],
            )
            self.assertEqual(
                [item["event_id"] for item in payload["events"]],
                [
                    "evt_merged_000001",
                    "evt_merged_000002",
                    "evt_merged_000003",
                    "evt_merged_000004",
                ],
            )
            self.assertEqual(
                (
                    event_1["action"],
                    event_1["side"],
                    event_1["review_reason"],
                ),
                ("serve", "far", "single_source_candidate"),
            )
            self.assertEqual(
                (
                    event_2["start_ms"],
                    event_2["end_ms"],
                    event_2["action"],
                    event_2["confidence"],
                    event_2["side"],
                    event_2["observed_sides"],
                    event_2["source_event_ids"],
                    event_2["source_window_count"],
                    event_2["source_window_max_confidence"],
                    event_2["primary_source_event_id"],
                    event_2["review_reason"],
                ),
                (
                    2000,
                    3200,
                    "attack",
                    0.95,
                    "near",
                    ["far", "near"],
                    ["far:evt_far_attack", "near:evt_near_attack_dup"],
                    2,
                    0.95,
                    "near:evt_near_attack_dup",
                    "same_action_cross_side_duplicate",
                ),
            )
            self.assertEqual(
                [ref["selected_as_primary"] for ref in event_2["source_event_refs"]],
                [False, True],
            )
            for event in (event_3, event_4):
                self.assertEqual(event["conflict_group_id"], "cg_000001")
                self.assertEqual(event["status"], "needs_review")
                self.assertEqual(
                    event["review_reason"],
                    "single_source_candidate|different_action_cross_side_conflict",
                )
            self.assertEqual(event_3["action"], "block")
            self.assertEqual(event_4["action"], "attack")

            self.assertEqual(
                payload["duplicate_groups"],
                [
                    {
                        "duplicate_group_id": "dg_000001",
                        "canonical_event_id": "evt_merged_000002",
                        "action": "attack",
                        "primary_source_event_id": "near:evt_near_attack_dup",
                        "source_event_ids": [
                            "far:evt_far_attack",
                            "near:evt_near_attack_dup",
                        ],
                        "observed_sides": ["far", "near"],
                        "links": [
                            {
                                "candidate_a_id": "far:evt_far_attack",
                                "candidate_b_id": "near:evt_near_attack_dup",
                                "metrics": {
                                    "overlap_ms": 800,
                                    "union_ms": 1200,
                                    "shorter_ms": 1000,
                                    "coverage_shorter": 0.8,
                                    "temporal_iou": 0.666667,
                                    "center_gap_ms": 200,
                                },
                            }
                        ],
                    }
                ],
            )
            self.assertEqual(
                payload["conflict_groups"],
                [
                    {
                        "conflict_group_id": "cg_000001",
                        "conflict_type": "different_action_cross_side",
                        "canonical_event_ids": [
                            "evt_merged_000003",
                            "evt_merged_000004",
                        ],
                        "source_links": [
                            {
                                "candidate_a_id": "far:evt_far_block",
                                "candidate_b_id": "near:evt_near_attack_conflict",
                                "metrics": {
                                    "overlap_ms": 900,
                                    "union_ms": 1100,
                                    "shorter_ms": 1000,
                                    "coverage_shorter": 0.9,
                                    "temporal_iou": 0.818182,
                                    "center_gap_ms": 100,
                                },
                            }
                        ],
                    }
                ],
            )
            self.assertEqual(csv_rows[0]["confidence"], "0.900000")
            self.assertEqual(csv_rows[1]["source_window_max_confidence"], "0.950000")
            self.assertEqual(
                json.loads(csv_rows[1]["source_event_refs"]),
                event_2["source_event_refs"],
            )

            second_output = Path(temporary_directory) / "review-second"
            build_dual_crop_review(
                FAR_FIXTURE,
                NEAR_FIXTURE,
                second_output,
                repo_root=ROOT,
            )
            self.assertEqual(
                json_bytes, (second_output / "merged_candidates.json").read_bytes()
            )
            self.assertEqual(
                csv_bytes, (second_output / "merged_candidates.csv").read_bytes()
            )

            verification = verify_dual_crop_review(json_path, csv_path=csv_path)
            self.assertEqual(
                verification["counts"],
                {
                    "far_events": 3,
                    "near_events": 2,
                    "far_windows": 4,
                    "near_windows": 4,
                    "source_candidates": 5,
                    "canonical_events": 4,
                    "duplicate_links": 1,
                    "duplicate_groups": 1,
                    "conflict_links": 1,
                    "conflict_groups": 1,
                },
            )
            self.assertEqual(
                verification,
                {
                    "verified": True,
                    "format_version": 2,
                    "merge_format_version": 2,
                    "csv_checked": True,
                    "counts": verification["counts"],
                    "hashes": {
                        "merged_json_sha256": hashlib.sha256(json_bytes).hexdigest(),
                        "merged_csv_sha256": hashlib.sha256(csv_bytes).hexdigest(),
                        "far_source_file_sha256": far_audit["source_file_sha256"],
                        "near_source_file_sha256": near_audit["source_file_sha256"],
                        "far_normalized_payload_sha256": far_audit[
                            "normalized_payload_sha256"
                        ],
                        "near_normalized_payload_sha256": near_audit[
                            "normalized_payload_sha256"
                        ],
                    },
                },
            )

    def test_exact_duplicate_and_conflict_predicate_boundaries(self):
        cases = (
            {
                "name": "duplicate coverage only",
                "far": ("attack", 0, 400),
                "near": ("attack", 0, 2000),
                "counts": (1, 1, 0, 1, 0),
                "metrics": {
                    "overlap_ms": 400,
                    "union_ms": 2000,
                    "shorter_ms": 400,
                    "coverage_shorter": 1.0,
                    "temporal_iou": 0.2,
                    "center_gap_ms": 800,
                },
            },
            {
                "name": "duplicate center only at overlap 400 and center 500",
                "far": ("attack", 0, 900),
                "near": ("attack", 500, 1400),
                "counts": (1, 1, 0, 1, 0),
                "metrics": {
                    "overlap_ms": 400,
                    "union_ms": 1400,
                    "shorter_ms": 900,
                    "coverage_shorter": 0.444444,
                    "temporal_iou": 0.285714,
                    "center_gap_ms": 500,
                },
            },
            {
                "name": "conflict overlap only at overlap 400",
                "far": ("block", 0, 400),
                "near": ("attack", 0, 2000),
                "counts": (2, 0, 1, 0, 1),
                "metrics": {
                    "overlap_ms": 400,
                    "union_ms": 2000,
                    "shorter_ms": 400,
                    "coverage_shorter": 1.0,
                    "temporal_iou": 0.2,
                    "center_gap_ms": 800,
                },
            },
            {
                "name": "conflict center only at center 500",
                "far": ("block", 0, 400),
                "near": ("attack", 500, 900),
                "counts": (2, 0, 1, 0, 1),
                "metrics": {
                    "overlap_ms": 0,
                    "union_ms": 900,
                    "shorter_ms": 400,
                    "coverage_shorter": 0.0,
                    "temporal_iou": 0.0,
                    "center_gap_ms": 500,
                },
            },
            {
                "name": "overlap 399 fails",
                "far": ("attack", 0, 399),
                "near": ("attack", 0, 2000),
                "counts": (2, 0, 0, 0, 0),
                "metrics": None,
            },
            {
                "name": "center gap 500.5 fails",
                "far": ("attack", 0, 901),
                "near": ("attack", 501, 1401),
                "counts": (2, 0, 0, 0, 0),
                "metrics": None,
            },
            {
                "name": "coverage 0.5 passes",
                "far": ("attack", 0, 800),
                "near": ("attack", 400, 2000),
                "counts": (1, 1, 0, 1, 0),
                "metrics": {
                    "overlap_ms": 400,
                    "union_ms": 2000,
                    "shorter_ms": 800,
                    "coverage_shorter": 0.5,
                    "temporal_iou": 0.2,
                    "center_gap_ms": 800,
                },
            },
            {
                "name": "coverage below 0.5 fails",
                "far": ("attack", 0, 801),
                "near": ("attack", 401, 2001),
                "counts": (2, 0, 0, 0, 0),
                "metrics": None,
            },
        )

        def set_candidate(payload, side, candidate):
            action, start_ms, end_ms = candidate
            event = copy.deepcopy(payload["events"][0])
            event.update(
                event_id=f"evt_{side}_boundary",
                start_ms=start_ms,
                end_ms=end_ms,
                action=action,
                confidence=0.9,
                source_window_indices=[0],
            )
            payload["events"] = [event]
            payload["windows"] = [
                {
                    "window_index": 0,
                    "start_seconds": start_ms / 1000,
                    "end_seconds": end_ms / 1000,
                    "action": action,
                    "confidence": 0.9,
                }
            ]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for case in cases:
                with self.subTest(case=case["name"]):
                    case_dir = root / case["name"].replace(" ", "_")
                    case_dir.mkdir()
                    far_payload, near_payload = _load_fixtures()
                    set_candidate(far_payload, "far", case["far"])
                    set_candidate(near_payload, "near", case["near"])
                    far_path, near_path = _write_inputs(
                        case_dir, far_payload, near_payload
                    )
                    output_dir = case_dir / "review"
                    payload = build_dual_crop_review(
                        far_path, near_path, output_dir, repo_root=ROOT
                    )
                    verification = verify_dual_crop_review(
                        output_dir / "merged_candidates.json",
                        csv_path=output_dir / "merged_candidates.csv",
                    )
                    self.assertEqual(
                        (
                            len(payload["events"]),
                            len(payload["duplicate_groups"]),
                            len(payload["conflict_groups"]),
                            verification["counts"]["duplicate_links"],
                            verification["counts"]["conflict_links"],
                        ),
                        case["counts"],
                    )
                    if case["metrics"] is not None:
                        group_name = (
                            "duplicate_groups"
                            if case["counts"][1] == 1
                            else "conflict_groups"
                        )
                        link_name = (
                            "links" if group_name == "duplicate_groups" else "source_links"
                        )
                        self.assertEqual(
                            payload[group_name][0][link_name][0]["metrics"],
                            case["metrics"],
                        )

    def test_primary_selection_uses_every_declared_tie_breaker(self):
        cases = (
            ("event confidence", "near:evt_near_attack_dup"),
            ("member confidence", "near:evt_near_attack_dup"),
            ("member count", "far:evt_far_attack"),
            ("shorter duration", "far:evt_far_attack"),
            ("side order", "far:evt_far_attack"),
            ("source event id", "far:evt_z"),
        )
        for case, expected_primary in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                far_payload, near_payload = _load_fixtures()
                far_attack = copy.deepcopy(far_payload["events"][1])
                near_attack = copy.deepcopy(near_payload["events"][0])
                far_payload["events"] = [far_attack]
                near_payload["events"] = [near_attack]
                if case != "event confidence":
                    far_attack["confidence"] = 0.9
                    near_attack["confidence"] = 0.9
                    far_payload["windows"][1]["confidence"] = 0.91
                    near_payload["windows"][0]["confidence"] = 0.92
                if case in {
                    "member count",
                    "shorter duration",
                    "side order",
                    "source event id",
                }:
                    far_payload["windows"][1]["confidence"] = 0.92
                if case == "member count":
                    far_attack["source_window_indices"] = [1, 2]
                    far_attack["end_ms"] = 5000
                    far_payload["windows"][2]["action"] = "attack"
                    far_payload["windows"][2]["confidence"] = 0.92
                elif case == "shorter duration":
                    far_attack["start_ms"] = 2100
                    far_payload["windows"][1]["start_seconds"] = 2.1
                elif case in {"side order", "source event id"}:
                    far_attack["start_ms"] = 2200
                    far_attack["end_ms"] = 3200
                    far_payload["windows"][1]["start_seconds"] = 2.2
                    far_payload["windows"][1]["end_seconds"] = 3.2
                if case == "source event id":
                    far_attack["event_id"] = "evt_z"
                    second_far = copy.deepcopy(far_attack)
                    second_far["event_id"] = "evt_ä"
                    second_far["source_window_indices"] = [2]
                    far_payload["windows"][2].update(
                        {
                            "start_seconds": 2.2,
                            "end_seconds": 3.2,
                            "action": "attack",
                            "confidence": 0.92,
                        }
                    )
                    far_payload["events"].append(second_far)

                # Source-event confidence is a declared mean of its members.
                for payload in (far_payload, near_payload):
                    windows_by_index = {
                        item["window_index"]: item for item in payload["windows"]
                    }
                    for event in payload["events"]:
                        members = [
                            windows_by_index[index]
                            for index in event["source_window_indices"]
                        ]
                        event["confidence"] = round(
                            sum(item["confidence"] for item in members)
                            / len(members),
                            6,
                        )

                far_path, near_path = _write_inputs(
                    directory, far_payload, near_payload
                )
                payload = build_dual_crop_review(
                    far_path,
                    near_path,
                    directory / "out",
                    repo_root=ROOT,
                )
                self.assertEqual(
                    payload["events"][0]["primary_source_event_id"],
                    expected_primary,
                )

    def test_uses_half_up_milliseconds_instead_of_bankers_rounding(self):
        far_payload, near_payload = _load_fixtures()
        far_payload["events"] = [far_payload["events"][0]]
        near_payload["events"] = []
        far_payload["windows"][0]["start_seconds"] = 0.0005
        far_payload["events"][0]["start_ms"] = 1
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            far_path, near_path = _write_inputs(directory, far_payload, near_payload)
            payload = build_dual_crop_review(
                far_path, near_path, directory / "out", repo_root=ROOT
            )
            self.assertEqual(payload["events"][0]["start_ms"], 1)

            far_payload["events"][0]["start_ms"] = 0
            far_path, near_path = _write_inputs(directory, far_payload, near_payload)
            with self.assertRaises(ValueError):
                build_dual_crop_review(
                    far_path, near_path, directory / "bad", repo_root=ROOT
                )

    def test_sorts_strings_by_code_point_not_locale(self):
        far_payload, near_payload = _load_fixtures()
        for index, action, event_id in (
            (4, "äction", "evt_umlaut"),
            (5, "zeta", "evt_ascii"),
        ):
            far_payload["windows"].append(
                {
                    "window_index": index,
                    "start_seconds": 8.0,
                    "end_seconds": 9.0,
                    "action": action,
                    "confidence": 0.8,
                }
            )
            far_payload["events"].append(
                {
                    "video_id": "rangitoto",
                    "event_id": event_id,
                    "start_ms": 8000,
                    "end_ms": 9000,
                    "action": action,
                    "confidence": 0.8,
                    "team_side": None,
                    "player_number": None,
                    "status": "predicted",
                    "model_version": "rangitoto-test-v1",
                    "source": "sliding_window",
                    "source_window_indices": [index],
                }
            )
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            far_path, near_path = _write_inputs(directory, far_payload, near_payload)
            payload = build_dual_crop_review(
                far_path, near_path, directory / "out", repo_root=ROOT
            )
            self.assertEqual(
                [event["action"] for event in payload["events"][-2:]],
                ["zeta", "äction"],
            )

    def test_rejects_invalid_or_mismatched_inference_inputs(self):
        mutations = {
            "legacy format": lambda far, near: far.update(format_version=1),
            "non-integer format": lambda far, near: far.update(format_version=2.0),
            "sampling contract": lambda far, near: far["settings"].update(
                sampling_contract="floor-sampled-v1"
            ),
            "video digest mismatch": lambda far, near: near["settings"].update(
                video_sha256="2" * 64
            ),
            "settings mismatch": lambda far, near: near["settings"].update(
                stride_seconds=0.5
            ),
            "wrong far crop": lambda far, near: far["settings"].update(
                crop=[0, 0, 1920, 646]
            ),
            "boolean crop coordinate": lambda far, near: far["settings"].update(
                crop=[False, 0, 1920, 645]
            ),
            "duplicate window index": lambda far, near: far["windows"][3].update(
                window_index=2
            ),
            "moved event member": lambda far, near: (
                near["events"][0].update(source_window_indices=[1]),
                near["events"][1].update(source_window_indices=[0]),
            ),
            "source event confidence mismatch": lambda far, near: far[
                "events"
            ][0].update(confidence=0.1),
            "source event bounds mismatch": lambda far, near: far["events"][0].update(
                end_ms=999
            ),
            "duplicate event member ownership": lambda far, near: near["events"][
                1
            ].update(source_window_indices=[0]),
            "source event video mismatch": lambda far, near: far["events"][0].update(
                video_id="other-video"
            ),
            "path-shaped event source": lambda far, near: far["events"][0].update(
                source="E:\\hidden\\source.json"
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                far_payload, near_payload = _load_fixtures()
                mutate(far_payload, near_payload)
                far_path, near_path = _write_inputs(
                    directory, far_payload, near_payload
                )
                with self.assertRaises(ValueError):
                    build_dual_crop_review(
                        far_path, near_path, directory / "out", repo_root=ROOT
                    )

    def test_rejects_duplicate_json_keys_and_non_finite_window_numbers(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            far_path = directory / "far.json"
            far_path.write_text(
                '{"format_version":2,"format_version":2}', encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                build_dual_crop_review(
                    far_path, NEAR_FIXTURE, directory / "duplicate", repo_root=ROOT
                )

            far_payload, near_payload = _load_fixtures()
            far_text = json.dumps(far_payload).replace(
                '"start_seconds": 0.0', '"start_seconds": NaN'
            )
            far_path.write_text(far_text, encoding="utf-8")
            near_path = directory / "near.json"
            _write_json(near_path, near_payload)
            with self.assertRaises(ValueError):
                build_dual_crop_review(
                    far_path, near_path, directory / "nan", repo_root=ROOT
                )

    def test_rejects_huge_integer_as_contextual_value_error(self):
        far_payload, near_payload = _load_fixtures()
        far_payload["windows"][0]["confidence"] = 10**400
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            far_path, near_path = _write_inputs(directory, far_payload, near_payload)
            with self.assertRaisesRegex(ValueError, "far window confidence"):
                build_dual_crop_review(
                    far_path, near_path, directory / "huge", repo_root=ROOT
                )

    def test_handles_real_rangitoto_shape_without_window_by_event_scans(self):
        window_count = 16448
        far_event_count = 1430
        near_event_count = 2749
        far_payload, near_payload = _load_fixtures()
        for payload in (far_payload, near_payload):
            payload["video"]["duration_seconds"] = 100000.0
            payload["settings"]["video"]["duration_seconds"] = 100000.0

        def populate(payload, event_count, action, offset, prefix):
            payload["windows"] = []
            payload["events"] = []
            for index in range(window_count):
                start = offset + index * 2
                is_member = index < event_count
                payload["windows"].append(
                    {
                        "window_index": index,
                        "start_seconds": float(start),
                        "end_seconds": float(start + 1),
                        "action": action if is_member else "background",
                        "confidence": 0.9 if is_member else 0.99,
                    }
                )
                if is_member:
                    payload["events"].append(
                        {
                            "video_id": "rangitoto",
                            "event_id": f"evt_{prefix}_{index:06d}",
                            "start_ms": start * 1000,
                            "end_ms": (start + 1) * 1000,
                            "action": action,
                            "confidence": 0.9,
                            "team_side": None,
                            "player_number": None,
                            "status": "predicted",
                            "model_version": "rangitoto-test-v1",
                            "source": "sliding_window",
                            "source_window_indices": [index],
                        }
                    )

        populate(far_payload, far_event_count, "serve", 0, "far")
        populate(near_payload, near_event_count, "attack", 40000, "near")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            far_path, near_path = _write_inputs(directory, far_payload, near_payload)
            started = time.monotonic()
            payload = build_dual_crop_review(
                far_path, near_path, directory / "out", repo_root=ROOT
            )
            elapsed = time.monotonic() - started
            self.assertEqual(len(payload["events"]), 4179)
            self.assertEqual(payload["duplicate_groups"], [])
            self.assertEqual(payload["conflict_groups"], [])
            self.assertLess(elapsed, 12.0)

    def test_cross_side_sweep_does_not_rescan_same_side_candidates(self):
        class SameSideCandidate:
            side = "far"

            def __init__(self, start_ms):
                self.start_ms = start_ms
                self._end_ms = start_ms + 1000
                self.end_reads = 0

            @property
            def end_ms(self):
                self.end_reads += 1
                if self.end_reads > 1:
                    raise AssertionError("same-side candidate was rescanned")
                return self._end_ms

        candidates = [SameSideCandidate(index) for index in range(4)]
        self.assertEqual(_find_cross_side_links(candidates), ([], []))


class DualCropReviewVerifierTests(unittest.TestCase):
    def _build_valid_artifact(self, directory):
        output_dir = directory / "review"
        payload = build_dual_crop_review(
            FAR_FIXTURE, NEAR_FIXTURE, output_dir, repo_root=ROOT
        )
        return payload, output_dir / "merged_candidates.json", output_dir / "merged_candidates.csv"

    def test_verifier_recomputes_without_requiring_original_source_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _payload, json_path, _csv_path = self._build_valid_artifact(directory)
            result = verify_dual_crop_review(json_path)
            self.assertTrue(result["verified"])
            self.assertFalse(result["csv_checked"])
            self.assertIsNone(result["hashes"]["merged_csv_sha256"])

    def test_real_audit_csv_is_lf_pinned_and_verifies_with_autocrlf(self):
        relative_csv = "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.csv"
        csv_path = ROOT / relative_csv
        json_path = csv_path.with_suffix(".json")
        attribute = subprocess.run(
            ["git", "check-attr", "eol", "--", relative_csv],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        autocrlf = subprocess.run(
            ["git", "config", "--get", "core.autocrlf"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(attribute.stdout.strip(), f"{relative_csv}: eol: lf")
        self.assertEqual(autocrlf.stdout.strip(), "true")
        self.assertNotIn(b"\r\n", csv_path.read_bytes())
        result = verify_dual_crop_review(json_path, csv_path=csv_path)
        self.assertTrue(result["verified"])
        self.assertTrue(result["csv_checked"])

    def test_rejects_independent_json_and_csv_tampering(self):
        mutations = {
            "deleted input window": lambda payload: payload["input_runs"]["far"][
                "windows"
            ].pop(),
            "duplicate input window index": lambda payload: payload["input_runs"][
                "far"
            ]["windows"][3].update(window_index=2),
            "source event member moved": lambda payload: (
                payload["input_runs"]["near"]["events"][0].update(
                    source_window_indices=[1]
                ),
                payload["input_runs"]["near"]["events"][1].update(
                    source_window_indices=[0]
                ),
            ),
            "event confidence changed": lambda payload: payload["events"][1].update(
                confidence=0.1
            ),
            "event group changed": lambda payload: payload["events"][1].update(
                duplicate_group_id=None
            ),
            "duplicate group link removed": lambda payload: payload[
                "duplicate_groups"
            ][0]["links"].clear(),
            "duplicate link metric changed": lambda payload: payload[
                "duplicate_groups"
            ][0]["links"][0]["metrics"].update(overlap_ms=799),
            "conflict candidate removed": lambda payload: payload["conflict_groups"][
                0
            ]["canonical_event_ids"].pop(),
            "absolute path inserted": lambda payload: payload["input_runs"]["far"][
                "video"
            ].update(path="E:\\secret\\rangitoto.mp4"),
            "source hash malformed": lambda payload: payload["settings"]["input_runs"][
                "far"
            ].update(source_file_sha256="A" * 64),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                payload, _json_path, _csv_path = self._build_valid_artifact(directory)
                tampered = copy.deepcopy(payload)
                mutate(tampered)
                tampered_path = directory / "tampered.json"
                _write_json(tampered_path, tampered)
                with self.assertRaises(ValueError):
                    verify_dual_crop_review(tampered_path)

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _payload, json_path, csv_path = self._build_valid_artifact(directory)
            csv_bytes = csv_path.read_bytes().replace(
                b"evt_merged_000001", b"evt_merged_999999", 1
            )
            tampered_csv = directory / "tampered.csv"
            tampered_csv.write_bytes(csv_bytes)
            with self.assertRaises(ValueError):
                verify_dual_crop_review(json_path, csv_path=tampered_csv)


class DualCropReviewCliTests(unittest.TestCase):
    def test_cli_build_and_verify_dispatch_real_files(self):
        parser = build_parser()
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "review"
            build_args = parser.parse_args(
                [
                    "build-dual-crop-review",
                    str(FAR_FIXTURE),
                    str(NEAR_FIXTURE),
                    str(output_dir),
                    "--repo-root",
                    str(ROOT),
                ]
            )
            payload = run_command(build_args)
            self.assertEqual(len(payload["events"]), 4)

            verify_args = parser.parse_args(
                [
                    "verify-dual-crop-review",
                    str(output_dir / "merged_candidates.json"),
                    "--csv",
                    str(output_dir / "merged_candidates.csv"),
                ]
            )
            result = run_command(verify_args)
            self.assertTrue(result["verified"])
            self.assertTrue(result["csv_checked"])


if __name__ == "__main__":
    unittest.main()
