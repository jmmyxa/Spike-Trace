import tempfile
import unittest
from pathlib import Path

from spiketrace.constants import ACTION_LABEL_SCHEMA_VERSION
from spiketrace.ml import create_model, load_checkpoint, make_checkpoint, require_torch


class CheckpointTests(unittest.TestCase):
    def test_new_checkpoint_records_action_label_schema_version(self):
        checkpoint = make_checkpoint(
            model=create_model("tiny3d", 2),
            model_name="tiny3d",
            labels=("background", "serve"),
            model_version="test",
            num_frames=2,
            image_size=8,
            window_seconds=1.0,
            epoch=1,
            metrics={},
        )

        self.assertEqual(
            checkpoint["action_label_schema_version"], ACTION_LABEL_SCHEMA_VERSION
        )

    def test_loads_legacy_six_label_checkpoint_without_schema_field(self):
        legacy_labels = (
            "background",
            "serve",
            "receive",
            "set",
            "attack",
            "block",
        )
        checkpoint = make_checkpoint(
            model=create_model("tiny3d", len(legacy_labels)),
            model_name="tiny3d",
            labels=legacy_labels,
            model_version="legacy",
            num_frames=2,
            image_size=8,
            window_seconds=1.0,
            epoch=1,
            metrics={},
        )
        checkpoint.pop("action_label_schema_version", None)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "legacy.pt"
            require_torch().save(checkpoint, path)
            _model, loaded_checkpoint = load_checkpoint(path, device="cpu")

        self.assertEqual(loaded_checkpoint["labels"], list(legacy_labels))


if __name__ == "__main__":
    unittest.main()
