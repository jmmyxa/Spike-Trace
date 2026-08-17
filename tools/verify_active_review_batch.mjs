import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

export const SHEET_NAMES = ["短片清单", "人工动作", "候选提示", "标签说明"];
export const ACTIONS = ["background", "serve", "receive", "set", "attack", "block", "dig"];
export const SIDES = ["far", "near"];
export const ACTION_SLOTS_PER_CLIP = 12;
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

function sha256(data) {
  return crypto.createHash("sha256").update(data).digest("hex");
}

async function sha256File(filePath) {
  const hash = crypto.createHash("sha256");
  const handle = await fs.open(filePath, "r");
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  let position = 0;
  try {
    while (true) {
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, position);
      if (bytesRead === 0) break;
      hash.update(buffer.subarray(0, bytesRead));
      position += bytesRead;
    }
  } finally {
    await handle.close();
  }
  return hash.digest("hex");
}

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
  const selection = JSON.parse(selectionBytes.toString("utf8"));
  const projection = selectionProjection(selection);
  const manifestPath = path.join(absoluteBatch, "proxy-manifest.json");
  const manifest = JSON.parse(await fs.readFile(manifestPath, "utf8"));
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
    invariant(SIDES.includes(side), `Manual row ${index + 4} needs far or near.`);
    if (action === "background") {
      invariant(start === null && end === null, `Manual background row ${index + 4} must leave both time cells blank.`);
    } else {
      invariant(Number.isFinite(start) && Number.isInteger(start) && start >= 0 && Number.isFinite(end) && Number.isInteger(end) && end >= 0, `Manual row ${index + 4} needs non-negative finite whole-second times.`);
    }
    invariant(note === null || typeof note === "string", `Manual row ${index + 4} note must be text.`);
  }
}

export async function verifyWorkbookFile(selectionPath, workbookPath, { allowManualValues = false } = {}) {
  const absoluteSelection = path.resolve(selectionPath);
  const absoluteWorkbook = path.resolve(workbookPath);
  const selectionBytes = await fs.readFile(absoluteSelection);
  const workbookBytes = await fs.readFile(absoluteWorkbook);
  const { selection, manifest, projection } = await verifyProxyBatch(absoluteSelection, path.dirname(absoluteWorkbook), { selectionBytes });
  const workbook = await importWorkbookSnapshot(absoluteWorkbook, workbookBytes);
  const sheetInspection = await workbook.inspect({ kind: "sheet", include: "name", maxChars: 2000 });
  assert.deepEqual(inspectedSheetNames(sheetInspection), SHEET_NAMES, "Workbook sheet order/count must be exact.");
  const clips = workbook.worksheets.getItem("短片清单");
  const actions = workbook.worksheets.getItem("人工动作");
  const hints = workbook.worksheets.getItem("候选提示");
  const labels = workbook.worksheets.getItem("标签说明");
  exactUsedRange(clips, 43, CLIP_HEADERS.length, "Clip sheet");
  exactUsedRange(actions, 3 + 40 * ACTION_SLOTS_PER_CLIP, ACTION_HEADERS.length, "Action sheet");
  exactUsedRange(hints, 3 + projection.hintRows.length, HINT_HEADERS.length, "Hint sheet");
  exactUsedRange(labels, 10, 2, "Label sheet");
  assert.deepEqual(clips.getRange("A3:K3").values[0], CLIP_HEADERS, "Clip headers");
  assert.deepEqual(actions.getRange("A3:I3").values[0], ACTION_HEADERS, "Action headers");
  assert.deepEqual(hints.getRange("A3:J3").values[0], HINT_HEADERS, "Hint headers");
  assert.deepEqual(normalizeRows(clips.getRange("A4:B43").values), projection.clipRows.map((row) => row.slice(0, 2)), "Clip identifiers");
  assert.deepEqual(normalizeRows(clips.getRange("D4:K43").values), projection.clipRows.map((row) => row.slice(3)), "Clip read-only values");
  assert.deepEqual(normalizeRows(hints.getRange(`A4:J${3 + projection.hintRows.length}`).values), projection.hintRows, "Hint read-only values");
  const actionRows = actions.getRange("A4:I483").values;
  const actionValues = normalizeRows(actionRows);
  assert.deepEqual(actionValues.map((row) => [row[0], row[1], row[3]]), projection.actionRows.map((row) => [row[0], row[1], row[3]]), "Action read-only values");
  for (const [index, clip] of selection.clips.entries()) {
    assert.equal(clips.getRange(`C${index + 4}`).formulas[0][0], `=HYPERLINK("clips/${clip.clip_id}.mp4","播放")`, `Clip hyperlink ${clip.clip_id}`);
  }
  for (const [index, row] of projection.actionRows.entries()) {
    assert.equal(actions.getRange(`C${index + 4}`).formulas[0][0], `=HYPERLINK("clips/${row[0]}.mp4","播放")`, `Action hyperlink row ${index + 4}`);
  }
  assert.deepEqual(listValidation(actions.getRange("E4:E483"), "Action"), ACTIONS);
  wholeNumberValidation(actions.getRange("F4:G483"), "Action time");
  assert.deepEqual(listValidation(actions.getRange("H4:H483"), "Action side"), SIDES);
  assert.deepEqual(labels.getRange("A3:B10").values, [
    ["规则", "说明"],
    ["receive / dig", "receive 仅指接对方发球的一传；dig 仅指对方进攻后的防守起球。"],
    ["相对秒", "片段内开始秒和结束秒必须填写非负整数，单位是相对当前短片的秒。"],
    ["background", "background 必须单独使用，且开始秒与结束秒两个单元格都必须保持空白。"],
    ["人工侧别", "background 仍须选择当前 far 或 near 的球队裁剪；far/near 只表示画面远端/近端裁剪，不表示球队身份。"],
    ["完成方式", "没有状态列、审核列或 checkbox；填写人工确认动作即是人工记录。"],
    ["容量", "每个短片固定 12 个动作槽。若 12 槽都不足，请不要覆盖现有行，应请求扩容版本。"],
    ["代理文件", "代理短片无音频且已降分辨率，只用于便携复核。"],
  ], "Label instructions");
  assertManualRows(actions, { allowManualValues });
  await scanFormulaErrors(workbook, "Workbook");
  const result = { batch_id: manifest.batch_id, clip_count: selection.clips.length };
  Object.defineProperties(result, {
    selection: { value: selection },
    selectionBytes: { value: selectionBytes },
    workbookBytes: { value: workbookBytes },
    actionRows: { value: actionRows },
  });
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
