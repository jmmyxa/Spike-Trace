import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from spiketrace.constants import ACTION_LABEL_SCHEMA_VERSION
from spiketrace.training import train_action_model


class TrainingConfigTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
