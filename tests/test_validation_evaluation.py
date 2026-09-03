import unittest
from pathlib import Path

from spiketrace.domain import VideoMetadata
from spiketrace.validation_contract import ValidationVideoBinding
from spiketrace.validation_inference import ValidationInferenceResult, ValidationPrediction
from spiketrace.validation_rallies import RallySegment
from spiketrace.validation_truth import GroundTruthAction, ValidationTruth, VisibilityInterval
from spiketrace.validation_evaluation import (
    EventMatchResult,
    evaluate_validation,
    expand_one_second_windows,
    match_events,
)


def prediction(pid, label, center, confidence, *, segment="r1", set_index=1, side="near", duration=0.2):
    return ValidationPrediction(pid, segment, set_index, side, center - duration / 2, center + duration / 2, label, confidence, ())


def truth_action(ref, label, center, *, rally="r1", visibility="visible", duration=0.2, projected=None):
    return GroundTruthAction(ref, "match", rally, label, projected or ("background" if label == "free_ball" else label), center - duration / 2, center + duration / 2, visibility, "video", None, "")


def truth(*, actions=(), coverage=(), visibility=()):
    metadata = VideoMetadata(Path("video.avi"), 1.0, 100, 1, 1, 100.0)
    binding = ValidationVideoBinding("match", Path("video.avi"), Path("."), "video.avi", "0" * 64, metadata)
    return ValidationTruth(binding, (), (), tuple(coverage), tuple(actions), tuple(visibility), "truth-v1", True, "locked", None)


def inference(*predictions):
    return ValidationInferenceResult((), tuple(predictions), {}, "checkpoint", "video")


class ValidationEvaluationTests(unittest.TestCase):
    def test_dynamic_matcher_maximizes_cardinality_and_ties_are_deterministic(self):
        result = match_events(
            (prediction("p1", "attack", 10.0, 0.60), prediction("p2", "attack", 10.4, 0.95)),
            (truth_action("t1", "attack", 10.0), truth_action("t2", "attack", 10.4)),
        )
        self.assertEqual(len(result.matches), 2)
        self.assertEqual(match_events((prediction("p", "attack", 10.0, 0.9),), (truth_action("t", "block", 10.0),)).matches, ())
        self.assertEqual(len(match_events((prediction("p", "attack", 11.0, 0.9),), (truth_action("t", "attack", 10.0),)).matches), 1)
        self.assertEqual(len(match_events((prediction("p", "attack", 11.01, 0.9),), (truth_action("t", "attack", 10.0),)).matches), 0)

    def test_windows_project_free_ball_background_and_exclude_visibility_non_rally(self):
        coverage = (
            RallySegment("r1", None, 1, "r1", 0.0, 3.0, "rally", "near", None, 0, 0, "manual", True, True, True),
            RallySegment("nr", None, 1, "", 3.0, 4.0, "non_rally", None, None, 0, 0, "manual", True, True, None),
            RallySegment("bad", None, 1, "", 4.0, 5.0, "unusable", None, None, 0, 0, "manual", True, True, None),
        )
        series = expand_one_second_windows(
            truth(actions=(truth_action("free", "free_ball", 1.4),), coverage=coverage, visibility=(VisibilityInterval("v", "r1", "fully_occluded", 2.0, 3.0, ""),)),
            inference(prediction("p", "attack", 0.5, 0.8, segment="r1"), prediction("nrp", "serve", 3.2, 0.9, segment="nr")),
        )
        self.assertEqual(series.starts, (0.0, 1.0))
        self.assertEqual(series.targets, ("background", "background"))
        self.assertEqual(series.predictions, ("attack", "background"))

    def test_equal_error_prefers_confidence_then_prediction_id(self):
        result = match_events((prediction("z", "attack", 9.0, 0.5), prediction("a", "attack", 11.0, 0.9)), (truth_action("t", "attack", 10.0),), tolerance_seconds=1.0)
        self.assertEqual(result.matches[0].prediction_id, "a")

    def test_visibility_interval_is_scoped_to_its_rally(self):
        coverage = (
            RallySegment("r1", None, 1, "r1", 0.0, 1.0, "rally", "near", None, 0, 0, "manual", True, True, True),
            RallySegment("r2", None, 1, "r2", 1.0, 2.0, "rally", "near", None, 0, 0, "manual", True, True, True),
        )
        series = expand_one_second_windows(truth(coverage=coverage, visibility=(VisibilityInterval("v", "r1", "fully_occluded", 0.0, 2.0, ""),)), inference())
        self.assertEqual(series.starts, (1.0,))

    def test_diagnostic_confusion_retains_label_swaps(self):
        result = match_events((prediction("p", "receive", 1.0, 0.8),), (truth_action("t", "set", 1.0),))
        self.assertEqual(result.matches, ())
        self.assertEqual((result.diagnostic_confusion[0].truth_label, result.diagnostic_confusion[0].predicted_label), ("set", "receive"))

    def test_split_rally_assigns_per_side_by_action_center(self):
        coverage = (
            RallySegment("r1-near-1", None, 1, "r1", 0.0, 1.0, "rally", "near", None, 0, 0, "manual", True, True, False),
            RallySegment("r1-far-2", None, 1, "r1", 1.0, 2.0, "rally", "far", None, 0, 0, "manual", True, True, False),
        )
        report = evaluate_validation(truth(actions=(truth_action("a", "serve", 0.5), truth_action("b", "attack", 1.5)), coverage=coverage), inference(prediction("pa", "serve", 0.5, 0.9, segment="r1-near-1"), prediction("pb", "attack", 1.5, 0.9, segment="r1-far-2")))
        self.assertEqual(report.event_metrics["per_side"]["near"]["matched"], 1)
        self.assertEqual(report.event_metrics["per_side"]["far"]["matched"], 1)

    def test_non_rally_suffix_prediction_is_counted(self):
        coverage = (
            RallySegment("r1", None, 1, "r1", 0.0, 1.0, "rally", "near", None, 0, 0, "manual", True, True, True),
            RallySegment("nr", None, None, "", 2.0, 3.0, "non_rally", None, None, 0, 0, "manual", True, True, None),
        )
        result = evaluate_validation(truth(coverage=coverage), inference(prediction("nrp", "serve", 2.4, 0.9, segment="nr-near-1")))
        self.assertEqual(result.event_metrics["non_rally_prediction_count"], 1)

    def test_report_has_zero_support_classes_and_counts_non_rally_predictions(self):
        coverage = (RallySegment("r1", None, 2, "r1", 0.0, 2.0, "rally", "far", None, 0, 0, "manual", True, True, True),)
        report = evaluate_validation(truth(actions=(truth_action("a", "serve", 0.5),), coverage=coverage), inference(prediction("p", "serve", 0.5, 0.9), prediction("nr", "attack", 3.0, 0.8, segment="non-rally")))
        self.assertIn("event_metrics", report.to_dict())
        self.assertEqual(report.event_metrics["false_positive_count"], 0)
        self.assertEqual(report.event_metrics["support"]["attack"], 0)
        self.assertEqual(report.window_metrics["per_class"]["attack"]["f1"], 0.0)


if __name__ == "__main__":
    unittest.main()
