from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
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
            review = _valid_review(selection, selection_bytes, workbook_bytes, overrides_bytes)
            clip = selection["clips"][0]
            result = review["result_set_id"]
            timed = _supplemental(clip, 1, "timed")
            bounds = _supplemental(clip, 2, "clip_bounds")
            review["action_observations"].extend((timed, bounds))
            review["visibility_observations"] = [
                _visibility(result, 1, "occlusion", clip, timed["action_ref"]),
                _visibility(result, 2, "off_camera", clip, bounds["action_ref"]),
            ]
            (directory / "review-v2.json").write_bytes(_json_bytes(review))
            snapshots = snapshot_review_sources_v2(directory / "review-v2.json", directory / "selection.json", ROOT)
            selected = load_review_selection_bytes(snapshots.selection.raw, merged_bytes=snapshots.merged_candidates.raw, merged_repo_path=snapshots.merged_candidates.repo_path, repo_root=ROOT, require_video=False)
            validated = load_review_sources_v2(snapshots, selected)
            self.assertEqual(validated.action_observations[2]["interval_scope"], "clip_bounds")
            self.assertEqual(validated.visibility_observations[1]["visibility_ref"], f"{result}/off_camera-source-002")


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
        "source_row": 4, "raw_values": {"review_label": "serve"},
        "normalized_values": {"review_label": "serve"}, "review_label": "serve",
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


def _supplemental(clip: dict[str, object], index: int, scope: str) -> dict[str, object]:
    ref = f"{clip['clip_id']}/supplemental-{index:03d}"
    if scope == "timed":
        relative_start, relative_end = 3, 4
        start, end = clip["start_seconds"] + 3, clip["start_seconds"] + 4
    else:
        relative_start = relative_end = start = end = None
    return {"action_ref": ref, "clip_id": clip["clip_id"], "source_action_slot": None, "source_row": None, "raw_values": {}, "normalized_values": {}, "review_label": "free_ball", "relative_start_seconds": relative_start, "relative_end_seconds": relative_end, "start_seconds": start, "end_seconds": end, "team_side": "far", "visibility": "direct_clear", "evidence_basis": "mixed", "interval_scope": scope, "background_scope": None, "side_inherited": False, "note": "", "source_reason": "supplemental", "source_repairs": []}


def _visibility(result: str, index: int, kind: str, clip: dict[str, object], action_ref: str) -> dict[str, object]:
    return {"visibility_ref": f"{result}/{kind}-source-{index:03d}", "event_kind": kind, "clip_id": clip["clip_id"], "team_side": "far", "start_seconds": clip["start_seconds"], "end_seconds": clip["end_seconds"], "interval_scope": "clip_bounds", "related_action_refs": [action_ref], "note": "", "source_reason": "observed"}


if __name__ == "__main__":
    unittest.main()
