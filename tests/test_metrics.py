import unittest

from spiketrace.metrics import classification_metrics


class ClassificationMetricsTests(unittest.TestCase):
    def test_computes_confusion_and_scores(self):
        metrics = classification_metrics(
            targets=[0, 0, 1, 1],
            predictions=[0, 1, 1, 1],
            labels=["background", "serve"],
        )

        self.assertEqual(metrics["accuracy"], 0.75)
        self.assertEqual(metrics["samples"], 4)
        self.assertEqual(metrics["confusion_matrix"]["values"], [[1, 1], [0, 2]])
        self.assertEqual(metrics["per_class"]["serve"]["recall"], 1.0)


if __name__ == "__main__":
    unittest.main()
