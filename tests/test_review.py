import csv
import json
import tempfile
import unittest
from pathlib import Path

from spiketrace.errors import ReviewError
from spiketrace.review import prepare_review_queue


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


if __name__ == "__main__":
    unittest.main()
