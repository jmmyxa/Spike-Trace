from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from unittest import mock

from spiketrace import _active_learning_review_outputs as outputs
from spiketrace._active_learning_review_contract import (
    ArtifactBinding,
    ReviewSourceHashes,
    ValidatedReviewInput,
    VideoBinding,
)
from spiketrace._active_learning_review_observations import (
    ActionObservation,
    ActionParticipant,
    ObservationSet,
    OutcomeObservation,
    VisibilityEvent,
)
from spiketrace._active_learning_review_outputs import (
    BundleSettings,
    publish_result_bundle,
    render_result_bundle,
    validate_result_bundle,
)
from spiketrace._active_learning_review_projection import (
    ProtectedInterval,
    TrainingDecision,
    TrainingProjection,
    TrainingWindow,
)

CSV_HEADERS = {
    "round-01-observations.csv": (
        "result_set_id,selection_sha256,workbook_sha256,generator_version,"
        "observation_type,observation_ref,action_ref,clip_id,source_action_slot,"
        "review_label,relative_start_seconds,relative_end_seconds,start_seconds,"
        "end_seconds,team_side,visibility,evidence_basis,training_decision,outcome,"
        "result_type,status,related_action_refs_json,note"
    ),
    "round-01-visibility-events.csv": (
        "result_set_id,selection_sha256,workbook_sha256,generator_version,event_kind,"
        "event_ref,team_side,start_seconds,end_seconds,duration_seconds,interval_scope,"
        "related_action_refs_json,source_refs_json,note"
    ),
    "round-01-action-participants.csv": (
        "result_set_id,selection_sha256,workbook_sha256,generator_version,action_ref,"
        "track_id,identity_ref,player_number,participation,touch_status,"
        "assignment_status,assignment_confidence,evidence_json"
    ),
}


class ResultBundleRenderingTests(unittest.TestCase):
    def test_renders_exact_six_file_bundle_with_portable_csv_bytes(self):
        bundle = _rendered_bundle()
        artifacts = dict(bundle.artifacts)

        self.assertEqual(
            tuple(artifacts),
            (
                "round-01-results.json",
                "action_training_round_01.csv",
                "round-01-observations.csv",
                "round-01-visibility-events.csv",
                "round-01-action-participants.csv",
                "round-01-exports.manifest.json",
            ),
        )
        for filename, expected_header in CSV_HEADERS.items():
            raw = artifacts[filename]
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            text = raw[3:].decode("utf-8")
            self.assertNotIn("\n", text.replace("\r\n", ""))
            self.assertEqual(text.split("\r\n", 1)[0], expected_header)

        participant_bytes = artifacts["round-01-action-participants.csv"]
        expected_participant = ("\ufeff" + CSV_HEADERS["round-01-action-participants.csv"] + "\r\n").encode("utf-8")
        self.assertEqual(participant_bytes, expected_participant)

        observations = _csv_rows(artifacts["round-01-observations.csv"])
        self.assertEqual(
            tuple((row["observation_type"], row["observation_ref"]) for row in observations),
            (
                ("action", "clip-001/action-001"),
                ("action", "clip-002/action-001"),
                ("outcome", "result-test/outcome-001"),
            ),
        )
        self.assertEqual(observations[2]["related_action_refs_json"], '["clip-001/action-001"]')

    def test_content_hash_is_derived_from_semantic_authority_and_exports_exact_bytes(self):
        bundle = _rendered_bundle()
        artifacts = dict(bundle.artifacts)
        authority = json.loads(artifacts["round-01-results.json"])
        manifest = json.loads(artifacts["round-01-exports.manifest.json"])

        semantic = dict(authority)
        del semantic["content_sha256"]
        del semantic["exports"]
        semantic_bytes = json.dumps(
            semantic,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        expected_digest = hashlib.sha256(semantic_bytes).hexdigest()

        self.assertEqual(authority["content_sha256"], expected_digest)
        self.assertEqual(manifest["content_sha256"], expected_digest)
        self.assertEqual(tuple(authority), (
            "format", "format_version", "result_set_id", "content_sha256", "batch_id",
            "round_id", "generator_version", "sources", "source_review_rows", "repairs",
            "action_observations", "outcome_observations", "occlusion_events",
            "off_camera_events", "action_participants", "protected_intervals",
            "training_projection", "summary", "exports",
        ))
        self.assertEqual(authority["format"], "spiketrace.active-review-observations")
        self.assertIs(type(authority["format_version"]), int)
        self.assertEqual(authority["format_version"], 2)
        self.assertEqual(authority["training_projection"]["training_video_path"], "video.mp4")
        self.assertEqual(authority["training_projection"]["review_match_id"], "review-match")
        self.assertEqual(
            authority["training_projection"]["base_training_view"]["data_rows"], 1
        )
        self.assertEqual(tuple(authority["sources"]), (
            "selection", "review_input", "workbook", "evidence_overrides",
            "merged_candidates", "base_manifest", "video", "verification",
        ))
        self.assertEqual(
            authority["exports"]["action_participants_csv"]["sha256"],
            hashlib.sha256(artifacts["round-01-action-participants.csv"]).hexdigest(),
        )
        self.assertEqual(authority["exports"]["manifest"], {"path": "round-01-exports.manifest.json"})
        self.assertEqual(tuple(manifest), (
            "format", "format_version", "result_set_id", "content_sha256",
            "generator_version", "sources", "artifacts",
        ))
        self.assertEqual(
            tuple(item["path"] for item in manifest["artifacts"]),
            (
                "round-01-results.json",
                "action_training_round_01.csv",
                "round-01-observations.csv",
                "round-01-visibility-events.csv",
                "round-01-action-participants.csv",
            ),
        )
        self.assertEqual(manifest["artifacts"][0]["entity_counts"], {
            "action_observations": 2,
            "outcome_observations": 1,
            "occlusion_events": 1,
            "off_camera_events": 0,
            "action_participants": 0,
            "training_rows": 3,
        })
        for filename, raw in bundle.artifacts:
            if filename.endswith(".json"):
                self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
                self.assertTrue(raw.endswith(b"\n"))
                self.assertNotIn(b"\r", raw)

    def test_normalizes_embedded_csv_field_newlines_to_crlf(self):
        bundle = _rendered_bundle(note="first\nsecond\rthird\r\nfourth")

        for filename, raw in bundle.artifacts:
            if not filename.endswith(".csv"):
                continue
            body = raw[3:]
            remaining = body.replace(b"\r\n", b"")
            self.assertNotIn(b"\r", remaining)
            self.assertNotIn(b"\n", remaining)


class ResultBundleValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.bundle_dir = Path(self.temporary.name) / "bundle"
        self.bundle_dir.mkdir()
        for filename, raw in _rendered_bundle().artifacts:
            (self.bundle_dir / filename).write_bytes(raw)

    def test_accepts_complete_cross_validated_bundle_and_returns_summary(self):
        result = validate_result_bundle(self.bundle_dir)

        self.assertEqual(result["result_set_id"], "result-test")
        self.assertEqual(result["content_sha256"], _authority(self.bundle_dir)["content_sha256"])
        self.assertEqual(result["summary"]["training_rows"], 3)

    def test_rejects_extra_or_missing_file(self):
        (self.bundle_dir / "extra.txt").write_text("extra", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exactly six"):
            validate_result_bundle(self.bundle_dir)


class ResultBundlePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = _rendered_bundle()

    def test_publishes_validated_bundle_and_calls_callback_immediately_before_rename(self):
        output_dir = self.root / "parent" / "bundle"
        publication_io = _TracingIO()
        callback_count = 0

        def callback():
            nonlocal callback_count
            callback_count += 1
            publication_io.trace.append("callback")

        publish_result_bundle(
            output_dir,
            self.bundle,
            before_publish=callback,
            io=publication_io,
        )

        self.assertEqual(callback_count, 1)
        self.assertEqual(publication_io.trace[-2:], ["callback", "rename"])
        self.assertEqual(validate_result_bundle(output_dir)["result_set_id"], "result-test")
        self.assertEqual(tuple(sorted(path.name for path in output_dir.iterdir())), tuple(sorted(dict(self.bundle.artifacts))))
        self.assertEqual(_staging_paths(output_dir), [])

    def test_each_injected_io_failure_leaves_no_final_or_staging_directory(self):
        operations = (
            "create_parent", "create_staging", "open_exclusive", "write", "flush",
            "fsync", "read", "rename",
        )
        for operation in operations:
            with self.subTest(operation=operation):
                output_dir = self.root / operation / "parent" / "bundle"
                with self.assertRaisesRegex(OSError, operation):
                    publish_result_bundle(
                        output_dir,
                        self.bundle,
                        io=_FailingIO(operation),
                    )
                self.assertFalse(output_dir.exists())
                self.assertEqual(_staging_paths(output_dir), [])

    def test_validation_and_callback_failures_remove_only_staging(self):
        for label, validate, callback in (
            ("validation", mock.Mock(side_effect=ValueError("validation")), None),
            ("callback", validate_result_bundle, mock.Mock(side_effect=RuntimeError("callback"))),
        ):
            with self.subTest(label=label):
                output_dir = self.root / label / "bundle"
                with self.assertRaisesRegex((ValueError, RuntimeError), label):
                    publish_result_bundle(
                        output_dir,
                        self.bundle,
                        validate=validate,
                        before_publish=callback,
                    )
                self.assertFalse(output_dir.exists())
                self.assertEqual(_staging_paths(output_dir), [])
                if callback is not None:
                    callback.assert_called_once_with()

    def test_existing_file_directories_and_symlink_remain_unchanged(self):
        targets = []
        existing_file = self.root / "existing-file"
        existing_file.write_bytes(b"preserve-file")
        targets.append((existing_file, lambda: self.assertEqual(existing_file.read_bytes(), b"preserve-file")))
        empty_dir = self.root / "empty-dir"
        empty_dir.mkdir()
        targets.append((empty_dir, lambda: self.assertEqual(list(empty_dir.iterdir()), [])))
        nonempty_dir = self.root / "nonempty-dir"
        nonempty_dir.mkdir()
        (nonempty_dir / "winner.txt").write_text("winner", encoding="utf-8")
        targets.append((nonempty_dir, lambda: self.assertEqual((nonempty_dir / "winner.txt").read_text(encoding="utf-8"), "winner")))
        symlink = self.root / "existing-link"
        symlink_target = self.root / "link-target"
        symlink_target.mkdir()
        try:
            symlink.symlink_to(symlink_target, target_is_directory=True)
        except OSError:
            symlink = None
        if symlink is not None:
            targets.append((symlink, lambda: self.assertTrue(symlink.is_symlink())))

        for target, assert_preserved in targets:
            with self.subTest(target=target.name):
                with self.assertRaises(OSError):
                    publish_result_bundle(target, self.bundle)
                assert_preserved()
                self.assertEqual(_staging_paths(target), [])

    def test_concurrent_publishers_have_exactly_one_winner(self):
        output_dir = self.root / "race" / "bundle"
        barrier = threading.Barrier(2)

        def publish():
            try:
                publish_result_bundle(output_dir, self.bundle, before_publish=barrier.wait)
                return "won"
            except OSError:
                return "lost"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(lambda _: publish(), range(2)))

        self.assertEqual(sorted(results), ["lost", "won"])
        self.assertEqual(validate_result_bundle(output_dir)["result_set_id"], "result-test")
        self.assertEqual(_staging_paths(output_dir), [])

    def test_macos_missing_renamex_fails_before_publication_side_effects(self):
        output_dir = self.root / "darwin-parent" / "bundle"
        publication_io = _CountingIO()
        callback = mock.Mock()

        with (
            mock.patch.object(outputs.sys, "platform", "darwin"),
            mock.patch.object(outputs.ctypes, "CDLL", return_value=object()),
            self.assertRaisesRegex(RuntimeError, "renamex_np"),
        ):
            publish_result_bundle(
                output_dir,
                self.bundle,
                before_publish=callback,
                io=publication_io,
            )

        self.assertEqual(publication_io.trace, [])
        callback.assert_not_called()
        self.assertFalse(output_dir.parent.exists())


class ResultBundleTamperValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.bundle_dir = Path(self.temporary.name) / "bundle"
        self.bundle_dir.mkdir()
        for filename, raw in _rendered_bundle().artifacts:
            (self.bundle_dir / filename).write_bytes(raw)

    def test_rejects_missing_file(self):
        (self.bundle_dir / "round-01-observations.csv").unlink()
        with self.assertRaisesRegex(ValueError, "exactly six"):
            validate_result_bundle(self.bundle_dir)

    def test_rejects_wrong_or_boolean_format_versions(self):
        cases = (
            ("round-01-results.json", "format", "wrong"),
            ("round-01-results.json", "format_version", True),
            ("round-01-exports.manifest.json", "format", "wrong"),
            ("round-01-exports.manifest.json", "format_version", True),
        )
        originals = {filename: (self.bundle_dir / filename).read_bytes() for filename, _, _ in cases}
        for filename, field, value in cases:
            with self.subTest(filename=filename, field=field):
                path = self.bundle_dir / filename
                path.write_bytes(originals[filename])
                payload = json.loads(path.read_bytes())
                payload[field] = value
                path.write_bytes(_json_presentation(payload))
                with self.assertRaisesRegex(ValueError, "format"):
                    validate_result_bundle(self.bundle_dir)

    def test_rejects_csv_bom_and_line_ending_mismatch(self):
        path = self.bundle_dir / "round-01-action-participants.csv"
        original = path.read_bytes()
        path.write_bytes(original[3:])
        with self.assertRaisesRegex(ValueError, "BOM"):
            validate_result_bundle(self.bundle_dir)
        path.write_bytes(original.replace(b"\r\n", b"\n"))
        with self.assertRaisesRegex(ValueError, "CRLF"):
            validate_result_bundle(self.bundle_dir)
        path.write_bytes(original.replace(b"\r\n", b"\r\r\n", 1))
        with self.assertRaisesRegex(ValueError, "CRLF"):
            validate_result_bundle(self.bundle_dir)

    def test_rejects_stale_artifact_hash_and_byte_count(self):
        manifest_path = self.bundle_dir / "round-01-exports.manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["artifacts"][1]["sha256"] = "0" * 64
        manifest["artifacts"][2]["bytes"] += 1
        manifest_path.write_bytes(_json_presentation(manifest))

        with self.assertRaisesRegex(ValueError, "artifact"):
            validate_result_bundle(self.bundle_dir)

    def test_rejects_copied_but_stale_content_hash(self):
        authority_path = self.bundle_dir / "round-01-results.json"
        manifest_path = self.bundle_dir / "round-01-exports.manifest.json"
        authority = json.loads(authority_path.read_bytes())
        authority["batch_id"] = "substituted-batch"
        authority_path.write_bytes(_json_presentation(authority))
        manifest = json.loads(manifest_path.read_bytes())
        manifest["artifacts"][0]["sha256"] = hashlib.sha256(authority_path.read_bytes()).hexdigest()
        manifest["artifacts"][0]["bytes"] = len(authority_path.read_bytes())
        manifest_path.write_bytes(_json_presentation(manifest))

        with self.assertRaisesRegex(ValueError, "content_sha256"):
            validate_result_bundle(self.bundle_dir)

    def test_rejects_wrong_data_rows_or_entity_counts(self):
        manifest_path = self.bundle_dir / "round-01-exports.manifest.json"
        original = json.loads(manifest_path.read_bytes())
        for mutate in (
            lambda value: value["artifacts"][2].__setitem__("data_rows", 99),
            lambda value: value["artifacts"][0]["entity_counts"].__setitem__("training_rows", 99),
        ):
            with self.subTest(mutate=mutate):
                manifest = deepcopy(original)
                mutate(manifest)
                manifest_path.write_bytes(_json_presentation(manifest))
                with self.assertRaisesRegex(ValueError, "rows|entity_counts"):
                    validate_result_bundle(self.bundle_dir)

    def test_rejects_boolean_entity_count(self):
        manifest_path = self.bundle_dir / "round-01-exports.manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["artifacts"][0]["entity_counts"]["action_participants"] = False
        manifest_path.write_bytes(_json_presentation(manifest))

        with self.assertRaisesRegex(ValueError, "entity_counts"):
            validate_result_bundle(self.bundle_dir)

    def test_rejects_cross_view_result_identifier_mismatch(self):
        observations_path = self.bundle_dir / "round-01-observations.csv"
        authority_path = self.bundle_dir / "round-01-results.json"
        manifest_path = self.bundle_dir / "round-01-exports.manifest.json"
        observations_bytes = observations_path.read_bytes().replace(
            b"result-test", b"result-evil", 1
        )
        observations_path.write_bytes(observations_bytes)
        authority = json.loads(authority_path.read_bytes())
        authority["exports"]["observations_csv"]["sha256"] = hashlib.sha256(observations_bytes).hexdigest()
        authority_path.write_bytes(_json_presentation(authority))
        manifest = json.loads(manifest_path.read_bytes())
        manifest["artifacts"][0]["sha256"] = hashlib.sha256(authority_path.read_bytes()).hexdigest()
        manifest["artifacts"][0]["bytes"] = len(authority_path.read_bytes())
        manifest["artifacts"][2]["sha256"] = hashlib.sha256(observations_bytes).hexdigest()
        manifest_path.write_bytes(_json_presentation(manifest))

        with self.assertRaisesRegex(ValueError, "result_set_id"):
            validate_result_bundle(self.bundle_dir)

    def test_rejects_rehashed_observation_view_that_diverges_from_authority(self):
        observations_path = self.bundle_dir / "round-01-observations.csv"
        authority_path = self.bundle_dir / "round-01-results.json"
        manifest_path = self.bundle_dir / "round-01-exports.manifest.json"
        observations_bytes = observations_path.read_bytes().replace(
            "自由球".encode(), "替代值".encode(), 1
        )
        observations_path.write_bytes(observations_bytes)
        authority = json.loads(authority_path.read_bytes())
        authority["exports"]["observations_csv"]["sha256"] = hashlib.sha256(
            observations_bytes
        ).hexdigest()
        authority_path.write_bytes(_json_presentation(authority))
        manifest = json.loads(manifest_path.read_bytes())
        manifest["artifacts"][0]["sha256"] = hashlib.sha256(
            authority_path.read_bytes()
        ).hexdigest()
        manifest["artifacts"][0]["bytes"] = len(authority_path.read_bytes())
        manifest["artifacts"][2]["sha256"] = hashlib.sha256(
            observations_bytes
        ).hexdigest()
        manifest["artifacts"][2]["bytes"] = len(observations_bytes)
        manifest_path.write_bytes(_json_presentation(manifest))

        with self.assertRaisesRegex(ValueError, "authority observations"):
            validate_result_bundle(self.bundle_dir)

    def test_rejects_rehashed_training_view_that_diverges_from_authority(self):
        training_path = self.bundle_dir / "action_training_round_01.csv"
        rows = _csv_rows(training_path.read_bytes())
        rows[-1]["label"] = "attack"
        training_bytes = _test_csv_bytes(tuple(rows[0]), rows)
        training_path.write_bytes(training_bytes)
        _refresh_export_hashes(self.bundle_dir, "action_training_round_01.csv")

        with self.assertRaisesRegex(ValueError, "authority training_projection"):
            validate_result_bundle(self.bundle_dir)

    def test_rejects_rehashed_base_training_prefix_tampering(self):
        cases = (
            ("label", "attack"),
            ("split", "val"),
            ("video_path", "other-legacy.mp4"),
            ("match_id", "other-legacy-match"),
        )
        for field, replacement in cases:
            with self.subTest(field=field):
                for filename, raw in _rendered_bundle().artifacts:
                    (self.bundle_dir / filename).write_bytes(raw)
                training_path = self.bundle_dir / "action_training_round_01.csv"
                rows = _csv_rows(training_path.read_bytes())
                rows[0][field] = replacement
                training_path.write_bytes(_test_csv_bytes(tuple(rows[0]), rows))
                _refresh_export_hashes(self.bundle_dir, training_path.name)

                with self.assertRaisesRegex(ValueError, "base training"):
                    validate_result_bundle(self.bundle_dir)

    def test_rejects_rehashed_empty_base_training_header_tampering(self):
        for filename, raw in _rendered_bundle(
            no_windows=True, no_base_rows=True
        ).artifacts:
            (self.bundle_dir / filename).write_bytes(raw)
        training_path = self.bundle_dir / "action_training_round_01.csv"
        original = training_path.read_bytes()
        training_path.write_bytes(original.replace(b"video_path,", b"extra,video_path,", 1))
        _refresh_export_hashes(self.bundle_dir, training_path.name)

        with self.assertRaisesRegex(ValueError, "base training"):
            validate_result_bundle(self.bundle_dir)

    def test_rejects_rehashed_training_identity_that_diverges_from_authority(self):
        training_path = self.bundle_dir / "action_training_round_01.csv"
        original = training_path.read_bytes()
        for field, replacement in (
            ("video_path", "other-video.mp4"),
            ("match_id", "other-match"),
        ):
            with self.subTest(field=field):
                training_path.write_bytes(original)
                rows = _csv_rows(original)
                for row in rows[-2:]:
                    row[field] = replacement
                training_path.write_bytes(_test_csv_bytes(tuple(rows[0]), rows))
                _refresh_export_hashes(self.bundle_dir, "action_training_round_01.csv")

                with self.assertRaisesRegex(ValueError, "authority training_projection"):
                    validate_result_bundle(self.bundle_dir)

    def test_rejects_empty_training_identity_for_base_only_bundle(self):
        for filename, raw in _rendered_bundle(no_windows=True).artifacts:
            (self.bundle_dir / filename).write_bytes(raw)
        authority = _authority(self.bundle_dir)
        authority["training_projection"]["training_video_path"] = ""
        authority["training_projection"]["review_match_id"] = ""
        _rewrite_semantic_authority(self.bundle_dir, authority)

        with self.assertRaisesRegex(ValueError, "training_projection"):
            validate_result_bundle(self.bundle_dir)

    def test_rejects_rehashed_summary_that_diverges_from_authority_entities(self):
        authority_path = self.bundle_dir / "round-01-results.json"
        authority = json.loads(authority_path.read_bytes())
        authority["summary"]["training_rows"] = 999
        _rewrite_semantic_authority(self.bundle_dir, authority)

        with self.assertRaisesRegex(ValueError, "summary"):
            validate_result_bundle(self.bundle_dir)

    def test_rejects_rehashed_invalid_video_binding_contract(self):
        cases = (
            ("fps bool", lambda video: video.__setitem__("fps", True)),
            ("duration zero", lambda video: video.__setitem__("duration_seconds", 0)),
            ("frame count bool", lambda video: video.__setitem__("frame_count", True)),
            ("width zero", lambda video: video.__setitem__("width", 0)),
            ("crop bool", lambda video: video["crops"]["far"].__setitem__(0, True)),
            ("crop bounds", lambda video: video["crops"]["far"].__setitem__(2, 101)),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                for filename, raw in _rendered_bundle().artifacts:
                    (self.bundle_dir / filename).write_bytes(raw)
                authority = _authority(self.bundle_dir)
                mutate(authority["sources"]["video"])
                _rewrite_semantic_authority(self.bundle_dir, authority)

                with self.assertRaisesRegex(ValueError, "sources.video"):
                    validate_result_bundle(self.bundle_dir)

    def test_rejects_rehashed_invalid_projection_decision_relation(self):
        cases = (
            ("duplicate", lambda decisions: decisions.append(deepcopy(decisions[0]))),
            ("orphan", lambda decisions: decisions.append({
                "action_ref": "orphan/action-001",
                "decision": "eligible",
                "training_label": "serve",
                "reason": "direct_visual",
            })),
            ("illegal", lambda decisions: decisions.append({
                "action_ref": "orphan/action-002",
                "decision": "maybe",
                "training_label": "serve",
                "reason": "invented",
            })),
            ("missing", lambda decisions: decisions.clear()),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                for filename, raw in _rendered_bundle().artifacts:
                    (self.bundle_dir / filename).write_bytes(raw)
                authority = _authority(self.bundle_dir)
                mutate(authority["training_projection"]["decisions"])
                _rewrite_semantic_authority(self.bundle_dir, authority)

                with self.assertRaisesRegex(ValueError, "training_projection decisions"):
                    validate_result_bundle(self.bundle_dir)

    def test_rejects_rehashed_invalid_projection_windows_and_caps(self):
        cases = (
            ("human window index", lambda projection: projection["human_windows"][0].__setitem__("window_index", 0)),
            ("human top1", lambda projection: projection["human_windows"][0].__setitem__("source_top1_confidence", 0.5)),
            ("generated clip", lambda projection: projection["generated_background_windows"][0].__setitem__("clip_id", "")),
            ("generated index", lambda projection: projection["generated_background_windows"][0].__setitem__("window_index", -1)),
            ("requested cap", lambda projection: projection.__setitem__("requested_background_cap", -1)),
            ("effective cap bool", lambda projection: projection.__setitem__("effective_background_cap", True)),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                for filename, raw in _rendered_bundle().artifacts:
                    (self.bundle_dir / filename).write_bytes(raw)
                authority = _authority(self.bundle_dir)
                mutate(authority["training_projection"])
                _rewrite_semantic_authority(self.bundle_dir, authority)

                with self.assertRaisesRegex(ValueError, "training_projection"):
                    validate_result_bundle(self.bundle_dir)

    def test_rejects_rehashed_player_projection_without_confirmed_participant(self):
        authority = _authority(self.bundle_dir)
        authority["training_projection"]["human_windows"][0]["player_number"] = "99"
        _rewrite_semantic_authority(self.bundle_dir, authority)
        training_path = self.bundle_dir / "action_training_round_01.csv"
        rows = _csv_rows(training_path.read_bytes())
        rows[-2]["player_number"] = "99"
        training_path.write_bytes(_test_csv_bytes(tuple(rows[0]), rows))
        _refresh_export_hashes(self.bundle_dir, training_path.name)

        with self.assertRaisesRegex(ValueError, "player|training_projection"):
            validate_result_bundle(self.bundle_dir)

    def test_rejects_rehashed_whitespace_required_source_identifiers(self):
        cases = [
            ("video_id", lambda sources: sources["video"].__setitem__("video_id", "   ")),
            ("video path", lambda sources: sources["video"].__setitem__("path", "   ")),
        ]
        cases.extend(
            (
                f"{name} path",
                lambda sources, binding=name: sources[binding].__setitem__("path", "   "),
            )
            for name in (
                "selection", "review_input", "workbook", "evidence_overrides",
                "merged_candidates", "base_manifest",
            )
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                for filename, raw in _rendered_bundle().artifacts:
                    (self.bundle_dir / filename).write_bytes(raw)
                authority = _authority(self.bundle_dir)
                mutate(authority["sources"])
                _rewrite_semantic_authority(self.bundle_dir, authority)

                with self.assertRaisesRegex(ValueError, "nonempty|POSIX|sources"):
                    validate_result_bundle(self.bundle_dir)

    def test_rejects_rehashed_whitespace_projection_video_and_match_identity(self):
        cases = (
            ("training_video_path", "video_path"),
            ("review_match_id", "match_id"),
        )
        for authority_field, csv_field in cases:
            with self.subTest(field=authority_field):
                for filename, raw in _rendered_bundle().artifacts:
                    (self.bundle_dir / filename).write_bytes(raw)
                authority = _authority(self.bundle_dir)
                authority["training_projection"][authority_field] = "   "
                _rewrite_semantic_authority(self.bundle_dir, authority)
                training_path = self.bundle_dir / "action_training_round_01.csv"
                rows = _csv_rows(training_path.read_bytes())
                for row in rows[-2:]:
                    row[csv_field] = "   "
                training_path.write_bytes(_test_csv_bytes(tuple(rows[0]), rows))
                _refresh_export_hashes(self.bundle_dir, training_path.name)

                with self.assertRaisesRegex(ValueError, "nonempty|training_projection"):
                    validate_result_bundle(self.bundle_dir)

    def test_rejects_rehashed_whitespace_projected_participant_identifiers(self):
        for field in ("track_id", "identity_ref", "player_number"):
            with self.subTest(field=field):
                for filename, raw in _rendered_bundle(with_participant=True).artifacts:
                    (self.bundle_dir / filename).write_bytes(raw)
                authority = _authority(self.bundle_dir)
                authority["action_participants"][0][field] = "   "
                if field == "player_number":
                    authority["training_projection"]["human_windows"][0][field] = "   "
                _rewrite_semantic_authority(self.bundle_dir, authority)

                participants_path = self.bundle_dir / "round-01-action-participants.csv"
                participant_rows = _csv_rows(participants_path.read_bytes())
                participant_rows[0][field] = "   "
                participants_path.write_bytes(
                    _test_csv_bytes(tuple(participant_rows[0]), participant_rows)
                )
                _refresh_export_hashes(self.bundle_dir, participants_path.name)
                if field == "player_number":
                    training_path = self.bundle_dir / "action_training_round_01.csv"
                    training_rows = _csv_rows(training_path.read_bytes())
                    training_rows[-2][field] = "   "
                    training_path.write_bytes(
                        _test_csv_bytes(tuple(training_rows[0]), training_rows)
                    )
                    _refresh_export_hashes(self.bundle_dir, training_path.name)

                with self.assertRaisesRegex(ValueError, "nonempty|participant"):
                    validate_result_bundle(self.bundle_dir)


def _csv_rows(raw: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline="")))


def _authority(bundle_dir: Path) -> dict[str, object]:
    return json.loads((bundle_dir / "round-01-results.json").read_bytes())


def _json_presentation(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _test_csv_bytes(
    fieldnames: tuple[str, ...], rows: list[dict[str, str]]
) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def _refresh_export_hashes(bundle_dir: Path, filename: str) -> None:
    authority_path = bundle_dir / "round-01-results.json"
    manifest_path = bundle_dir / "round-01-exports.manifest.json"
    authority = json.loads(authority_path.read_bytes())
    export_field = {
        "action_training_round_01.csv": "training_csv",
        "round-01-observations.csv": "observations_csv",
        "round-01-visibility-events.csv": "visibility_events_csv",
        "round-01-action-participants.csv": "action_participants_csv",
    }[filename]
    raw = (bundle_dir / filename).read_bytes()
    authority["exports"][export_field]["sha256"] = hashlib.sha256(raw).hexdigest()
    authority_path.write_bytes(_json_presentation(authority))
    manifest = json.loads(manifest_path.read_bytes())
    authority_raw = authority_path.read_bytes()
    for artifact in manifest["artifacts"]:
        if artifact["path"] == "round-01-results.json":
            artifact["sha256"] = hashlib.sha256(authority_raw).hexdigest()
            artifact["bytes"] = len(authority_raw)
        elif artifact["path"] == filename:
            artifact["sha256"] = hashlib.sha256(raw).hexdigest()
            artifact["bytes"] = len(raw)
    manifest_path.write_bytes(_json_presentation(manifest))


def _rewrite_semantic_authority(bundle_dir: Path, authority: dict[str, object]) -> None:
    authority_path = bundle_dir / "round-01-results.json"
    manifest_path = bundle_dir / "round-01-exports.manifest.json"
    semantic = dict(authority)
    del semantic["content_sha256"]
    del semantic["exports"]
    content_sha256 = hashlib.sha256(
        json.dumps(
            semantic,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    authority["content_sha256"] = content_sha256
    authority_path.write_bytes(_json_presentation(authority))
    authority_raw = authority_path.read_bytes()
    manifest = json.loads(manifest_path.read_bytes())
    manifest["content_sha256"] = content_sha256
    manifest["sources"] = authority["sources"]
    manifest["artifacts"][0]["sha256"] = hashlib.sha256(authority_raw).hexdigest()
    manifest["artifacts"][0]["bytes"] = len(authority_raw)
    manifest_path.write_bytes(_json_presentation(manifest))


def _staging_paths(output_dir: Path) -> list[Path]:
    if not output_dir.parent.exists():
        return []
    return list(output_dir.parent.glob(f".{output_dir.name}.staging-*"))


class _TracingIO:
    def __init__(self):
        self.delegate = outputs._PublicationIO()
        self.trace: list[str] = []

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    def rename(self, source: Path, destination: Path) -> None:
        self.trace.append("rename")
        self.delegate.rename(source, destination)


class _CountingIO:
    def __init__(self):
        self.delegate = outputs._PublicationIO()
        self.trace: list[str] = []

    def _call(self, operation: str, *args):
        self.trace.append(operation)
        return getattr(self.delegate, operation)(*args)

    def create_parent(self, path: Path) -> None:
        self._call("create_parent", path)

    def create_staging(self, path: Path) -> None:
        self._call("create_staging", path)

    def open_exclusive(self, path: Path):
        return self._call("open_exclusive", path)

    def write(self, handle, raw: bytes) -> None:
        self._call("write", handle, raw)

    def flush(self, handle) -> None:
        self._call("flush", handle)

    def fsync(self, handle) -> None:
        self._call("fsync", handle)

    def read(self, path: Path) -> bytes:
        return self._call("read", path)

    def rename(self, source: Path, destination: Path) -> None:
        self._call("rename", source, destination)


class _FailingIO:
    def __init__(self, fail_operation: str):
        self.delegate = outputs._PublicationIO()
        self.fail_operation = fail_operation
        self.failed = False

    def _call(self, operation: str, *args):
        if operation == self.fail_operation and not self.failed:
            self.failed = True
            raise OSError(operation)
        return getattr(self.delegate, operation)(*args)

    def create_parent(self, path: Path) -> None:
        self._call("create_parent", path)

    def create_staging(self, path: Path) -> None:
        self._call("create_staging", path)

    def open_exclusive(self, path: Path):
        return self._call("open_exclusive", path)

    def write(self, handle, raw: bytes) -> None:
        self._call("write", handle, raw)

    def flush(self, handle) -> None:
        self._call("flush", handle)

    def fsync(self, handle) -> None:
        self._call("fsync", handle)

    def read(self, path: Path) -> bytes:
        return self._call("read", path)

    def rename(self, source: Path, destination: Path) -> None:
        self._call("rename", source, destination)


def _rendered_bundle(
    note: str = "自由球",
    *,
    no_windows: bool = False,
    no_base_rows: bool = False,
    with_participant: bool = False,
):
    action = ActionObservation(
        "clip-001/action-001", "clip-001", 1, 4,
        {"review_label": "serve"}, {"review_label": "serve"},
        "serve", 1, 2, 101.0, 102.0, "far",
        "fully_occluded" if no_windows else "direct_clear",
        "direct_video", "timed", None, False, note, None, (),
    )
    sentinel = ActionObservation(
        "clip-002/action-001", "clip-002", 1, 5,
        {"review_label": "background"}, {"review_label": "background"},
        "background", None, None, None, None, "far", "direct_clear",
        "direct_video", None, "clip_sentinel", False, "", None, (),
    )
    outcome = OutcomeObservation(
        "result-test/outcome-001", (action.action_ref,), "continued", None,
        "referee_signal", "observed_or_inferred", "",
    )
    occlusion = VisibilityEvent(
        "result-test/occlusion-001", "occlusion", "far", 103.0, 104.0, 1.0,
        "timed", (action.action_ref,), ("result-test/source-001",),
        ((103.0, 104.0),), "screened",
    )
    participant = ActionParticipant(
        action.action_ref,
        "track-001",
        "player-008",
        "8",
        "primary_actor",
        "touched",
        "confirmed",
        0.9,
        (),
    )
    participants = (participant,) if with_participant else ()
    observations = ObservationSet(
        "result-test", (action, sentinel), (outcome,), (), (occlusion,), (), participants,
    )
    human_window = TrainingWindow(
        action.action_ref, action.clip_id, 101.0, 102.0, "serve", "serve",
        "far", (0, 0, 100, 50), "8" if with_participant else None,
        False, None, None, None, note,
    )
    generated_window = TrainingWindow(
        "clip-002/hard-negative-far-9", "clip-002", 201.0, 202.0,
        "background", "background", "far", (0, 0, 100, 50), None, True,
        9, "attack", 0.9, "",
    )
    projection = TrainingProjection(
        (
            (action.action_ref, TrainingDecision(
                "eligible", "serve", "direct_visual"
            )),
            (sentinel.action_ref, TrainingDecision(
                "excluded", None, "background_sentinel_only"
            )),
        ),
        (human_window,),
        (ProtectedInterval(action.action_ref, action.clip_id, "far", 101.0, 102.0, "human_observation"),),
        (generated_window,), 1, 1, 1,
    )
    if no_windows:
        projection = TrainingProjection(
            (
                (action.action_ref, TrainingDecision(
                    "excluded", None, "insufficient_visual_evidence"
                )),
                projection.decisions[1],
            ),
            (),
            projection.protected_intervals,
            (),
            0,
            0,
            0,
        )
    review = ValidatedReviewInput(
        "result-test", "rangitoto/round-01", "batch-test", "round-01", 1,
        ReviewSourceHashes("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64),
        ArtifactBinding("data/selection.json", "a" * 64),
        ArtifactBinding("data/review.json", "d" * 64),
        ArtifactBinding("outputs/review.xlsx", "b" * 64),
        ArtifactBinding("data/overrides.json", "c" * 64),
        ArtifactBinding("outputs/merged.json", "e" * 64),
        VideoBinding(
            "video-test", "data/video.mp4", "f" * 64, 25.0, 7500, 100, 100,
            300.0, {"far": (0, 0, 100, 50), "near": (0, 50, 100, 100)},
        ),
        {}, (
            {"action_ref": action.action_ref},
            {"action_ref": sentinel.action_ref},
        ), (), (), (), (), (), (),
    )
    return render_result_bundle(
        review=review,
        observations=observations,
        projection=projection,
        base_fieldnames=("video_path", "start_seconds", "end_seconds", "label", "split"),
        base_rows=() if no_base_rows else ({
            "video_path": "legacy.mp4", "start_seconds": "1", "end_seconds": "2",
            "label": "serve", "split": "train",
        },),
        base_manifest_binding=ArtifactBinding("data/base.csv", "9" * 64),
        settings=BundleSettings(
            "spike-trace/0.1.0", "legacy-match", "review-match",
            {"kind": "repo_relative", "path": "data"}, "video.mp4", False,
            0.5, 1, 7,
        ),
    )


if __name__ == "__main__":
    unittest.main()
