import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const BUILD_DIR = path.join(ROOT, "outputs", ".rangitoto-review-build");
const requireFromBuild = createRequire(pathToFileURL(path.join(BUILD_DIR, "artifact-runtime.mjs")));
const { FileBlob, SpreadsheetFile } = await import(
  pathToFileURL(requireFromBuild.resolve("@oai/artifact-tool")).href
);

export const SHEET_NAMES = ["概览", "候选动作", "来源事件", "标签说明"];
export const ACTIONS = ["background", "serve", "receive", "set", "attack", "block", "dig"];
export const SIDES = ["far", "near", "不确定"];
export const CANDIDATE_HEADERS = [
  "候选ID", "视频ID", "预测动作", "预测开始时间", "预测结束时间", "预测开始秒", "预测结束秒",
  "预测侧别", "观察侧别", "置信度", "冲突组", "重复组", "合并说明", "来源候选ID", "来源窗口数",
  "窗口最高置信度", "复核原因", "人工确认动作", "人工开始时间", "人工结束时间", "人工侧别", "备注",
];
export const MANUAL_HEADERS = CANDIDATE_HEADERS.slice(-5);
export const SOURCE_HEADERS = [
  "候选ID", "来源候选ID", "侧别", "来源事件ID", "动作", "置信度", "开始秒", "结束秒", "开始时间",
  "结束时间", "主来源", "重复组", "冲突组", "来源窗口数", "窗口最高置信度", "裁剪", "模型", "来源",
  "状态", "来源窗口索引",
];

function invariant(condition, message) {
  if (!condition) throw new Error(message);
}

function parseNdjson(ndjson, context) {
  return String(ndjson ?? "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, index) => {
      try {
        return JSON.parse(line);
      } catch {
        throw new Error(`${context}: invalid NDJSON line ${index + 1}`);
      }
    });
}

function sheetNamesFromInspection(inspection) {
  return parseNdjson(inspection.ndjson, "sheet inspection")
    .map((record) => record?.name)
    .filter((name) => typeof name === "string");
}

function formulaErrorMatches(inspection) {
  return parseNdjson(inspection.ndjson, "formula error inspection").filter((record) => {
    if (record?.type === "notice" || record?.kind === "notice") return false;
    return record?.type === "match" || record?.kind === "match" || record?.match !== undefined;
  });
}

function normalizeBlank(value) {
  return value === undefined || value === "" ? null : value;
}

function validationValues(range, context) {
  const validation = range.dataValidation;
  const values = validation?.rule?.values ?? validation?.values;
  invariant(Array.isArray(values), `${context}: list validation is missing after XLSX import.`);
  return values;
}

export async function scanFormulaErrors(workbook, context) {
  const inspection = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!",
    options: { useRegex: true, maxResults: 300 },
    summary: `${context} formula error scan`,
  });
  const matches = formulaErrorMatches(inspection);
  invariant(matches.length === 0, `${context}: found ${matches.length} formula error match(es).`);
  return matches.length;
}

export function validateMergedShape(payload) {
  invariant(payload && typeof payload === "object", "Merged JSON must be an object.");
  invariant(payload.format_version === 2 && payload.merge_format_version === 2, "Merged JSON must use format version 2.");
  invariant(Array.isArray(payload.events), "Merged JSON events must be an array.");
  invariant(payload.events.length > 0, "Merged JSON must contain at least one candidate event.");
  invariant(payload.input_runs && typeof payload.input_runs === "object", "Merged JSON input_runs must be an object.");
  assert.deepEqual(Object.keys(payload.input_runs), ["far", "near"], "input_runs must be ordered far, near");
  for (const side of ["far", "near"]) {
    invariant(Array.isArray(payload.input_runs[side]?.events), `${side} input events must be an array.`);
    invariant(Array.isArray(payload.input_runs[side]?.windows), `${side} input windows must be an array.`);
  }
}

export async function verifyWorkbook(payload, workbook, context = "workbook") {
  validateMergedShape(payload);
  const sheetInspection = await workbook.inspect({ kind: "sheet", include: "name", maxChars: 2000 });
  assert.deepEqual(sheetNamesFromInspection(sheetInspection), SHEET_NAMES, `${context}: sheet order/names`);

  const candidateCount = payload.events.length;
  const sourceCount = payload.input_runs.far.events.length + payload.input_runs.near.events.length;
  const candidateLastRow = candidateCount + 3;
  const sourceLastRow = sourceCount + 3;
  const candidates = workbook.worksheets.getItem("候选动作");
  const sources = workbook.worksheets.getItem("来源事件");

  assert.deepEqual(candidates.getRange("A3:V3").values[0], CANDIDATE_HEADERS, `${context}: candidate headers`);
  assert.deepEqual(sources.getRange("A3:T3").values[0], SOURCE_HEADERS, `${context}: source headers`);
  const candidateIds = candidates.getRange(`A4:A${candidateLastRow}`).values.map(([value]) => value);
  assert.deepEqual(candidateIds, payload.events.map((event) => event.event_id), `${context}: candidate rows`);
  const sourceIds = sources.getRange(`B4:B${sourceLastRow}`).values.map(([value]) => value);
  invariant(sourceIds.length === sourceCount && sourceIds.every(Boolean), `${context}: source row count`);
  invariant(new Set(sourceIds).size === sourceCount, `${context}: source candidate IDs must be unique.`);

  for (const row of candidates.getRange(`R4:V${candidateLastRow}`).values) {
    assert.deepEqual(row.map(normalizeBlank), [null, null, null, null, null], `${context}: manual cells must be blank`);
  }
  assert.deepEqual(validationValues(candidates.getRange(`R4:R${candidateLastRow}`), `${context} action validation`), ACTIONS);
  assert.deepEqual(validationValues(candidates.getRange(`U4:U${candidateLastRow}`), `${context} side validation`), SIDES);
  const formulaErrorCount = await scanFormulaErrors(workbook, context);

  return { sheetNames: SHEET_NAMES, candidateCount, sourceCount, formulaErrorCount };
}

export async function verifyWorkbookFile(mergedJsonPath, workbookPath) {
  const payload = JSON.parse(await fs.readFile(mergedJsonPath, "utf8"));
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
  return verifyWorkbook(payload, workbook, "imported workbook");
}

async function main() {
  const [, , mergedJsonArg, workbookArg] = process.argv;
  invariant(mergedJsonArg && workbookArg && process.argv.length === 4, "Usage: node tools/verify_rangitoto_review.mjs MERGED_JSON OUTPUT_XLSX");
  const result = await verifyWorkbookFile(path.resolve(mergedJsonArg), path.resolve(workbookArg));
  console.log(JSON.stringify({ verified: true, ...result }));
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  main().then(
    () => process.exit(0),
    (error) => {
      console.error(error);
      process.exit(1);
    },
  );
}
