from __future__ import annotations

import hashlib
import unittest

from spiketrace._active_learning_review_observations import (
    ActionObservation,
    ActionParticipant,
    ObservationSet,
    VisibilityEvent,
)
from spiketrace._active_learning_review_projection import (
    TrainingDecision,
    build_protected_intervals,
    build_training_projection,
    derive_training_decision,
    project_training_windows,
    select_hard_negatives,
)


class TrainingDecisionTests(unittest.TestCase):
    def test_derives_fail_closed_training_decisions(self):
        cases = (
            (_action("serve"), TrainingDecision("eligible", "serve", "direct_visual")),
            (_action("block", visibility="direct_partial"), TrainingDecision("eligible", "block", "direct_visual")),
            (_action("free_ball"), TrainingDecision("eligible_as_background", "background", "free_ball_projects_to_background")),
            (_action("background", background_scope="timed_interval"), TrainingDecision("eligible", "background", "direct_visual")),
            (_action("serve", visibility="fully_occluded"), TrainingDecision("excluded", None, "insufficient_visual_evidence")),
            (_action("serve", visibility="off_camera"), TrainingDecision("excluded", None, "insufficient_visual_evidence")),
            (_action("serve", visibility="unresolved"), TrainingDecision("excluded", None, "insufficient_visual_evidence")),
            (_action("serve", evidence_basis="referee_signal"), TrainingDecision("excluded", None, "insufficient_visual_evidence")),
            (_action("serve", evidence_basis="scoreboard"), TrainingDecision("excluded", None, "insufficient_visual_evidence")),
            (_action("serve", evidence_basis="sequence_context"), TrainingDecision("excluded", None, "insufficient_visual_evidence")),
            (_action("serve", evidence_basis="mixed"), TrainingDecision("excluded", None, "insufficient_visual_evidence")),
        )

        self.assertEqual(tuple(derive_training_decision(action) for action, _ in cases), tuple(expected for _, expected in cases))

    def test_untimed_background_sentinel_has_no_human_window(self):
        observations = _observations(actions=(_sentinel("clip-001", "far"),))

        self.assertEqual(project_training_windows(observations, _selection("clip-001")), ())


class TrainingProtectionTests(unittest.TestCase):
    def test_protects_all_timed_actions_and_visibility_ranges(self):
        observations = _observations(
            actions=(
                _action("serve", action_ref="clip-001/visible", start=101.0, end=102.0),
                _action("background", action_ref="clip-001/timed-background", start=103.0, end=104.0, background_scope="timed_interval"),
                _action("free_ball", action_ref="clip-001/free-ball", start=105.0, end=106.0),
                _action("attack", action_ref="clip-001/excluded", start=107.0, end=108.0, visibility="fully_occluded"),
            ),
            occlusions=(_visibility("occlusion-001", "clip-001", "far", 109.0, 110.0),),
            off_camera=(_visibility("off-camera-001", "clip-001", "near", 111.0, 112.0),),
        )

        protected = build_protected_intervals(
            observations, _selection("clip-001", start=100.0, end=120.0)
        )

        self.assertEqual(
            tuple((item.source_ref, item.team_side, item.start_seconds, item.end_seconds) for item in protected),
            (
                ("clip-001/visible", "far", 101.0, 102.0),
                ("clip-001/timed-background", "far", 103.0, 104.0),
                ("clip-001/free-ball", "far", 105.0, 106.0),
                ("clip-001/excluded", "far", 107.0, 108.0),
                ("occlusion-001", "far", 109.0, 110.0),
                ("off-camera-001", "near", 111.0, 112.0),
            ),
        )

    def test_clip_bounds_visibility_protects_entire_selected_clip(self):
        observations = _observations(
            occlusions=(_visibility("occlusion-001", "clip-001", "far", 103.0, 104.0, scope="clip_bounds"),),
        )

        protected = build_protected_intervals(observations, _selection("clip-001", start=100.0, end=120.0))

        self.assertEqual(
            tuple((item.start_seconds, item.end_seconds, item.reason) for item in protected),
            ((100.0, 120.0, "occlusion_clip_bounds"),),
        )


class TrainingProjectionTests(unittest.TestCase):
    def test_projects_one_window_per_eligible_action_and_only_one_confirmed_player(self):
        action = _action("block", action_ref="clip-001/block", start=101.0, end=102.0)
        observations = _observations(
            actions=(action,),
            participants=(
                _participant(action.action_ref, "10", "confirmed"),
                _participant(action.action_ref, None, "candidate"),
            ),
        )

        windows = project_training_windows(observations, _selection("clip-001"))

        self.assertEqual(len(windows), 1)
        self.assertEqual(
            windows[0],
            windows[0].__class__(
                source_ref="clip-001/block", clip_id="clip-001", start_seconds=101.0,
                end_seconds=102.0, training_label="block", review_label="block",
                team_side="far", crop=(0, 0, 100, 50), player_number="10",
                generated=False, window_index=None, source_top1_action=None,
                source_top1_confidence=None, note="",
            ),
        )
        multi_confirmed = _observations(
            actions=(action,),
            participants=(_participant(action.action_ref, "10", "confirmed"), _participant(action.action_ref, "11", "confirmed")),
        )
        self.assertIsNone(project_training_windows(multi_confirmed, _selection("clip-001"))[0].player_number)

    def test_only_an_exact_single_sentinel_clip_side_can_donate(self):
        donor = _sentinel("clip-donor", "far")
        disqualified = _sentinel("clip-disqualified", "far")
        observations = _observations(
            actions=(donor, disqualified, _action("serve", action_ref="clip-disqualified/near-action", clip_id="clip-disqualified", side="near", start=21.0, end=22.0)),
        )
        selection = _selection("clip-donor", start=0.0, end=10.0, extra_clips=(("clip-disqualified", 20.0, 30.0),))
        protected = build_protected_intervals(observations, selection)

        selected = select_hard_negatives(selection, _merged({"far": ((_window(3, 2.0, 3.0, "attack", 0.8),),), "near": ((_window(4, 21.0, 22.0, "attack", 0.9),),)}), observations, protected, 0.0, 4, 7)

        self.assertEqual(tuple((window.clip_id, window.team_side, window.window_index) for window in selected), (("clip-donor", "far", 3),))

    def test_hard_negatives_apply_same_side_guard_and_allow_other_side_same_time(self):
        sentinel_far = _sentinel("clip-far", "far")
        sentinel_near = _sentinel("clip-near", "near")
        protected_action = _action("serve", action_ref="clip-protected/action", clip_id="clip-protected", side="far", start=4.0, end=5.0)
        observations = _observations(actions=(sentinel_far, sentinel_near, protected_action))
        selection = _selection("clip-far", start=0.0, end=10.0, extra_clips=(("clip-near", 0.0, 10.0),))
        protected = build_protected_intervals(observations, selection)
        merged = _merged({
            "far": ((_window(0, 3.5, 4.0, "attack", 0.9), _window(1, 2.9, 3.5, "attack", 0.8), _window(2, 7.0, 8.0, "attack", 0.7)),),
            "near": ((_window(3, 4.0, 5.0, "block", 0.95),),),
        })

        selected = select_hard_negatives(selection, merged, observations, protected, 0.5, 4, 1)

        self.assertEqual(tuple((item.clip_id, item.team_side, item.window_index) for item in selected), (("clip-near", "near", 3), ("clip-far", "far", 1), ("clip-far", "far", 2)))

    def test_hard_negative_ranking_is_non_background_confidence_then_stable_sha(self):
        observations = _observations(actions=(_sentinel("clip-001", "far"),))
        selection = _selection("clip-001", start=0.0, end=10.0)
        protected = build_protected_intervals(observations, selection)
        first, second = sorted((5, 7), key=lambda index: hashlib.sha256(f"4/clip-001/far/{index}".encode()).hexdigest())
        merged = _merged({"far": ((_window(9, 1.0, 2.0, "background", 1.0), _window(second, 3.0, 4.0, "attack", 0.8), _window(first, 5.0, 6.0, "attack", 0.8), _window(3, 7.0, 8.0, "block", 0.9)),)})

        selected = select_hard_negatives(selection, merged, observations, protected, 0.0, 4, 4)

        self.assertEqual(tuple(item.window_index for item in selected), (3, first, second, 9))
        self.assertEqual(selected[0].source_ref, "clip-001/hard-negative-far-3")
        self.assertEqual(selected[0].source_top1_action, "block")
        self.assertEqual(selected[0].source_top1_confidence, 0.9)

    def test_full_clip_occlusion_and_zero_positive_cap_eliminate_generated_negatives(self):
        sentinel = _sentinel("clip-001", "far")
        occluded = _observations(
            actions=(sentinel,),
            occlusions=(_visibility("occlusion-001", "clip-001", "far", 1.0, 2.0, scope="clip_bounds"),),
        )
        selection = _selection("clip-001", start=0.0, end=10.0)
        merged = _merged({"far": ((_window(0, 3.0, 4.0, "attack", 0.9),),)})

        projection = build_training_projection(occluded, selection, merged, 0.0, None, 0)

        self.assertEqual(projection.positive_training_count, 0)
        self.assertEqual(projection.requested_background_cap, 0)
        self.assertEqual(projection.effective_background_cap, 0)
        self.assertEqual(projection.generated_background_windows, ())


def _selection(clip_id: str, *, start: float = 100.0, end: float = 110.0, extra_clips: tuple[tuple[str, float, float], ...] = ()) -> dict[str, object]:
    clips = ((clip_id, start, end),) + extra_clips
    return {
        "source": {"merged_json": "outputs/merged.json", "merged_json_sha256": "a" * 64},
        "video": {"crops": {"far": [0, 0, 100, 50], "near": [0, 50, 100, 100]}},
        "settings": {"seed": 0},
        "clips": [{"clip_id": item_id, "start_seconds": item_start, "end_seconds": item_end} for item_id, item_start, item_end in clips],
    }


def _merged(runs: dict[str, tuple[tuple[dict[str, object], ...], ...]]) -> dict[str, object]:
    return {"input_runs": {side: {"windows": [window for group in groups for window in group]} for side, groups in runs.items()}}


def _window(index: int, start: float, end: float, action: str, confidence: float) -> dict[str, object]:
    return {"window_index": index, "start_seconds": start, "end_seconds": end, "action": action, "confidence": confidence}


def _observations(*, actions: tuple[ActionObservation, ...] = (), occlusions: tuple[VisibilityEvent, ...] = (), off_camera: tuple[VisibilityEvent, ...] = (), participants: tuple[ActionParticipant, ...] = ()) -> ObservationSet:
    return ObservationSet("result-test", actions, (), (), occlusions, off_camera, participants)


def _action(label: str, *, action_ref: str = "clip-001/action-001", clip_id: str = "clip-001", side: str = "far", start: float = 101.0, end: float = 102.0, visibility: str = "direct_clear", evidence_basis: str = "direct_video", background_scope: str | None = None) -> ActionObservation:
    return ActionObservation(action_ref, clip_id, 1, 1, {}, {}, label, 1, 2, start, end, side, visibility, evidence_basis, "timed", background_scope, False, "", None, ())


def _sentinel(clip_id: str, side: str) -> ActionObservation:
    return ActionObservation(f"{clip_id}/sentinel-{side}", clip_id, 1, 1, {}, {}, "background", None, None, None, None, side, "direct_clear", "direct_video", None, "clip_sentinel", False, "", None, ())


def _participant(action_ref: str, number: str | None, status: str) -> ActionParticipant:
    return ActionParticipant(action_ref, None, None, number, "primary_actor", "touched", status, 1.0 if status == "confirmed" else None, ())


def _visibility(ref: str, clip_id: str, side: str, start: float, end: float, *, scope: str = "timed") -> VisibilityEvent:
    return VisibilityEvent(ref, "occlusion", side, start, end, end - start, scope, (), (), (), "")


if __name__ == "__main__":
    unittest.main()
