import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PYTHON = path.join(ROOT, ".venv", "Scripts", "python.exe");
const BUILD_DIR = path.join(ROOT, "outputs", ".rangitoto-review-build");
const requireFromBuild = createRequire(pathToFileURL(path.join(BUILD_DIR, "artifact-runtime.mjs")));
const artifactTool = await import(pathToFileURL(requireFromBuild.resolve("@oai/artifact-tool")).href);
const { FileBlob, SpreadsheetFile } = artifactTool;

const SHEET_NAMES = ["概览", "候选动作", "来源事件", "标签说明"];
const ACTIONS = ["background", "serve", "receive", "set", "attack", "block", "dig"];
const SIDES = ["far", "near", "不确定"];
const MANUAL_HEADERS = ["人工确认动作", "人工开始时间", "人工结束时间", "人工侧别", "备注"];

function run(executable, args, context) {
  const result = spawnSync(executable, args, {
    cwd: ROOT,
    encoding: "utf8",
    env: { ...process.env, PYTHONUTF8: "1" },
  });
  assert.equal(
    result.status,
    0,
    `${context} failed (${result.status})\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`,
  );
  return result;
}

function parseNdjson(ndjson) {
  return String(ndjson ?? "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

function sheetNamesFromInspection(inspection) {
  return parseNdjson(inspection.ndjson)
    .map((record) => record?.name)
    .filter((name) => typeof name === "string");
}

function countFormulaErrorMatches(inspection) {
  return parseNdjson(inspection.ndjson).filter((record) => {
    if (record?.type === "notice" || record?.kind === "notice") return false;
    return record?.type === "match" || record?.kind === "match" || record?.match !== undefined;
  }).length;
}

function validationValues(range) {
  const validation = range.dataValidation;
  const values = validation?.rule?.values ?? validation?.values;
  assert.ok(Array.isArray(values), `expected list validation values, actual=${JSON.stringify(validation)}`);
  return values;
}

async function main() {
  await fs.access(PYTHON);
  const temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), "spiketrace-rangitoto-review-"));
  try {
    const mergedDir = path.join(temporaryRoot, "merged");
    const mergedJson = path.join(mergedDir, "merged_candidates.json");
    const mergedCsv = path.join(mergedDir, "merged_candidates.csv");
    const workbookPath = path.join(temporaryRoot, "rangitoto_action_review.xlsx");
    const previewDir = path.join(temporaryRoot, "previews");
    const farFixture = path.join(ROOT, "tests", "fixtures", "dual_crop_review", "far.json");
    const nearFixture = path.join(ROOT, "tests", "fixtures", "dual_crop_review", "near.json");

    run(
      PYTHON,
      ["-m", "spiketrace", "build-dual-crop-review", farFixture, nearFixture, mergedDir, "--repo-root", ROOT],
      "format-2 fixture build",
    );
    const merged = JSON.parse(await fs.readFile(mergedJson, "utf8"));

    run(
      process.execPath,
      [path.join(ROOT, "tools", "build_rangitoto_review.mjs"), mergedJson, workbookPath, previewDir],
      "review workbook build",
    );
    run(
      process.execPath,
      [path.join(ROOT, "tools", "verify_rangitoto_review.mjs"), mergedJson, workbookPath],
      "review workbook verification",
    );

    const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
    const sheetInspection = await workbook.inspect({ kind: "sheet", include: "name", maxChars: 2000 });
    assert.deepEqual(sheetNamesFromInspection(sheetInspection), SHEET_NAMES, "sheet order must be exact");

    const candidateCount = merged.events.length;
    const sourceCount = Object.values(merged.input_runs).reduce((total, inputRun) => total + inputRun.events.length, 0);
    const candidateSheet = workbook.worksheets.getItem("候选动作");
    const sourceSheet = workbook.worksheets.getItem("来源事件");
    const candidateLastRow = candidateCount + 3;
    const sourceLastRow = sourceCount + 3;
    const candidateHeaders = candidateSheet.getRange("A3:V3").values[0];

    assert.deepEqual(candidateHeaders.slice(-5), MANUAL_HEADERS, "candidate sheet must end with five manual columns");
    assert.equal(candidateHeaders.filter((header) => MANUAL_HEADERS.includes(header)).length, 5, "manual headers must appear exactly once");
    assert.equal(candidateSheet.getRange(`A4:A${candidateLastRow}`).values.filter(([value]) => value !== null && value !== "").length, candidateCount);
    assert.equal(sourceSheet.getRange(`A4:A${sourceLastRow}`).values.filter(([value]) => value !== null && value !== "").length, sourceCount);
    assert.ok(
      sourceSheet.getRange(`K4:K${sourceLastRow}`).values.every(([value]) => value === "是" || value === "否"),
      "source primary flags must be text, not checkbox booleans",
    );

    for (const row of candidateSheet.getRange(`R4:V${candidateLastRow}`).values) {
      assert.deepEqual(row, [null, null, null, null, null], "all manual cells must start blank");
    }
    assert.deepEqual(validationValues(candidateSheet.getRange(`R4:R${candidateLastRow}`)), ACTIONS);
    assert.deepEqual(validationValues(candidateSheet.getRange(`U4:U${candidateLastRow}`)), SIDES);

    const formulaErrors = await workbook.inspect({
      kind: "match",
      searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!",
      options: { useRegex: true, maxResults: 300 },
      summary: "workbook formula error scan",
    });
    assert.equal(countFormulaErrorMatches(formulaErrors), 0, "formula scan must return zero error matches");

    for (const sheetName of SHEET_NAMES) {
      await fs.access(path.join(previewDir, `${sheetName}.png`));
    }
    await fs.access(mergedCsv);
    console.log(`PASS: ${candidateCount} candidate rows, ${sourceCount} source rows, 4 sheets, 5 blank manual columns`);
  } finally {
    await fs.rm(temporaryRoot, { recursive: true, force: true });
  }
}

main().then(
  () => process.exit(0),
  (error) => {
    console.error(error);
    process.exit(1);
  },
);
