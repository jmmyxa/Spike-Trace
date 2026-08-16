import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from spiketrace.constants import ACTION_LABEL_SCHEMA_VERSION
from spiketrace.ml import load_checkpoint
from spiketrace.training import train_action_model


class TrainingConfigTests(unittest.TestCase):
    def _write_train_only_manifest(self, root: Path) -> Path:
        (root / "train.avi").touch()
        manifest = root / "annotations.csv"
        manifest.write_text(
            "video_path,start_seconds,end_seconds,label,split\n"
            "train.avi,0,1,serve,train\n",
            encoding="utf-8",
        )
        return manifest

    def test_requires_validation_records_without_train_only_opt_in(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_train_only_manifest(root)

            with (
                mock.patch("spiketrace.training._validate_annotation_bounds"),
                self.assertRaisesRegex(
                    ValueError, "The manifest must contain at least one val record."
                ),
            ):
                train_action_model(manifest, root / "output")

    def test_train_only_mode_reports_training_selection_without_validation_metrics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_train_only_manifest(root)
            output = root / "output"

            with (
                mock.patch("spiketrace.training._validate_annotation_bounds"),
                mock.patch(
                    "spiketrace.training._run_epoch",
                    return_value=(0.25, [0], [0]),
                ) as run_epoch,
                mock.patch("builtins.print"),
            ):
                report = train_action_model(
                    manifest,
                    output,
                    allow_train_only=True,
                    model_name="tiny3d",
                    epochs=1,
                    batch_size=1,
                    num_frames=2,
                    image_size=8,
                    device="cpu",
                )

            config = json.loads((output / "training_config.json").read_text())
            epoch = report["history"][0]

        self.assertEqual(run_epoch.call_count, 1)
        self.assertTrue(config["allow_train_only"])
        self.assertEqual(config["selection_split"], "train")
        self.assertFalse(config["generalization_metrics_available"])
        self.assertEqual(report["selection_split"], "train")
        self.assertFalse(report["generalization_metrics_available"])
        self.assertTrue(report["allow_train_only"])
        self.assertEqual(report["best_macro_f1"], epoch["train"]["macro_f1"])
        self.assertEqual(
            report["best_selection_macro_f1"], epoch["train"]["macro_f1"]
        )
        self.assertEqual(epoch["selection_split"], "train")
        self.assertIn("train_loss", epoch)
        self.assertIn("train", epoch)
        self.assertNotIn("val_loss", epoch)
        self.assertNotIn("val", epoch)

    def test_train_only_best_checkpoint_uses_highest_training_macro_f1(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_train_only_manifest(root)
            output = root / "output"

            with (
                mock.patch("spiketrace.training._validate_annotation_bounds"),
                mock.patch(
                    "spiketrace.training._run_epoch",
                    side_effect=[
                        (0.25, [0], [0]),
                        (0.25, [0], [1]),
                    ],
                ),
                mock.patch("builtins.print"),
            ):
                train_action_model(
                    manifest,
                    output,
                    allow_train_only=True,
                    model_name="tiny3d",
                    epochs=2,
                    batch_size=1,
                    num_frames=2,
                    image_size=8,
                    device="cpu",
                )

            _, best_checkpoint = load_checkpoint(output / "best.pt", device="cpu")
            _, latest_checkpoint = load_checkpoint(
                output / "latest.pt", device="cpu"
            )

        self.assertEqual(best_checkpoint["epoch"], 1)
        self.assertEqual(latest_checkpoint["epoch"], 2)
        self.assertEqual(best_checkpoint["metrics"]["selection_split"], "train")
        self.assertGreater(
            best_checkpoint["metrics"]["train"]["macro_f1"],
            latest_checkpoint["metrics"]["train"]["macro_f1"],
        )

    def test_writes_action_label_schema_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "train.avi").touch()
            (root / "val.avi").touch()
            manifest = root / "annotations.csv"
            manifest.write_text(
                "video_path,start_seconds,end_seconds,label,split\n"
                "train.avi,0,1,serve,train\n"
                "val.avi,0,1,attack,val\n",
                encoding="utf-8",
            )
            output = root / "output"

            with (
                mock.patch("spiketrace.training._validate_annotation_bounds"),
                mock.patch(
                    "spiketrace.training._run_epoch",
                    side_effect=[(0.0, [1], [1]), (0.0, [4], [4])],
                ),
                mock.patch("builtins.print"),
            ):
                train_action_model(
                    manifest,
                    output,
                    model_name="tiny3d",
                    epochs=1,
                    batch_size=1,
                    num_frames=2,
                    image_size=8,
                    device="cpu",
                )

            config = json.loads((output / "training_config.json").read_text())

        self.assertEqual(
            config["action_label_schema_version"], ACTION_LABEL_SCHEMA_VERSION
        )


class TrainingCommandTests(unittest.TestCase):
    def test_train_command_parses_and_dispatches_train_only_opt_in(self):
        from spiketrace.cli import build_parser, run_command

        args = build_parser().parse_args(
            ["train", "annotations.csv", "runs/train-only", "--allow-train-only"]
        )
        expected = {"status": "ok"}

        with mock.patch(
            "spiketrace.training.train_action_model", return_value=expected
        ) as train:
            result = run_command(args)

        self.assertTrue(args.allow_train_only)
        self.assertEqual(result, expected)
        self.assertTrue(train.call_args.kwargs["allow_train_only"])


if __name__ == "__main__":
    unittest.main()
