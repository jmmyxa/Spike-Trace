import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import {
  assertStableInput,
  normalizeRepoPath,
  parseJsonObjectStrict,
  publishJsonNoReplace,
  readInputSnapshot,
  sha256,
} from "./active_review_io.mjs";
import {
  loadEvidenceOverrideEnvelope,
  validateEvidenceOverrideReferences,
} from "./active_review_evidence_overrides.mjs";

assert.equal(typeof loadEvidenceOverrideEnvelope, "function");
assert.equal(typeof validateEvidenceOverrideReferences, "function");

const root = await fs.mkdtemp(path.join(os.tmpdir(), "active-review-evidence-"));
try {
  const inputPath = path.join(root, "selection.json");
  const inputBytes = Buffer.from('{"selection":{"path":"a"}}\n', "utf8");
  await fs.writeFile(inputPath, inputBytes);
  const snapshot = await readInputSnapshot(inputPath, "Selection");
  assert.equal(snapshot.path, inputPath);
  assert.deepEqual(snapshot.bytes, inputBytes);
  assert.equal(snapshot.sha256, sha256(inputBytes));
  await assertStableInput(inputPath, inputBytes, "Selection");
  assert.equal(normalizeRepoPath(inputPath, root), "selection.json");

  const duplicate = Buffer.from(
    '{"selection":{"path":"a"},"selection":{"path":"b"}}',
    "utf8",
  );
  assert.throws(
    () => parseJsonObjectStrict(duplicate, "override"),
    /override contains duplicate key "selection"/,
  );
  assert.throws(() => parseJsonObjectStrict(Buffer.from([0x7b, 0x22, 0x78, 0x22, 0x3a, 0xc3, 0x28, 0x7d]), "encoding"), /encoding contains invalid UTF-8/);
  assert.throws(() => parseJsonObjectStrict(Buffer.from("[]"), "array"), /array contains invalid JSON/);
  assert.deepEqual(parseJsonObjectStrict(Buffer.from('{"items":[{"x":1},{"x":1}]}'), "valid"), { items: [{ x: 1 }, { x: 1 }] });

  const expectedDraft = { format_version: 1, selection: { path: "selection.json" }, clips: [] };
  const outputPath = path.join(root, "draft.json");
  const expectedBytes = Buffer.from(`${JSON.stringify(expectedDraft, null, 2)}\n`, "utf8");
  assert.deepEqual(await publishJsonNoReplace(outputPath, expectedDraft), expectedBytes);
  assert.deepEqual(await fs.readFile(outputPath), expectedBytes);

  const failureCases = [
    ["open", { open: async () => { throw new Error("open failure"); } }],
    ["write", { open: async (...args) => { const handle = await fs.open(...args); return { writeFile: async () => { throw new Error("write failure"); }, sync: handle.sync.bind(handle), close: handle.close.bind(handle) }; } }],
    ["sync", { open: async (...args) => { const handle = await fs.open(...args); return { writeFile: handle.writeFile.bind(handle), sync: async () => { throw new Error("sync failure"); }, close: handle.close.bind(handle) }; } }],
    ["reread", { readFile: async () => Buffer.from("changed") }],
    ["publish", { link: async () => { throw new Error("publish failure"); } }],
  ];
  for (const [name, io] of failureCases) {
    const target = path.join(root, `${name}.json`);
    await assert.rejects(() => publishJsonNoReplace(target, expectedDraft, { io }));
    assert.equal(await fs.stat(target).then(() => true).catch(() => false), false, `${name} target must not publish`);
    assert.equal((await fs.readdir(root)).some((entry) => entry.startsWith(`.${name}.json.tmp-`)), false, `${name} temp must be removed`);
  }

  const collisionPath = path.join(root, "collision.json");
  const collisionTemporary = path.join(root, `.collision.json.tmp-${process.pid}-fixed`);
  const collisionBytes = Buffer.from("pre-existing sibling", "utf8");
  await fs.writeFile(collisionTemporary, collisionBytes);
  await assert.rejects(() => publishJsonNoReplace(collisionPath, expectedDraft, { io: { randomUUID: () => "fixed" } }));
  assert.deepEqual(await fs.readFile(collisionTemporary), collisionBytes);
  await fs.unlink(collisionTemporary);

  const competingPath = path.join(root, "competing.json");
  const competingBytes = Buffer.from("competing writer", "utf8");
  await assert.rejects(() => publishJsonNoReplace(competingPath, expectedDraft, {
    beforePublish: async () => fs.writeFile(competingPath, competingBytes, { flag: "wx" }),
  }));
  assert.deepEqual(await fs.readFile(competingPath), competingBytes);
  assert.equal((await fs.readdir(root)).some((entry) => entry.startsWith(".competing.json.tmp-")), false);

  const selectionValue = {
    batch_id: "batch-1", round_id: "round-01",
    video: { path: "video.mp4", sha256: "a".repeat(64) },
    clips: [{ clip_id: "clip-001", duration_seconds: 5 }],
  };
  const selectionSnapshot = Buffer.from(JSON.stringify(selectionValue), "utf8");
  const workbookSnapshot = Buffer.from("workbook", "utf8");
  const overrideValue = {
    format: "spiketrace.active-review-evidence-overrides", format_version: 1,
    review_set_key: "review/round-01", batch_id: "batch-1", round_id: "round-01",
    selection: { path: "selection.json", sha256: sha256(selectionSnapshot) },
    workbook: { path: "review.xlsx", sha256: sha256(workbookSnapshot) },
    video: { path: "video.mp4", sha256: "a".repeat(64) },
    workbook_compatibility: { trimmed_banner_cells: [], shared_formula_ranges: [], validation_import_gaps: [], read_only_repairs: [] },
    action_overrides: [], supplemental_actions: [], outcome_observations: [], visibility_observations: [], action_participants: [],
  };
  const bound = await loadEvidenceOverrideEnvelope(path.join(root, "override.json"), {
    overrideBytes: Buffer.from(JSON.stringify(overrideValue)), selection: selectionValue,
    selectionPath: path.join(root, "selection.json"), selectionBytes: selectionSnapshot,
    workbookPath: path.join(root, "review.xlsx"), workbookBytes: workbookSnapshot, repoRoot: root,
  });
  assert.deepEqual(Object.keys(bound).sort(), ["overrideBytes", "overridePath", "overrideRepoPath", "overrideSha256", "payload", "selectionBinding", "videoBinding", "workbookBinding"].sort());
  const validated = validateEvidenceOverrideReferences(bound, { selection: selectionValue, canonicalActionRows: [] });
  assert.deepEqual(validated.sourceRepairs, []);
  const loadVariant = (value) => loadEvidenceOverrideEnvelope(path.join(root, "override.json"), {
    overrideBytes: Buffer.from(JSON.stringify(value)), selection: selectionValue,
    selectionPath: path.join(root, "selection.json"), selectionBytes: selectionSnapshot,
    workbookPath: path.join(root, "review.xlsx"), workbookBytes: workbookSnapshot, repoRoot: root,
  });
  await assert.rejects(() => loadVariant({ ...overrideValue, unknown: true }), /Evidence override unknown is unknown/);
  await assert.rejects(() => loadVariant({ ...overrideValue, review_set_key: " \t\/round-01" }), /Evidence override review_set_key/);
  await assert.rejects(() => loadVariant({ ...overrideValue, workbook: { ...overrideValue.workbook, sha256: "A".repeat(64) } }), /Evidence override workbook.sha256/);
  const duplicateGap = { sheet: "人工动作", range: "E4:E483", validation_kind: "list", expected_rule: { type: "list", values: ["background", "serve", "receive", "set", "attack", "block", "dig"] } };
  await assert.rejects(() => loadVariant({ ...overrideValue, workbook_compatibility: { ...overrideValue.workbook_compatibility, validation_import_gaps: [duplicateGap, duplicateGap] } }), /validation_import_gaps.*duplicate target/);
  const action = { action_ref: "clip-001/action-001", expected_source: { review_label: "serve", relative_start_seconds: 1, relative_end_seconds: 2, team_side: "far", note: null }, replacement_review_label: "attack", visibility: null, evidence_basis: null, replacement_note: null, reason: "manual correction" };
  await assert.rejects(() => loadVariant({ ...overrideValue, action_overrides: [{ ...action, replacement_review_label: "bogus" }] }), /replacement_review_label/);
  const actionBound = await loadVariant({ ...overrideValue, action_overrides: [action] });
  await assert.rejects(async () => validateEvidenceOverrideReferences(actionBound, { selection: selectionValue, canonicalActionRows: [{ action_ref: "clip-001/action-001", clip_id: "clip-001", source_action_slot: 1, raw_values: { review_label: "receive", relative_start_seconds: 1, relative_end_seconds: 2, team_side: "far", note: null } }] }), /expected_source.review_label/);
  await assert.rejects(() => loadVariant({ ...overrideValue, action_overrides: [{ ...action, action_ref: "clip-001/action-001/action-002" }] }), /action_overrides\[0\]\.action_ref/);
  const supplemental = { supplemental_index: 1, clip_id: "clip-001", review_label: "free_ball", relative_start_seconds: 1, relative_end_seconds: 2, team_side: "near", visibility: "direct_clear", evidence_basis: "direct_video", interval_scope: "timed", note: "", reason: "manual addition" };
  const supplementalBound = await loadVariant({ ...overrideValue, supplemental_actions: [supplemental], outcome_observations: [{ outcome_index: 2, related_action_refs: ["clip-001/supplemental-001"], outcome: "continued", result_type: null, evidence_basis: "direct_video", status: "observed_or_inferred", note: "" }] });
  await assert.rejects(async () => validateEvidenceOverrideReferences(supplementalBound, { selection: selectionValue, canonicalActionRows: [] }), /outcome_observations\[0\]\.outcome_index/);
  await assert.rejects(() => loadVariant({ ...overrideValue, outcome_observations: [{ outcome_index: 1, related_action_refs: ["clip-001/action-001"], outcome: "continued", result_type: null, evidence_basis: "direct_video", status: "observed_or_inferred", note: "" }] }).then((b) => validateEvidenceOverrideReferences(b, { selection: selectionValue, canonicalActionRows: [] })), /does not resolve to an action/);
  const participant = { action_ref: "clip-001/action-001", track_id: null, identity_ref: null, player_number: null, participation: "support", touch_status: "unknown", assignment_status: "unresolved", assignment_confidence: null, evidence: [] };
  await assert.rejects(() => loadVariant({ ...overrideValue, action_participants: [{ ...participant, assignment_status: "confirmed" }] }), /action_participants\[0\]/);
  await assert.rejects(() => loadEvidenceOverrideEnvelope(path.join(root, "override.json"), {
    overrideBytes: Buffer.from(JSON.stringify({ ...overrideValue, workbook: { path: "../review.xlsx", sha256: sha256(workbookSnapshot) } })), selection: selectionValue,
    selectionPath: path.join(root, "selection.json"), selectionBytes: selectionSnapshot,
    workbookPath: path.join(root, "review.xlsx"), workbookBytes: workbookSnapshot, repoRoot: root,
  }), /Evidence override workbook.path/);
} finally {
  await fs.rm(root, { recursive: true, force: true });
}
