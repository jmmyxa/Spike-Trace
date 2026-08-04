import importlib.util
import unittest
from pathlib import Path


def load_smoke_dataset_module():
    path = Path(__file__).parents[1] / "tools" / "generate_smoke_dataset.py"
    spec = importlib.util.spec_from_file_location("generate_smoke_dataset", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeCv2:
    def __init__(self):
        self.line_colors: list[tuple[int, int, int]] = []

    def circle(self, *_args, **_kwargs):
        pass

    def rectangle(self, *_args, **_kwargs):
        pass

    def line(self, _frame, _start, _end, color, **_kwargs):
        self.line_colors.append(color)


class SmokeDatasetTests(unittest.TestCase):
    def test_draws_distinct_dig_pattern(self):
        module = load_smoke_dataset_module()
        cv2 = FakeCv2()

        self.assertIn("dig", module.LABELS)
        module._draw_frame(cv2, "dig", 2, 4, 64)

        self.assertIn((60, 180, 240), cv2.line_colors)


if __name__ == "__main__":
    unittest.main()
