import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from spiketrace.errors import VideoError
from spiketrace.video import (
    clip_sample_frame_indices,
    iter_window_times,
    sample_video_clip,
    write_proxy_video,
)


def _write_test_video(path: Path, *, frame_count: int = 12, fps: float = 10.0) -> None:
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (8, 6)
    )
    if not writer.isOpened():
        raise RuntimeError("Could not create temporary test video.")
    try:
        for frame_index in range(frame_count):
            frame = np.empty((6, 8, 3), dtype=np.uint8)
            frame[:, :, 0] = np.arange(8, dtype=np.uint8) * 20
            frame[:, :, 1] = np.arange(6, dtype=np.uint8)[:, None] * 30
            frame[:, :, 2] = frame_index * 15
            writer.write(frame)
    finally:
        writer.release()


def _read_frame_mean(path: Path, frame_index: int) -> np.ndarray:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not reopen temporary test video: {path}")
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = capture.read()
        if not ok or frame is None:
            raise RuntimeError(f"Could not read frame {frame_index} from {path}")
        return frame.mean(axis=(0, 1))
    finally:
        capture.release()


class WindowTimeTests(unittest.TestCase):
    def test_covers_trailing_video_without_duplicate_window(self):
        windows = list(iter_window_times(5.0, window_seconds=2.0, stride_seconds=1.5))
        self.assertEqual(windows, [(0.0, 2.0), (1.5, 3.5), (3.0, 5.0)])

    def test_short_video_produces_one_window(self):
        self.assertEqual(
            list(iter_window_times(0.8, window_seconds=1.6, stride_seconds=0.4)),
            [(0.0, 0.8)],
        )

    def test_rejects_stride_larger_than_window(self):
        with self.assertRaises(ValueError):
            list(iter_window_times(5.0, window_seconds=1.0, stride_seconds=2.0))


class ClipSampleFrameIndexTests(unittest.TestCase):
    def test_rounds_exact_half_frame_up(self):
        self.assertEqual(
            clip_sample_frame_indices(
                0.0, 1.0, num_frames=1, fps=1.0, frame_count=2
            ),
            (1,),
        )

    def test_uses_half_up_nearest_frames_for_thirty_fps_window(self):
        self.assertEqual(
            clip_sample_frame_indices(
                0.0, 1.0, num_frames=16, fps=30.0, frame_count=120
            ),
            (1, 3, 5, 7, 8, 10, 12, 14, 16, 18, 20, 22, 23, 25, 27, 29),
        )

    def test_preserves_duplicate_indices_and_clamps_the_tail(self):
        self.assertEqual(
            clip_sample_frame_indices(
                0.0, 0.2, num_frames=4, fps=10.0, frame_count=2
            ),
            (0, 1, 1, 1),
        )

    def test_supports_non_integer_fps_and_non_integer_window(self):
        self.assertEqual(
            clip_sample_frame_indices(
                0.2, 0.7, num_frames=3, fps=29.97, frame_count=100
            ),
            (8, 13, 18),
        )

    def test_rejects_invalid_sampling_parameters(self):
        invalid = [
            (-0.1, 1.0, 1, 30.0, 30),
            (1.0, 1.0, 1, 30.0, 30),
            (0.0, 1.0, 0, 30.0, 30),
            (0.0, 1.0, 1, 0.0, 30),
            (0.0, 1.0, 1, 30.0, 0),
        ]
        for start, end, frames, fps, count in invalid:
            with self.subTest((start, end, frames, fps, count)), self.assertRaises(
                VideoError
            ):
                clip_sample_frame_indices(
                    start,
                    end,
                    num_frames=frames,
                    fps=fps,
                    frame_count=count,
                )


class ProxyVideoTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.source = Path(self.temporary_directory.name) / "source.avi"
        self.destination = Path(self.temporary_directory.name) / "proxy.mp4"
        _write_test_video(self.source)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_writes_reopenable_silent_mp4_with_stable_geometry(self):
        metadata = write_proxy_video(
            self.source,
            self.destination,
            0.2,
            1.2,
            output_fps=10.0,
            max_width=6,
        )
        self.assertEqual(metadata.frame_count, 10)
        self.assertAlmostEqual(metadata.fps, 10.0, places=2)
        self.assertEqual((metadata.width, metadata.height), (6, 4))
        self.assertAlmostEqual(metadata.duration_seconds, 1.0, delta=0.11)
        self.assertTrue(self.destination.is_file())

        first_mean = _read_frame_mean(self.destination, 0)
        last_mean = _read_frame_mean(self.destination, metadata.frame_count - 1)
        np.testing.assert_allclose(first_mean, [70.0, 75.0, 45.0], atol=25.0)
        np.testing.assert_allclose(last_mean, [70.0, 75.0, 165.0], atol=25.0)
        self.assertGreater(last_mean[2], first_mean[2] + 80.0)

    def test_refuses_bounds_errors_and_an_existing_destination(self):
        self.destination.write_bytes(b"keep")
        for start, end in ((-1, 1), (1, 1), (0, 99), (float("nan"), 1)):
            with self.subTest((start, end)), self.assertRaises(VideoError):
                write_proxy_video(self.source, self.destination, start, end)
        self.assertEqual(self.destination.read_bytes(), b"keep")

    def test_rejects_invalid_codec_before_writing(self):
        for codec in ("mp4", "abcde", "mp4\u00e9"):
            with self.subTest(codec=codec), self.assertRaises(VideoError):
                write_proxy_video(self.source, self.destination, 0.0, 0.4, codec=codec)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.destination.parent.glob(".proxy.*.mp4")), [])

    def test_rejects_invalid_proxy_output_fps_before_writing(self):
        for output_fps in (0, -1, float("nan"), float("inf")):
            with self.subTest(output_fps=output_fps), self.assertRaises(VideoError):
                write_proxy_video(
                    self.source,
                    self.destination,
                    0.0,
                    0.4,
                    output_fps=output_fps,
                )
        self.assertFalse(self.destination.exists())

    def test_rejects_invalid_proxy_max_width_before_writing(self):
        for max_width in (1, 0, True, 3.5):
            with self.subTest(max_width=max_width), self.assertRaisesRegex(
                VideoError, "max_width must be an integer of at least 2"
            ):
                write_proxy_video(
                    self.source,
                    self.destination,
                    0.0,
                    0.4,
                    max_width=max_width,
                )
        self.assertFalse(self.destination.exists())

    def test_cleans_up_temporary_file_when_writer_cannot_open(self):
        import spiketrace.video as video_module

        class ClosedWriter:
            def isOpened(self):
                return False

            def release(self):
                pass

        with patch.object(
            video_module._cv2(), "VideoWriter", return_value=ClosedWriter()
        ), self.assertRaises(VideoError):
            write_proxy_video(self.source, self.destination, 0.0, 0.4)

        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.destination.parent.glob(".proxy.*.mp4")), [])

    def test_missing_source_does_not_leave_output_or_temporary_file(self):
        missing_source = Path(self.temporary_directory.name) / "missing.avi"
        with self.assertRaises(VideoError):
            write_proxy_video(missing_source, self.destination, 0.0, 0.4)

        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.destination.parent.glob(".proxy.*.mp4")), [])


class SequentialClipBatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.video_path = Path(self.temporary_directory.name) / "fixture.avi"
        _write_test_video(self.video_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_decodes_ordered_batches_once_with_shared_frames_and_duplicates(self):
        import spiketrace.video as video_module

        self.assertTrue(
            hasattr(video_module, "iter_sequential_video_clip_batches"),
            "sequential clip batch helper is part of the public video API",
        )
        original_capture = cv2.VideoCapture
        with patch.object(video_module._cv2(), "VideoCapture", wraps=original_capture) as capture:
            batches = list(
                video_module.iter_sequential_video_clip_batches(
                    self.video_path,
                    [(0.0, 0.6), (0.2, 0.8), (0.8, 1.0)],
                    num_frames=3,
                    image_size=4,
                    batch_size=2,
                )
            )

        self.assertEqual(capture.call_count, 1)
        self.assertEqual([times for times, _ in batches], [
            [(0.0, 0.6), (0.2, 0.8)],
            [(0.8, 1.0)],
        ])
        self.assertEqual(batches[0][1].shape, (2, 3, 4, 4, 3))
        self.assertEqual(batches[1][1].shape, (1, 3, 4, 4, 3))
        first_clip, second_clip = batches[0][1]
        self.assertTrue(np.allclose(first_clip[1], second_clip[0], atol=8))
        self.assertTrue(np.allclose(first_clip[2], second_clip[1], atol=8))

    def test_preserves_duplicate_sample_slots_and_applies_crop(self):
        import spiketrace.video as video_module

        batches = list(
            video_module.iter_sequential_video_clip_batches(
                self.video_path,
                [(0.0, 0.1)],
                num_frames=4,
                image_size=3,
                batch_size=2,
                crop=(2, 1, 6, 5),
            )
        )

        clip = batches[0][1][0]
        self.assertTrue(np.allclose(clip[0], clip[1], atol=8))
        self.assertTrue(np.allclose(clip[2], clip[3], atol=8))
        self.assertGreater(int(clip[0, 1, 1, 2]), 25)
        self.assertGreater(int(clip[0, 1, 1, 1]), 25)

    def test_random_and_sequential_decoders_return_identical_rgb_clips(self):
        import spiketrace.video as video_module

        expected = sample_video_clip(
            self.video_path,
            0.2,
            0.8,
            num_frames=6,
            image_size=4,
            crop=(1, 1, 7, 5),
        )
        batches = list(
            video_module.iter_sequential_video_clip_batches(
                self.video_path,
                [(0.2, 0.8)],
                num_frames=6,
                image_size=4,
                batch_size=1,
                crop=(1, 1, 7, 5),
            )
        )

        np.testing.assert_array_equal(batches[0][1][0], expected)

    def test_rejects_invalid_batch_parameters_crop_and_window_order(self):
        import spiketrace.video as video_module

        invalid_calls = [
            {"num_frames": 0, "image_size": 4, "batch_size": 1},
            {"num_frames": 1, "image_size": 0, "batch_size": 1},
            {"num_frames": 1, "image_size": 4, "batch_size": 0},
            {
                "num_frames": 1,
                "image_size": 4,
                "batch_size": 1,
                "crop": (0, 0, 9, 6),
            },
        ]
        for arguments in invalid_calls:
            with self.subTest(arguments=arguments), self.assertRaises(VideoError):
                list(
                    video_module.iter_sequential_video_clip_batches(
                        self.video_path, [(0.0, 0.4)], **arguments
                    )
                )

        for windows in [[(-0.1, 0.4)], [(0.4, 0.4)], [(0.4, 0.8), (0.0, 0.4)]]:
            with self.subTest(windows=windows), self.assertRaises(VideoError):
                list(
                    video_module.iter_sequential_video_clip_batches(
                        self.video_path,
                        windows,
                        num_frames=1,
                        image_size=4,
                        batch_size=1,
                    )
                )


if __name__ == "__main__":
    unittest.main()
