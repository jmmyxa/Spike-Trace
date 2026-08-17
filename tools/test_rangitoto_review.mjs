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

function runVerifier(mergedJson, workbookPath) {
  return spawnSync(
    process.execPath,
    [path.join(ROOT, "tools", "verify_rangitoto_review.mjs"), mergedJson, workbookPath],
    {
      cwd: ROOT,
      encoding: "utf8",
      env: { ...process.env, PYTHONUTF8: "1" },
    },
  );
}

async function exportTamperedWorkbook(sourcePath, destinationPath, mutate) {
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(sourcePath));
  mutate(workbook);
  const exported = await SpreadsheetFile.exportXlsx(workbook);
  await exported.save(destinationPath);
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
  const readme = await fs.readFile(path.join(ROOT, "README.md"), "utf8");
  for (const stalePhrase of ["旧双裁剪扫描已经失效", "必须先由 Task 5", "因此下一步先完成 Rangitoto 候选复核"]) {
    assert.equal(readme.includes(stalePhrase), false, `README must not retain stale status phrase: ${stalePhrase}`);
  }
  assert.ok(readme.includes("2,942"));
  assert.ok(readme.includes("人工复核"));
  assert.match(readme, /nearest-v1/);
  for (const command of [
    "select-review-batch",
    "build_active_review_batch.mjs",
    "extract_active_review_results.mjs",
  ]) {
    assert.ok(readme.includes(command), `README must document active-learning command: ${command}`);
  }
  for (const requiredPhrase of ["40", "片段内", "无音频", "background", "receive", "dig"]) {
    assert.ok(readme.includes(requiredPhrase), `README must document active-learning guidance: ${requiredPhrase}`);
  }
  assert.match(
    readme,
    /Rangitoto[\s\S]*(?:不能继续作为独立 `val`|不能继续作为独立.*val)/,
    "README must state that Rangitoto cannot remain an independent validation match after training use",
  );
  const selectionIgnore = spawnSync(
    "git",
    ["check-ignore", "--no-index", "data/active-learning/rangitoto/round-01-selection.json"],
    { cwd: ROOT, encoding: "utf8" },
  );
  assert.equal(selectionIgnore.status, 1, "selection JSON must be recognized by Git instead of ignored");
  const selectionEol = spawnSync(
    "git",
    ["check-attr", "eol", "--", "data/active-learning/rangitoto/round-01-selection.json"],
    { cwd: ROOT, encoding: "utf8" },
  );
  assert.equal(selectionEol.status, 0, "Git must be able to inspect selection line-ending policy");
  assert.match(selectionEol.stdout, /eol: lf/, "selection JSON must be checked out with LF bytes for SHA-256 portability");
  const proxyIgnore = spawnSync(
    "git",
    ["check-ignore", "outputs/active-learning/rangitoto/round-01/clips/example.mp4"],
    { cwd: ROOT, encoding: "utf8" },
  );
  assert.equal(proxyIgnore.status, 0, "generated proxy MP4 files must remain ignored");
  const temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), "spiketrace-rangitoto-review-"));
  try {
    const mergedDir = path.join(temporaryRoot, "merged");
    const mergedJson = path.join(mergedDir, "merged_candidates.json");
    const mergedCsv = path.join(mergedDir, "merged_candidates.csv");
    const workbookPath = path.join(temporaryRoot, "rangitoto_action_review.xlsx");
    const previousWorkbookLink = path.join(temporaryRoot, "previous-workbook-link.xlsx");
    const previewDir = path.join(temporaryRoot, "previews");
    const farFixture = path.join(ROOT, "tests", "fixtures", "dual_crop_review", "far.json");
    const nearFixture = path.join(ROOT, "tests", "fixtures", "dual_crop_review", "near.json");

    run(
      PYTHON,
      ["-m", "spiketrace", "build-dual-crop-review", farFixture, nearFixture, mergedDir, "--repo-root", ROOT],
      "format-2 fixture build",
    );
    const merged = JSON.parse(await fs.readFile(mergedJson, "utf8"));
    const previousWorkbookContents = Buffer.from("existing-review-workbook");
    await fs.writeFile(workbookPath, previousWorkbookContents);
    await fs.link(workbookPath, previousWorkbookLink);

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
    assert.ok(
      (await fs.readFile(previousWorkbookLink)).equals(previousWorkbookContents),
      "final workbook replacement must rename the verified temp file instead of overwriting the old file",
    );

    const rejectedMergedJson = path.join(temporaryRoot, "rejected-format-1.json");
    const rejectedOutput = path.join(temporaryRoot, "existing-final.xlsx");
    await fs.writeFile(rejectedMergedJson, JSON.stringify({ ...merged, format_version: 1 }));
    await fs.writeFile(rejectedOutput, previousWorkbookContents);
    const rejectedBuild = spawnSync(
      process.execPath,
      [path.join(ROOT, "tools", "build_rangitoto_review.mjs"), rejectedMergedJson, rejectedOutput, previewDir],
      { cwd: ROOT, encoding: "utf8", env: { ...process.env, PYTHONUTF8: "1" } },
    );
    assert.notEqual(rejectedBuild.status, 0, "format-1 workbook build must fail");
    assert.ok(
      (await fs.readFile(rejectedOutput)).equals(previousWorkbookContents),
      "a failed build must leave an existing final workbook intact",
    );

    const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
    const sheetInspection = await workbook.inspect({ kind: "sheet", include: "name", maxChars: 2000 });
    assert.deepEqual(sheetNamesFromInspection(sheetInspection), SHEET_NAMES, "sheet order must be exact");

    const candidateCount = merged.events.length;
    const sourceCount = Object.values(merged.input_runs).reduce((total, inputRun) => total + inputRun.events.length, 0);
    const candidateSheet = workbook.worksheets.getItem("候选动作");
    const sourceSheet = workbook.worksheets.getItem("来源事件");
    const overviewSheet = workbook.worksheets.getItem("概览");
    const candidateLastRow = candidateCount + 3;
    const sourceLastRow = sourceCount + 3;
    const candidateHeaders = candidateSheet.getRange("A3:V3").values[0];

    assert.deepEqual(candidateHeaders.slice(-5), MANUAL_HEADERS, "candidate sheet must end with five manual columns");
    assert.equal(candidateHeaders.filter((header) => MANUAL_HEADERS.includes(header)).length, 5, "manual headers must appear exactly once");
    assert.equal(
      candidateSheet.getRange("R3:V3").format.fill.color.hex,
      "#FFF4CC",
      "manual headers must use the same yellow fill as editable cells",
    );
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

    assert.ok(candidateSheet.getRange("B:B").format.columnWidthPx >= 240, "candidate video ID column must be wide enough for wrapped values");
    assert.equal(candidateSheet.getRange("B4:B10").format.wrapText, true, "candidate video IDs must wrap");
    assert.ok(candidateSheet.getRange("4:4").format.rowHeightPx >= 60, "candidate rows must leave room for wrapped video IDs");
    assert.equal(candidateSheet.getRange("B4").values[0][0], merged.events[0].video_id, "candidate video ID must remain full text");
    assert.ok(sourceSheet.getRange("T:T").format.columnWidthPx >= 400, "source member-index column must be wide enough for wrapped values");
    assert.equal(sourceSheet.getRange("T4:T10").format.wrapText, true, "source member indexes must wrap");
    const sourceMemberIndexValues = sourceSheet.getRange(`T4:T${sourceLastRow}`).values;
    const expectedMemberIndexValues = merged.events.flatMap((event) =>
      event.source_event_refs.map((reference) => reference.member_window_indices.join("|")),
    );
    assert.deepEqual(
      sourceMemberIndexValues.map(([value]) => value),
      expectedMemberIndexValues,
      "source member-index text must remain complete after layout formatting",
    );
    const longestSourceRow = sourceMemberIndexValues.reduce(
      (best, [value], index) => (value.length > best.length ? { length: value.length, row: index + 4 } : best),
      { length: 0, row: 4 },
    );
    assert.ok(sourceSheet.getRange(`${longestSourceRow.row}:${longestSourceRow.row}`).format.rowHeightPx >= 72, "source rows must leave room for wrapped member indexes");
    assert.ok(
      sourceMemberIndexValues.every(([value]) => typeof value === "string"),
      "source member-index values must remain full text strings",
    );
    assert.equal(overviewSheet.getRange("B14:I15").format.wrapText, true, "overview provenance must wrap");
    assert.ok(overviewSheet.getRange("14:14").format.rowHeightPx >= 60, "overview provenance rows must leave room for wrapped values");
    for (const column of ["B", "C", "G", "H", "I"]) {
      assert.ok(overviewSheet.getRange(`${column}:${column}`).format.columnWidthPx >= 200, `overview ${column} provenance column must be wide enough`);
    }

    const tamperCases = [
      {
        name: "candidate provenance",
        mutate: (tampered) => { tampered.worksheets.getItem("候选动作").getRange("N4").values = [["tampered:source"]]; },
      },
      {
        name: "source candidate ownership",
        mutate: (tampered) => { tampered.worksheets.getItem("来源事件").getRange("A4").values = [["tampered-owner"]]; },
      },
      {
        name: "source side",
        mutate: (tampered) => { tampered.worksheets.getItem("来源事件").getRange("C4").values = [["tampered-side"]]; },
      },
      {
        name: "source event ID",
        mutate: (tampered) => { tampered.worksheets.getItem("来源事件").getRange("D4").values = [["tampered-event"]]; },
      },
      {
        name: "source member indices",
        mutate: (tampered) => { tampered.worksheets.getItem("来源事件").getRange("T4").values = [["999"]]; },
      },
      {
        name: "appended candidate row",
        mutate: (tampered) => {
          tampered.worksheets.getItem("候选动作").getRange(`A${candidateLastRow + 1}`).values = [["extra-candidate"]];
        },
      },
      {
        name: "appended source row",
        mutate: (tampered) => {
          tampered.worksheets.getItem("来源事件").getRange(`A${sourceLastRow + 1}`).values = [["extra-source"]];
        },
      },
    ];
    const acceptedTampering = [];
    for (const [index, tamperCase] of tamperCases.entries()) {
      const tamperedPath = path.join(temporaryRoot, `tampered-${index}.xlsx`);
      await exportTamperedWorkbook(workbookPath, tamperedPath, tamperCase.mutate);
      if (runVerifier(mergedJson, tamperedPath).status === 0) acceptedTampering.push(tamperCase.name);
    }
    assert.deepEqual(acceptedTampering, [], `verifier accepted workbook tampering: ${acceptedTampering.join(", ")}`);

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
