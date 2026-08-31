from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from spiketrace._active_learning_review_contract import (
    assert_review_snapshots_stable,
    derive_result_set_id,
    load_review_selection_bytes,
    load_review_sources_v2,
    snapshot_review_sources_v2,
)

ROOT = Path(__file__).resolve().parents[1]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, separators=(",", ":")) + "\n").encode("utf-8")


class ReviewContractTests(unittest.TestCase):
    def test_derives_result_identifier_from_nul_delimited_source_hashes(self):
        actual = derive_result_set_id(
            "batch-1",
            "round-01",
            "a" * 64,
            "b" * 64,
            "c" * 64,
        )
        expected = "batch-1/result-549fd199e806acab"
        self.assertEqual(actual, expected)

    def test_derives_result_identifier_from_unicode_batch_id(self):
        result = derive_result_set_id("批次", "round-01", "a" * 64, "b" * 64, "c" * 64)
        self.assertRegex(result, r"^批次/result-[0-9a-f]{16}$")

    def test_frozen_sources_validate_v2_bindings_and_detect_live_mutation(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_path = directory / "merged.json"
            selection_path = directory / "selection.json"
            workbook_path = directory / "review.xlsx"
            overrides_path = directory / "overrides.json"
            review_path = directory / "review-v2.json"

            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            merged_path.write_bytes(merged_bytes)
            workbook_bytes = b"workbook snapshot"
            workbook_path.write_bytes(workbook_bytes)
            overrides_bytes = b'{"bound":true}\n'
            overrides_path.write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            selection_path.write_bytes(selection_bytes)
            review = _valid_review(
                selection, selection_bytes, workbook_bytes, overrides_bytes
            )
            review_path.write_bytes(_json_bytes(review))

            snapshots = snapshot_review_sources_v2(review_path, selection_path, ROOT)
            selected = load_review_selection_bytes(
                snapshots.selection.raw,
                merged_bytes=snapshots.merged_candidates.raw,
                merged_repo_path=snapshots.merged_candidates.repo_path,
                repo_root=ROOT,
                require_video=False,
            )
            validated = load_review_sources_v2(snapshots, selected)

            self.assertEqual(validated.result_set_id, review["result_set_id"])
            self.assertEqual(validated.selection_binding.path, _repo(selection_path))
            self.assertEqual(validated.workbook_binding.sha256, _sha256(workbook_bytes))
            self.assertEqual(validated.merged_candidates_binding.path, _repo(merged_path))
            self.assertEqual(validated.video_binding.crops["far"], (0, 0, 1920, 645))
            self.assertEqual(
                validated.action_observations[0]["start_seconds"],
                selected["clips"][0]["start_seconds"] + 1,
            )
            self.assertEqual(
                validated.outcome_observations[0]["related_action_refs"],
                [f"{selected['clips'][0]['clip_id']}/action-001"],
            )

            merged_path.write_bytes(b"{\"changed\":true}\n")
            self.assertEqual(validated.merged_candidates["format_version"], 2)
            with self.assertRaisesRegex(ValueError, "changed"):
                assert_review_snapshots_stable(snapshots)

    def test_rejects_unknown_enum_and_dangling_observation_reference(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            (directory / "merged.json").write_bytes(merged_bytes)
            workbook_bytes = b"workbook"
            (directory / "review.xlsx").write_bytes(workbook_bytes)
            overrides_bytes = b"{}\n"
            (directory / "overrides.json").write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            (directory / "selection.json").write_bytes(selection_bytes)
            review = _valid_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
            review["action_observations"][0]["evidence_basis"] = "invented"
            (directory / "review-v2.json").write_bytes(_json_bytes(review))
            snapshots = snapshot_review_sources_v2(
                directory / "review-v2.json", directory / "selection.json", ROOT
            )
            selected = load_review_selection_bytes(
                snapshots.selection.raw,
                merged_bytes=snapshots.merged_candidates.raw,
                merged_repo_path=snapshots.merged_candidates.repo_path,
                repo_root=ROOT,
                require_video=False,
            )
            with self.assertRaisesRegex(ValueError, "evidence_basis"):
                load_review_sources_v2(snapshots, selected)

    def test_rejects_review_set_key_outside_batch_round_form(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            (directory / "merged.json").write_bytes(merged_bytes)
            workbook_bytes = b"workbook"
            (directory / "review.xlsx").write_bytes(workbook_bytes)
            overrides_bytes = b"{}\n"
            (directory / "overrides.json").write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            (directory / "selection.json").write_bytes(selection_bytes)
            review = _valid_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
            review["review_set_key"] = "not-a-review-set"
            (directory / "review-v2.json").write_bytes(_json_bytes(review))
            snapshots = snapshot_review_sources_v2(
                directory / "review-v2.json", directory / "selection.json", ROOT
            )
            selected = load_review_selection_bytes(
                snapshots.selection.raw,
                merged_bytes=snapshots.merged_candidates.raw,
                merged_repo_path=snapshots.merged_candidates.repo_path,
                repo_root=ROOT,
                require_video=False,
            )
            with self.assertRaisesRegex(ValueError, "review_set_key"):
                load_review_sources_v2(snapshots, selected)

    def test_rejects_review_set_key_for_a_different_round(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            (directory / "merged.json").write_bytes(merged_bytes)
            workbook_bytes, overrides_bytes = b"workbook", b"{}\n"
            (directory / "review.xlsx").write_bytes(workbook_bytes)
            (directory / "overrides.json").write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            (directory / "selection.json").write_bytes(selection_bytes)
            review = _valid_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
            review["review_set_key"] = "rangitoto/round-02"
            (directory / "review-v2.json").write_bytes(_json_bytes(review))
            snapshots = snapshot_review_sources_v2(directory / "review-v2.json", directory / "selection.json", ROOT)
            selected = load_review_selection_bytes(snapshots.selection.raw, merged_bytes=snapshots.merged_candidates.raw, merged_repo_path=snapshots.merged_candidates.repo_path, repo_root=ROOT, require_video=False)

            with self.assertRaisesRegex(ValueError, "review_set_key"):
                load_review_sources_v2(snapshots, selected)

    def test_accepts_node_supplemental_timing_and_result_wide_visibility_refs(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            (directory / "merged.json").write_bytes(merged_bytes)
            workbook_bytes, overrides_bytes = b"workbook", b"{}\n"
            (directory / "review.xlsx").write_bytes(workbook_bytes)
            (directory / "overrides.json").write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            (directory / "selection.json").write_bytes(selection_bytes)
            review = _node_producer_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
            (directory / "review-v2.json").write_bytes(_json_bytes(review))
            snapshots = snapshot_review_sources_v2(directory / "review-v2.json", directory / "selection.json", ROOT)
            selected = load_review_selection_bytes(snapshots.selection.raw, merged_bytes=snapshots.merged_candidates.raw, merged_repo_path=snapshots.merged_candidates.repo_path, repo_root=ROOT, require_video=False)
            validated = load_review_sources_v2(snapshots, selected)
            self.assertIsNone(validated.action_observations[2]["raw_values"])
            self.assertEqual(validated.action_observations[2]["source_reason"], "missed action")
            self.assertEqual(validated.normalization_audit[0]["kind"], "side_inheritance")

    def test_rejects_supplemental_action_with_source_only_state(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            (directory / "merged.json").write_bytes(merged_bytes)
            workbook_bytes, overrides_bytes = b"workbook", b"{}\n"
            (directory / "review.xlsx").write_bytes(workbook_bytes)
            (directory / "overrides.json").write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            (directory / "selection.json").write_bytes(selection_bytes)
            review = _valid_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
            supplemental = _supplemental(selection["clips"][0], 1, "timed")
            supplemental["side_inherited"] = True
            review["action_observations"].append(supplemental)
            (directory / "review-v2.json").write_bytes(_json_bytes(review))
            snapshots = snapshot_review_sources_v2(directory / "review-v2.json", directory / "selection.json", ROOT)
            selected = load_review_selection_bytes(snapshots.selection.raw, merged_bytes=snapshots.merged_candidates.raw, merged_repo_path=snapshots.merged_candidates.repo_path, repo_root=ROOT, require_video=False)

            with self.assertRaisesRegex(ValueError, "supplemental"):
                load_review_sources_v2(snapshots, selected)

    def test_rejects_non_background_source_with_background_scope(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            (directory / "merged.json").write_bytes(merged_bytes)
            workbook_bytes, overrides_bytes = b"workbook", b"{}\n"
            (directory / "review.xlsx").write_bytes(workbook_bytes)
            (directory / "overrides.json").write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            (directory / "selection.json").write_bytes(selection_bytes)
            review = _valid_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
            review["source_review_rows"][0]["background_scope"] = "timed_interval"
            review["action_observations"][0]["background_scope"] = "timed_interval"
            (directory / "review-v2.json").write_bytes(_json_bytes(review))
            snapshots = snapshot_review_sources_v2(directory / "review-v2.json", directory / "selection.json", ROOT)
            selected = load_review_selection_bytes(snapshots.selection.raw, merged_bytes=snapshots.merged_candidates.raw, merged_repo_path=snapshots.merged_candidates.repo_path, repo_root=ROOT, require_video=False)

            with self.assertRaisesRegex(ValueError, "background_scope"):
                load_review_sources_v2(snapshots, selected)

    def test_preserves_source_scope_when_effective_override_changes_label(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            (directory / "merged.json").write_bytes(merged_bytes)
            workbook_bytes, overrides_bytes = b"workbook", b"{}\n"
            (directory / "review.xlsx").write_bytes(workbook_bytes)
            (directory / "overrides.json").write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            (directory / "selection.json").write_bytes(selection_bytes)
            review = _valid_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
            source = review["source_review_rows"][0]
            source["raw_values"].update({"review_label": "background", "relative_start_seconds": 1, "relative_end_seconds": 2, "note": None})
            source["normalized_values"].update({"review_label": "background", "relative_start_seconds": 1, "relative_end_seconds": 2, "note": None})
            source["background_scope"] = "timed_interval"
            action = review["action_observations"][0]
            action.update({"raw_values": deepcopy(source["raw_values"]), "normalized_values": deepcopy(source["normalized_values"]), "review_label": "attack", "background_scope": None, "source_reason": "evidence override"})

            def load(value: dict[str, object]):
                (directory / "review-v2.json").write_bytes(_json_bytes(value))
                snapshots = snapshot_review_sources_v2(directory / "review-v2.json", directory / "selection.json", ROOT)
                selected = load_review_selection_bytes(snapshots.selection.raw, merged_bytes=merged_bytes, merged_repo_path=snapshots.merged_candidates.repo_path, repo_root=ROOT, require_video=False)
                return load_review_sources_v2(snapshots, selected)

            validated = load(review)
            self.assertEqual(validated.source_review_rows[0]["background_scope"], "timed_interval")
            self.assertIsNone(validated.source_review_rows[0]["normalized_values"]["note"])
            self.assertEqual(validated.action_observations[0]["review_label"], "attack")
            self.assertEqual(validated.action_observations[0]["note"], "")
            self.assertIsNone(validated.action_observations[0]["background_scope"])
            for inherited, raw_side in (
                (False, None),
                (True, "far"),
            ):
                candidate = deepcopy(review)
                candidate["source_review_rows"][0]["side_inherited"] = inherited
                candidate["action_observations"][0]["side_inherited"] = inherited
                candidate["source_review_rows"][0]["raw_values"]["team_side"] = raw_side
                candidate["action_observations"][0]["raw_values"]["team_side"] = raw_side
                with self.assertRaisesRegex(ValueError, "side|source"):
                    load(candidate)
            inherited_candidate = deepcopy(review)
            inherited_candidate["source_review_rows"][0]["side_inherited"] = True
            inherited_candidate["action_observations"][0]["side_inherited"] = True
            inherited_candidate["source_review_rows"][0]["raw_values"]["team_side"] = None
            inherited_candidate["action_observations"][0]["raw_values"]["team_side"] = None
            inherited_candidate["normalization_audit"] = [{
                "kind": "side_inheritance", "clip_id": selection["clips"][0]["clip_id"],
                "action_ref": review["action_observations"][0]["action_ref"], "source_row": 4,
                "raw_value": None, "normalized_value": "far", "reason": "inherit side",
            }]
            inherited_validated = load(inherited_candidate)
            self.assertTrue(inherited_validated.source_review_rows[0]["side_inherited"])
            self.assertIsNone(inherited_validated.source_review_rows[0]["raw_values"]["team_side"])
            for mutation, message in (
                (lambda value: value["action_observations"][0].update({"background_scope": "timed_interval"}), "effective scope"),
                (lambda value: value["action_observations"][0].update({"relative_start_seconds": 2}), "moved times"),
                (lambda value: value["action_observations"][0]["raw_values"].update({"review_label": "attack"}), "changed source data"),
            ):
                candidate = deepcopy(review)
                mutation(candidate)
                with self.assertRaisesRegex(ValueError, "source|background_scope|relative|action"):
                    load(candidate)

    def test_rejects_timed_source_action_with_clip_bounds_scope(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            (directory / "merged.json").write_bytes(merged_bytes)
            workbook_bytes, overrides_bytes = b"workbook", b"{}\n"
            (directory / "review.xlsx").write_bytes(workbook_bytes)
            (directory / "overrides.json").write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            (directory / "selection.json").write_bytes(selection_bytes)
            review = _valid_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
            review["action_observations"][0]["interval_scope"] = "clip_bounds"
            (directory / "review-v2.json").write_bytes(_json_bytes(review))
            snapshots = snapshot_review_sources_v2(directory / "review-v2.json", directory / "selection.json", ROOT)
            selected = load_review_selection_bytes(snapshots.selection.raw, merged_bytes=snapshots.merged_candidates.raw, merged_repo_path=snapshots.merged_candidates.repo_path, repo_root=ROOT, require_video=False)

            with self.assertRaisesRegex(ValueError, "interval_scope"):
                load_review_sources_v2(snapshots, selected)

    def test_timed_visibility_must_stay_within_selected_clip(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            (directory / "merged.json").write_bytes(merged_bytes)
            workbook_bytes, overrides_bytes = b"workbook", b"{}\n"
            (directory / "review.xlsx").write_bytes(workbook_bytes)
            (directory / "overrides.json").write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            (directory / "selection.json").write_bytes(selection_bytes)
            clip = selection["clips"][0]
            for label, start, end, valid in (
                ("at bounds", clip["start_seconds"], clip["end_seconds"], True),
                ("below start", clip["start_seconds"] - 1, clip["start_seconds"] + 1, False),
                ("past end", clip["end_seconds"] - 1, clip["end_seconds"] + 1, False),
            ):
                with self.subTest(label=label):
                    review = _valid_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
                    observation = _visibility(review["result_set_id"], 1, "occlusion", clip, review["action_observations"][0]["action_ref"])
                    observation.update({"interval_scope": "timed", "start_seconds": start, "end_seconds": end})
                    review["visibility_observations"] = [observation]
                    (directory / "review-v2.json").write_bytes(_json_bytes(review))
                    snapshots = snapshot_review_sources_v2(directory / "review-v2.json", directory / "selection.json", ROOT)
                    selected = load_review_selection_bytes(snapshots.selection.raw, merged_bytes=snapshots.merged_candidates.raw, merged_repo_path=snapshots.merged_candidates.repo_path, repo_root=ROOT, require_video=False)
                    if valid:
                        load_review_sources_v2(snapshots, selected)
                    else:
                        with self.assertRaisesRegex(ValueError, "timed visibility"):
                            load_review_sources_v2(snapshots, selected)

    def test_validates_participant_assignment_matrix(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            (directory / "merged.json").write_bytes(merged_bytes)
            workbook_bytes, overrides_bytes = b"workbook", b"{}\n"
            (directory / "review.xlsx").write_bytes(workbook_bytes)
            (directory / "overrides.json").write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            (directory / "selection.json").write_bytes(selection_bytes)
            valid = _participant_matrix(selection["clips"][0]["clip_id"])
            for label, participants, valid_case in (
                ("valid assignments", valid, True),
                ("confirmed lacks identity", [{**valid[0], "identity_ref": None}], False),
                ("candidate lacks handle", [{**valid[1], "track_id": None}], False),
                ("unresolved claims identity", [{**valid[2], "identity_ref": "player-3"}], False),
                ("duplicate track", [valid[0], {**valid[0], "participation": "support"}], False),
            ):
                with self.subTest(label=label):
                    review = _valid_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
                    review["action_participants"] = participants
                    (directory / "review-v2.json").write_bytes(_json_bytes(review))
                    snapshots = snapshot_review_sources_v2(directory / "review-v2.json", directory / "selection.json", ROOT)
                    selected = load_review_selection_bytes(snapshots.selection.raw, merged_bytes=snapshots.merged_candidates.raw, merged_repo_path=snapshots.merged_candidates.repo_path, repo_root=ROOT, require_video=False)
                    if valid_case:
                        validated = load_review_sources_v2(snapshots, selected)
                        self.assertEqual(len(validated.action_participants), 3)
                    else:
                        with self.assertRaisesRegex(ValueError, "assignment|track"):
                            load_review_sources_v2(snapshots, selected)

    def test_rejects_unlinked_or_malformed_source_repairs(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            (directory / "merged.json").write_bytes(merged_bytes)
            workbook_bytes, overrides_bytes = b"workbook", b"{}\n"
            (directory / "review.xlsx").write_bytes(workbook_bytes)
            (directory / "overrides.json").write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            (directory / "selection.json").write_bytes(selection_bytes)
            review = _valid_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
            repair = {"clip_id": selection["clips"][0]["clip_id"], "source_action_slot": 1, "sheet": "人工动作", "cell": "A4", "field": "clip_id", "original_value": None, "normalized_value": selection["clips"][0]["clip_id"], "reason": "restore read-only ID"}
            review["source_repairs"] = [repair]
            (directory / "review-v2.json").write_bytes(_json_bytes(review))
            snapshots = snapshot_review_sources_v2(directory / "review-v2.json", directory / "selection.json", ROOT)
            selected = load_review_selection_bytes(snapshots.selection.raw, merged_bytes=snapshots.merged_candidates.raw, merged_repo_path=snapshots.merged_candidates.repo_path, repo_root=ROOT, require_video=False)
            with self.assertRaisesRegex(ValueError, "source repair"):
                load_review_sources_v2(snapshots, selected)

    def test_snapshot_stability_rejects_each_frozen_source_mutation(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            (directory / "merged.json").write_bytes(merged_bytes)
            workbook_bytes, overrides_bytes = b"workbook", b"{}\n"
            (directory / "review.xlsx").write_bytes(workbook_bytes)
            (directory / "overrides.json").write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            (directory / "selection.json").write_bytes(selection_bytes)
            review = _valid_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
            (directory / "review-v2.json").write_bytes(_json_bytes(review))
            for attribute in ("selection", "review_input", "workbook", "evidence_overrides", "merged_candidates"):
                with self.subTest(attribute=attribute):
                    snapshots = snapshot_review_sources_v2(directory / "review-v2.json", directory / "selection.json", ROOT)
                    getattr(snapshots, attribute).absolute_path.write_bytes(b"changed")
                    with self.assertRaisesRegex(ValueError, "changed"):
                        assert_review_snapshots_stable(snapshots)
                    getattr(snapshots, attribute).absolute_path.write_bytes(getattr(snapshots, attribute).raw)

    def test_rejects_actual_dangling_outcome_reference(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            (directory / "merged.json").write_bytes(merged_bytes)
            workbook_bytes, overrides_bytes = b"workbook", b"{}\n"
            (directory / "review.xlsx").write_bytes(workbook_bytes)
            (directory / "overrides.json").write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            (directory / "selection.json").write_bytes(selection_bytes)
            review = _valid_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
            review["outcome_observations"][0]["related_action_refs"] = ["missing/action-001"]
            (directory / "review-v2.json").write_bytes(_json_bytes(review))
            snapshots = snapshot_review_sources_v2(directory / "review-v2.json", directory / "selection.json", ROOT)
            selected = load_review_selection_bytes(snapshots.selection.raw, merged_bytes=snapshots.merged_candidates.raw, merged_repo_path=snapshots.merged_candidates.repo_path, repo_root=ROOT, require_video=False)
            with self.assertRaisesRegex(ValueError, "dangling"):
                load_review_sources_v2(snapshots, selected)

    def test_accepts_repair_lineage_when_top_level_repairs_are_reordered(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            (directory / "merged.json").write_bytes(merged_bytes)
            workbook_bytes, overrides_bytes = b"workbook", b"{}\n"
            (directory / "review.xlsx").write_bytes(workbook_bytes)
            (directory / "overrides.json").write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            (directory / "selection.json").write_bytes(selection_bytes)
            review = _valid_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
            first = _repair(selection["clips"][0], 1)
            second = _repair(selection["clips"][0], 2)
            extra = deepcopy(review["action_observations"][0])
            extra.update({
                "action_ref": f"{selection['clips'][0]['clip_id']}/action-002",
                "source_action_slot": 2,
                "source_row": 5,
                "relative_start_seconds": 3,
                "relative_end_seconds": 4,
                "start_seconds": selection["clips"][0]["start_seconds"] + 3,
                "end_seconds": selection["clips"][0]["start_seconds"] + 4,
                "source_repairs": [second],
            })
            review["action_observations"][0]["source_repairs"] = [first]
            review["action_observations"][0]["raw_values"]["clip_id"] = None
            extra["raw_values"].update({"clip_id": None, "relative_start_seconds": 3, "relative_end_seconds": 4})
            extra["normalized_values"].update({"relative_start_seconds": 3, "relative_end_seconds": 4})
            review["source_review_rows"] = [
                {key: action[key] for key in ("action_ref", "clip_id", "source_action_slot", "source_row", "raw_values", "normalized_values", "background_scope", "side_inherited", "source_repairs")}
                for action in (review["action_observations"][0], extra)
            ]
            review["action_observations"].append(extra)
            review["source_repairs"] = [second, first]
            review["normalization_audit"] = [
                _repair_audit(first, review["action_observations"][0]),
                _repair_audit(second, extra),
            ]
            (directory / "review-v2.json").write_bytes(_json_bytes(review))
            snapshots = snapshot_review_sources_v2(directory / "review-v2.json", directory / "selection.json", ROOT)
            selected = load_review_selection_bytes(snapshots.selection.raw, merged_bytes=snapshots.merged_candidates.raw, merged_repo_path=snapshots.merged_candidates.repo_path, repo_root=ROOT, require_video=False)

            validated = load_review_sources_v2(snapshots, selected)

            self.assertEqual(validated.source_repairs, (second, first))

    def test_rejects_normalization_audit_that_does_not_match_its_repair(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            (directory / "merged.json").write_bytes(merged_bytes)
            workbook_bytes, overrides_bytes = b"workbook", b"{}\n"
            (directory / "review.xlsx").write_bytes(workbook_bytes)
            (directory / "overrides.json").write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            (directory / "selection.json").write_bytes(selection_bytes)
            review = _valid_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
            repair = _repair(selection["clips"][0], 1)
            review["source_repairs"] = [repair]
            review["source_review_rows"][0]["source_repairs"] = [repair]
            review["action_observations"][0]["source_repairs"] = [repair]
            review["source_review_rows"][0]["raw_values"]["clip_id"] = None
            review["action_observations"][0]["raw_values"]["clip_id"] = None
            audit = _repair_audit(repair, review["action_observations"][0])
            audit["normalized_value"] = "wrong-clip"
            review["normalization_audit"] = [audit]
            (directory / "review-v2.json").write_bytes(_json_bytes(review))
            snapshots = snapshot_review_sources_v2(directory / "review-v2.json", directory / "selection.json", ROOT)
            selected = load_review_selection_bytes(snapshots.selection.raw, merged_bytes=snapshots.merged_candidates.raw, merged_repo_path=snapshots.merged_candidates.repo_path, repo_root=ROOT, require_video=False)

            with self.assertRaisesRegex(ValueError, "normalization audit"):
                load_review_sources_v2(snapshots, selected)

    def test_rejects_incomplete_source_value_payloads(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            (directory / "merged.json").write_bytes(merged_bytes)
            workbook_bytes, overrides_bytes = b"workbook", b"{}\n"
            (directory / "review.xlsx").write_bytes(workbook_bytes)
            (directory / "overrides.json").write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            (directory / "selection.json").write_bytes(selection_bytes)
            review = _valid_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
            review["source_review_rows"][0]["raw_values"] = {"review_label": "serve"}
            review["action_observations"][0]["raw_values"] = {"review_label": "serve"}
            (directory / "review-v2.json").write_bytes(_json_bytes(review))
            snapshots = snapshot_review_sources_v2(directory / "review-v2.json", directory / "selection.json", ROOT)
            selected = load_review_selection_bytes(snapshots.selection.raw, merged_bytes=snapshots.merged_candidates.raw, merged_repo_path=snapshots.merged_candidates.repo_path, repo_root=ROOT, require_video=False)

            with self.assertRaisesRegex(ValueError, "source values"):
                load_review_sources_v2(snapshots, selected)

    def test_rejects_source_values_that_diverge_from_action_fields(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            merged_bytes = (ROOT / "outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json").read_bytes()
            (directory / "merged.json").write_bytes(merged_bytes)
            workbook_bytes, overrides_bytes = b"workbook", b"{}\n"
            (directory / "review.xlsx").write_bytes(workbook_bytes)
            (directory / "overrides.json").write_bytes(overrides_bytes)
            selection = _valid_selection(directory, merged_bytes)
            selection_bytes = _json_bytes(selection)
            (directory / "selection.json").write_bytes(selection_bytes)
            review = _valid_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
            review["source_review_rows"][0]["normalized_values"]["review_label"] = "attack"
            review["action_observations"][0]["normalized_values"]["review_label"] = "attack"
            (directory / "review-v2.json").write_bytes(_json_bytes(review))
            snapshots = snapshot_review_sources_v2(directory / "review-v2.json", directory / "selection.json", ROOT)
            selected = load_review_selection_bytes(snapshots.selection.raw, merged_bytes=snapshots.merged_candidates.raw, merged_repo_path=snapshots.merged_candidates.repo_path, repo_root=ROOT, require_video=False)

            with self.assertRaisesRegex(ValueError, "source values"):
                load_review_sources_v2(snapshots, selected)


def _repo(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _valid_selection(directory: Path, merged_bytes: bytes) -> dict[str, object]:
    selection = json.loads(
        (ROOT / "data/active-learning/rangitoto/round-01-selection.json").read_text(
            encoding="utf-8"
        )
    )
    selection["source"]["merged_json"] = _repo(directory / "merged.json")
    selection["source"]["merged_json_sha256"] = _sha256(merged_bytes)
    return selection


def _valid_review(
    selection: dict[str, object], selection_bytes: bytes, workbook_bytes: bytes, overrides_bytes: bytes
) -> dict[str, object]:
    clip = selection["clips"][0]
    clip_id = clip["clip_id"]
    action_ref = f"{clip_id}/action-001"
    result_set_id = derive_result_set_id(
        selection["batch_id"], selection["round_id"], _sha256(selection_bytes),
        _sha256(workbook_bytes), _sha256(overrides_bytes),
    )
    action = {
        "action_ref": action_ref, "clip_id": clip_id, "source_action_slot": 1,
        "source_row": 4,
        "raw_values": {"clip_id": clip_id, "review_label": "serve", "relative_start_seconds": 1, "relative_end_seconds": 2, "team_side": "far", "note": ""},
        "normalized_values": {"clip_id": clip_id, "review_label": "serve", "relative_start_seconds": 1, "relative_end_seconds": 2, "team_side": "far", "note": ""}, "review_label": "serve",
        "relative_start_seconds": 1, "relative_end_seconds": 2,
        "start_seconds": clip["start_seconds"] + 1, "end_seconds": clip["start_seconds"] + 2, "team_side": "far",
        "visibility": "direct_clear", "evidence_basis": "direct_video",
        "interval_scope": "timed", "background_scope": None,
        "side_inherited": False, "note": "", "source_reason": None,
        "source_repairs": [],
    }
    return {
        "format": "spiketrace.active-review-evidence-input", "format_version": 2,
        "result_set_id": result_set_id, "review_set_key": "rangitoto/round-01",
        "batch_id": selection["batch_id"], "round_id": selection["round_id"],
        "selection": {"path": selection["source"]["merged_json"].replace("merged.json", "selection.json"), "sha256": _sha256(selection_bytes)},
        "workbook": {"path": selection["source"]["merged_json"].replace("merged.json", "review.xlsx"), "sha256": _sha256(workbook_bytes)},
        "evidence_overrides": {"path": selection["source"]["merged_json"].replace("merged.json", "overrides.json"), "sha256": _sha256(overrides_bytes)},
        "video": selection["video"], "time_precision_seconds": 1,
        "source_review_rows": [{key: action[key] for key in ("action_ref", "clip_id", "source_action_slot", "source_row", "raw_values", "normalized_values", "background_scope", "side_inherited", "source_repairs")}],
        "source_repairs": [], "action_observations": [action],
        "outcome_observations": [{"outcome_ref": f"{result_set_id}/outcome-001", "related_action_refs": [action_ref], "outcome": "continued", "result_type": None, "evidence_basis": "referee_signal", "status": "observed_or_inferred", "note": ""}],
        "visibility_observations": [], "action_participants": [], "normalization_audit": [],
    }


def _node_producer_review(
    selection: dict[str, object], selection_bytes: bytes, workbook_bytes: bytes, overrides_bytes: bytes
) -> dict[str, object]:
    fixture = json.loads(
        (ROOT / "tests/fixtures/node_active_review_evidence_input_v2.json").read_text(
            encoding="utf-8"
        )
    )
    clip = selection["clips"][0]
    review = json.loads(json.dumps(fixture).replace("clip-001", clip["clip_id"]))
    review.update({
        "result_set_id": derive_result_set_id(
            selection["batch_id"], selection["round_id"], _sha256(selection_bytes),
            _sha256(workbook_bytes), _sha256(overrides_bytes),
        ),
        "review_set_key": f"rangitoto/{selection['round_id']}",
        "batch_id": selection["batch_id"], "round_id": selection["round_id"],
        "selection": {"path": selection["source"]["merged_json"].replace("merged.json", "selection.json"), "sha256": _sha256(selection_bytes)},
        "workbook": {"path": selection["source"]["merged_json"].replace("merged.json", "review.xlsx"), "sha256": _sha256(workbook_bytes)},
        "evidence_overrides": {"path": selection["source"]["merged_json"].replace("merged.json", "overrides.json"), "sha256": _sha256(overrides_bytes)},
        "video": selection["video"],
    })
    for action in review["action_observations"]:
        if action["relative_start_seconds"] is not None:
            action["start_seconds"] = clip["start_seconds"] + action["relative_start_seconds"]
            action["end_seconds"] = clip["start_seconds"] + action["relative_end_seconds"]
    return review


def _supplemental(clip: dict[str, object], index: int, scope: str) -> dict[str, object]:
    ref = f"{clip['clip_id']}/supplemental-{index:03d}"
    if scope == "timed":
        relative_start, relative_end = 3, 4
        start, end = clip["start_seconds"] + 3, clip["start_seconds"] + 4
    else:
        relative_start = relative_end = None
        start, end = clip["start_seconds"], clip["end_seconds"]
    visibility = "direct_clear" if scope == "timed" else "fully_occluded"
    return {"action_ref": ref, "clip_id": clip["clip_id"], "source_action_slot": None, "source_row": None, "raw_values": None, "normalized_values": None, "review_label": "free_ball", "relative_start_seconds": relative_start, "relative_end_seconds": relative_end, "start_seconds": start, "end_seconds": end, "team_side": "far", "visibility": visibility, "evidence_basis": "mixed", "interval_scope": scope, "background_scope": None, "side_inherited": False, "note": "", "source_reason": "supplemental", "source_repairs": []}


def _repair(clip: dict[str, object], slot: int) -> dict[str, object]:
    return {"clip_id": clip["clip_id"], "source_action_slot": slot, "sheet": "人工动作", "cell": f"A{3 + slot}", "field": "clip_id", "original_value": None, "normalized_value": clip["clip_id"], "reason": "restore read-only ID"}


def _repair_audit(repair: dict[str, object], action: dict[str, object]) -> dict[str, object]:
    return {"kind": "read_only_repair", "clip_id": repair["clip_id"], "action_ref": action["action_ref"], "source_row": action["source_row"], "raw_value": None, "normalized_value": repair["normalized_value"], "reason": repair["reason"]}


def _participant_matrix(clip_id: str) -> list[dict[str, object]]:
    action_ref = f"{clip_id}/action-001"
    return [
        {"action_ref": action_ref, "track_id": "track-1", "identity_ref": "player-1", "player_number": "1", "participation": "primary_actor", "touch_status": "touched", "assignment_status": "confirmed", "assignment_confidence": 0.95, "evidence": [{"kind": "manual_review", "source_ref": "reviewer", "value": "1", "confidence": 1.0}]},
        {"action_ref": action_ref, "track_id": "track-2", "identity_ref": None, "player_number": None, "participation": "support", "touch_status": "unknown", "assignment_status": "candidate", "assignment_confidence": 0.5, "evidence": [{"kind": "track", "source_ref": "track-2", "value": "candidate", "confidence": 0.5}]},
        {"action_ref": action_ref, "track_id": None, "identity_ref": None, "player_number": None, "participation": "block_attempt", "touch_status": "no_touch", "assignment_status": "unresolved", "assignment_confidence": None, "evidence": []},
    ]


def _visibility(result: str, index: int, kind: str, clip: dict[str, object], action_ref: str) -> dict[str, object]:
    return {"visibility_ref": f"{result}/{kind}-source-{index:03d}", "event_kind": kind, "clip_id": clip["clip_id"], "team_side": "far", "start_seconds": clip["start_seconds"], "end_seconds": clip["end_seconds"], "interval_scope": "clip_bounds", "related_action_refs": [action_ref], "note": "", "source_reason": "observed"}


if __name__ == "__main__":
    unittest.main()
