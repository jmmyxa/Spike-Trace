import unittest

from spiketrace.video import iter_window_times


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


if __name__ == "__main__":
    unittest.main()
