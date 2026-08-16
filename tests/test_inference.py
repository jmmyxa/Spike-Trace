import hashlib
import importlib.metadata
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import torch

from spiketrace.constants import SAMPLING_CONTRACT
from spiketrace.inference import infer_video


def _write_test_video(path: Path) -> None:
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (8, 6)
    )
    if not writer.isOpened():
        raise RuntimeError("Could not create temporary test video.")
    try:
        for frame_index in range(12):
            writer.write(np.full((6, 8, 3), frame_index * 15, dtype=np.uint8))
    finally:
        writer.release()


class _ConstantModel:
    def __call__(self, batch):
        return torch.tensor([[0.0, 2.0]], dtype=torch.float32).repeat(batch.shape[0], 1)


class InferenceTests(unittest.TestCase):
    def test_inference_uses_sequential_batches_without_a_heavy_model(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            video_path = temporary_path / "fixture.avi"
            checkpoint_path = temporary_path / "checkpoint.pt"
            output_dir = temporary_path / "outputs"
            _write_test_video(video_path)
            checkpoint_path.write_bytes(b"checkpoint fixture")
            checkpoint = {
                "num_frames": 2,
                "image_size": 4,
                "window_seconds": 0.4,
                "labels": ["background", "serve"],
                "model_version": "test-v1",
            }
            with patch(
                "spiketrace.inference.load_checkpoint",
                return_value=(_ConstantModel(), checkpoint),
            ), patch(
                "spiketrace.inference.resolve_device", return_value="cpu"
            ), patch(
                "spiketrace.inference.sample_video_clip",
                side_effect=AssertionError("per-window sampler must not be used"),
                create=True,
            ):
                try:
                    result = infer_video(
                        video_path,
                        checkpoint_path,
                        output_dir,
                        stride_seconds=0.2,
                        confidence_threshold=0.0,
                        device="cpu",
                    )
                except AssertionError as exc:
                    self.fail(str(exc))

            self.assertEqual(result["window_count"], 5)
            payload = json.loads((output_dir / "events.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["format_version"], 2)
            self.assertEqual(
                [(item["start_seconds"], item["end_seconds"]) for item in payload["windows"]],
                [(0.0, 0.4), (0.2, 0.6), (0.4, 0.8), (0.6, 1.0), (0.8, 1.2)],
            )
            self.assertEqual({item["action"] for item in payload["windows"]}, {"serve"})
            self.assertEqual(
                payload["events"][0]["source_window_indices"], [0, 1, 2, 3, 4]
            )
            self.assertEqual(
                [item["window_index"] for item in payload["windows"]], [0, 1, 2, 3, 4]
            )
            self.assertEqual(
                payload["settings"]["sampling_contract"], SAMPLING_CONTRACT
            )
            self.assertEqual(
                payload["settings"]["checkpoint_sha256"],
                hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                payload["settings"]["video_sha256"],
                hashlib.sha256(video_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(payload["settings"]["opencv_version"], str(cv2.__version__))
            self.assertEqual(payload["settings"]["torch_version"], str(torch.__version__))
            self.assertEqual(
                payload["settings"]["torchvision_version"],
                importlib.metadata.version("torchvision"),
            )
            self.assertEqual(payload["settings"]["video"], payload["video"])


if __name__ == "__main__":
    unittest.main()
