import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  ACTION_SLOTS_PER_CLIP,
  ACTIONS,
  SIDES,
  verifyWorkbookFile,
} from "./verify_active_review_batch.mjs";
import {
  assertStableInput,
  normalizeRepoPath,
  publishJsonNoReplace,
  sha256,
} from "./active_review_io.mjs";

function invariant(condition, message) {
  if (!condition) throw new Error(message);
}

function blank(value) {
  return value === null || value === undefined || value === "";
}

function normalizeClipActions(clip, rows) {
  const clipId = clip.clip_id;
  const duration = clip.duration_seconds;
  invariant(Number.isFinite(duration) && duration > 0, `Clip ${clipId} has an invalid duration.`);
  const populated = [];
  for (const [slotIndex, row] of rows.entries()) {
    const [action, start, end, side, note] = row;
    if ([action, start, end, side, note].every(blank)) continue;
    invariant(!blank(action), `Clip ${clipId} action slot ${slotIndex + 1} has manual values without an action.`);
    invariant(ACTIONS.includes(action), `Clip ${clipId} action slot ${slotIndex + 1} has an invalid action.`);
    invariant(SIDES.includes(side), `Clip ${clipId} action slot ${slotIndex + 1} must select far or near.`);
    invariant(blank(note) || typeof note === "string", `Clip ${clipId} action slot ${slotIndex + 1} note must be text.`);
    populated.push({ action, start, end, side, note: blank(note) ? "" : note });
  }
  invariant(populated.length > 0, `Clip ${clipId} has no completed action rows.`);
  invariant(populated.length < ACTION_SLOTS_PER_CLIP, `Clip ${clipId} uses all ${ACTION_SLOTS_PER_CLIP} action slots; request a deliberately expanded workbook version.`);

  const backgrounds = populated.filter((row) => row.action === "background");
  invariant(backgrounds.length <= 1, `Clip ${clipId} may contain only one background row.`);
  invariant(!(backgrounds.length && populated.length > 1), `Clip ${clipId} cannot mix background with team actions.`);
  if (backgrounds.length) {
    const background = backgrounds[0];
    invariant(blank(background.start) && blank(background.end), `Clip ${clipId} background must leave both time cells blank.`);
    return [{
      action: "background",
      relative_start_seconds: 0,
      relative_end_seconds: duration,
      team_side: background.side,
      note: background.note,
    }];
  }

  return populated.map((row, actionIndex) => {
    invariant(Number.isFinite(row.start) && Number.isInteger(row.start), `Clip ${clipId} action ${actionIndex + 1} start must be a finite whole second.`);
    invariant(Number.isFinite(row.end) && Number.isInteger(row.end), `Clip ${clipId} action ${actionIndex + 1} end must be a finite whole second.`);
    invariant(row.start >= 0 && row.start < row.end && row.end <= duration, `Clip ${clipId} action ${actionIndex + 1} must stay within the clip with start before end.`);
    return {
      action: row.action,
      relative_start_seconds: row.start,
      relative_end_seconds: row.end,
      team_side: row.side,
      note: row.note,
    };
  });
}

export async function extractActiveReviewResults(selectionPath, workbookPath, outputPath, { io, afterVerification } = {}) {
  const verified = await verifyWorkbookFile(selectionPath, workbookPath, { allowManualValues: true });
  if (afterVerification) await afterVerification();
  const { selection, selectionBytes, workbookBytes, actionRows } = verified;
  invariant(Array.isArray(selection.clips) && selection.clips.length === 40, "Selection must contain exactly 40 clips.");

  const clips = selection.clips.map((clip, clipIndex) => {
    const offset = clipIndex * ACTION_SLOTS_PER_CLIP;
    const actions = normalizeClipActions(clip, actionRows.slice(offset, offset + ACTION_SLOTS_PER_CLIP).map((row) => row.slice(4)));
    return {
      clip_id: clip.clip_id,
      ordinal: clip.ordinal,
      source_start_seconds: clip.start_seconds,
      source_end_seconds: clip.end_seconds,
      actions,
    };
  });
  const draft = {
    format_version: 1,
    batch_id: selection.batch_id,
    round_id: selection.round_id,
    selection: normalizeRepoPath(selectionPath),
    selection_sha256: sha256(selectionBytes),
    workbook: { path: normalizeRepoPath(workbookPath), sha256: sha256(workbookBytes) },
    video: { path: selection.video.path, sha256: selection.video.sha256 },
    time_precision_seconds: 1,
    clips,
  };
  await assertStableInput(selectionPath, selectionBytes, "Selection");
  await assertStableInput(workbookPath, workbookBytes, "Workbook");
  await publishJsonNoReplace(outputPath, draft, { io });
  return draft;
}

async function main() {
  const [selectionPath, workbookPath, outputPath] = process.argv.slice(2);
  invariant(selectionPath && workbookPath && outputPath && process.argv.length === 5, "Usage: node tools/extract_active_review_results.mjs SELECTION_JSON REVIEW_XLSX OUTPUT_DRAFT_JSON");
  const draft = await extractActiveReviewResults(selectionPath, workbookPath, outputPath);
  process.stdout.write(`${JSON.stringify({ batch_id: draft.batch_id, round_id: draft.round_id, clips: draft.clips.length, output: normalizeRepoPath(outputPath) })}\n`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error.stack ?? error.message}\n`);
    process.exitCode = 1;
  });
}
