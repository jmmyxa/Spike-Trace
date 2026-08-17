import assert from "node:assert/strict";
import crypto from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";

import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
import {
  ACTIONS,
  ACTION_SLOTS_PER_CLIP,
  SHEET_NAMES,
  SIDES,
  sha256File,
  verifyProxyBatch,
  verifyWorkbookFile,
} from "./verify_active_review_batch.mjs";
import { buildActiveReviewBatch } from "./build_active_review_batch.mjs";
import { extractActiveReviewResults } from "./extract_active_review_results.mjs";

assert.deepEqual(SHEET_NAMES, ["短片清单", "人工动作", "候选提示", "标签说明"]);
assert.deepEqual(ACTIONS, ["background", "serve", "receive", "set", "attack", "block", "dig"]);
assert.deepEqual(SIDES, ["far", "near"]);
assert.equal(ACTION_SLOTS_PER_CLIP, 12);
assert.equal(typeof buildActiveReviewBatch, "function");

const ROOT = path.resolve(".");

function digest(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

async function verifyStreamingHashRegression() {
  const bytes = Buffer.from("streaming SHA-256 must not materialize the source video", "utf8");
  const reads = [];
  let closeCount = 0;
  const digestResult = await sha256File("virtual-over-2-gib-source.mp4", {
    chunkBytes: 4,
    open: async (filePath, flags) => {
      assert.equal(filePath, "virtual-over-2-gib-source.mp4");
      assert.equal(flags, "r");
      return {
        async read(buffer, offset, length, position) {
          reads.push({ offset, length, position });
          const copied = bytes.copy(buffer, offset, position, Math.min(position + 3, bytes.length));
          return { bytesRead: copied };
        },
        async close() {
          closeCount += 1;
        },
      };
    },
  });
  assert.equal(digestResult, digest(bytes), "streamed hashing must digest every partial read exactly once");
  assert.deepEqual(reads.map(({ offset, length }) => ({ offset, length })), reads.map(() => ({ offset: 0, length: 4 })));
  assert.deepEqual(reads.map(({ position }) => position), [0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45, 48, 51, 54, 55]);
  assert.equal(closeCount, 1, "streamed hashing must close its file handle");
}

function runPython(args, context) {
  const python = process.env.SPIKETRACE_PYTHON;
  assert.ok(python, "SPIKETRACE_PYTHON must point to the supplied synthetic-pipeline Python.");
  const result = spawnSync(python, args, {
    cwd: ROOT,
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: path.join(ROOT, "src"), PYTHONUTF8: "1" },
  });
  assert.equal(result.status, 0, `${context} failed (${result.status})\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
}

function inferencePayload(side, video, videoHash) {
  const crop = side === "far" ? [0, 0, 1920, 645] : [0, 255, 1920, 1080];
  return {
    format_version: 2,
    video,
    model_version: "synthetic-active-review-v1",
    settings: {
      device: "cpu", checkpoint: "runs/synthetic/best.pt", checkpoint_sha256: "a".repeat(64), video_sha256: videoHash,
      opencv_version: "synthetic", torch_version: "synthetic", torchvision_version: "synthetic", video,
      num_frames: 4, image_size: 32, window_seconds: 1, stride_seconds: 1, confidence_threshold: 0,
      merge_gap_seconds: 0.25, min_event_seconds: 0.2, batch_size: 1, crop, sampling_contract: "center-nearest-frame-v1",
    },
    events: [], windows: [],
  };
}

function addCandidate(run, side, eventId, start, action, confidence) {
  const roundedConfidence = Math.round(confidence * 1_000_000) / 1_000_000;
  const windowIndex = run.windows.length;
  run.windows.push({ window_index: windowIndex, start_seconds: start, end_seconds: start + 1, action, confidence: roundedConfidence });
  run.events.push({
    video_id: "round-01", event_id: eventId, start_ms: start * 1000, end_ms: (start + 1) * 1000,
    action, confidence: roundedConfidence, team_side: null, player_number: null, status: "predicted", model_version: "synthetic-active-review-v1",
    source: "sliding_window", source_window_indices: [windowIndex],
  });
}

function addBackground(run, start) {
  run.windows.push({ window_index: run.windows.length, start_seconds: start, end_seconds: start + 1, action: "background", confidence: 0.99 });
}

async function createSyntheticSelection(fixtureDir) {
  const videoPath = path.join(fixtureDir, "round-01.mp4");
  await fs.writeFile(path.join(fixtureDir, "make_video.py"), [
    "import cv2, numpy as np, sys",
    "writer = cv2.VideoWriter(sys.argv[1], cv2.VideoWriter_fourcc(*'mp4v'), 1.0, (1920, 1080))",
    "assert writer.isOpened()",
    "for index in range(400):",
    "    frame = np.full((1080, 1920, 3), index % 255, dtype=np.uint8)",
    "    writer.write(frame)",
    "writer.release()",
  ].join("\n"));
  runPython([path.join(fixtureDir, "make_video.py"), videoPath], "synthetic low-resolution video creation");
  const videoBytes = await fs.readFile(videoPath);
  const video = { path: "tests/.active-review-fixture/round-01.mp4", fps: 1, frame_count: 400, width: 1920, height: 1080, duration_seconds: 400 };
  const far = inferencePayload("far", video, digest(videoBytes));
  const near = inferencePayload("near", video, digest(videoBytes));
  const minority = ["receive", "block", "dig"];
  for (let index = 0; index < 20; index += 1) {
    const start = 5 + index * 6;
    addCandidate(far, "far", `conflict-far-${index}`, start, minority[index % minority.length], 0.8);
    addCandidate(near, "near", `conflict-near-${index}`, start, "attack", 0.82);
  }
  for (let index = 0; index < 8; index += 1) addCandidate(far, "far", `tail-${index}`, 132 + index * 6, "set", 0.95 - index / 100);
  for (let index = 0; index < 4; index += 1) {
    const start = 184 + index * 6;
    addCandidate(far, "far", `dual-${index}`, start, "set", 0.6);
    addCandidate(near, "near", `dual-${index}`, start, "set", 0.6);
  }
  for (let index = 0; index < 4; index += 1) addCandidate(far, "far", `random-${index}`, 210 + index * 6, "tip", 0.3);
  for (let index = 0; index < 16; index += 1) addCandidate(far, "far", `reserve-${index}`, 270 + index * 6, "serve", 0.5);
  for (let second = 340; second < 400; second += 1) {
    addBackground(far, second);
    addBackground(near, second);
  }
  const farPath = path.join(fixtureDir, "far.json");
  const nearPath = path.join(fixtureDir, "near.json");
  await fs.writeFile(farPath, JSON.stringify(far));
  await fs.writeFile(nearPath, JSON.stringify(near));
  const mergedDir = path.join(fixtureDir, "merged");
  const selectionPath = path.join(fixtureDir, "selection.json");
  runPython(["-c", [
    "from pathlib import Path",
    "from spiketrace.dual_crop_review import build_dual_crop_review",
    "from spiketrace.active_learning_selection import select_review_batch",
    "root, far, near, merged, selection = map(Path, __import__('sys').argv[1:])",
    "build_dual_crop_review(far, near, merged, repo_root=root)",
    "select_review_batch(merged / 'merged_candidates.json', selection, repo_root=root, preferred_clip_seconds=5, min_clip_seconds=5, max_clip_seconds=5)",
  ].join("\n"), ROOT, farPath, nearPath, mergedDir, selectionPath], "synthetic selector pipeline");
  return selectionPath;
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

async function rewriteJson(filePath, mutate) {
  const payload = JSON.parse(await fs.readFile(filePath, "utf8"));
  mutate(payload);
  await fs.writeFile(filePath, JSON.stringify(payload, null, 2));
}

async function writeCompletedWorkbook(sourcePath, destinationPath, mutate = undefined) {
  await exportWorkbook(sourcePath, destinationPath, async (workbook) => {
    const actions = workbook.worksheets.getItem("人工动作");
    for (let clipIndex = 0; clipIndex < 40; clipIndex += 1) {
      actions.getRange(`E${4 + clipIndex * ACTION_SLOTS_PER_CLIP}:I${4 + clipIndex * ACTION_SLOTS_PER_CLIP}`).values = [["background", null, null, "near", ""]];
    }
    actions.getRange("E4:I5").values = [
      ["receive", 1, 2, "far", "接发球"],
      ["set", 3, 4, "far", ""],
    ];
    if (mutate) await mutate(workbook);
  });
}

async function assertWorkbookUnchanged(workbookPath, before) {
  assert.deepEqual(await fs.readFile(workbookPath), before, "extraction must not edit the completed workbook");
}

async function assertRejectedExtraction(selectionPath, workbookPath, outputPath, description, options = undefined) {
  const before = await fs.readFile(workbookPath);
  await assert.rejects(() => extractActiveReviewResults(selectionPath, workbookPath, outputPath, options), undefined, description);
  await assertWorkbookUnchanged(workbookPath, before);
}

async function assertNoTemporarySibling(directory, outputName) {
  const entries = await fs.readdir(directory);
  assert.equal(entries.some((entry) => entry.startsWith(`.${outputName}.tmp-`)), false, "temporary draft sibling must be removed");
}

async function assertNoSnapshotSibling(directory, inputName) {
  const entries = await fs.readdir(directory);
  assert.equal(entries.some((entry) => entry.startsWith(`.${inputName}.snapshot-`)), false, "input snapshot sibling must be removed");
}

async function main() {
  await verifyStreamingHashRegression();
  const fixtureDir = path.join(ROOT, "tests", ".active-review-fixture");
  const outputDir = path.join(fixtureDir, "batch");
  const previewDir = path.join(fixtureDir, "previews");
  await fs.rm(fixtureDir, { recursive: true, force: true });
  await fs.mkdir(fixtureDir, { recursive: true });
  const selectionPath = await createSyntheticSelection(fixtureDir);
  try {
    await buildActiveReviewBatch(selectionPath, outputDir, previewDir);
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
    assert.deepEqual(workbook.worksheets.getItem("标签说明").getRange("A3:B10").values, [
      ["规则", "说明"],
      ["receive / dig", "receive 仅指接对方发球的一传；dig 仅指对方进攻后的防守起球。"],
      ["相对秒", "片段内开始秒和结束秒必须填写非负整数，单位是相对当前短片的秒。"],
      ["background", "background 必须单独使用，且开始秒与结束秒两个单元格都必须保持空白。"],
      ["人工侧别", "background 仍须选择当前 far 或 near 的球队裁剪；far/near 只表示画面远端/近端裁剪，不表示球队身份。"],
      ["完成方式", "没有状态列、审核列或 checkbox；填写人工确认动作即是人工记录。"],
      ["容量", "每个短片固定 12 个动作槽。若 12 槽都不足，请不要覆盖现有行，应请求扩容版本。"],
      ["代理文件", "代理短片无音频且已降分辨率，只用于便携复核。"],
    ]);

    const corruptions = [
      ["changed clip ID", async (book) => book.worksheets.getItem("短片清单").getRange("B4").values = [["changed-clip"]]],
      ["absolute hyperlink", async (book) => book.worksheets.getItem("人工动作").getRange("C5").formulas = [['=HYPERLINK("C:/clips/a.mp4","播放")']]],
      ["altered later hyperlink", async (book) => book.worksheets.getItem("人工动作").getRange("C15").formulas = [['=HYPERLINK("clips/replaced.mp4","播放")']]],
      ["added sheet", async (book) => book.worksheets.add("extra").getRange("A1").values = [["non-empty"]]],
      ["appended row", async (book) => book.worksheets.getItem("人工动作").getRange("A484").values = [["extra"]]],
      ["changed hint", async (book) => book.worksheets.getItem("候选提示").getRange("B4").values = [["changed-hint"]]],
      ["validation loss", async (book) => book.worksheets.getItem("人工动作").getRange("E4:E483").dataValidation = null],
      ["prefilled manual cell", async (book) => book.worksheets.getItem("人工动作").getRange("E4").values = [["attack"]]],
      ["formula error", async (book) => book.worksheets.getItem("人工动作").getRange("C4").formulas = [["=#REF!"]]],
      ["changed label instruction", async (book) => book.worksheets.getItem("标签说明").getRange("B4").values = [["erased"]]],
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

    const completedWorkbook = path.join(outputDir, "completed-review.xlsx");
    const draftPath = path.join(fixtureDir, "review-draft.json");
    await writeCompletedWorkbook(workbookPath, completedWorkbook);
    const selectionBytes = await fs.readFile(selectionPath);
    const completedBytes = await fs.readFile(completedWorkbook);
    const draft = await extractActiveReviewResults(selectionPath, completedWorkbook, draftPath);
    assert.deepEqual(draft.clips[0].actions, [
      { action: "receive", relative_start_seconds: 1, relative_end_seconds: 2, team_side: "far", note: "接发球" },
      { action: "set", relative_start_seconds: 3, relative_end_seconds: 4, team_side: "far", note: "" },
    ]);
    assert.deepEqual(draft.clips[1].actions, [
      { action: "background", relative_start_seconds: 0, relative_end_seconds: draft.clips[1].source_end_seconds - draft.clips[1].source_start_seconds, team_side: "near", note: "" },
    ]);
    assert.equal(draft.time_precision_seconds, 1);
    assert.equal(draft.selection_sha256, digest(selectionBytes));
    assert.equal(draft.workbook.sha256, digest(completedBytes));
    assert.deepEqual(JSON.parse(await fs.readFile(draftPath, "utf8")), draft);
    await assertWorkbookUnchanged(completedWorkbook, completedBytes);
    const cliDraftPath = path.join(fixtureDir, "review-draft-cli.json");
    const cliResult = spawnSync(process.execPath, ["tools/extract_active_review_results.mjs", selectionPath, completedWorkbook, cliDraftPath], {
      cwd: ROOT,
      encoding: "utf8",
    });
    assert.equal(cliResult.status, 0, `extraction CLI failed (${cliResult.status})\nstdout:\n${cliResult.stdout}\nstderr:\n${cliResult.stderr}`);
    assert.deepEqual(JSON.parse(await fs.readFile(cliDraftPath, "utf8")), draft);
    await assertWorkbookUnchanged(completedWorkbook, completedBytes);

    const rejectionCases = [
      ["incomplete clip", async (book) => book.worksheets.getItem("人工动作").getRange("E28:I28").values = [[null, null, null, null, null]]],
      ["orphan manual cells", async (book) => book.worksheets.getItem("人工动作").getRange("E28:I28").values = [[null, 1, null, null, null]]],
      ["fractional seconds", async (book) => book.worksheets.getItem("人工动作").getRange("E28:I28").values = [["attack", 1.5, 2, "far", ""]]],
      ["NaN seconds", async (book) => book.worksheets.getItem("人工动作").getRange("E28:I28").values = [["attack", Number.NaN, 2, "far", ""]]],
      ["infinite seconds", async (book) => book.worksheets.getItem("人工动作").getRange("E28:I28").values = [["attack", 1, Number.POSITIVE_INFINITY, "far", ""]]],
      ["missing background side", async (book) => book.worksheets.getItem("人工动作").getRange("E28:I28").values = [["background", null, null, null, ""]]],
      ["all action slots populated", async (book) => book.worksheets.getItem("人工动作").getRange("E28:I39").values = Array.from({ length: 12 }, () => ["background", null, null, "near", ""])],
      ["timed background", async (book) => book.worksheets.getItem("人工动作").getRange("E28:I28").values = [["background", 1, 2, "near", ""]]],
      ["partially timed background", async (book) => book.worksheets.getItem("人工动作").getRange("E28:I28").values = [["background", 1, null, "near", ""]]],
      ["background mixed with action", async (book) => book.worksheets.getItem("人工动作").getRange("E28:I29").values = [["background", null, null, "near", ""], ["attack", 1, 2, "near", ""]]],
      ["time outside clip", async (book) => book.worksheets.getItem("人工动作").getRange("E28:I28").values = [["attack", 0, 6, "far", ""]]],
      ["equal start and end", async (book) => book.worksheets.getItem("人工动作").getRange("E28:I28").values = [["attack", 2, 2, "far", ""]]],
      ["invalid side", async (book) => book.worksheets.getItem("人工动作").getRange("E28:I28").values = [["attack", 1, 2, "middle", ""]]],
      ["invalid action", async (book) => book.worksheets.getItem("人工动作").getRange("E28:I28").values = [["tip", 1, 2, "far", ""]]],
      ["tampered read-only cell", async (book) => book.worksheets.getItem("人工动作").getRange("A28").values = [["reordered-clip"]]],
    ];
    for (const [name, mutate] of rejectionCases) {
      const rejectedWorkbook = path.join(outputDir, `rejected-${name.replaceAll(" ", "-")}.xlsx`);
      const rejectedOutput = path.join(fixtureDir, `rejected-${name.replaceAll(" ", "-")}.json`);
      await writeCompletedWorkbook(workbookPath, rejectedWorkbook, mutate);
      await assertRejectedExtraction(selectionPath, rejectedWorkbook, rejectedOutput, name);
      assert.equal(await fs.stat(rejectedOutput).then(() => true).catch(() => false), false, `${name} must not publish a draft`);
    }

    const mismatchSelection = path.join(fixtureDir, "mismatch-selection.json");
    await fs.copyFile(selectionPath, mismatchSelection);
    await rewriteJson(mismatchSelection, (selection) => { selection.batch_id = "mismatched-batch"; });
    await assertRejectedExtraction(mismatchSelection, completedWorkbook, path.join(fixtureDir, "mismatch.json"), "selection mismatch");

    const existingOutput = await fs.readFile(draftPath);
    await assertRejectedExtraction(selectionPath, completedWorkbook, draftPath, "existing output");
    assert.deepEqual(await fs.readFile(draftPath), existingOutput, "existing draft bytes must be preserved");

    const atomicCases = [
      ["temporary-write", {
        open: async (...args) => {
          const handle = await fs.open(...args);
          return { writeFile: async () => { throw new Error("injected write failure"); }, sync: handle.sync.bind(handle), close: handle.close.bind(handle) };
        },
      }],
      ["sync", {
        open: async (...args) => {
          const handle = await fs.open(...args);
          return { writeFile: handle.writeFile.bind(handle), sync: async () => { throw new Error("injected sync failure"); }, close: handle.close.bind(handle) };
        },
      }],
      ["hard-link", { link: async () => { throw new Error("injected hard-link failure"); } }],
    ];
    for (const [name, io] of atomicCases) {
      const output = path.join(fixtureDir, `atomic-${name}.json`);
      await assertRejectedExtraction(selectionPath, completedWorkbook, output, `injected ${name} failure`, { io });
      assert.equal(await fs.stat(output).then(() => true).catch(() => false), false, `${name} failure must not publish a draft`);
      await assertNoTemporarySibling(fixtureDir, path.basename(output));
    }

    const racingOutput = path.join(fixtureDir, "atomic-racing-link.json");
    const racingBytes = Buffer.from("racing writer bytes", "utf8");
    await assertRejectedExtraction(selectionPath, completedWorkbook, racingOutput, "racing hard-link collision", {
      io: {
        link: async (_temporary, output) => {
          await fs.writeFile(output, racingBytes, { flag: "wx" });
          const error = new Error("injected racing hard-link collision");
          error.code = "EEXIST";
          throw error;
        },
      },
    });
    assert.deepEqual(await fs.readFile(racingOutput), racingBytes, "racing output bytes must be preserved");
    await assertNoTemporarySibling(fixtureDir, path.basename(racingOutput));

    const originalSelectionBytes = await fs.readFile(selectionPath);
    const racedSelection = path.join(fixtureDir, "raced-selection.json");
    await fs.copyFile(selectionPath, racedSelection);
    await rewriteJson(racedSelection, (selection) => { selection.batch_id = "raced-selection"; });
    const racedSelectionOutput = path.join(fixtureDir, "raced-selection-draft.json");
    await assert.rejects(() => extractActiveReviewResults(selectionPath, completedWorkbook, racedSelectionOutput, {
      afterVerification: async () => fs.copyFile(racedSelection, selectionPath),
    }), undefined, "selection replacement after verification must be rejected");
    await fs.writeFile(selectionPath, originalSelectionBytes);
    assert.deepEqual(await fs.readFile(selectionPath), originalSelectionBytes, "selection bytes must remain unchanged after race cleanup");
    assert.equal(await fs.stat(racedSelectionOutput).then(() => true).catch(() => false), false, "selection race must not publish a draft");
    await assertNoTemporarySibling(fixtureDir, path.basename(racedSelectionOutput));
    await assertNoSnapshotSibling(path.dirname(selectionPath), path.basename(selectionPath));

    const originalWorkbookBytes = await fs.readFile(completedWorkbook);
    const racedWorkbook = path.join(outputDir, "raced-review.xlsx");
    await writeCompletedWorkbook(workbookPath, racedWorkbook, async (book) => {
      book.worksheets.getItem("人工动作").getRange("E4:I4").values = [["attack", 0, 1, "near", "raced"]];
    });
    const racedWorkbookOutput = path.join(fixtureDir, "raced-workbook-draft.json");
    await assert.rejects(() => extractActiveReviewResults(selectionPath, completedWorkbook, racedWorkbookOutput, {
      afterVerification: async () => fs.copyFile(racedWorkbook, completedWorkbook),
    }), undefined, "workbook replacement after verification must be rejected");
    await fs.writeFile(completedWorkbook, originalWorkbookBytes);
    assert.deepEqual(await fs.readFile(completedWorkbook), originalWorkbookBytes, "workbook bytes must remain unchanged after race cleanup");
    assert.equal(await fs.stat(racedWorkbookOutput).then(() => true).catch(() => false), false, "workbook race must not publish a draft");
    await assertNoTemporarySibling(fixtureDir, path.basename(racedWorkbookOutput));
    await assertNoSnapshotSibling(path.dirname(completedWorkbook), path.basename(completedWorkbook));

    const duplicatedSelection = path.join(fixtureDir, "duplicate-selection.json");
    const duplicatedBatch = path.join(fixtureDir, "duplicate-batch");
    await fs.cp(outputDir, duplicatedBatch, { recursive: true });
    await fs.copyFile(selectionPath, duplicatedSelection);
    await rewriteJson(duplicatedSelection, (selection) => { selection.clips[1].clip_id = selection.clips[0].clip_id; });
    const duplicatedSelectionBytes = await fs.readFile(duplicatedSelection);
    await rewriteJson(path.join(duplicatedBatch, "proxy-manifest.json"), (manifest) => {
      manifest.selection = path.relative(ROOT, duplicatedSelection).split(path.sep).join("/");
      manifest.selection_sha256 = digest(duplicatedSelectionBytes);
      manifest.clips[1].clip_id = manifest.clips[0].clip_id;
      manifest.clips[1].path = manifest.clips[0].path;
      manifest.clips[1].sha256 = manifest.clips[0].sha256;
    });
    await assert.rejects(() => verifyProxyBatch(duplicatedSelection, duplicatedBatch), undefined, "duplicate clip IDs must be rejected before workbook verification");
    await fs.appendFile(missingProxy, "hash change");
    await rejects(selectionPath, workbookPath, "proxy hash change");
    await fs.writeFile(missingProxy, savedProxy);

    const rollbackOutput = path.join(fixtureDir, "rollback-output");
    const rollbackPreviews = path.join(fixtureDir, "rollback-previews");
    let renameCalls = 0;
    await assert.rejects(() => buildActiveReviewBatch(selectionPath, rollbackOutput, rollbackPreviews, {
      rename: async (...args) => { renameCalls += 1; if (renameCalls === 2) throw new Error("second rename failure"); return fs.rename(...args); },
    }));
    assert.equal(await fs.stat(rollbackOutput).then(() => true).catch(() => false), false);
    assert.equal(await fs.stat(rollbackPreviews).then(() => true).catch(() => false), false);
  } finally {
    await fs.rm(fixtureDir, { recursive: true, force: true });
  }
}

await main();
