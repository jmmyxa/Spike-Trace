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
  verifyProxyBatch,
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

async function main() {
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
