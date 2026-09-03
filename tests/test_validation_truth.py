import csv, json, tempfile, unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import cv2, numpy as np

from spiketrace.domain import VideoMetadata
from spiketrace.errors import ValidationError
from spiketrace.validation_contract import ValidationVideoBinding, sha256_file, canonical_json_bytes
from spiketrace.validation_rallies import RallySegment, RallyDetectionSettings, write_rally_queue
from spiketrace.validation_truth import (
    CSV_HEADER, GroundTruthAction, VisibilityInterval, ValidationTruth,
    init_truth_draft, validate_truth_draft, lock_truth_bundle,
    load_locked_truth, verify_truth_bundle,
)


class TruthTests(unittest.TestCase):
    def fixture(self):
        root = Path(tempfile.mkdtemp())
        video = root / "match.avi"
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 1.0, (100, 80))
        for _ in range(20): writer.write(np.zeros((80, 100, 3), dtype=np.uint8))
        writer.release()
        metadata = VideoMetadata(video, 1.0, 20, 100, 80, 20.0)
        binding = ValidationVideoBinding("match-1", video, root, "match.avi", sha256_file(video), metadata)
        segments = (
            RallySegment("set-01-rally-001", None, 1, "set-01-rally-001", 10.0, 14.0, "rally", "near", (0,0,100,80), 0,0,"manual",False,False,None),
            RallySegment("set-01-rally-002", None, 1, "set-01-rally-002", 14.0, 16.0, "rally", "near", (0,0,100,80), 0,0,"manual",True,True,True),
            RallySegment("non-rally-001", None, None, "", 16.0, 20.0, "non_rally", None, None, 0,0,"motion",True,True,None),
        )
        queue = root / "queue.json"
        write_rally_queue(queue, binding=binding, segments=segments,
                          set_intervals=[{"set_index":1,"start_seconds":0,"end_seconds":20}],
                          side_intervals=[{"set_index":1,"start_seconds":0,"end_seconds":20,"team_side":"near","crop":[0,0,100,80]}],
                          settings=RallyDetectionSettings(), code_sha="q")
        return root,binding,queue

    def test_draft_is_prediction_blind_and_free_ball_projects(self):
        root,binding,queue=self.fixture(); draft=root/"draft.json"
        init_truth_draft(queue,draft,code_sha="abc123")
        payload=json.loads(draft.read_text()); self.assertNotIn("predictions",payload); self.assertNotIn("confidence",json.dumps(payload))
        payload["coverage"][0]["coverage_confirmed"]=True; payload["coverage"][0]["all_c2_actions_checked"]=True; payload["coverage"][0]["no_c2_action"]=False
        payload["actions"]=[{"action_ref":"set-01-rally-001/action-001","rally_id":"set-01-rally-001","label":"free_ball","start_seconds":12,"end_seconds":13,"visibility":"visible","evidence":"direct_video","player_number":None,"notes":"passive return"}]
        draft.write_text(json.dumps(payload)); truth=validate_truth_draft(draft,binding=binding)
        self.assertEqual(truth.actions[0].projected_label,"background"); self.assertEqual(truth.actions[0].match_id,"match-1")

    def test_rejects_invalid_truth_records(self):
        root,binding,queue=self.fixture(); draft=root/"draft.json"; init_truth_draft(queue,draft,code_sha="x"); payload=json.loads(draft.read_text())
        payload["coverage"][0].update(coverage_confirmed=True,all_c2_actions_checked=True,no_c2_action=False)
        base={"action_ref":"a","rally_id":"set-01-rally-001","label":"serve","start_seconds":12,"end_seconds":13,"visibility":"visible","evidence":"x","player_number":None,"notes":""}
        for bad in ({**base,"start_seconds":12.5},{**base,"player_number":"7"},{**base,"label":"wat"},{**base,"rally_id":"non"}):
            payload["actions"]=[bad]; draft.write_text(json.dumps(payload))
            with self.assertRaises(ValidationError): validate_truth_draft(draft,binding=binding)

    def test_lock_projection_and_verify(self):
        root,binding,queue=self.fixture(); draft=root/"draft.json"; init_truth_draft(queue,draft,code_sha="x"); payload=json.loads(draft.read_text()); payload["coverage"][0].update(coverage_confirmed=True,all_c2_actions_checked=True,no_c2_action=False); payload["actions"]=[{"action_ref":"a","rally_id":"set-01-rally-001","label":"serve","start_seconds":12,"end_seconds":13,"visibility":"visible","evidence":"x","player_number":None,"notes":""}]; draft.write_text(json.dumps(payload))
        csv_path=root/"truth.csv"; json_path=root/"truth.json"; out=lock_truth_bundle(draft,csv_path,json_path,binding=binding,repo_root=root,code_sha="x",created_at="2026-01-01T00:00:00Z")
        self.assertEqual(out["csv"],csv_path); raw_csv=csv_path.read_bytes(); self.assertTrue(raw_csv.startswith(b"\xef\xbb\xbf")); self.assertNotIn(b"\r\n", raw_csv); self.assertEqual(csv_path.read_text(encoding="utf-8-sig").splitlines()[0],"video_path,start_seconds,end_seconds,label,team_side,player_number,crop_x1,crop_y1,crop_x2,crop_y2,split,match_id,rally_id"); self.assertEqual(csv_path.read_text(encoding="utf-8-sig").splitlines()[1],"match.avi,12,13,serve,near,,0,0,100,80,val,match-1,set-01-rally-001")
        truth=load_locked_truth(json_path,csv_path,binding=binding); self.assertTrue(truth.locked); report=verify_truth_bundle(json_path,csv_path,binding=binding,repo_root=root); self.assertEqual(report["visible_actions"],1)

    def test_no_action_and_visibility_do_not_emit_rows(self):
        root,binding,queue=self.fixture(); draft=root/"draft.json"; init_truth_draft(queue,draft,code_sha="x"); payload=json.loads(draft.read_text()); payload["coverage"][0].update(coverage_confirmed=True,all_c2_actions_checked=True,no_c2_action=False); payload["visibility_events"]=[{"event_ref":"v1","rally_id":"set-01-rally-001","kind":"fully_occluded","start_seconds":10,"end_seconds":11,"notes":"net"}]; payload["actions"]=[{"action_ref":"a","rally_id":"set-01-rally-001","label":"serve","start_seconds":12,"end_seconds":13,"visibility":"fully_occluded","evidence":"direct_video","player_number":None,"notes":""}]; draft.write_text(json.dumps(payload)); csv_path=root/"truth.csv"; json_path=root/"truth.json"; lock_truth_bundle(draft,csv_path,json_path,binding=binding,repo_root=root,code_sha="x",created_at="now"); self.assertEqual(len(csv_path.read_text(encoding="utf-8-sig").splitlines()),1); self.assertEqual(verify_truth_bundle(json_path,csv_path,binding=binding,repo_root=root)["visibility_intervals"],1)

    def test_duplicate_and_unknown_root_fields_fail_closed(self):
        root,binding,queue=self.fixture(); draft=root/"draft.json"; init_truth_draft(queue,draft,code_sha="x"); text=draft.read_text(); draft.write_text(text[:-1] + ',"extra":1}')
        with self.assertRaises(ValidationError): validate_truth_draft(draft,binding=binding)

    def test_csv_hash_rebinding_is_rejected(self):
        root,binding,queue=self.fixture(); draft=root/"draft.json"; init_truth_draft(queue,draft,code_sha="x"); payload=json.loads(draft.read_text()); payload["coverage"][0].update(coverage_confirmed=True,all_c2_actions_checked=True,no_c2_action=False); payload["actions"]=[{"action_ref":"a","rally_id":"set-01-rally-001","label":"serve","start_seconds":12,"end_seconds":13,"visibility":"visible","evidence":"x","player_number":None,"notes":""}]; draft.write_text(json.dumps(payload)); csv_path=root/"truth.csv"; json_path=root/"truth.json"; lock_truth_bundle(draft,csv_path,json_path,binding=binding,repo_root=root,code_sha="x",created_at="now"); csv_path.write_bytes(csv_path.read_bytes().replace(b"\n",b"\r\n")); locked=json.loads(json_path.read_text()); locked["integrity"]["csv_sha256"]=sha256_file(csv_path); json_path.write_bytes(canonical_json_bytes(locked));
        with self.assertRaises(ValidationError): verify_truth_bundle(json_path,csv_path,binding=binding,repo_root=root)

    def test_explicit_video_root_mismatch_is_rejected(self):
        root,binding,queue=self.fixture(); draft=root/"draft.json"; init_truth_draft(queue,draft,code_sha="x"); payload=json.loads(draft.read_text()); payload["coverage"][0].update(coverage_confirmed=True,all_c2_actions_checked=True,no_c2_action=True); payload["actions"]=[]; draft.write_text(json.dumps(payload)); csv_path=root/"truth.csv"; json_path=root/"truth.json"; lock_truth_bundle(draft,csv_path,json_path,binding=binding,repo_root=root,code_sha="x",created_at="now"); other=root/"other"; other.mkdir(); (other/"match.avi").write_bytes(binding.video_path.read_bytes());
        with self.assertRaises(ValidationError): verify_truth_bundle(json_path,csv_path,binding=binding,repo_root=root,video_root=other)

    def test_split_rally_requires_every_part_confirmed(self):
        root,binding,queue=self.fixture(); draft=root/"draft.json"; init_truth_draft(queue,draft,code_sha="x"); payload=json.loads(draft.read_text()); payload["coverage"][0]["coverage_confirmed"]=True; payload["coverage"][0]["all_c2_actions_checked"]=False; payload["coverage"][0]["no_c2_action"]=True; payload["coverage"].insert(1,dict(payload["coverage"][0],segment_id="set-01-rally-001-b",start_seconds=11,end_seconds=12,coverage_confirmed=True,all_c2_actions_checked=True)); draft.write_text(json.dumps(payload));
        with self.assertRaises(ValidationError): validate_truth_draft(draft,binding=binding)

    def test_strict_interval_and_text_validation(self):
        root,binding,queue=self.fixture(); draft=root/"draft.json"; init_truth_draft(queue,draft,code_sha="x"); payload=json.loads(draft.read_text()); payload["coverage"][0].update(coverage_confirmed=True,all_c2_actions_checked=True,no_c2_action=False); payload["actions"]=[{"action_ref":"a","rally_id":"set-01-rally-001","label":"serve","start_seconds":12,"end_seconds":13,"visibility":"visible","evidence":3,"player_number":None,"notes":""}]; draft.write_text(json.dumps(payload));
        with self.assertRaises(ValidationError): validate_truth_draft(draft,binding=binding)

    def test_paired_publication_rolls_back_first_file(self):
        root,binding,queue=self.fixture(); draft=root/"draft.json"; init_truth_draft(queue,draft,code_sha="x"); payload=json.loads(draft.read_text()); payload["coverage"][0].update(coverage_confirmed=True,all_c2_actions_checked=True,no_c2_action=True); draft.write_text(json.dumps(payload)); csv_path=root/"truth.csv"; json_path=root/"truth.json"
        import spiketrace.validation_truth as truth_mod
        original = truth_mod.write_new_bytes
        calls = {"count": 0}
        def fail_second(path, data):
            calls["count"] += 1
            if calls["count"] == 2: raise ValidationError("injected publication failure")
            return original(path, data)
        with patch.object(truth_mod, "write_new_bytes", side_effect=fail_second), self.assertRaises(ValidationError): lock_truth_bundle(draft,csv_path,json_path,binding=binding,repo_root=root,code_sha="x",created_at="now")
        self.assertFalse(csv_path.exists()); self.assertFalse(json_path.exists())

    def test_repo_root_authority_and_metadata_tamper_fail(self):
        root,binding,queue=self.fixture(); draft=root/"draft.json"; init_truth_draft(queue,draft,code_sha="x"); payload=json.loads(draft.read_text()); payload["coverage"][0].update(coverage_confirmed=True,all_c2_actions_checked=True,no_c2_action=True); draft.write_text(json.dumps(payload)); csv_path=root/"truth.csv"; json_path=root/"truth.json"; lock_truth_bundle(draft,csv_path,json_path,binding=binding,repo_root=root,code_sha="x",created_at="now")
        invalid_repo = root / "invalid-repo"; invalid_repo.write_text("x")
        with self.assertRaises(ValidationError): verify_truth_bundle(json_path,csv_path,binding=binding,repo_root=invalid_repo)
        tampered=json.loads(json_path.read_text()); tampered["video"]["metadata"]["width"]=999; json_path.write_bytes(canonical_json_bytes(tampered))
        with self.assertRaises(ValidationError): load_locked_truth(json_path,csv_path,binding=binding)

    def test_coverage_rejects_string_numeric(self):
        root,binding,queue=self.fixture(); draft=root/"draft.json"; init_truth_draft(queue,draft,code_sha="x"); payload=json.loads(draft.read_text()); payload["coverage"][0]["start_seconds"]="10"; payload["coverage"][0]["no_c2_action"]=True; draft.write_text(json.dumps(payload))
        with self.assertRaises(ValidationError): validate_truth_draft(draft,binding=binding)

    def test_unique_no_action_count_for_split_rally(self):
        root,binding,queue=self.fixture(); draft=root/"draft.json"; init_truth_draft(queue,draft,code_sha="x"); payload=json.loads(draft.read_text()); first=payload["coverage"][0]; first.update(coverage_confirmed=True,all_c2_actions_checked=True,no_c2_action=True); second=payload["coverage"][1]; second.update(rally_id=first["rally_id"],coverage_confirmed=True,all_c2_actions_checked=True,no_c2_action=True); draft.write_text(json.dumps(payload)); csv_path=root/"truth.csv"; json_path=root/"truth.json"; lock_truth_bundle(draft,csv_path,json_path,binding=binding,repo_root=root,code_sha="x",created_at="now"); report=verify_truth_bundle(json_path,csv_path,binding=binding,repo_root=root); self.assertEqual(report["no_action_rallies"],1)
        draft.write_text('{"format_version":1,"format_version":1}')
        with self.assertRaises(ValidationError): validate_truth_draft(draft,binding=binding)


if __name__ == "__main__": unittest.main()
