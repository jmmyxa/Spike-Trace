import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
import {
  ACTIONS,
  ACTION_SLOTS_PER_CLIP,
  SHEET_NAMES,
  SIDES,
  verifyWorkbookFile,
} from "./verify_active_review_batch.mjs";
import { buildActiveReviewBatch } from "./build_active_review_batch.mjs";

assert.deepEqual(SHEET_NAMES, ["短片清单", "人工动作", "候选提示", "标签说明"]);
assert.deepEqual(ACTIONS, ["background", "serve", "receive", "set", "attack", "block", "dig"]);
assert.deepEqual(SIDES, ["far", "near"]);
assert.equal(ACTION_SLOTS_PER_CLIP, 12);
assert.equal(typeof buildActiveReviewBatch, "function");

const ROOT = path.resolve(".");

function digest(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function clipId(ordinal) {
  return `round-01-clip-${String(ordinal).padStart(3, "0")}`;
}

function fixtureSelection(videoBytes) {
  const clips = Array.from({ length: 40 }, (_, index) => {
    const ordinal = index + 1;
    const start = index * 5;
    return {
      clip_id: clipId(ordinal), ordinal, start_seconds: start, end_seconds: start + 4, duration_seconds: 4,
      time_stratum: index % 10, selection_bucket: "synthetic-control",
      selection_reasons: ["Synthetic selection reason long enough to require visible wrapping in the rendered workbook."],
      candidate_hints: [{
        canonical_event_id: `candidate-${String(ordinal).padStart(3, "0")}`,
        relative_start_seconds: 1, relative_end_seconds: 2, action: "attack", confidence: 0.81,
        observed_sides: [ordinal % 2 ? "far" : "near"], duplicate_group_id: null, conflict_group_id: null,
        source_candidate_ids: [`${ordinal % 2 ? "far" : "near"}:candidate-${String(ordinal).padStart(3, "0")}`],
      }],
    };
  });
  return {
    format_version: 1, batch_id: "synthetic-round-01", round_id: "round-01", video: { path: "tests/.active-review-fixture/video.mp4", sha256: digest(videoBytes) }, clips,
  };
}

async function writeProxyBatch(selectionPath, outputDir) {
  const selectionBytes = await fs.readFile(selectionPath);
  const selection = JSON.parse(selectionBytes);
  await fs.mkdir(path.join(outputDir, "clips"), { recursive: true });
  const clips = [];
  for (const clip of selection.clips) {
    const bytes = Buffer.from(`synthetic proxy ${clip.clip_id}`);
    const relative = `clips/${clip.clip_id}.mp4`;
    await fs.writeFile(path.join(outputDir, relative), bytes);
    clips.push({ clip_id: clip.clip_id, ordinal: clip.ordinal, path: relative, sha256: digest(bytes) });
  }
  await fs.writeFile(path.join(outputDir, "proxy-manifest.json"), JSON.stringify({
    format_version: 1, batch_id: selection.batch_id, round_id: selection.round_id,
    selection: path.relative(ROOT, selectionPath).split(path.sep).join("/"), selection_sha256: digest(selectionBytes),
    video: { path: selection.video.path, sha256: selection.video.sha256 }, settings: { codec: "mp4v", fps: 15, max_width: 960, audio: false }, clips,
  }, null, 2));
}

function validationValues(range) {
  return range.dataValidation?.rule?.values ?? range.dataValidation?.values;
}

function assertWholeNumberValidation(range) {
  const rule = range.dataValidation?.rule;
  assert.equal(rule?.type, "whole");
  assert.equal(rule?.operator, "greaterThanOrEqual");
  assert.equal(Number(rule?.formula1), 0);
}

async function exportWorkbook(sourcePath, destinationPath, mutate) {
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(sourcePath));
  await mutate(workbook);
  const exported = await SpreadsheetFile.exportXlsx(workbook);
  await exported.save(destinationPath);
}

async function rejects(selectionPath, workbookPath, description) {
  await assert.rejects(() => verifyWorkbookFile(selectionPath, workbookPath), undefined, description);
}

async function main() {
  const fixtureDir = path.join(ROOT, "tests", ".active-review-fixture");
  const outputDir = path.join(fixtureDir, "batch");
  const previewDir = path.join(fixtureDir, "previews");
  const selectionPath = path.join(fixtureDir, "selection.json");
  await fs.rm(fixtureDir, { recursive: true, force: true });
  await fs.mkdir(fixtureDir, { recursive: true });
  const videoBytes = Buffer.from("synthetic low-resolution source video");
  await fs.writeFile(path.join(fixtureDir, "video.mp4"), videoBytes);
  await fs.writeFile(selectionPath, JSON.stringify(fixtureSelection(videoBytes), null, 2));
  try {
    await buildActiveReviewBatch(selectionPath, outputDir, previewDir, { buildProxies: ({ selectionPath: input, outputDir: destination }) => writeProxyBatch(input, destination) });
    const workbookPath = path.join(outputDir, "review.xlsx");
    await verifyWorkbookFile(selectionPath, workbookPath);
    const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
    const names = Array.from({ length: 4 }, (_, index) => workbook.worksheets.getItemAt(index).name);
    const actionSheet = workbook.worksheets.getItem("人工动作");
    assert.deepEqual(names, SHEET_NAMES);
    assert.deepEqual(actionSheet.getRange("A3:I3").values[0], ["短片ID", "动作序号", "播放短片", "片段长度(秒)", "人工确认动作", "片段内开始秒", "片段内结束秒", "人工侧别", "备注"]);
    assert.equal(actionSheet.getUsedRange().rowCount, 3 + 40 * 12);
    assert.equal(actionSheet.getRange("C4").formulas[0][0], '=HYPERLINK("clips/round-01-clip-001.mp4","播放")');
    assert.deepEqual(validationValues(actionSheet.getRange("E4:E483")), ACTIONS);
    assertWholeNumberValidation(actionSheet.getRange("F4:G483"));
    assert.deepEqual(validationValues(actionSheet.getRange("H4:H483")), ["far", "near"]);
    assert.ok(actionSheet.getRange("E4:I483").values.flat().every((value) => value == null || value === ""));
    for (const sheetName of SHEET_NAMES) {
      const blob = await workbook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
      assert.ok((await blob.arrayBuffer()).byteLength > 0, `${sheetName} render must be non-empty`);
    }
    assert.equal(workbook.worksheets.getItem("短片清单").getRange("J4:J43").format.wrapText, true);
    assert.ok(workbook.worksheets.getItem("短片清单").getRange("4:43").format.rowHeightPx >= 54);
    const headers = SHEET_NAMES.flatMap((name) => workbook.worksheets.getItem(name).getRange("3:3").values[0]).filter(Boolean);
    assert.equal(headers.some((header) => /审核|状态|确认栏|checkbox|review status/i.test(header)), false);

    const corruptions = [
      ["changed clip ID", async (book) => book.worksheets.getItem("短片清单").getRange("B4").values = [["changed-clip"]]],
      ["absolute hyperlink", async (book) => book.worksheets.getItem("人工动作").getRange("C4").formulas = [['=HYPERLINK("C:/clips/a.mp4","播放")']]],
      ["added sheet", async (book) => book.worksheets.add("extra")],
      ["appended row", async (book) => book.worksheets.getItem("人工动作").getRange("A484").values = [["extra"]]],
      ["changed hint", async (book) => book.worksheets.getItem("候选提示").getRange("B4").values = [["changed-hint"]]],
      ["validation loss", async (book) => book.worksheets.getItem("人工动作").getRange("E4:E483").dataValidation = null],
      ["prefilled manual cell", async (book) => book.worksheets.getItem("人工动作").getRange("E4").values = [["attack"]]],
      ["formula error", async (book) => book.worksheets.getItem("人工动作").getRange("C4").formulas = [["=#REF!"]]],
    ];
    for (const [name, mutate] of corruptions) {
      const corrupted = path.join(fixtureDir, `corrupt-${name.replaceAll(" ", "-")}.xlsx`);
      await exportWorkbook(workbookPath, corrupted, mutate);
      await rejects(selectionPath, corrupted, name);
    }
    const missingProxy = path.join(outputDir, "clips", "round-01-clip-001.mp4");
    const savedProxy = await fs.readFile(missingProxy);
    await fs.rm(missingProxy);
    await rejects(selectionPath, workbookPath, "missing proxy");
    await fs.writeFile(missingProxy, savedProxy);
    await fs.appendFile(missingProxy, "hash change");
    await rejects(selectionPath, workbookPath, "proxy hash change");
    await fs.writeFile(missingProxy, savedProxy);

    const rollbackOutput = path.join(fixtureDir, "rollback-output");
    const rollbackPreviews = path.join(fixtureDir, "rollback-previews");
    let renameCalls = 0;
    await assert.rejects(() => buildActiveReviewBatch(selectionPath, rollbackOutput, rollbackPreviews, {
      buildProxies: ({ selectionPath: input, outputDir: destination }) => writeProxyBatch(input, destination),
      rename: async (...args) => { renameCalls += 1; if (renameCalls === 2) throw new Error("second rename failure"); return fs.rename(...args); },
    }));
    assert.equal(await fs.stat(rollbackOutput).then(() => true).catch(() => false), false);
    assert.equal(await fs.stat(rollbackPreviews).then(() => true).catch(() => false), false);
  } finally {
    await fs.rm(fixtureDir, { recursive: true, force: true });
  }
}

await main();
