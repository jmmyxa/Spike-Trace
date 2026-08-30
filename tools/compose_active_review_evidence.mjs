import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  assertStableInput,
  parseJsonObjectStrict,
  publishJsonNoReplace,
  readInputSnapshot,
  sha256,
  sha256File,
} from "./active_review_io.mjs";
import {
  loadEvidenceOverrideEnvelope,
  validateEvidenceOverrideReferences,
} from "./active_review_evidence_overrides.mjs";

const FORMAT = "spiketrace.active-review-evidence-input";
const USAGE = "Usage: node tools/compose_active_review_evidence.mjs SELECTION_JSON REVIEW_XLSX EVIDENCE_OVERRIDES_JSON OUTPUT_JSON";

function invariant(condition, message) {
  if (!condition) throw new Error(message);
}

function integer(value, message) {
  invariant(Number.isInteger(value) && value >= 0, message);
  return value;
}

function duration(value, message) {
  invariant(typeof value === "number" && Number.isFinite(value) && value > 0, message);
  return value;
}

function fullVideo(selection) {
  const video = selection?.video;
  invariant(video && typeof video === "object" && !Array.isArray(video), "Selection video must be an object.");
  invariant(typeof video.video_id === "string" && video.video_id.length > 0, "Selection video_id is invalid.");
  invariant(typeof video.path === "string" && video.path.length > 0, "Selection video path is invalid.");
  invariant(/^[0-9a-f]{64}$/.test(video.sha256), "Selection video SHA-256 is invalid.");
  duration(video.fps, "Selection video fps is invalid.");
  integer(video.frame_count, "Selection video frame count is invalid.");
  const width = integer(video.width, "Selection video width is invalid.");
  const height = integer(video.height, "Selection video height is invalid.");
  duration(video.duration_seconds, "Selection video duration is invalid.");
  invariant(video.crops && typeof video.crops === "object" && !Array.isArray(video.crops), "Selection video crops are invalid.");
  const crops = {};
  for (const side of ["far", "near"]) {
    const crop = video.crops[side];
    invariant(Array.isArray(crop) && crop.length === 4, `Selection video ${side} crop is invalid.`);
    const [x1, y1, x2, y2] = crop.map((value) => integer(value, `Selection video ${side} crop is invalid.`));
    invariant(x1 < x2 && x2 <= width && y1 < y2 && y2 <= height, `Selection video ${side} crop is invalid.`);
    crops[side] = [x1, y1, x2, y2];
  }
  return { video_id: video.video_id, path: video.path, sha256: video.sha256, fps: video.fps, frame_count: video.frame_count, width, height, duration_seconds: video.duration_seconds, crops };
}

export function deriveResultSetId({ batchId, roundId, selectionSha256, workbookSha256, evidenceOverridesSha256 }) {
  for (const [name, value] of Object.entries({ batchId, roundId, selectionSha256, workbookSha256, evidenceOverridesSha256 })) {
    invariant(typeof value === "string" && value.length > 0, `Result set ${name} is invalid.`);
  }
  return `active-review-${sha256(Buffer.from(`${batchId}\n${roundId}\n${selectionSha256}\n${workbookSha256}\n${evidenceOverridesSha256}`, "utf8")).slice(0, 24)}`;
}

function absoluteTime(clip, relative) {
  return relative === null ? null : clip.start_seconds + relative;
}

function actionObservation(row, clip, override) {
  const values = row.normalized_values;
  const timed = values.relative_start_seconds !== null;
  invariant(timed || row.background_scope === "clip_sentinel", `Source action ${row.action_ref} has invalid interval scope.`);
  const reviewLabel = override?.replacement_review_label ?? values.review_label;
  const visibility = override?.visibility ?? "direct_clear";
  const evidenceBasis = override?.evidence_basis ?? "direct_video";
  const note = override?.replacement_note ?? values.note;
  return {
    action_ref: row.action_ref,
    clip_id: row.clip_id,
    source_action_slot: row.source_action_slot,
    source_row: row.source_row,
    raw_values: row.raw_values,
    normalized_values: row.normalized_values,
    review_label: reviewLabel,
    relative_start_seconds: values.relative_start_seconds,
    relative_end_seconds: values.relative_end_seconds,
    start_seconds: absoluteTime(clip, values.relative_start_seconds),
    end_seconds: absoluteTime(clip, values.relative_end_seconds),
    team_side: values.team_side,
    visibility,
    evidence_basis: evidenceBasis,
    interval_scope: timed ? "timed" : null,
    background_scope: row.background_scope,
    side_inherited: row.side_inherited,
    note,
    source_reason: override?.reason ?? null,
    source_repairs: row.source_repairs,
  };
}

function supplementalObservation(row, clip) {
  const timed = row.interval_scope === "timed";
  return {
    action_ref: `${row.clip_id}/supplemental-${String(row.supplemental_index).padStart(3, "0")}`,
    clip_id: row.clip_id,
    source_action_slot: null,
    source_row: null,
    raw_values: null,
    normalized_values: null,
    review_label: row.review_label,
    relative_start_seconds: row.relative_start_seconds,
    relative_end_seconds: row.relative_end_seconds,
    start_seconds: timed ? absoluteTime(clip, row.relative_start_seconds) : clip.start_seconds,
    end_seconds: timed ? absoluteTime(clip, row.relative_end_seconds) : clip.end_seconds,
    team_side: row.team_side,
    visibility: row.visibility,
    evidence_basis: row.evidence_basis,
    interval_scope: row.interval_scope,
    background_scope: null,
    side_inherited: false,
    note: row.note,
    source_reason: row.reason,
    source_repairs: [],
  };
}

function visibilityObservation(row, clip, resultSetId, index) {
  const timed = row.interval_scope === "timed";
  return {
    visibility_ref: `${resultSetId}/${row.event_kind}-source-${String(index + 1).padStart(3, "0")}`,
    event_kind: row.event_kind,
    clip_id: row.clip_id,
    team_side: row.team_side,
    start_seconds: timed ? absoluteTime(clip, row.relative_start_seconds) : clip.start_seconds,
    end_seconds: timed ? absoluteTime(clip, row.relative_end_seconds) : clip.end_seconds,
    interval_scope: row.interval_scope,
    related_action_refs: row.related_action_refs,
    note: row.note,
    source_reason: row.reason,
  };
}

function covers(action, observation) {
  const expected = action.visibility === "fully_occluded" ? "occlusion" : "off_camera";
  return observation.event_kind === expected
    && observation.clip_id === action.clip_id
    && observation.team_side === action.team_side
    && observation.related_action_refs.includes(action.action_ref)
    && observation.start_seconds <= (action.start_seconds ?? observation.start_seconds)
    && observation.end_seconds >= (action.end_seconds ?? observation.end_seconds);
}

export function composeEvidenceSynthesisInput({ selection, canonicalActionRows, validatedOverrides, normalizationAudit }) {
  invariant(selection && Array.isArray(selection.clips), "Selection clips are invalid.");
  invariant(validatedOverrides?.bound, "Validated evidence overrides are required.");
  invariant(Array.isArray(canonicalActionRows) && Array.isArray(normalizationAudit), "Verified workbook semantics are required.");
  const clips = new Map(selection.clips.map((clip) => [clip.clip_id, clip]));
  const bound = validatedOverrides.bound;
  const resultSetId = deriveResultSetId({ batchId: selection.batch_id, roundId: selection.round_id, selectionSha256: bound.selectionBinding.sha256, workbookSha256: bound.workbookBinding.sha256, evidenceOverridesSha256: bound.overrideSha256 });
  const overrides = new Map(validatedOverrides.actionOverrides.map((row) => [row.action_ref, row]));
  const source = canonicalActionRows.map((row) => {
    const clip = clips.get(row.clip_id);
    invariant(clip, `Source action ${row.action_ref} names an unknown clip.`);
    return actionObservation(row, clip, overrides.get(row.action_ref));
  });
  const supplemental = validatedOverrides.supplementalActions.map((row) => {
    const clip = clips.get(row.clip_id);
    invariant(clip, `Supplemental action ${row.clip_id} names an unknown clip.`);
    return supplementalObservation(row, clip);
  });
  const actions = [...source, ...supplemental];
  const visibility = validatedOverrides.visibilityObservations.map((row, index) => visibilityObservation(row, clips.get(row.clip_id), resultSetId, index));
  for (const action of actions) {
    if (["off_camera", "fully_occluded"].includes(action.visibility)) invariant(visibility.some((entry) => covers(action, entry)), `Action ${action.action_ref} lacks matching ${action.visibility} visibility coverage.`);
  }
  return {
    format: FORMAT,
    format_version: 2,
    result_set_id: resultSetId,
    review_set_key: bound.payload.review_set_key,
    batch_id: selection.batch_id,
    round_id: selection.round_id,
    selection: { path: bound.selectionBinding.path, sha256: bound.selectionBinding.sha256 },
    workbook: { path: bound.workbookBinding.path, sha256: bound.workbookBinding.sha256 },
    evidence_overrides: { path: bound.overrideRepoPath, sha256: bound.overrideSha256 },
    video: fullVideo(selection),
    time_precision_seconds: 1,
    source_review_rows: canonicalActionRows,
    source_repairs: validatedOverrides.sourceRepairs,
    action_observations: actions,
    outcome_observations: validatedOverrides.outcomes.map((row, index) => ({ outcome_ref: `${resultSetId}/outcome-${String(index + 1).padStart(3, "0")}`, related_action_refs: row.related_action_refs, outcome: row.outcome, result_type: row.result_type, evidence_basis: row.evidence_basis, status: row.status, note: row.note })),
    visibility_observations: visibility,
    action_participants: validatedOverrides.participants.map((row) => ({ action_ref: row.action_ref, track_id: row.track_id, identity_ref: row.identity_ref, player_number: row.player_number, participation: row.participation, touch_status: row.touch_status, assignment_status: row.assignment_status, assignment_confidence: row.assignment_confidence, evidence: row.evidence })),
    normalization_audit: normalizationAudit,
  };
}

export async function composeActiveReviewEvidence(selectionPath, workbookPath, overridePath, outputPath, { repoRoot = process.cwd(), io = {}, afterVerification } = {}) {
  const root = path.resolve(repoRoot);
  const selectionSnapshot = await readInputSnapshot(selectionPath, "Selection");
  const workbookSnapshot = await readInputSnapshot(workbookPath, "Workbook");
  const overrideSnapshot = await readInputSnapshot(overridePath, "Evidence override");
  const selection = parseJsonObjectStrict(selectionSnapshot.bytes, "Selection");
  const boundEvidenceOverrides = await loadEvidenceOverrideEnvelope(overrideSnapshot.path, {
    overrideBytes: overrideSnapshot.bytes,
    selection,
    selectionPath: selectionSnapshot.path,
    selectionBytes: selectionSnapshot.bytes,
    workbookPath: workbookSnapshot.path,
    workbookBytes: workbookSnapshot.bytes,
    repoRoot: root,
  });
  const { verifyWorkbookFile } = await import("./verify_active_review_batch.mjs");
  const verifiedWorkbook = await verifyWorkbookFile(selectionSnapshot.path, workbookSnapshot.path, {
    allowManualValues: true,
    selectionBytes: selectionSnapshot.bytes,
    workbookBytes: workbookSnapshot.bytes,
    boundEvidenceOverrides,
  });
  const validatedOverrides = validateEvidenceOverrideReferences(boundEvidenceOverrides, { selection: verifiedWorkbook.selection, canonicalActionRows: verifiedWorkbook.canonicalActionRows });
  const payload = composeEvidenceSynthesisInput({ selection: verifiedWorkbook.selection, canonicalActionRows: verifiedWorkbook.canonicalActionRows, validatedOverrides, normalizationAudit: verifiedWorkbook.normalizationAudit });
  if (afterVerification) await afterVerification(payload);
  const sourceVideo = path.resolve(root, fullVideo(verifiedWorkbook.selection).path);
  await publishJsonNoReplace(outputPath, payload, {
    io,
    beforePublish: async () => {
      await assertStableInput(selectionSnapshot.path, selectionSnapshot.bytes, "Selection");
      await assertStableInput(workbookSnapshot.path, workbookSnapshot.bytes, "Workbook");
      await assertStableInput(overrideSnapshot.path, overrideSnapshot.bytes, "Evidence override");
      invariant(await sha256File(sourceVideo) === fullVideo(verifiedWorkbook.selection).sha256, "Source video changed during composition.");
    },
  });
  return payload;
}

export async function main(argv = process.argv.slice(2)) {
  assert.equal(argv.length, 4, USAGE);
  const payload = await composeActiveReviewEvidence(...argv);
  process.stdout.write(`${JSON.stringify({ result_set_id: payload.result_set_id, action_count: payload.action_observations.length })}\n`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error.stack ?? error.message}\n`);
    process.exitCode = 1;
  });
}
