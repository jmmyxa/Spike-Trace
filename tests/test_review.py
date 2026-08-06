import csv
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

    def test_real_results_produce_the_second_reviewed_manifest(self):
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
            self.assertEqual(output.read_bytes(), committed_output.read_bytes())
            match = json.loads(
                (
                    root / "data" / "annotations" / "usa_germany_2024_match.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(match["annotation_manifest"], committed_output.name)
            self.assertEqual(match["annotation_status"], "second_pass_reviewed")
            self.assertEqual(match["annotation_summary"]["records"], 68)
            self.assertEqual(
                match["annotation_summary"]["records_by_label"],
                summary["records_by_label"],
            )
            self.assertEqual(match["second_review"]["status"], "applied")


if __name__ == "__main__":
    unittest.main()
