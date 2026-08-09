import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spiketrace.errors import ManifestError, ReviewError
from spiketrace.manifest import load_manifest
from spiketrace.review import apply_review_results, prepare_review_queue


class ReviewQueueTests(unittest.TestCase):
    def _write_manifest(self, root: Path) -> Path:
        manifest = root / "annotations.csv"
        manifest.write_text(
            "video_path,start_seconds,end_seconds,label,team_side,split,notes\n"
            "match.avi,0,1,serve,far,train,Human-reviewed\n"
            "match.avi,1,2,receive,far,train,"
            "Human-reviewed; reviewer note: 动作稍晚\n"
            "match.avi,2,3,background,far,train,Human-reviewed\n",
            encoding="utf-8",
        )
        return manifest

    def _write_spec(self, root: Path, requests: list[dict[str, object]]) -> Path:
        spec = root / "review.json"
        spec.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "manifest": "annotations.csv",
                    "target_team": "USA",
                    "requests": requests,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return spec

    def _write_results(
        self, root: Path, confirmations: list[dict[str, object]]
    ) -> Path:
        results = root / "review-results.json"
        results.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "manifest": "annotations.csv",
                    "spec": "review.json",
                    "time_precision_seconds": 1.0,
                    "confirmations": confirmations,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return results

    def _confirmation(self, **overrides: object) -> dict[str, object]:
        confirmation: dict[str, object] = {
            "record_index": 1,
            "source_video_path": "match.avi",
            "source_start_seconds": 0.0,
            "source_end_seconds": 1.0,
            "source_action": "serve",
            "source_split": "train",
            "source_team_side": "far",
            "source_player_number": "",
            "source_crop": "",
            "source_notes": "Human-reviewed",
            "operation": "move_window",
            "confirmed_action": "serve",
            "confirmed_start_seconds": 0.2,
            "confirmed_end_seconds": 1.2,
            "confirmation_note": "",
        }
        confirmation.update(overrides)
        return confirmation

    def test_applies_updates_and_appends_confirmed_new_windows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root)
            spec = self._write_spec(
                root,
                [
                    {
                        "record_index": 1,
                        "reason": "move serve",
                        "suggested_operation": "move_window",
                    },
                    {
                        "record_index": 2,
                        "reason": "add attack",
                        "suggested_operation": "add_window",
                    },
                ],
            )
            results = self._write_results(
                root,
                [
                    self._confirmation(confirmation_note="later contact"),
                    self._confirmation(
                        record_index=2,
                        source_start_seconds=1.0,
                        source_end_seconds=2.0,
                        source_action="receive",
                        source_notes="Human-reviewed; reviewer note: 动作稍晚",
                        operation="add_window",
                        confirmed_action="attack",
                        confirmed_start_seconds=1.5,
                        confirmed_end_seconds=2.5,
                    ),
                ],
            )
            output = root / "annotations-second-reviewed.csv"

            summary = apply_review_results(
                manifest, spec, results, output, require_files=False
            )

            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(summary["updated_records"], 1)
            self.assertEqual(summary["added_records"], 1)
            self.assertEqual(summary["output_records"], 4)
            self.assertEqual(rows[0]["start_seconds"], "0.2")
            self.assertEqual(rows[0]["end_seconds"], "1.2")
            self.assertEqual(rows[1]["label"], "receive")
            self.assertEqual(rows[3]["label"], "attack")
            self.assertEqual(rows[3]["start_seconds"], "1.5")
            self.assertEqual(rows[3]["review_status"], "reviewed")
            self.assertTrue(rows[0]["notes"].startswith("Human-reviewed"))
            self.assertIn("later contact", rows[0]["notes"])

    def test_refuses_to_overwrite_any_input(self):
        for protected_input in ("manifest", "spec", "results"):
            with (
                self.subTest(protected_input=protected_input),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                manifest = self._write_manifest(root)
                spec = self._write_spec(
                    root,
                    [
                        {
                            "record_index": 1,
                            "reason": "move serve",
                            "suggested_operation": "move_window",
                        }
                    ],
                )
                results = self._write_results(root, [self._confirmation()])
                protected = {
                    "manifest": manifest,
                    "spec": spec,
                    "results": results,
                }[protected_input]

                with self.assertRaisesRegex(ReviewError, "different"):
                    apply_review_results(
                        manifest,
                        spec,
                        results,
                        protected,
                        require_files=False,
                    )

    def test_uses_source_video_root_for_output_in_another_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root)
            (root / "match.avi").touch()
            spec = self._write_spec(
                root,
                [
                    {
                        "record_index": 1,
                        "reason": "move serve",
                        "suggested_operation": "move_window",
                    }
                ],
            )
            results = self._write_results(root, [self._confirmation()])
            output = root / "reviewed" / "annotations.csv"

            summary = apply_review_results(manifest, spec, results, output)

            self.assertEqual(summary["output_records"], 3)
            self.assertTrue(output.is_file())

    def test_preserves_existing_output_when_post_write_validation_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root)
            spec = self._write_spec(
                root,
                [
                    {
                        "record_index": 1,
                        "reason": "move serve",
                        "suggested_operation": "move_window",
                    }
                ],
            )
            results = self._write_results(root, [self._confirmation()])
            output = root / "output.csv"
            sentinel = b"existing reviewed manifest\n"
            output.write_bytes(sentinel)
            source_records = load_manifest(manifest, require_files=False)

            with (
                patch(
                    "spiketrace.review.load_manifest",
                    side_effect=[
                        source_records,
                        ManifestError("forced output validation failure"),
                    ],
                ),
                self.assertRaisesRegex(ManifestError, "forced output validation"),
            ):
                apply_review_results(
                    manifest, spec, results, output, require_files=False
                )

            self.assertEqual(output.read_bytes(), sentinel)

    def test_rejects_source_rows_with_extra_cells_without_touching_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root)
            source = manifest.read_text(encoding="utf-8")
            manifest.write_text(
                source.replace(
                    "match.avi,0,1,serve,far,train,Human-reviewed",
                    "match.avi,0,1,serve,far,train,Human-reviewed,unexpected",
                ),
                encoding="utf-8",
            )
            spec = self._write_spec(
                root,
                [
                    {
                        "record_index": 1,
                        "reason": "move serve",
                        "suggested_operation": "move_window",
                    }
                ],
            )
            results = self._write_results(root, [self._confirmation()])
            output = root / "output.csv"
            sentinel = "existing reviewed manifest\n"
            output.write_text(sentinel, encoding="utf-8")

            with self.assertRaisesRegex(ReviewError, "extra cell"):
                apply_review_results(
                    manifest, spec, results, output, require_files=False
                )

            self.assertEqual(output.read_text(encoding="utf-8"), sentinel)

    def test_rejects_changed_source_snapshot_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root)
            spec = self._write_spec(
                root,
                [
                    {
                        "record_index": 1,
                        "reason": "move serve",
                        "suggested_operation": "move_window",
                    }
                ],
            )
            confirmation = self._confirmation(
                source_split="train",
                source_team_side="far",
                source_player_number="",
                source_crop="",
                source_notes="older notes",
            )
            results = self._write_results(root, [confirmation])

            with self.assertRaisesRegex(ReviewError, "source snapshot"):
                apply_review_results(
                    manifest,
                    spec,
                    results,
                    root / "output.csv",
                    require_files=False,
                )

    def test_rejects_micro_change_in_source_snapshot_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root)
            source = manifest.read_text(encoding="utf-8")
            manifest.write_text(
                source.replace(
                    "match.avi,0,1,serve",
                    "match.avi,1000.0000005,1001,serve",
                ),
                encoding="utf-8",
            )
            spec = self._write_spec(
                root,
                [
                    {
                        "record_index": 1,
                        "reason": "move serve",
                        "suggested_operation": "move_window",
                    }
                ],
            )
            results = self._write_results(
                root,
                [
                    self._confirmation(
                        source_start_seconds=1000.0,
                        source_end_seconds=1001.0,
                        confirmed_start_seconds=1000.2,
                        confirmed_end_seconds=1001.2,
                    )
                ],
            )

            with self.assertRaisesRegex(ReviewError, "source snapshot"):
                apply_review_results(
                    manifest,
                    spec,
                    results,
                    root / "output.csv",
                    require_files=False,
                )

    def test_rejects_invalid_declared_time_precision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root)
            spec = self._write_spec(
                root,
                [
                    {
                        "record_index": 1,
                        "reason": "move serve",
                        "suggested_operation": "move_window",
                    }
                ],
            )
            results = self._write_results(root, [self._confirmation()])
            payload = json.loads(results.read_text(encoding="utf-8"))
            payload["time_precision_seconds"] = -1
            results.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ReviewError, "time_precision_seconds"):
                apply_review_results(
                    manifest,
                    spec,
                    results,
                    root / "output.csv",
                    require_files=False,
                )

    def test_rejects_incomplete_duplicate_extra_or_reordered_confirmations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root)
            spec = self._write_spec(
                root,
                [
                    {
                        "record_index": 1,
                        "reason": "move serve",
                        "suggested_operation": "move_window",
                    },
                    {
                        "record_index": 2,
                        "reason": "add attack",
                        "suggested_operation": "add_window",
                    },
                ],
            )
            first = self._confirmation()
            second = self._confirmation(
                record_index=2,
                source_start_seconds=1.0,
                source_end_seconds=2.0,
                source_action="receive",
                source_notes="Human-reviewed; reviewer note: 动作稍晚",
                operation="add_window",
                confirmed_action="attack",
                confirmed_start_seconds=1.5,
                confirmed_end_seconds=2.5,
            )
            extra = self._confirmation(record_index=3)
            cases = (
                ("incomplete", [first], "confirm every"),
                ("duplicate", [first, first], "duplicate"),
                ("extra", [first, extra], "not requested"),
                ("reordered", [second, first], "follow"),
            )

            for name, confirmations, message in cases:
                with self.subTest(name=name):
                    results = self._write_results(root, confirmations)
                    output = root / f"{name}.csv"
                    with self.assertRaisesRegex(ReviewError, message):
                        apply_review_results(
                            manifest, spec, results, output, require_files=False
                        )
                    self.assertFalse(output.exists())

    def test_rejects_invalid_confirmation_values_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root)
            spec = self._write_spec(
                root,
                [
                    {
                        "record_index": 1,
                        "reason": "move serve",
                        "suggested_operation": "move_window",
                    }
                ],
            )
            cases = (
                ("action", {"confirmed_action": "spike"}, "unknown action"),
                (
                    "negative-time",
                    {"confirmed_start_seconds": -1.0},
                    "non-negative",
                ),
                (
                    "non-finite-time",
                    {"confirmed_start_seconds": float("nan")},
                    "finite",
                ),
                (
                    "reversed-time",
                    {
                        "confirmed_start_seconds": 2.0,
                        "confirmed_end_seconds": 1.0,
                    },
                    "start < end",
                ),
                ("operation", {"operation": "relabel"}, "operation"),
            )

            for name, overrides, message in cases:
                with self.subTest(name=name):
                    results = self._write_results(
                        root, [self._confirmation(**overrides)]
                    )
                    output = root / f"{name}.csv"
                    with self.assertRaisesRegex(ReviewError, message):
                        apply_review_results(
                            manifest, spec, results, output, require_files=False
                        )
                    self.assertFalse(output.exists())

    def test_requires_an_explicit_supported_operation_when_applying(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root)
            spec = self._write_spec(
                root, [{"record_index": 1, "reason": "check this window"}]
            )
            results = self._write_results(root, [self._confirmation(operation=None)])
            output = root / "output.csv"

            with self.assertRaisesRegex(ReviewError, "supported operation"):
                apply_review_results(
                    manifest, spec, results, output, require_files=False
                )
            self.assertFalse(output.exists())

    def test_writes_manifest_order_notes_and_blank_confirmation_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root)
            spec = self._write_spec(
                root,
                [
                    {
                        "record_index": 2,
                        "reason": "区分接发球和防守起球",
                        "suggested_operation": "relabel",
                    },
                    {
                        "record_index": 1,
                        "reason": "时间需对齐",
                        "suggested_operation": "move_window",
                        "suggested_action": "serve",
                        "suggested_start_seconds": 0.2,
                        "suggested_end_seconds": 1.0,
                    },
                ],
            )
            output = root / "output" / "queue.csv"

            result = prepare_review_queue(manifest, spec, output, require_files=False)

            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(result["record_indices"], [1, 2])
            self.assertEqual([int(row["record_index"]) for row in rows], [1, 2])
            self.assertEqual(rows[1]["reviewer_note"], "动作稍晚")
            self.assertEqual(rows[0]["current_start_time"], "00:00:00.00")
            self.assertEqual(rows[0]["suggested_start_time"], "00:00:00.20")
            self.assertEqual(rows[0]["confirmed_action"], "")
            self.assertEqual(rows[0]["confirmed_start_time"], "")
            self.assertEqual(rows[0]["confirmed_end_time"], "")
            self.assertEqual(rows[0]["confirmation_note"], "")

    def test_prepare_review_refuses_to_overwrite_an_input(self):
        for protected_input in ("manifest", "spec"):
            with (
                self.subTest(protected_input=protected_input),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                manifest = self._write_manifest(root)
                spec = self._write_spec(root, [{"record_index": 1, "reason": "check"}])
                protected = {"manifest": manifest, "spec": spec}[protected_input]
                original = protected.read_bytes()

                with self.assertRaisesRegex(ReviewError, "different"):
                    prepare_review_queue(manifest, spec, protected, require_files=False)

                self.assertEqual(protected.read_bytes(), original)

    def test_rejects_duplicate_request_indices(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root)
            spec = self._write_spec(
                root,
                [
                    {"record_index": 1, "reason": "first"},
                    {"record_index": 1, "reason": "duplicate"},
                ],
            )

            with self.assertRaisesRegex(ReviewError, "duplicate"):
                prepare_review_queue(
                    manifest, spec, root / "queue.csv", require_files=False
                )

    def test_rejects_out_of_range_request_indices(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root)
            spec = self._write_spec(
                root, [{"record_index": 4, "reason": "outside manifest"}]
            )

            with self.assertRaisesRegex(ReviewError, "out of range"):
                prepare_review_queue(
                    manifest, spec, root / "queue.csv", require_files=False
                )

    def test_writes_start_only_suggestion_without_an_invented_end_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root)
            spec = self._write_spec(
                root,
                [
                    {
                        "record_index": 1,
                        "reason": "confirmed start only",
                        "suggested_operation": "move_window",
                        "suggested_start_seconds": 0.2,
                    }
                ],
            )

            output = root / "queue.csv"
            prepare_review_queue(manifest, spec, output, require_files=False)

            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["suggested_start_seconds"], "0.2")
            self.assertEqual(row["suggested_start_time"], "00:00:00.20")
            self.assertEqual(row["suggested_end_seconds"], "")
            self.assertEqual(row["suggested_end_time"], "")

    def test_rejects_end_only_suggestion_without_operation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root)
            spec = self._write_spec(
                root,
                [
                    {
                        "record_index": 1,
                        "reason": "confirmed end only",
                        "suggested_end_seconds": 0.8,
                    }
                ],
            )

            with self.assertRaisesRegex(ReviewError, "suggestions require"):
                prepare_review_queue(
                    manifest, spec, root / "queue.csv", require_files=False
                )

    def test_rejects_manifest_name_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root)
            spec = self._write_spec(root, [{"record_index": 1, "reason": "check"}])
            payload = json.loads(spec.read_text(encoding="utf-8"))
            payload["manifest"] = "another.csv"
            spec.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ReviewError, "manifest"):
                prepare_review_queue(
                    manifest, spec, root / "queue.csv", require_files=False
                )

    def test_parser_accepts_prepare_review_command(self):
        from spiketrace.cli import build_parser

        args = build_parser().parse_args(
            [
                "prepare-review",
                "annotations.csv",
                "review.json",
                "queue.csv",
                "--allow-missing-videos",
            ]
        )

        self.assertEqual(args.command, "prepare-review")
        self.assertTrue(args.allow_missing_videos)

    def test_apply_review_command_executes(self):
        from spiketrace.cli import build_parser, run_command

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root)
            spec = self._write_spec(
                root,
                [
                    {
                        "record_index": 1,
                        "reason": "move serve",
                        "suggested_operation": "move_window",
                    }
                ],
            )
            results = self._write_results(root, [self._confirmation()])
            output = root / "output.csv"
            args = build_parser().parse_args(
                [
                    "apply-review",
                    str(manifest),
                    str(spec),
                    str(results),
                    str(output),
                    "--allow-missing-videos",
                ]
            )

            summary = run_command(args)

            self.assertEqual(args.command, "apply-review")
            self.assertEqual(summary["output_records"], 3)
            self.assertTrue(output.is_file())

    def test_real_spec_contains_expected_record_indices(self):
        root = Path(__file__).parents[1]
        result = prepare_review_queue(
            root / "data" / "annotations" / "usa_germany_2024_annotations.csv",
            root / "data" / "annotations" / "usa_germany_2024_second_review.json",
            root / "outputs" / "test-second-review" / "queue.csv",
            require_files=False,
        )

        self.assertEqual(
            result["record_indices"],
            [1, 19, 21, 22, 23, 27, 31, 32, 35, 39, 43, 46, 47, 53, 65, 66, 67],
        )

    def test_real_results_produce_expanded_manifest_and_baseline(self):
        root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "annotations-second-reviewed.csv"

            summary = apply_review_results(
                root / "data" / "annotations" / "usa_germany_2024_annotations.csv",
                root / "data" / "annotations" / "usa_germany_2024_second_review.json",
                root
                / "data"
                / "annotations"
                / "usa_germany_2024_second_review_results.json",
                output,
                require_files=False,
            )

            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            with (
                root / "data" / "annotations" / "usa_germany_2024_annotations.csv"
            ).open("r", encoding="utf-8-sig", newline="") as handle:
                source_rows = list(csv.DictReader(handle))
            self.assertEqual(summary["updated_records"], 16)
            self.assertEqual(summary["added_records"], 1)
            self.assertEqual(summary["output_records"], 68)
            self.assertEqual(summary["duration_seconds"], 60.3)
            self.assertEqual(
                summary["records_by_label"],
                {
                    "attack": 2,
                    "background": 39,
                    "block": 9,
                    "dig": 4,
                    "receive": 3,
                    "serve": 9,
                    "set": 2,
                },
            )
            self.assertEqual(rows[20]["start_seconds"], "3660.5")
            self.assertEqual(rows[20]["label"], "dig")
            self.assertIn("dig失败", rows[20]["notes"])
            self.assertEqual(rows[45]["label"], "set")
            self.assertEqual(rows[45]["start_seconds"], "4026.0")
            self.assertEqual(rows[67]["label"], "attack")
            self.assertEqual(rows[67]["start_seconds"], "4026")
            self.assertEqual(rows[67]["end_seconds"], "4027")
            updated_indices = {
                1,
                19,
                21,
                22,
                23,
                27,
                31,
                32,
                35,
                39,
                43,
                47,
                53,
                65,
                66,
                67,
            }
            mutable_fields = {"start_seconds", "end_seconds", "label", "notes"}
            for record_index, (source, reviewed) in enumerate(
                zip(source_rows, rows[: len(source_rows)], strict=True), start=1
            ):
                if record_index not in updated_indices:
                    self.assertEqual(reviewed, source)
                    continue
                self.assertEqual(
                    {
                        key: value
                        for key, value in reviewed.items()
                        if key not in mutable_fields
                    },
                    {
                        key: value
                        for key, value in source.items()
                        if key not in mutable_fields
                    },
                )
                self.assertTrue(reviewed["notes"].startswith(source["notes"]))
            self.assertTrue(rows[67]["notes"].startswith(source_rows[45]["notes"]))
            self.assertEqual(len({tuple(row.items()) for row in rows}), 68)
            self.assertTrue(
                all(
                    0
                    <= float(row["start_seconds"])
                    < float(row["end_seconds"])
                    <= 7687.333333333333
                    for row in rows
                )
            )
            committed_output = (
                root
                / "data"
                / "annotations"
                / "usa_germany_2024_annotations_second_reviewed.csv"
            )
            expanded_output = (
                root
                / "data"
                / "annotations"
                / "usa_germany_2024_annotations_expanded_batch_01.csv"
            )
            self.assertEqual(output.read_bytes(), committed_output.read_bytes())
            match = json.loads(
                (
                    root / "data" / "annotations" / "usa_germany_2024_match.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(match["annotation_manifest"], expanded_output.name)
            self.assertEqual(match["annotation_status"], "expansion_batch_01_applied")
            self.assertEqual(match["annotation_summary"]["records"], 73)
            self.assertEqual(match["annotation_summary"]["duration_seconds"], 64.7)
            self.assertEqual(
                match["annotation_summary"]["records_by_split"], {"train": 73}
            )
            self.assertEqual(
                match["annotation_summary"]["records_by_label"],
                {
                    "attack": 3,
                    "background": 39,
                    "block": 10,
                    "dig": 4,
                    "receive": 4,
                    "serve": 9,
                    "set": 4,
                },
            )
            self.assertEqual(match["second_review"]["status"], "applied")
            baseline = match["pretrained_baseline"]
            self.assertEqual(baseline["status"], "completed")
            self.assertEqual(baseline["evaluated_on"], "2026-08-09")
            self.assertEqual(baseline["manifest"], expanded_output.name)
            self.assertEqual(
                baseline["manifest_sha256"],
                hashlib.sha256(expanded_output.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                baseline["weights_sha256"],
                "bfd7f2354ff15c91839cbe987a069d5f04b2311d296989487c87fb04bddef109",
            )
            self.assertEqual(
                baseline["settings"],
                {
                    "device": "cpu",
                    "frames_per_window": 6,
                    "confidence_threshold": 0.25,
                },
            )
            self.assertEqual(
                baseline["model_labels"],
                ["ball", "block", "receive", "set", "spike", "serve"],
            )
            self.assertEqual(
                baseline["external_label_mapping"],
                {
                    "ball": None,
                    "block": "block",
                    "receive": "receive",
                    "set": "set",
                    "spike": "attack",
                    "serve": "serve",
                },
            )
            self.assertEqual(
                baseline["outputs"],
                {
                    "directory": "outputs/pretrained-usa-germany-expanded-batch-01",
                    "report": "pretrained_evaluation.json",
                    "report_sha256": "5191782323886fa1184bf301b0552f274c2342d6ef7bd7b7e849a127779d1cdf",
                    "review_csv": "pretrained_review.csv",
                    "review_csv_sha256": "74e8908e36c5967d6599be917ab03927964e25ef89f21ca5f3c50cecfb327945",
                    "git_tracked": False,
                },
            )
            strict_metrics = baseline["strict_seven_class_metrics"]
            self.assertEqual(
                strict_metrics["records"], match["annotation_summary"]["records"]
            )
            self.assertEqual(strict_metrics["correct"], 46)
            self.assertEqual(strict_metrics["accuracy"], 0.630137)
            self.assertEqual(strict_metrics["macro_f1"], 0.344159)
            self.assertEqual(
                strict_metrics["per_class"],
                {
                    "background": {
                        "precision": 0.698113,
                        "recall": 0.948718,
                        "f1": 0.804348,
                        "support": 39,
                    },
                    "serve": {
                        "precision": 0.666667,
                        "recall": 0.222222,
                        "f1": 0.333333,
                        "support": 9,
                    },
                    "receive": {
                        "precision": 0.666667,
                        "recall": 0.5,
                        "f1": 0.571429,
                        "support": 4,
                    },
                    "set": {
                        "precision": 0.166667,
                        "recall": 0.25,
                        "f1": 0.2,
                        "support": 4,
                    },
                    "attack": {
                        "precision": 0.0,
                        "recall": 0.0,
                        "f1": 0.0,
                        "support": 3,
                    },
                    "block": {
                        "precision": 0.666667,
                        "recall": 0.4,
                        "f1": 0.5,
                        "support": 10,
                    },
                    "dig": {
                        "precision": 0.0,
                        "recall": 0.0,
                        "f1": 0.0,
                        "support": 4,
                    },
                },
            )
            self.assertEqual(
                baseline["strict_confusion_matrix"],
                {
                    "labels": [
                        "background",
                        "serve",
                        "receive",
                        "set",
                        "attack",
                        "block",
                        "dig",
                    ],
                    "values": [
                        [37, 0, 0, 1, 0, 1, 0],
                        [7, 2, 0, 0, 0, 0, 0],
                        [2, 0, 2, 0, 0, 0, 0],
                        [2, 0, 1, 1, 0, 0, 0],
                        [1, 1, 0, 1, 0, 0, 0],
                        [3, 0, 0, 3, 0, 4, 0],
                        [1, 0, 0, 0, 2, 1, 0],
                    ],
                },
            )
            self.assertEqual(
                baseline["compatibility_six_class_metrics"],
                {
                    "dig_mapped_to": "receive",
                    "accuracy": 0.630137,
                    "macro_f1": 0.366886,
                    "confusion_matrix": {
                        "labels": [
                            "background",
                            "serve",
                            "receive",
                            "set",
                            "attack",
                            "block",
                        ],
                        "values": [
                            [37, 0, 0, 1, 0, 1],
                            [7, 2, 0, 0, 0, 0],
                            [3, 0, 2, 0, 2, 1],
                            [2, 0, 1, 1, 0, 0],
                            [1, 1, 0, 1, 0, 0],
                            [3, 0, 0, 3, 0, 4],
                        ],
                    },
                },
            )
            self.assertEqual(
                baseline["largest_error_groups"],
                [
                    {"expected": "serve", "predicted": "background", "count": 7},
                    {"expected": "block", "predicted": "background", "count": 3},
                    {"expected": "block", "predicted": "set", "count": 3},
                ],
            )
            previous_baseline = baseline["previous_baseline"]
            self.assertEqual(previous_baseline["evaluated_on"], "2026-08-07")
            self.assertEqual(previous_baseline["manifest"], committed_output.name)
            self.assertEqual(
                previous_baseline["manifest_sha256"],
                hashlib.sha256(committed_output.read_bytes()).hexdigest(),
            )
            self.assertTrue(previous_baseline["same_weights_and_settings"])
            self.assertEqual(
                {
                    key: previous_baseline["strict_seven_class_metrics"][key]
                    for key in ("records", "correct", "accuracy", "macro_f1")
                },
                {
                    "records": 68,
                    "correct": 46,
                    "accuracy": 0.676471,
                    "macro_f1": 0.353654,
                },
            )
            self.assertEqual(
                previous_baseline["compatibility_six_class_metrics"],
                {
                    "dig_mapped_to": "receive",
                    "accuracy": 0.676471,
                    "macro_f1": 0.368151,
                    "confusion_matrix": {
                        "labels": [
                            "background",
                            "serve",
                            "receive",
                            "set",
                            "attack",
                            "block",
                        ],
                        "values": [
                            [37, 0, 0, 1, 0, 1],
                            [7, 2, 0, 0, 0, 0],
                            [2, 0, 2, 0, 2, 1],
                            [1, 0, 1, 0, 0, 0],
                            [1, 1, 0, 0, 0, 0],
                            [0, 0, 0, 4, 0, 5],
                        ],
                    },
                },
            )
            self.assertEqual(
                previous_baseline["strict_confusion_matrix"]["values"],
                [
                    [37, 0, 0, 1, 0, 1, 0],
                    [7, 2, 0, 0, 0, 0, 0],
                    [1, 0, 2, 0, 0, 0, 0],
                    [1, 0, 1, 0, 0, 0, 0],
                    [1, 1, 0, 0, 0, 0, 0],
                    [0, 0, 0, 4, 0, 5, 0],
                    [1, 0, 0, 0, 2, 1, 0],
                ],
            )
            self.assertEqual(
                baseline["comparison_to_previous"],
                {
                    "shared_record_count": 65,
                    "shared_prediction_changes": 0,
                    "removed_duplicate_record_count": 3,
                    "removed_correct_count": 1,
                    "added_record_count": 8,
                    "added_correct_count": 1,
                    "record_count_delta": 5,
                    "correct_count_delta": 0,
                    "accuracy_delta": -0.046334,
                    "macro_f1_delta": -0.009495,
                    "compatibility_accuracy_delta": -0.046334,
                    "compatibility_macro_f1_delta": -0.001265,
                    "interpretation": (
                        "Dataset composition changed; this is not a model "
                        "regression comparison."
                    ),
                },
            )
            expansion = match["annotation_expansion"]
            expansion_spec_path = root / "data" / "annotations" / expansion["spec"]
            expansion_spec = json.loads(expansion_spec_path.read_text(encoding="utf-8"))
            self.assertEqual(expansion["status"], "applied")
            self.assertEqual(expansion["interval_count"], 6)
            self.assertEqual(expansion["duration_seconds"], 70.6)
            self.assertEqual(
                expansion["results"], "usa_germany_2024_expansion_batch_01_results.json"
            )
            self.assertEqual(expansion["output_manifest"], expanded_output.name)
            self.assertEqual(
                expansion["output_manifest_sha256"],
                hashlib.sha256(expanded_output.read_bytes()).hexdigest(),
            )
            self.assertEqual(expansion["confirmed_interval_count"], 6)
            self.assertEqual(expansion["added_action_count"], 8)
            self.assertEqual(expansion["added_duration_seconds"], 8.0)
            self.assertEqual(
                expansion["added_records_by_label"],
                {"attack": 1, "block": 3, "dig": 1, "receive": 1, "set": 2},
            )
            self.assertEqual(expansion["removed_duplicate_count"], 3)
            self.assertEqual(
                expansion["removed_records_by_label"], {"block": 2, "dig": 1}
            )
            self.assertEqual(expansion["net_record_change"], 5)
            self.assertEqual(
                expansion["source_interval_indices"], [19, 13, 11, 2, 6, 14]
            )
            self.assertEqual(
                expansion["priority_labels"],
                ["set", "attack", "receive", "dig", "block"],
            )
            self.assertEqual(expansion_spec["source_manifest"], committed_output.name)
            self.assertEqual(
                expansion_spec["source_manifest_sha256"],
                hashlib.sha256(committed_output.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                expansion_spec["workbook"],
                expansion["workbook"],
            )
            self.assertTrue((root / expansion["workbook"]).is_file())
            self.assertEqual(expansion_spec["annotation_mode"], "exhaustive_full_rally")
            self.assertEqual(expansion_spec["time_precision_seconds"], 1.0)
            self.assertEqual(expansion_spec["action_slots_per_interval"], 8)
            self.assertEqual(
                expansion_spec["priority_labels"], expansion["priority_labels"]
            )
            self.assertEqual(
                expansion_spec["review_rules"],
                {
                    "confirm_entire_interval": True,
                    "record_only_missing_usa_actions": True,
                    "blank_action_rows_are_ignored": True,
                    "input_time_format": "HH:MM:SS",
                    "existing_actions_remain_unchanged": True,
                },
            )
            self.assertEqual(
                expansion["interval_count"], len(expansion_spec["intervals"])
            )
            self.assertEqual(
                [
                    interval["source_interval_index"]
                    for interval in expansion_spec["intervals"]
                ],
                expansion["source_interval_indices"],
            )
            for interval in expansion_spec["intervals"]:
                source_interval = match["reviewed_intervals"][
                    interval["source_interval_index"] - 1
                ]
                self.assertEqual(
                    {
                        key: interval[key]
                        for key in ("start_seconds", "end_seconds", "usa_side")
                    },
                    source_interval,
                )
            self.assertAlmostEqual(
                sum(
                    interval["end_seconds"] - interval["start_seconds"]
                    for interval in expansion_spec["intervals"]
                ),
                expansion["duration_seconds"],
            )

            with expanded_output.open("r", encoding="utf-8-sig", newline="") as handle:
                expanded_rows = list(csv.DictReader(handle))
            self.assertEqual(len(expanded_rows), 73)
            removed_record_indices = {22, 40, 52}
            self.assertEqual(
                expanded_rows[:-8],
                [
                    row
                    for record_index, row in enumerate(rows, start=1)
                    if record_index not in removed_record_indices
                ],
            )
            self.assertEqual(
                [row["label"] for row in expanded_rows[-8:]],
                ["receive", "set", "attack", "block", "dig", "set", "block", "block"],
            )
            self.assertEqual(
                [
                    (row["start_seconds"], row["end_seconds"])
                    for row in expanded_rows[-8:]
                ],
                [
                    ("6945", "6946"),
                    ("6946", "6947"),
                    ("6948", "6949"),
                    ("6951", "6952"),
                    ("6952", "6953"),
                    ("6953", "6954"),
                    ("1323", "1324"),
                    ("1327", "1328"),
                ],
            )
            results = json.loads(
                (
                    root
                    / "data"
                    / "annotations"
                    / "usa_germany_2024_expansion_batch_01_results.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(results["status"], "applied")
            self.assertEqual(results["summary"]["added_action_count"], 8)
            self.assertEqual(results["summary"]["removed_duplicate_count"], 3)
            self.assertEqual(results["summary"]["net_record_change"], 5)
            self.assertEqual(
                results["summary"]["output_manifest_summary"],
                match["annotation_summary"],
            )
            self.assertEqual(
                results["output_manifest_sha256"],
                hashlib.sha256(expanded_output.read_bytes()).hexdigest(),
            )
            self.assertEqual(len(results["interval_confirmations"]), 6)
            self.assertTrue(
                all(item["confirmed"] for item in results["interval_confirmations"])
            )
            self.assertEqual(len(results["ignored_notes"]), 1)
            self.assertIn("没碰到球", results["ignored_notes"][0]["source_text"])
            removed_records = results["removed_source_records"]
            self.assertEqual(
                [item["remove_record_index"] for item in removed_records],
                [22, 40, 52],
            )
            self.assertEqual(
                [item["keep_record_index"] for item in removed_records],
                [21, 39, 51],
            )
            for item in removed_records:
                self.assertEqual(
                    item["removed_source_snapshot"],
                    rows[item["remove_record_index"] - 1],
                )
                self.assertEqual(
                    item["kept_source_snapshot"],
                    rows[item["keep_record_index"] - 1],
                )
            self.assertEqual(
                results["workbook_sha256"],
                hashlib.sha256(
                    (
                        root
                        / "outputs"
                        / "expansion-batch-01"
                        / "usa_germany_2024_expansion_batch_01.xlsx"
                    ).read_bytes()
                ).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
