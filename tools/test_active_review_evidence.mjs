import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

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
import { verifyWorkbookSemantics } from "./active_review_workbook_semantics.mjs";
import { buildActiveReviewBatch } from "./build_active_review_batch.mjs";
import { createSyntheticSelection } from "./active_review_synthetic_fixture.mjs";
import {
  composeEvidenceSynthesisInput,
  composeActiveReviewEvidence,
  deriveResultSetId,
} from "./compose_active_review_evidence.mjs";

assert.equal(typeof loadEvidenceOverrideEnvelope, "function");
assert.equal(typeof validateEvidenceOverrideReferences, "function");
assert.equal(typeof composeEvidenceSynthesisInput, "function");
assert.equal(typeof composeActiveReviewEvidence, "function");
assert.equal(typeof deriveResultSetId, "function");

const SEMANTIC_ACTIONS = ["background", "serve", "receive", "set", "attack", "block", "dig"];
const semanticSelection = {
  format_version: 1,
  clips: Array.from({ length: 40 }, (_, index) => ({ clip_id: `round-01-clip-${String(index + 1).padStart(3, "0")}`, ordinal: index + 1, duration_seconds: 10, start_seconds: 0, end_seconds: 10, candidate_hints: [] })),
};
const semanticProjection = {
  clipRows: semanticSelection.clips.map((clip) => [clip.ordinal, clip.clip_id, null, `clips/${clip.clip_id}.mp4`, clip.duration_seconds, 0, 10, null, null, null, 0]),
  actionRows: semanticSelection.clips.flatMap((clip) => Array.from({ length: 12 }, (_, slot) => [clip.clip_id, slot + 1, null, clip.duration_seconds, null, null, null, null, null])),
  hintRows: [],
};
const semanticBanners = [
  ["主动学习短片清单", "只读投影；请用相对链接播放同目录 clips 文件夹中的代理短片。选择理由会自动换行。"],
  ["人工动作", "只填写浅黄色的五列。每条记录使用当前短片的相对整秒；没有状态列或勾选框。"],
  ["候选提示", "只读模型提示；它们是参考，不是人工审核结果。"],
  ["标签说明", "请先阅读再填写“人工动作”页。"],
];
const semanticLabels = [["规则", "说明"], ["receive / dig", "receive 仅指接对方发球的一传；dig 仅指对方进攻后的防守起球。"], ["相对秒", "片段内开始秒和结束秒必须填写非负整数，单位是相对当前短片的秒。"], ["background", "background 必须单独使用，且开始秒与结束秒两个单元格都必须保持空白。"], ["人工侧别", "background 仍须选择当前 far 或 near 的球队裁剪；far/near 只表示画面远端/近端裁剪，不表示球队身份。"], ["完成方式", "没有状态列、审核列或 checkbox；填写人工确认动作即是人工记录。"], ["容量", "每个短片固定 12 个动作槽。若 12 槽都不足，请不要覆盖现有行，应请求扩容版本。"], ["代理文件", "代理短片无音频且已降分辨率，只用于便携复核。"]];

const compositionSelection = {
  batch_id: "batch-1", round_id: "round-01",
  video: { video_id: "video-1", path: "video.mp4", sha256: "a".repeat(64), fps: 30, frame_count: 300, width: 100, height: 50, duration_seconds: 10, crops: { far: [0, 0, 100, 25], near: [0, 25, 100, 50] } },
  clips: [{ clip_id: "clip-001", start_seconds: 100, end_seconds: 110, duration_seconds: 10 }],
};
const compositionRows = [
  { action_ref: "clip-001/action-001", clip_id: "clip-001", source_action_slot: 1, source_row: 4, raw_values: { clip_id: "clip-001", review_label: "receive", relative_start_seconds: 1, relative_end_seconds: 2, team_side: "far", note: null }, normalized_values: { clip_id: "clip-001", review_label: "receive", relative_start_seconds: 1, relative_end_seconds: 2, team_side: "far", note: null }, background_scope: null, side_inherited: false, source_repairs: [] },
  { action_ref: "clip-001/action-002", clip_id: "clip-001", source_action_slot: 2, source_row: 5, raw_values: { clip_id: "clip-001", review_label: "dig", relative_start_seconds: 3, relative_end_seconds: 4, team_side: "near", note: "被拦回的保护" }, normalized_values: { clip_id: "clip-001", review_label: "dig", relative_start_seconds: 3, relative_end_seconds: 4, team_side: "near", note: "被拦回的保护" }, background_scope: null, side_inherited: true, source_repairs: [] },
  { action_ref: "clip-001/action-003", clip_id: "clip-001", source_action_slot: 3, source_row: 6, raw_values: { clip_id: "clip-001", review_label: "background", relative_start_seconds: 5, relative_end_seconds: 6, team_side: "near", note: "source background" }, normalized_values: { clip_id: "clip-001", review_label: "background", relative_start_seconds: 5, relative_end_seconds: 6, team_side: "near", note: "source background" }, background_scope: "timed_interval", side_inherited: false, source_repairs: [] },
];
const compositionBound = { overrideSha256: "c".repeat(64), overrideRepoPath: "evidence.json", selectionBinding: { path: "selection.json", sha256: "a".repeat(64) }, workbookBinding: { path: "review.xlsx", sha256: "b".repeat(64) }, payload: { review_set_key: "review/round-01" } };
const compositionOverrides = {
  bound: compositionBound, sourceRepairs: [],
  actionOverrides: [
    { action_ref: "clip-001/action-002", replacement_review_label: "background", visibility: "off_camera", evidence_basis: "sequence_context", replacement_note: "sequence context", reason: "block-cover context" },
    { action_ref: "clip-001/action-003", replacement_review_label: "attack", visibility: null, evidence_basis: null, replacement_note: null, reason: "visible action" },
  ],
  supplementalActions: [{ supplemental_index: 1, clip_id: "clip-001", review_label: "free_ball", relative_start_seconds: 5, relative_end_seconds: 6, team_side: "near", visibility: "direct_clear", evidence_basis: "direct_video", interval_scope: "timed", note: "added", reason: "missed action" }],
  outcomes: [{ related_action_refs: ["clip-001/supplemental-001"], outcome: "point_lost", result_type: "free_ball_error", evidence_basis: "direct_video", status: "observed_or_inferred", note: "result" }],
  visibilityObservations: [{ event_kind: "off_camera", clip_id: "clip-001", team_side: "near", relative_start_seconds: 3, relative_end_seconds: 4, interval_scope: "timed", related_action_refs: ["clip-001/action-002"], note: "camera moved", reason: "visible gap" }], participants: [],
};
const compositionAudit = [{ kind: "side_inheritance", clip_id: "clip-001", action_ref: "clip-001/action-002", source_row: 5, raw_value: null, normalized_value: "near", reason: "inherit side" }];
const compositionPayload = composeEvidenceSynthesisInput({ selection: compositionSelection, canonicalActionRows: compositionRows, validatedOverrides: compositionOverrides, normalizationAudit: compositionAudit });
assert.deepEqual(Object.keys(compositionPayload), ["format", "format_version", "result_set_id", "review_set_key", "batch_id", "round_id", "selection", "workbook", "evidence_overrides", "video", "time_precision_seconds", "source_review_rows", "source_repairs", "action_observations", "outcome_observations", "visibility_observations", "action_participants", "normalization_audit"]);
assert.equal(compositionPayload.format, "spiketrace.active-review-evidence-input");
assert.equal(compositionPayload.format_version, 2);
assert.equal(compositionPayload.time_precision_seconds, 1);
assert.equal(compositionPayload.action_observations[0].visibility, "direct_clear");
assert.equal(compositionPayload.action_observations[0].evidence_basis, "direct_video");
assert.equal(compositionPayload.action_observations[0].note, "");
assert.equal(compositionPayload.source_review_rows[0].normalized_values.note, null);
assert.deepEqual(compositionPayload.action_observations[0].raw_values, compositionRows[0].raw_values);
assert.equal(compositionPayload.action_observations[1].review_label, "background");
assert.equal(compositionPayload.action_observations[1].background_scope, "timed_interval");
assert.equal(compositionPayload.action_observations[1].source_reason, "block-cover context");
assert.equal(compositionPayload.action_observations[1].start_seconds, 103);
assert.equal(compositionPayload.source_review_rows[1].normalized_values.review_label, "dig");
assert.equal(compositionPayload.source_review_rows[1].background_scope, null);
assert.equal(compositionPayload.action_observations[2].review_label, "attack");
assert.equal(compositionPayload.action_observations[2].background_scope, null);
assert.equal(compositionPayload.source_review_rows[2].normalized_values.review_label, "background");
assert.equal(compositionPayload.source_review_rows[2].background_scope, "timed_interval");
assert.equal(compositionPayload.action_observations[3].action_ref, "clip-001/supplemental-001");
assert.equal(compositionPayload.action_observations[3].source_action_slot, null);
assert.equal(compositionPayload.visibility_observations[0].source_reason, "visible gap");
assert.match(compositionPayload.visibility_observations[0].visibility_ref, /off_camera-source-001$/);
assert.match(compositionPayload.outcome_observations[0].outcome_ref, /outcome-001$/);
assert.deepEqual(compositionPayload.normalization_audit, compositionAudit);
assert.equal("training_decision" in compositionPayload, false);
assert.equal(compositionPayload.result_set_id, "batch-1/result-549fd199e806acab");
assert.equal(compositionPayload.result_set_id, deriveResultSetId({ batchId: "batch-1", roundId: "round-01", selectionSha256: "a".repeat(64), workbookSha256: "b".repeat(64), evidenceOverridesSha256: "c".repeat(64) }));
assert.throws(() => composeEvidenceSynthesisInput({ selection: compositionSelection, canonicalActionRows: compositionRows, validatedOverrides: { ...compositionOverrides, visibilityObservations: [] }, normalizationAudit: compositionAudit }), /lacks matching off_camera visibility coverage/);

async function verifyRealEvidence(argv) {
  assert.equal(argv.length, 5, "Usage: node tools/test_active_review_evidence.mjs --real SELECTION_JSON REVIEW_XLSX EVIDENCE_OVERRIDES_JSON REVIEW_INPUT_JSON");
  const [, selectionPath, workbookPath, overridePath, reviewInputPath] = argv;
  const [selectionSnapshot, workbookSnapshot, overrideSnapshot, reviewInputBytes] = await Promise.all([
    readInputSnapshot(selectionPath, "Selection"),
    readInputSnapshot(workbookPath, "Workbook"),
    readInputSnapshot(overridePath, "Evidence override"),
    fs.readFile(reviewInputPath),
  ]);
  const selection = parseJsonObjectStrict(selectionSnapshot.bytes, "Selection");
  const boundEvidenceOverrides = await loadEvidenceOverrideEnvelope(overrideSnapshot.path, {
    overrideBytes: overrideSnapshot.bytes, selection,
    selectionPath: selectionSnapshot.path, selectionBytes: selectionSnapshot.bytes,
    workbookPath: workbookSnapshot.path, workbookBytes: workbookSnapshot.bytes,
    repoRoot: process.cwd(),
  });
  const { verifyWorkbookFile } = await import("./verify_active_review_batch.mjs");
  const verifiedWorkbook = await verifyWorkbookFile(selectionSnapshot.path, workbookSnapshot.path, {
    allowManualValues: true, selectionBytes: selectionSnapshot.bytes, workbookBytes: workbookSnapshot.bytes, boundEvidenceOverrides,
  });
  const validatedOverrides = validateEvidenceOverrideReferences(boundEvidenceOverrides, { selection: verifiedWorkbook.selection, canonicalActionRows: verifiedWorkbook.canonicalActionRows });
  const payload = composeEvidenceSynthesisInput({ selection: verifiedWorkbook.selection, canonicalActionRows: verifiedWorkbook.canonicalActionRows, validatedOverrides, normalizationAudit: verifiedWorkbook.normalizationAudit });
  const rendered = Buffer.from(`${JSON.stringify(payload, null, 2)}\n`, "utf8");
  assert.deepEqual(rendered, reviewInputBytes);
  assert.equal(payload.selection.sha256, selectionSnapshot.sha256);
  assert.equal(payload.workbook.sha256, workbookSnapshot.sha256);
  assert.equal(payload.evidence_overrides.sha256, overrideSnapshot.sha256);
  assert.equal(payload.selection.path, normalizeRepoPath(selectionSnapshot.path));
  assert.equal(payload.workbook.path, normalizeRepoPath(workbookSnapshot.path));
  assert.equal(payload.video.path, selection.video.path);
  assert.equal(payload.selection.path, normalizeRepoPath(selectionSnapshot.path));
  assert.equal(payload.workbook.path, normalizeRepoPath(workbookSnapshot.path));
  assert.equal(verifiedWorkbook.selection.clips.length, 40);
  assert.equal(payload.source_review_rows.length, 83);
  assert.equal(payload.source_repairs.length, 1);
  assert.equal(payload.action_observations.filter((row) => row.source_action_slot !== null).length, 83);
  assert.equal(new Set(payload.source_review_rows.map((row) => row.action_ref)).size, 83);
  const sourceActions = payload.action_observations.filter((row) => row.source_action_slot !== null);
  assert.ok(sourceActions.every((row) => Number.isInteger(row.source_action_slot) && row.source_action_slot > 0));
  assert.equal(new Set(sourceActions.map((row) => row.action_ref)).size, 83);
  assert.ok(payload.action_observations.filter((row) => row.source_action_slot === null).every((row) => row.action_ref.includes("/supplemental-") && row.source_action_slot === null));
  const inherited = sourceActions.filter((row) => row.side_inherited);
  assert.equal(inherited.length, 42);
  assert.equal(new Set(inherited.map((row) => row.clip_id)).size, 14);
  assert.ok(inherited.every((row) => row.team_side === "near" && row.raw_values.team_side === null));
  assert.ok(payload.action_observations.every((row) => row.visibility && row.evidence_basis));
  const actionsFor = (suffix) => payload.action_observations.filter((row) => row.clip_id === `round-01-clip-${suffix}`);
  const visibleFor = (suffix) => payload.visibility_observations.filter((row) => row.clip_id === `round-01-clip-${suffix}`);
  const clip006 = actionsFor("006");
  assert.deepEqual(clip006.filter((row) => row.source_action_slot === 1).map((row) => [row.review_label, row.relative_start_seconds, row.relative_end_seconds]), [["background", 0, 9]]);
  assert.deepEqual(clip006.filter((row) => row.source_action_slot !== null).map((row) => row.source_action_slot), [1, 2, 3, 4]);
  for (const suffix of ["009", "010"]) {
    const excludedServe = actionsFor(suffix).find((row) => row.review_label === "serve" && row.visibility === "off_camera");
    assert.ok(excludedServe);
    assert.ok(visibleFor(suffix).some((row) => row.event_kind === "off_camera" && row.related_action_refs.includes(excludedServe.action_ref)));
  }
  assert.equal(actionsFor("011").filter((row) => row.source_action_slot === null && row.review_label === "block" && row.visibility === "direct_clear").length, 1);
  assert.equal(payload.action_participants.filter((row) => row.action_ref.startsWith("round-01-clip-011/")).length, 0);
  const clip017 = actionsFor("017").filter((row) => row.review_label === "free_ball");
  assert.ok(clip017.length === 1 && ((clip017[0].interval_scope === "timed" && clip017[0].visibility === "direct_clear") || (clip017[0].interval_scope === "clip_bounds" && clip017[0].visibility === "unresolved")));
  const clip018 = actionsFor("018").find((row) => row.review_label === "free_ball" && row.relative_start_seconds === 8 && row.relative_end_seconds === 9 && row.visibility === "direct_clear");
  assert.ok(clip018);
  assert.ok(payload.outcome_observations.some((row) => row.outcome === "point_lost" && row.result_type === "free_ball_error" && row.related_action_refs.includes(clip018.action_ref)));
  assert.equal(actionsFor("023").filter((row) => row.review_label === "dig" && row.visibility === "direct_clear").length, 0);
  const clip024 = actionsFor("024").find((row) => row.source_action_slot === 5);
  assert.deepEqual([clip024.review_label, clip024.relative_start_seconds, clip024.relative_end_seconds], ["free_ball", 11, 12]);
  const clip034Serve = actionsFor("034").find((row) => row.review_label === "serve");
  assert.ok(clip034Serve && clip034Serve.visibility === "direct_clear" && clip034Serve.evidence_basis === "direct_video");
  assert.ok(payload.outcome_observations.some((row) => row.evidence_basis === "referee_signal" && row.related_action_refs.includes(clip034Serve.action_ref)));
  assert.equal(actionsFor("035").filter((row) => row.visibility === "fully_occluded").length, 0);
  assert.ok(visibleFor("035").some((row) => row.event_kind === "occlusion" && row.interval_scope === "clip_bounds"));
  for (const suffix of ["007", "014"]) assert.ok(actionsFor(suffix).every((row) => row.source_action_slot !== null));
  await assertStableInput(selectionSnapshot.path, selectionSnapshot.bytes, "Selection");
  await assertStableInput(workbookSnapshot.path, workbookSnapshot.bytes, "Workbook");
  await assertStableInput(overrideSnapshot.path, overrideSnapshot.bytes, "Evidence override");
}

if (process.argv[2] === "--real") await verifyRealEvidence(process.argv.slice(2));

async function composerFixture() {
  const pipelineRoot = path.resolve(".");
  const repoRoot = await fs.mkdtemp(path.join(path.join(pipelineRoot, "tests"), ".active-review-evidence-root-"));
  await fs.copyFile(path.join(pipelineRoot, "pyproject.toml"), path.join(repoRoot, "pyproject.toml"));
  const fixtureRoot = path.join(repoRoot, "input");
  await fs.mkdir(fixtureRoot);
  const selectionPath = await createSyntheticSelection(fixtureRoot, { repoRoot, pipelineRoot });
  const batchDir = path.join(fixtureRoot, "batch");
  const previousCwd = process.cwd();
  try { process.chdir(repoRoot); await buildActiveReviewBatch(selectionPath, batchDir, path.join(fixtureRoot, "previews")); } finally { process.chdir(previousCwd); }
  const workbookPath = path.join(batchDir, "review.xlsx");
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
  const actions = workbook.worksheets.getItem("人工动作");
  for (let index = 0; index < 40; index += 1) actions.getRange(`E${4 + index * 12}:H${4 + index * 12}`).values = [["background", null, null, "near"]];
  await (await SpreadsheetFile.exportXlsx(workbook)).save(workbookPath);
  const overridePath = path.join(fixtureRoot, "override.json");
  const selectionBytes = await fs.readFile(selectionPath);
  const workbookBytes = await fs.readFile(workbookPath);
  const selection = parseJsonObjectStrict(selectionBytes, "Synthetic selection");
  const override = { format: "spiketrace.active-review-evidence-overrides", format_version: 1, review_set_key: "review/round-01", batch_id: selection.batch_id, round_id: selection.round_id, selection: { path: normalizeRepoPath(selectionPath, repoRoot), sha256: sha256(selectionBytes) }, workbook: { path: normalizeRepoPath(workbookPath, repoRoot), sha256: sha256(workbookBytes) }, video: { path: selection.video.path, sha256: selection.video.sha256 }, workbook_compatibility: { trimmed_banner_cells: [], shared_formula_ranges: [], validation_import_gaps: [], read_only_repairs: [] }, action_overrides: [], supplemental_actions: [], outcome_observations: [], visibility_observations: [], action_participants: [] };
  await fs.writeFile(overridePath, JSON.stringify(override));
  return { fixtureRoot, repoRoot, selectionPath, workbookPath, overridePath, videoPath: path.join(repoRoot, selection.video.path), io: {} };
}

async function assertNoComposerLeak(fixture, outputPath) {
  assert.equal(await fs.stat(outputPath).then(() => true).catch(() => false), false);
  assert.equal((await fs.readdir(path.dirname(outputPath))).some((entry) => entry.startsWith(`.${path.basename(outputPath)}.tmp-`)), false);
}

for (const [name, mutate] of [
  ["selection", async (fixture) => fs.writeFile(fixture.selectionPath, "changed")],
  ["workbook", async (fixture) => fs.writeFile(fixture.workbookPath, "changed")],
  ["override", async (fixture) => fs.writeFile(fixture.overridePath, "changed")],
  ["video", async (fixture) => fs.writeFile(fixture.videoPath, "changed")],
]) {
  const fixture = await composerFixture();
  try {
    const outputPath = path.join(fixture.fixtureRoot, `output-${name}.json`);
    await assert.rejects(() => composeActiveReviewEvidence(fixture.selectionPath, fixture.workbookPath, fixture.overridePath, outputPath, { repoRoot: fixture.repoRoot, io: fixture.io, afterVerification: () => mutate(fixture) }), /changed during (extraction|composition)/);
    await assertNoComposerLeak(fixture, outputPath);
  } finally { await fs.rm(fixture.repoRoot, { recursive: true, force: true }); }
}

for (const [name, makeIo] of [
  ["write", () => ({ open: async (...args) => { const handle = await fs.open(...args); return { writeFile: async () => { throw new Error("write failure"); }, sync: handle.sync.bind(handle), close: handle.close.bind(handle) }; } })],
  ["sync", () => ({ open: async (...args) => { const handle = await fs.open(...args); return { writeFile: handle.writeFile.bind(handle), sync: async () => { throw new Error("sync failure"); }, close: handle.close.bind(handle) }; } })],
  ["publish", () => ({ link: async () => { throw new Error("publish failure"); } })],
]) {
  const fixture = await composerFixture();
  try {
    const outputPath = path.join(fixture.fixtureRoot, `${name}.json`);
    await assert.rejects(() => composeActiveReviewEvidence(fixture.selectionPath, fixture.workbookPath, fixture.overridePath, outputPath, { repoRoot: fixture.repoRoot, io: { ...fixture.io, ...makeIo() } }));
    await assertNoComposerLeak(fixture, outputPath);
  } finally { await fs.rm(fixture.repoRoot, { recursive: true, force: true }); }
}

const firstComposer = await composerFixture();
const copiedRoot = await fs.mkdtemp(path.join(path.join(path.resolve("."), "tests"), ".active-review-evidence-copy-"));
try {
  const firstOutput = path.join(firstComposer.fixtureRoot, "result.json");
  await fs.cp(firstComposer.repoRoot, copiedRoot, { recursive: true });
  const relativeSelection = path.relative(firstComposer.repoRoot, firstComposer.selectionPath);
  const relativeWorkbook = path.relative(firstComposer.repoRoot, firstComposer.workbookPath);
  const relativeOverride = path.relative(firstComposer.repoRoot, firstComposer.overridePath);
  const secondOutput = path.join(copiedRoot, "input", "result-copy.json");
  await composeActiveReviewEvidence(firstComposer.selectionPath, firstComposer.workbookPath, firstComposer.overridePath, firstOutput, { repoRoot: firstComposer.repoRoot, io: firstComposer.io });
  await composeActiveReviewEvidence(path.join(copiedRoot, relativeSelection), path.join(copiedRoot, relativeWorkbook), path.join(copiedRoot, relativeOverride), secondOutput, { repoRoot: copiedRoot });
  assert.deepEqual(await fs.readFile(firstOutput), await fs.readFile(secondOutput));
  const collisionPath = path.join(firstComposer.fixtureRoot, "collision.json");
  await fs.writeFile(collisionPath, "existing");
  await assert.rejects(() => composeActiveReviewEvidence(firstComposer.selectionPath, firstComposer.workbookPath, firstComposer.overridePath, collisionPath, { repoRoot: firstComposer.repoRoot, io: firstComposer.io }));
  assert.deepEqual(await fs.readFile(collisionPath), Buffer.from("existing"));
  assert.equal((await fs.readdir(firstComposer.fixtureRoot)).some((entry) => entry.startsWith(".collision.json.tmp-")), false);
} finally {
  await fs.rm(firstComposer.repoRoot, { recursive: true, force: true });
  await fs.rm(copiedRoot, { recursive: true, force: true });
}

function semanticWorkbook({ mutate } = {}) {
  const clips = Array.from({ length: 43 }, () => Array(11).fill(null));
  const actions = Array.from({ length: 483 }, () => Array(9).fill(null));
  const hints = Array.from({ length: 3 }, () => Array(10).fill(null));
  const labels = Array.from({ length: 10 }, () => Array(2).fill(null));
  const clipFormulas = Array.from({ length: 43 }, () => Array(11).fill(null));
  const actionFormulas = Array.from({ length: 483 }, () => Array(9).fill(null));
  for (const [index, values] of semanticProjection.clipRows.entries()) { clips[index + 3] = [...values]; clips[index + 3][2] = "播放"; clipFormulas[index + 3][2] = `=HYPERLINK("clips/${values[1]}.mp4","播放")`; }
  for (const [index, values] of semanticProjection.actionRows.entries()) {
    actions[index + 3] = [...values];
    actions[index + 3][2] = "播放";
    actionFormulas[index + 3][2] = `=HYPERLINK("clips/${values[0]}.mp4","播放")`;
    if (index % 12 === 0) {
      actions[index + 3][4] = "background";
      actions[index + 3][7] = "near";
    }
  }
  clips[0][0] = semanticBanners[0][0]; clips[1][0] = semanticBanners[0][1]; clips[2] = ["序号", "短片ID", "播放短片", "代理文件", "片段长度(秒)", "原视频开始", "原视频结束", "时间分层", "选择桶", "选择原因", "候选提示数"];
  actions[0][0] = semanticBanners[1][0]; actions[1][0] = semanticBanners[1][1]; actions[2] = ["短片ID", "动作序号", "播放短片", "片段长度(秒)", "人工确认动作", "片段内开始秒", "片段内结束秒", "人工侧别", "备注"];
  hints[0][0] = semanticBanners[2][0]; hints[1][0] = semanticBanners[2][1]; hints[2] = ["短片ID", "候选ID", "相对开始秒", "相对结束秒", "预测动作", "置信度", "观察裁剪", "重复组", "冲突组", "来源候选ID"];
  labels[0][0] = semanticBanners[3][0]; labels[1][0] = semanticBanners[3][1]; labels.splice(2, 8, ...semanticLabels.map((row) => [...row]));
  const tables = [["短片清单", clips, clipFormulas], ["人工动作", actions, actionFormulas], ["候选提示", hints, Array.from({ length: 3 }, () => Array(10).fill(null))], ["标签说明", labels, Array.from({ length: 10 }, () => Array(2).fill(null))]];
  const validations = { E: { rule: { type: "list", values: SEMANTIC_ACTIONS } }, FG: { rule: { type: "whole", operator: "greaterThanOrEqual", formula1: 0 } }, H: { rule: { type: "list", values: ["far", "near"] } } };
  const sheet = (name, values, formulas) => ({ name, getUsedRange: () => ({ rowIndex: 0, columnIndex: 0, rowCount: values.length, columnCount: values[0].length, values, formulas }), getRange: (address) => ({ values: address === "A1" ? [[values[0][0]]] : address === "A2" ? [[values[1][0]]] : null, dataValidation: address === "E4:E483" ? validations.E : address === "F4:G483" ? validations.FG : address === "H4:H483" ? validations.H : null }) });
  const sheets = tables.map(([name, values, formulas]) => sheet(name, values, formulas));
  if (mutate) mutate({ clips, actions, hints, labels, clipFormulas, actionFormulas, sheets, validations });
  return { worksheets: { getItemAt: (index) => sheets[index] } };
}

async function semanticResult(options) {
  const workbook = semanticWorkbook(options);
  const actions = workbook.worksheets.getItemAt(1).getUsedRange().values.slice(3);
  return verifyWorkbookSemantics(workbook, semanticSelection, semanticProjection, "a".repeat(64), actions, options ?? {});
}

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
  const unauthorizedA1Trim = { sheet: "人工动作", cell: "A1", expected_value: "人工动作 ", actual_value: "人工动作" };
  await loadVariant({ ...overrideValue, workbook_compatibility: { ...overrideValue.workbook_compatibility, trimmed_banner_cells: [unauthorizedA1Trim] } });
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
  await assert.rejects(() => loadVariant({ ...overrideValue, outcome_observations: [{ outcome_index: 1, related_action_refs: [], outcome: "continued", result_type: null, evidence_basis: "direct_video", status: "observed_or_inferred", note: "" }] }), /outcome_observations\[0\]\.related_action_refs/);
  await assert.rejects(() => loadVariant({ ...overrideValue, visibility_observations: [{ visibility_index: 1, event_kind: "off_camera", clip_id: "clip-001", team_side: "far", relative_start_seconds: 1, relative_end_seconds: 2, interval_scope: "timed", related_action_refs: [], note: "", reason: "gap" }] }), /visibility_observations\[0\]\.related_action_refs/);
  const participant = { action_ref: "clip-001/action-001", track_id: null, identity_ref: null, player_number: null, participation: "support", touch_status: "unknown", assignment_status: "unresolved", assignment_confidence: null, evidence: [] };
  await assert.rejects(() => loadVariant({ ...overrideValue, action_participants: [{ ...participant, assignment_status: "confirmed" }] }), /action_participants\[0\]/);
  await assert.rejects(() => loadEvidenceOverrideEnvelope(path.join(root, "override.json"), {
    overrideBytes: Buffer.from(JSON.stringify({ ...overrideValue, workbook: { path: "../review.xlsx", sha256: sha256(workbookSnapshot) } })), selection: selectionValue,
    selectionPath: path.join(root, "selection.json"), selectionBytes: selectionSnapshot,
    workbookPath: path.join(root, "review.xlsx"), workbookBytes: workbookSnapshot, repoRoot: root,
  }), /Evidence override workbook.path/);

  const repair = { clip_id: "round-01-clip-002", source_action_slot: 1, sheet: "人工动作", cell: "A16", field: "clip_id", original_value: null, normalized_value: "round-01-clip-002", reason: "restore fixed identifier" };
  const semanticBound = { payload: { workbook_compatibility: { trimmed_banner_cells: [], shared_formula_ranges: [], validation_import_gaps: [], read_only_repairs: [repair] } } };
  const semantic = await semanticResult({ boundEvidenceOverrides: semanticBound, mutate: ({ actions }) => {
    actions[3][4] = "background"; actions[3][5] = 0; actions[3][6] = 1; actions[3][7] = "near";
    actions[4][4] = "attack"; actions[4][5] = 1; actions[4][6] = 2; actions[4][7] = null;
    actions[15][0] = null;
  } });
  const repaired = semantic.canonicalActionRows.find((row) => row.source_row === 16);
  assert.equal(repaired.raw_values.clip_id, null, "A16 raw value remains audited");
  assert.equal(repaired.normalized_values.clip_id, "round-01-clip-002");
  assert.deepEqual(repaired.source_repairs, [repair]);
  const inherited = semantic.canonicalActionRows.find((row) => row.source_row === 5);
  assert.equal(inherited.side_inherited, true);
  assert.equal(inherited.normalized_values.team_side, "near");
  assert.deepEqual(semantic.normalizationAudit, [
    { kind: "side_inheritance", clip_id: "round-01-clip-001", action_ref: "round-01-clip-001/action-002", source_row: 5, raw_value: null, normalized_value: "near", reason: "inherit side" },
    { kind: "read_only_repair", clip_id: "round-01-clip-002", action_ref: "round-01-clip-002/action-001", source_row: 16, raw_value: null, normalized_value: "round-01-clip-002", reason: "restore fixed identifier" },
  ]);
  assert.equal(semantic.canonicalActionRows.find((row) => row.source_row === 4).background_scope, "timed_interval");
  const overlapCases = [
    ["same-label exact duplicate", ["attack", 1, 3], ["attack", 1, 3]],
    ["different-label exact overlap", ["attack", 1, 3], ["dig", 1, 3]],
    ["partial overlap", ["attack", 1, 4], ["dig", 3, 5]],
    ["containment overlap", ["attack", 1, 5], ["dig", 2, 4]],
  ];
  for (const [name, first, second] of overlapCases) {
    const result = await semanticResult({ mutate: ({ actions }) => {
      actions[3].splice(4, 5, first[0], first[1], first[2], "near", "first");
      actions[4].splice(4, 5, second[0], second[1], second[2], null, "second");
    } });
    assert.deepEqual(result.canonicalActionRows.slice(0, 2), [
      {
        action_ref: "round-01-clip-001/action-001", clip_id: "round-01-clip-001", source_action_slot: 1, source_row: 4,
        raw_values: { clip_id: "round-01-clip-001", review_label: first[0], relative_start_seconds: first[1], relative_end_seconds: first[2], team_side: "near", note: "first" },
        normalized_values: { clip_id: "round-01-clip-001", review_label: first[0], relative_start_seconds: first[1], relative_end_seconds: first[2], team_side: "near", note: "first" },
        background_scope: null, side_inherited: false, source_repairs: [],
      },
      {
        action_ref: "round-01-clip-001/action-002", clip_id: "round-01-clip-001", source_action_slot: 2, source_row: 5,
        raw_values: { clip_id: "round-01-clip-001", review_label: second[0], relative_start_seconds: second[1], relative_end_seconds: second[2], team_side: null, note: "second" },
        normalized_values: { clip_id: "round-01-clip-001", review_label: second[0], relative_start_seconds: second[1], relative_end_seconds: second[2], team_side: "near", note: "second" },
        background_scope: null, side_inherited: true, source_repairs: [],
      },
    ], name);
  }
  await assert.rejects(() => semanticResult({ mutate: ({ actions }) => { actions[15][0] = null; } }), /Action read-only values/);
  await assert.rejects(() => semanticResult({ mutate: ({ labels }) => { labels[3][1] = "tampered"; } }), /Label instructions/);
  await assert.rejects(() => semanticResult({ mutate: ({ actions }) => { actions[2][4] = "tampered header"; } }), /Action headers/);
  await assert.rejects(() => semanticResult({ mutate: ({ clips }) => { clips[3][2] = "changed display"; } }), /Clip hyperlink display/);
  await assert.rejects(() => semanticResult({ mutate: ({ actions }) => { actions[3][2] = "changed display"; } }), /Action hyperlink display/);
  await assert.rejects(() => semanticResult({ mutate: ({ validations }) => { validations.E = { rule: { type: "list", values: ["attack"] } }; } }), /validation changed/);
  await assert.rejects(() => semanticResult({ mutate: ({ actions }) => { for (let index = 3; index < 15; index += 1) actions[index].splice(4, 5, null, null, null, null, null); } }), /zero populated source rows/);
  await assert.rejects(() => semanticResult({ mutate: ({ actions }) => { actions[3][5] = 0; actions[3][6] = 1; actions[4][4] = "attack"; actions[4][5] = 1; actions[4][6] = 2; actions[4][7] = "far"; } }), /conflicting sides/);
  await assert.rejects(() => semanticResult({ mutate: ({ actions }) => { actions[4].splice(4, 5, "attack", 1, 2, null, "mixed with sentinel"); } }), /untimed background must be the only populated row/);
  await assert.rejects(() => semanticResult({ mutate: ({ sheets }) => { sheets.push({ name: "extra", getUsedRange: () => ({ rowIndex: 0, columnIndex: 0, rowCount: 1, columnCount: 1, values: [[null]], formulas: [[null]] }), getRange: () => ({ values: [[null]], dataValidation: null }) }); } }), /sheet order\/count/);
  await assert.rejects(() => semanticResult({ mutate: ({ actionFormulas }) => { actionFormulas[3][2] = { formula: null, sharedFormula: { index: 2, ref: null, text: "" } }; actionFormulas[4][2] = { formula: '=HYPERLINK("clips/round-01-clip-001.mp4","播放")', sharedFormula: { index: 2, ref: "C4:C15", text: "" } }; for (let index = 5; index < 15; index += 1) actionFormulas[index][2] = { formula: null, sharedFormula: { index: 2, ref: null, text: "" } }; } }), /shared hyperlink/);
  const sharedBound = { payload: { workbook_compatibility: { trimmed_banner_cells: [], shared_formula_ranges: [{ sheet: "人工动作", range: "C4:C15", block_size: 12, expected_display_value: "播放" }], validation_import_gaps: [], read_only_repairs: [] } } };
  const a1Bound = { payload: { workbook_compatibility: { ...sharedBound.payload.workbook_compatibility, trimmed_banner_cells: [unauthorizedA1Trim] } } };
  await assert.rejects(() => semanticResult({ boundEvidenceOverrides: a1Bound }), /banner compatibility target/);
  await assert.rejects(() => semanticResult({ boundEvidenceOverrides: sharedBound, mutate: ({ actionFormulas }) => { actionFormulas[3][2] = { formula: null, sharedFormula: { index: 2, ref: "C4:C15", text: "" } }; for (let index = 4; index < 15; index += 1) actionFormulas[index][2] = { formula: null, sharedFormula: { index: 2, ref: null, text: "" } }; } }), /shared hyperlink/);
  await assert.rejects(() => semanticResult({ boundEvidenceOverrides: sharedBound, mutate: ({ actionFormulas }) => { for (let index = 3; index < 15; index += 1) actionFormulas[index][2] = { formula: null, sharedFormula: { index: 2, ref: null, text: "" } }; } }), /shared hyperlink/);
  await assert.rejects(() => semanticResult({ boundEvidenceOverrides: sharedBound, mutate: ({ actionFormulas }) => { actionFormulas[3][2] = { formula: '=HYPERLINK("clips/round-01-clip-001.mp4","播放")', sharedFormula: { index: 2, ref: "C4:C15", text: "" } }; actionFormulas[4][2] = { formula: '=HYPERLINK("clips/round-01-clip-001.mp4","播放")', sharedFormula: { index: 2, ref: "C4:C15", text: "" } }; for (let index = 5; index < 15; index += 1) actionFormulas[index][2] = { formula: null, sharedFormula: { index: 2, ref: null, text: "" } }; } }), /shared hyperlink/);
  await assert.rejects(() => semanticResult({ boundEvidenceOverrides: sharedBound, mutate: ({ actionFormulas }) => { actionFormulas[3][2] = { formula: null, sharedFormula: { index: 3, ref: null, text: "" } }; actionFormulas[4][2] = { formula: '=HYPERLINK("clips/round-01-clip-001.mp4","播放")', sharedFormula: { index: 2, ref: "C4:C15", text: "bad" } }; for (let index = 5; index < 15; index += 1) actionFormulas[index][2] = { formula: null, sharedFormula: { index: 2, ref: null, text: "" } }; } }), /shared hyperlink/);
  await assert.rejects(() => semanticResult({ boundEvidenceOverrides: { payload: { workbook_compatibility: { ...sharedBound.payload.workbook_compatibility, shared_formula_ranges: [{ sheet: "人工动作", range: "C5:C16", block_size: 12, expected_display_value: "播放" }] } } }, mutate: ({ actionFormulas }) => { actionFormulas[3][2] = { formula: null, sharedFormula: { index: 2, ref: null, text: "" } }; actionFormulas[4][2] = { formula: '=HYPERLINK("clips/round-01-clip-001.mp4","播放")', sharedFormula: { index: 2, ref: "C4:C15", text: "" } }; for (let index = 5; index < 15; index += 1) actionFormulas[index][2] = { formula: null, sharedFormula: { index: 2, ref: null, text: "" } }; } }), /shared hyperlink/);
  await assert.rejects(() => semanticResult({ boundEvidenceOverrides: { payload: { workbook_compatibility: { ...sharedBound.payload.workbook_compatibility, shared_formula_ranges: [{ sheet: "人工动作", range: "C4:C15", block_size: 11, expected_display_value: "播放" }] } } }, mutate: ({ actionFormulas }) => { actionFormulas[3][2] = { formula: null, sharedFormula: { index: 2, ref: null, text: "" } }; actionFormulas[4][2] = { formula: '=HYPERLINK("clips/round-01-clip-001.mp4","播放")', sharedFormula: { index: 2, ref: "C4:C15", text: "" } }; for (let index = 5; index < 15; index += 1) actionFormulas[index][2] = { formula: null, sharedFormula: { index: 2, ref: null, text: "" } }; } }), /shared hyperlink/);
  await semanticResult({ boundEvidenceOverrides: sharedBound, mutate: ({ actionFormulas }) => { actionFormulas[3][2] = { formula: null, sharedFormula: { index: 2, ref: null, text: "" } }; actionFormulas[4][2] = { formula: '=HYPERLINK("clips/round-01-clip-001.mp4","播放")', sharedFormula: { index: 2, ref: "C4:C15", text: "" } }; for (let index = 5; index < 15; index += 1) actionFormulas[index][2] = { formula: null, sharedFormula: { index: 2, ref: null, text: "" } }; } });
} finally {
  await fs.rm(root, { recursive: true, force: true });
}
