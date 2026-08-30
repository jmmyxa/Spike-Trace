import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

import {
  ACTION_HEADERS,
  ACTIONS,
  CLIP_HEADERS,
  HINT_HEADERS,
  SHEET_NAMES,
  SIDES,
  scanFormulaErrors,
  selectionProjection,
  verifyProxyBatch,
  verifyWorkbookFile,
} from "./verify_active_review_batch.mjs";

const COLORS = { navy: "#173F5F", teal: "#0F766E", paleTeal: "#DCEFEA", yellow: "#FFF4CC", ink: "#243746", line: "#D5DEE5", white: "#FFFFFF" };

function invariant(condition, message) {
  if (!condition) throw new Error(message);
}

function styleHeader(range, fill = COLORS.teal) {
  range.format = { fill, font: { bold: true, color: COLORS.white }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true, borders: { preset: "outside", style: "thin", color: COLORS.line } };
  range.format.rowHeightPx = 34;
}

function title(sheet, lastColumn, heading, detail) {
  sheet.getRange(`A1:${lastColumn}1`).merge();
  sheet.getRange("A1").values = [[heading]];
  sheet.getRange("A1").format = { fill: COLORS.navy, font: { bold: true, color: COLORS.white, size: 16 }, horizontalAlignment: "center", verticalAlignment: "center" };
  sheet.getRange("1:1").format.rowHeightPx = 38;
  sheet.getRange(`A2:${lastColumn}2`).merge();
  sheet.getRange("A2").values = [[detail]];
  sheet.getRange("A2").format = { fill: COLORS.paleTeal, font: { color: COLORS.ink }, wrapText: true, verticalAlignment: "center" };
  sheet.getRange("2:2").format.rowHeightPx = 42;
}

function setWidths(sheet, widths) {
  for (const [column, pixels] of Object.entries(widths)) sheet.getRange(`${column}:${column}`).format.columnWidthPx = pixels;
}

export function createReviewWorkbook(selection) {
  const projection = selectionProjection(selection);
  const workbook = Workbook.create();
  const clips = workbook.worksheets.add("短片清单");
  const actions = workbook.worksheets.add("人工动作");
  const hints = workbook.worksheets.add("候选提示");
  const labels = workbook.worksheets.add("标签说明");
  for (const sheet of [clips, actions, hints, labels]) sheet.showGridLines = false;

  title(clips, "K", "主动学习短片清单", "只读投影；请用相对链接播放同目录 clips 文件夹中的代理短片。选择理由会自动换行。");
  clips.getRange("A3:K3").values = [CLIP_HEADERS];
  clips.getRange("A4:K43").values = projection.clipRows;
  clips.getRange("C4:C43").formulas = selection.clips.map((clip) => [`=HYPERLINK("clips/${clip.clip_id}.mp4","播放")`]);
  styleHeader(clips.getRange("A3:K3"));
  clips.getRange("A4:K43").format = { verticalAlignment: "center", font: { color: COLORS.ink }, borders: { insideHorizontal: { style: "thin", color: "#E9EEF2" } } };
  clips.getRange("J4:J43").format.wrapText = true;
  clips.getRange("4:43").format.rowHeightPx = 54;
  clips.getRange("E4:G43").format.numberFormat = "0.000";
  clips.freezePanes.freezeRows(3);
  setWidths(clips, { A: 56, B: 190, C: 74, D: 190, E: 110, F: 110, G: 110, H: 92, I: 180, J: 420, K: 95 });

  title(actions, "I", "人工动作", "只填写浅黄色的五列。每条记录使用当前短片的相对整秒；没有状态列或勾选框。");
  actions.getRange("A3:I3").values = [ACTION_HEADERS];
  actions.getRange("A4:I483").values = projection.actionRows;
  actions.getRange("C4:C483").formulas = projection.actionRows.map((row) => [`=HYPERLINK("clips/${row[0]}.mp4","播放")`]);
  styleHeader(actions.getRange("A3:D3"));
  styleHeader(actions.getRange("E3:I3"), "#BA8B00");
  actions.getRange("A4:I483").format = { verticalAlignment: "center", font: { color: COLORS.ink }, borders: { insideHorizontal: { style: "thin", color: "#E9EEF2" } } };
  actions.getRange("E4:I483").format.fill = COLORS.yellow;
  actions.getRange("E4:E483").dataValidation = { rule: { type: "list", values: ACTIONS } };
  actions.getRange("F4:G483").dataValidation = { rule: { type: "whole", operator: "greaterThanOrEqual", formula1: 0 } };
  actions.getRange("H4:H483").dataValidation = { rule: { type: "list", values: SIDES } };
  actions.getRange("D4:D483").format.numberFormat = "0.000";
  actions.getRange("I4:I483").format.wrapText = true;
  actions.getRange("4:483").format.rowHeightPx = 24;
  actions.freezePanes.freezeRows(3);
  actions.freezePanes.freezeColumns(2);
  setWidths(actions, { A: 190, B: 76, C: 74, D: 116, E: 130, F: 130, G: 130, H: 104, I: 310 });

  const hintLastRow = 3 + projection.hintRows.length;
  title(hints, "J", "候选提示", "只读模型提示；它们是参考，不是人工审核结果。");
  hints.getRange("A3:J3").values = [HINT_HEADERS];
  if (projection.hintRows.length) hints.getRange(`A4:J${hintLastRow}`).values = projection.hintRows;
  styleHeader(hints.getRange("A3:J3"));
  if (projection.hintRows.length) {
    hints.getRange(`A4:J${hintLastRow}`).format = { verticalAlignment: "center", font: { color: COLORS.ink }, borders: { insideHorizontal: { style: "thin", color: "#E9EEF2" } } };
    hints.getRange(`G4:J${hintLastRow}`).format.wrapText = true;
    hints.getRange(`4:${hintLastRow}`).format.rowHeightPx = 42;
    hints.getRange(`C4:F${hintLastRow}`).format.numberFormat = "0.000";
  }
  hints.freezePanes.freezeRows(3);
  setWidths(hints, { A: 190, B: 250, C: 110, D: 110, E: 112, F: 92, G: 110, H: 130, I: 130, J: 320 });

  title(labels, "B", "标签说明", "请先阅读再填写“人工动作”页。");
  labels.getRange("A3:B10").values = [
    ["规则", "说明"],
    ["receive / dig", "receive 仅指接对方发球的一传；dig 仅指对方进攻后的防守起球。"],
    ["相对秒", "片段内开始秒和结束秒必须填写非负整数，单位是相对当前短片的秒。"],
    ["background", "background 必须单独使用，且开始秒与结束秒两个单元格都必须保持空白。"],
    ["人工侧别", "background 仍须选择当前 far 或 near 的球队裁剪；far/near 只表示画面远端/近端裁剪，不表示球队身份。"],
    ["完成方式", "没有状态列、审核列或 checkbox；填写人工确认动作即是人工记录。"],
    ["容量", "每个短片固定 12 个动作槽。若 12 槽都不足，请不要覆盖现有行，应请求扩容版本。"],
    ["代理文件", "代理短片无音频且已降分辨率，只用于便携复核。"],
  ];
  styleHeader(labels.getRange("A3:B3"));
  labels.getRange("A4:B10").format = { verticalAlignment: "center", font: { color: COLORS.ink }, borders: { insideHorizontal: { style: "thin", color: "#E9EEF2" } } };
  labels.getRange("B4:B10").format.wrapText = true;
  labels.getRange("4:10").format.rowHeightPx = 46;
  setWidths(labels, { A: 145, B: 620 });
  return workbook;
}

async function renderPreviews(workbook, previewDir) {
  await fs.mkdir(previewDir, { recursive: true });
  for (const sheetName of SHEET_NAMES) {
    const blob = await workbook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
    const bytes = new Uint8Array(await blob.arrayBuffer());
    invariant(bytes.length > 0, `${sheetName} preview is empty.`);
    await fs.writeFile(path.join(previewDir, `${sheetName}.png`), bytes);
  }
}

function pythonExecutable(root) {
  if (process.env.SPIKETRACE_PYTHON) return process.env.SPIKETRACE_PYTHON;
  return process.platform === "win32" ? path.join(root, ".venv", "Scripts", "python.exe") : path.join(root, ".venv", "bin", "python");
}

export async function buildActiveReviewBatch(selectionPath, outputDir, previewDir, { rename = fs.rename } = {}) {
  const root = process.cwd();
  invariant(await fs.stat(path.join(root, "pyproject.toml")).then(() => true).catch(() => false), "Run this command from the repository root containing pyproject.toml.");
  const selection = path.resolve(root, selectionPath);
  const output = path.resolve(root, outputDir);
  const previews = path.resolve(root, previewDir);
  invariant(!(await fs.stat(output).then(() => true).catch(() => false)), `Output directory already exists: ${output}`);
  invariant(!(await fs.stat(previews).then(() => true).catch(() => false)), `Preview directory already exists: ${previews}`);
  const staging = path.join(path.dirname(output), `.${path.basename(output)}.tmp-${process.pid}`);
  const previewStaging = path.join(path.dirname(previews), `.${path.basename(previews)}.tmp-${process.pid}`);
  invariant(!(await fs.stat(staging).then(() => true).catch(() => false)), `Staging directory already exists: ${staging}`);
  invariant(!(await fs.stat(previewStaging).then(() => true).catch(() => false)), `Preview staging directory already exists: ${previewStaging}`);
  let outputPublished = false;
  try {
    await fs.mkdir(path.dirname(output), { recursive: true });
    const result = spawnSync(pythonExecutable(root), ["-m", "spiketrace", "build-review-clips", selection, staging, "--repo-root", root], { cwd: root, encoding: "utf8", env: { ...process.env, PYTHONUTF8: "1" } });
    invariant(result.status === 0, `Proxy build failed (${result.status}).\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
    const { selection: payload } = await verifyProxyBatch(selection, staging, { repoRoot: root });
    const workbook = createReviewWorkbook(payload);
    await renderPreviews(workbook, previewStaging);
    await scanFormulaErrors(workbook, "Built workbook");
    const exported = await SpreadsheetFile.exportXlsx(workbook);
    const workbookPath = path.join(staging, "review.xlsx");
    await exported.save(workbookPath);
    await verifyWorkbookFile(selection, workbookPath);
    await rename(staging, output);
    outputPublished = true;
    await rename(previewStaging, previews);
    return { output_dir: output, preview_dir: previews, workbook: path.join(output, "review.xlsx") };
  } catch (error) {
    if (outputPublished) await fs.rm(output, { recursive: true, force: true });
    throw error;
  } finally {
    await fs.rm(staging, { recursive: true, force: true });
    await fs.rm(previewStaging, { recursive: true, force: true });
  }
}

async function main() {
  const [selectionPath, outputDir, previewDir] = process.argv.slice(2);
  invariant(selectionPath && outputDir && previewDir && process.argv.length === 5, "Usage: node tools/build_active_review_batch.mjs SELECTION_JSON OUTPUT_DIR PREVIEW_DIR");
  const result = await buildActiveReviewBatch(selectionPath, outputDir, previewDir);
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error.stack ?? error.message}\n`);
    process.exitCode = 1;
  });
}
