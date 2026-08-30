import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
import { parseJsonObjectStrict, sha256, sha256File as sharedSha256File } from "./active_review_io.mjs";
import { verifyWorkbookSemantics } from "./active_review_workbook_semantics.mjs";
import { validateEvidenceOverrideReferences } from "./active_review_evidence_overrides.mjs";

export const SHEET_NAMES = ["短片清单", "人工动作", "候选提示", "标签说明"];
export const ACTIONS = ["background", "serve", "receive", "set", "attack", "block", "dig"];
export const SIDES = ["far", "near"];
export const ACTION_SLOTS_PER_CLIP = 12;
const SHEET_BANNERS = [
  ["短片清单", "主动学习短片清单", "只读投影；请用相对链接播放同目录 clips 文件夹中的代理短片。选择理由会自动换行。"],
  ["人工动作", "人工动作", "只填写浅黄色的五列。每条记录使用当前短片的相对整秒；没有状态列或勾选框。"],
  ["候选提示", "候选提示", "只读模型提示；它们是参考，不是人工审核结果。"],
  ["标签说明", "标签说明", "请先阅读再填写“人工动作”页。"],
];
export const CLIP_HEADERS = [
  "序号", "短片ID", "播放短片", "代理文件", "片段长度(秒)", "原视频开始",
  "原视频结束", "时间分层", "选择桶", "选择原因", "候选提示数",
];
export const HINT_HEADERS = [
  "短片ID", "候选ID", "相对开始秒", "相对结束秒", "预测动作", "置信度",
  "观察裁剪", "重复组", "冲突组", "来源候选ID",
];
export const ACTION_HEADERS = [
  "短片ID", "动作序号", "播放短片", "片段长度(秒)", "人工确认动作",
  "片段内开始秒", "片段内结束秒", "人工侧别", "备注",
];

function invariant(condition, message) {
  if (!condition) throw new Error(message);
}

function blank(value) {
  return value === null || value === undefined || value === "";
}

function normalize(value) {
  return blank(value) ? null : value;
}

function normalizeRows(rows) {
  return rows.map((row) => row.map(normalize));
}

export const sha256File = sharedSha256File;

function pathInside(root, value, label) {
  invariant(typeof value === "string" && value.length > 0, `${label} must be a non-empty path.`);
  invariant(!path.isAbsolute(value), `${label} must be relative.`);
  const resolved = path.resolve(root, value);
  invariant(resolved === root || resolved.startsWith(`${root}${path.sep}`), `${label} escapes its root.`);
  return resolved;
}

function inspectedSheetNames(inspection) {
  return String(inspection.ndjson ?? "")
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line))
    .map((record) => record?.name)
    .filter((name) => typeof name === "string");
}

function listValidation(range, context) {
  const values = range.dataValidation?.rule?.values ?? range.dataValidation?.values;
  invariant(Array.isArray(values), `${context} list validation is missing.`);
  return values;
}

function wholeNumberValidation(range, context) {
  const rule = range.dataValidation?.rule;
  invariant(rule?.type === "whole", `${context} whole-number validation is missing.`);
  invariant(rule.operator === "greaterThanOrEqual" && Number(rule.formula1) === 0, `${context} must allow whole numbers from zero.`);
}

function exactUsedRange(sheet, rowCount, columnCount, context) {
  const range = sheet.getUsedRange();
  assert.deepEqual(
    [range.rowIndex, range.columnIndex, range.rowCount, range.columnCount],
    [0, 0, rowCount, columnCount],
    `${context} used range`,
  );
}

export function selectionProjection(selection) {
  invariant(selection?.format_version === 1, "Selection must use format version 1.");
  invariant(Array.isArray(selection.clips) && selection.clips.length === 40, "Selection must contain exactly 40 clips.");
  const clipIds = new Set();
  const clipRows = [];
  const actionRows = [];
  const hintRows = [];
  for (const [index, clip] of selection.clips.entries()) {
    invariant(clip && typeof clip === "object", `Clip ${index + 1} must be an object.`);
    const clipId = clip.clip_id;
    invariant(typeof clipId === "string" && clipId.length > 0, `Clip ${index + 1} has no clip_id.`);
    invariant(!clipIds.has(clipId), `Clip IDs must be unique; duplicate ${clipId}.`);
    clipIds.add(clipId);
    invariant(clip.ordinal === index + 1, `Clip ${clipId} ordinal must be ${index + 1}.`);
    const duration = clip.duration_seconds;
    invariant(Number.isFinite(duration) && duration > 0, `Clip ${clipId} duration must be positive.`);
    const hyperlink = `=HYPERLINK("clips/${clipId}.mp4","播放")`;
    const hints = Array.isArray(clip.candidate_hints) ? clip.candidate_hints : [];
    clipRows.push([
      clip.ordinal, clipId, null, `clips/${clipId}.mp4`, duration, clip.start_seconds, clip.end_seconds,
      clip.time_stratum ?? null, clip.selection_bucket ?? null,
      Array.isArray(clip.selection_reasons) ? clip.selection_reasons.join("\n") : null, hints.length,
    ]);
    for (let slot = 1; slot <= ACTION_SLOTS_PER_CLIP; slot += 1) {
      actionRows.push([clipId, slot, null, duration, null, null, null, null, null]);
    }
    for (const hint of hints) {
      hintRows.push([
        clipId, hint.canonical_event_id, hint.relative_start_seconds, hint.relative_end_seconds,
        hint.action, hint.confidence, Array.isArray(hint.observed_sides) ? hint.observed_sides.join("|") : null,
        hint.duplicate_group_id ?? null, hint.conflict_group_id ?? null,
        Array.isArray(hint.source_candidate_ids) ? hint.source_candidate_ids.join("|") : null,
      ]);
    }
  }
  return { clipRows, actionRows, hintRows };
}

export async function verifyProxyBatch(selectionPath, batchDir, { repoRoot = process.cwd(), selectionBytes: suppliedSelectionBytes } = {}) {
  const absoluteSelection = path.resolve(selectionPath);
  const absoluteBatch = path.resolve(batchDir);
  const selectionBytes = suppliedSelectionBytes ?? await fs.readFile(absoluteSelection);
  const selection = parseJsonObjectStrict(selectionBytes, "Selection");
  const projection = selectionProjection(selection);
  const manifestPath = path.join(absoluteBatch, "proxy-manifest.json");
  const manifest = parseJsonObjectStrict(await fs.readFile(manifestPath), "Proxy manifest");
  const selectionHash = sha256(selectionBytes);
  invariant(manifest.format_version === 1, "Proxy manifest must use format version 1.");
  invariant(manifest.selection_sha256 === selectionHash, "Proxy manifest selection SHA-256 does not match selection.");
  invariant(manifest.selection === path.relative(repoRoot, absoluteSelection).split(path.sep).join("/"), "Proxy manifest selection path does not match selection.");
  invariant(manifest.video?.sha256 === selection.video?.sha256, "Proxy manifest video SHA-256 does not match selection.");
  const videoPath = pathInside(path.resolve(repoRoot), selection.video?.path, "Selection source video path");
  invariant(await sha256File(videoPath) === selection.video.sha256, "Selection source video SHA-256 does not match source video.");
  invariant(Array.isArray(manifest.clips) && manifest.clips.length === 40, "Proxy manifest must contain 40 clips.");
  for (const [index, entry] of manifest.clips.entries()) {
    const clip = selection.clips[index];
    invariant(entry.clip_id === clip.clip_id, `Proxy manifest clip ${index + 1} ID does not match selection.`);
    invariant(entry.ordinal === clip.ordinal, `Proxy manifest clip ${entry.clip_id} ordinal does not match selection.`);
    invariant(entry.path === `clips/${clip.clip_id}.mp4`, `Proxy path for ${clip.clip_id} is not canonical.`);
    const proxyPath = pathInside(absoluteBatch, entry.path, `Proxy path for ${clip.clip_id}`);
    invariant(await sha256File(proxyPath) === entry.sha256, `Proxy SHA-256 does not match ${entry.path}.`);
  }
  return { selection, manifest, projection };
}

async function importWorkbookSnapshot(workbookPath, workbookBytes) {
  const temporary = path.join(
    path.dirname(workbookPath),
    `.${path.basename(workbookPath)}.snapshot-${process.pid}-${crypto.randomUUID()}`,
  );
  let handle;
  try {
    handle = await fs.open(temporary, "wx");
    await handle.writeFile(workbookBytes);
    await handle.sync();
    await handle.close();
    handle = undefined;
    return await SpreadsheetFile.importXlsx(await FileBlob.load(temporary));
  } finally {
    if (handle) await handle.close().catch(() => undefined);
    await fs.unlink(temporary).catch((error) => {
      if (error?.code !== "ENOENT") throw error;
    });
  }
}

export async function scanFormulaErrors(workbook, context = "workbook") {
  const inspection = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!",
    options: { useRegex: true, maxResults: 300 },
    summary: `${context} formula-error scan`,
  });
  const records = String(inspection.ndjson ?? "").split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
  const matches = records.filter((record) => record?.type === "match" || record?.kind === "match" || record?.match !== undefined);
  invariant(matches.length === 0, `${context} contains formula errors.`);
}

function assertManualRows(sheet, { allowManualValues }) {
  const rows = sheet.getRange("E4:I483").values;
  for (const [index, row] of rows.entries()) {
    const [action, start, end, side, note] = row.map(normalize);
    if (!allowManualValues) {
      invariant([action, start, end, side, note].every((value) => value === null), `Manual row ${index + 4} must be blank.`);
      continue;
    }
    if ([action, start, end, side, note].every((value) => value === null)) continue;
    invariant(ACTIONS.includes(action), `Manual row ${index + 4} needs an allowed action.`);
    invariant(side === null || SIDES.includes(side), `Manual row ${index + 4} needs far or near.`);
    if (action === "background") {
      invariant(start === null && end === null || (Number.isInteger(start) && start >= 0 && Number.isInteger(end) && end > start), `Manual background row ${index + 4} needs paired times or both cells blank.`);
    } else {
      invariant(Number.isFinite(start) && Number.isInteger(start) && start >= 0 && Number.isFinite(end) && Number.isInteger(end) && end >= 0, `Manual row ${index + 4} needs non-negative finite whole-second times.`);
    }
    invariant(note === null || typeof note === "string", `Manual row ${index + 4} note must be text.`);
  }
}

export async function verifyWorkbookFile(selectionPath, workbookPath, { allowManualValues = false, selectionBytes: suppliedSelectionBytes = null, workbookBytes: suppliedWorkbookBytes = null, boundEvidenceOverrides = null } = {}) {
  const absoluteSelection = path.resolve(selectionPath);
  const absoluteWorkbook = path.resolve(workbookPath);
  const selectionBytes = suppliedSelectionBytes ?? await fs.readFile(absoluteSelection);
  const workbookBytes = suppliedWorkbookBytes ?? await fs.readFile(absoluteWorkbook);
  invariant(selectionBytes instanceof Uint8Array && workbookBytes instanceof Uint8Array, "Input snapshots must be UTF-8 bytes.");
  if (boundEvidenceOverrides) {
    const expectedSelectionPath = path.relative(process.cwd(), absoluteSelection).split(path.sep).join("/");
    const expectedWorkbookPath = path.relative(process.cwd(), absoluteWorkbook).split(path.sep).join("/");
    invariant(boundEvidenceOverrides.selectionBinding?.path === expectedSelectionPath && boundEvidenceOverrides.selectionBinding?.sha256 === sha256(selectionBytes), "Bound evidence selection binding does not match supplied snapshot.");
    invariant(boundEvidenceOverrides.workbookBinding?.path === expectedWorkbookPath && boundEvidenceOverrides.workbookBinding?.sha256 === sha256(workbookBytes), "Bound evidence workbook binding does not match supplied snapshot.");
  }
  const { selection, manifest, projection } = await verifyProxyBatch(absoluteSelection, path.dirname(absoluteWorkbook), { selectionBytes });
  const workbook = await importWorkbookSnapshot(absoluteWorkbook, workbookBytes);
  const sheetInspection = await workbook.inspect({ kind: "sheet", include: "name", maxChars: 2000 });
  assert.deepEqual(inspectedSheetNames(sheetInspection), SHEET_NAMES, "Workbook sheet order/count must be exact.");
  const clips = workbook.worksheets.getItem("短片清单");
  const actions = workbook.worksheets.getItem("人工动作");
  const hints = workbook.worksheets.getItem("候选提示");
  const labels = workbook.worksheets.getItem("标签说明");
  const actionRows = actions.getRange("A4:I483").values;
  const semantic = await verifyWorkbookSemantics(workbook, selection, projection, sha256(workbookBytes), actionRows, { boundEvidenceOverrides, requirePopulatedSources: allowManualValues });
  assertManualRows(actions, { allowManualValues });
  await scanFormulaErrors(workbook, "Workbook");
  const result = { batch_id: manifest.batch_id, clip_count: selection.clips.length };
  Object.defineProperties(result, {
    selection: { value: selection },
    selectionBytes: { value: selectionBytes },
    workbookBytes: { value: workbookBytes },
    actionRows: { value: actionRows },
    canonicalActionRows: { value: semantic.canonicalActionRows },
    normalizationAudit: { value: semantic.normalizationAudit },
    compatibilityAudit: { value: semantic.compatibility },
    boundEvidenceOverrides: { value: boundEvidenceOverrides },
  });
  if (boundEvidenceOverrides) validateEvidenceOverrideReferences(boundEvidenceOverrides, { selection, canonicalActionRows: semantic.canonicalActionRows });
  return result;
}

async function main() {
  const [selectionPath, workbookPath] = process.argv.slice(2);
  invariant(selectionPath && workbookPath && process.argv.length === 4, "Usage: node tools/verify_active_review_batch.mjs SELECTION_JSON OUTPUT_DIR/review.xlsx");
  const result = await verifyWorkbookFile(selectionPath, workbookPath, { allowManualValues: true });
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error.stack ?? error.message}\n`);
    process.exitCode = 1;
  });
}
