import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
  ACTIONS,
  CANDIDATE_HEADERS,
  SHEET_NAMES,
  SIDES,
  SOURCE_HEADERS,
  validateMergedShape,
  verifyWorkbook,
  verifyWorkbookFile,
} from "./verify_rangitoto_review.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const BUILD_DIR = path.join(ROOT, "outputs", ".rangitoto-review-build");
const requireFromBuild = createRequire(pathToFileURL(path.join(BUILD_DIR, "artifact-runtime.mjs")));
const { SpreadsheetFile, Workbook } = await import(
  pathToFileURL(requireFromBuild.resolve("@oai/artifact-tool")).href
);

const PYTHON = process.platform === "win32"
  ? path.join(ROOT, ".venv", "Scripts", "python.exe")
  : path.join(ROOT, ".venv", "bin", "python");

const COLORS = {
  navy: "#173F5F",
  teal: "#0F766E",
  tealDark: "#115E59",
  tealLight: "#DCEFEA",
  yellow: "#FFF4CC",
  yellowDark: "#6D5200",
  grayFill: "#F3F6F8",
  grayLine: "#C9D3DC",
  white: "#FFFFFF",
  text: "#243746",
  muted: "#5B6B7B",
  dangerFill: "#FDE8E7",
  dangerText: "#A12722",
};

function invariant(condition, message) {
  if (!condition) throw new Error(message);
}

function sameArray(left, right) {
  return Array.isArray(left) && Array.isArray(right)
    && left.length === right.length
    && left.every((value, index) => value === right[index]);
}

function formatTime(ms) {
  const sign = ms < 0 ? "-" : "";
  let remaining = Math.abs(ms);
  const hours = Math.floor(remaining / 3_600_000);
  remaining -= hours * 3_600_000;
  const minutes = Math.floor(remaining / 60_000);
  remaining -= minutes * 60_000;
  const seconds = Math.floor(remaining / 1000);
  const millis = remaining - seconds * 1000;
  return `${sign}${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

function setWidths(sheet, widths) {
  for (const [column, widthPx] of Object.entries(widths)) {
    sheet.getRange(`${column}:${column}`).format.columnWidthPx = widthPx;
  }
}

function styleHeader(range, fill = COLORS.teal) {
  range.format = {
    fill,
    font: { bold: true, color: COLORS.white },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "outside", style: "thin", color: COLORS.grayLine },
  };
  range.format.rowHeightPx = 34;
}

function styleBody(range) {
  range.format = {
    font: { color: COLORS.text },
    verticalAlignment: "center",
    borders: {
      insideHorizontal: { style: "thin", color: "#E6EDF2" },
    },
  };
}

function addTitle(sheet, lastColumn, title, subtitle) {
  sheet.getRange(`A1:${lastColumn}1`).merge();
  sheet.getRange("A1").values = [[title]];
  sheet.getRange(`A1:${lastColumn}1`).format = {
    fill: COLORS.navy,
    font: { bold: true, color: COLORS.white, size: 16 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange("1:1").format.rowHeightPx = 40;
  sheet.getRange(`A2:${lastColumn}2`).merge();
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange(`A2:${lastColumn}2`).format = {
    fill: COLORS.tealLight,
    font: { color: COLORS.tealDark },
    wrapText: true,
    verticalAlignment: "center",
  };
  sheet.getRange("2:2").format.rowHeightPx = 38;
}

async function runPythonVerifier(mergedJsonPath) {
  await fs.access(PYTHON).catch(() => {
    throw new Error(`Repository Python verifier is missing: ${PYTHON}`);
  });
  const mergedCsvPath = path.join(path.dirname(mergedJsonPath), "merged_candidates.csv");
  await fs.access(mergedCsvPath).catch(() => {
    throw new Error(`Sibling merged CSV is missing: ${mergedCsvPath}`);
  });
  const result = spawnSync(
    PYTHON,
    ["-m", "spiketrace", "verify-dual-crop-review", mergedJsonPath, "--csv", mergedCsvPath],
    { cwd: ROOT, encoding: "utf8", env: { ...process.env, PYTHONUTF8: "1" } },
  );
  invariant(
    result.status === 0,
    `Python merged-artifact verification failed (${result.status}).\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`,
  );
}

function buildProjection(payload) {
  const runBySide = new Map();
  const sourceEventByCandidateId = new Map();
  const windowBySideAndIndex = new Map();
  const allSourceCandidateIds = new Set();

  for (const side of ["far", "near"]) {
    const run = payload.input_runs[side];
    runBySide.set(side, run);
    const windows = new Map();
    for (const window of run.windows) {
      invariant(Number.isInteger(window.window_index), `${side} window_index must be an integer.`);
      invariant(!windows.has(window.window_index), `Duplicate ${side} window_index ${window.window_index}.`);
      windows.set(window.window_index, window);
    }
    windowBySideAndIndex.set(side, windows);
    for (const event of run.events) {
      const candidateId = `${side}:${event.event_id}`;
      invariant(!sourceEventByCandidateId.has(candidateId), `Duplicate source candidate ID ${candidateId}.`);
      sourceEventByCandidateId.set(candidateId, event);
      allSourceCandidateIds.add(candidateId);
    }
  }

  const candidateRows = [];
  const sourceRows = [];
  const referencedCandidateIds = new Set();
  for (const event of payload.events) {
    invariant(Array.isArray(event.source_event_refs) && event.source_event_refs.length > 0, `${event.event_id} has no source_event_refs.`);
    const directCandidateIds = [];
    let directWindowCount = 0;
    let directWindowMaxConfidence = -Infinity;
    for (const ref of event.source_event_refs) {
      const expectedCandidateId = `${ref.side}:${ref.source_event_id}`;
      invariant(ref.candidate_id === expectedCandidateId, `${event.event_id}: malformed candidate ID ${ref.candidate_id}.`);
      invariant(!referencedCandidateIds.has(ref.candidate_id), `Source candidate ${ref.candidate_id} is referenced more than once.`);
      const sourceEvent = sourceEventByCandidateId.get(ref.candidate_id);
      invariant(sourceEvent, `${event.event_id}: source candidate ${ref.candidate_id} does not exist.`);
      invariant(
        sameArray(ref.member_window_indices, sourceEvent.source_window_indices),
        `${event.event_id}: member windows differ for ${ref.candidate_id}.`,
      );
      invariant(
        ref.selected_as_primary === (ref.candidate_id === event.primary_source_event_id),
        `${event.event_id}: primary flag differs for ${ref.candidate_id}.`,
      );

      const windowIndex = windowBySideAndIndex.get(ref.side);
      const memberWindows = ref.member_window_indices.map((index) => {
        invariant(windowIndex.has(index), `${event.event_id}: ${ref.side} window ${index} does not exist.`);
        return windowIndex.get(index);
      });
      const memberMaxConfidence = Math.max(...memberWindows.map((window) => window.confidence));
      directWindowCount += memberWindows.length;
      directWindowMaxConfidence = Math.max(directWindowMaxConfidence, memberMaxConfidence);
      directCandidateIds.push(ref.candidate_id);
      referencedCandidateIds.add(ref.candidate_id);

      const run = runBySide.get(ref.side);
      sourceRows.push([
        event.event_id,
        ref.candidate_id,
        ref.side,
        sourceEvent.event_id,
        sourceEvent.action,
        sourceEvent.confidence,
        sourceEvent.start_ms / 1000,
        sourceEvent.end_ms / 1000,
        formatTime(sourceEvent.start_ms),
        formatTime(sourceEvent.end_ms),
        ref.selected_as_primary ? "是" : "否",
        event.duplicate_group_id,
        event.conflict_group_id,
        memberWindows.length,
        memberMaxConfidence,
        JSON.stringify(run.settings.crop),
        sourceEvent.model_version,
        sourceEvent.source,
        sourceEvent.status,
        ref.member_window_indices.join("|"),
      ]);
    }

    invariant(sameArray(directCandidateIds, event.source_event_ids), `${event.event_id}: source candidate order differs.`);
    invariant(directWindowCount === event.source_window_count, `${event.event_id}: source window count differs.`);
    invariant(directWindowMaxConfidence === event.source_window_max_confidence, `${event.event_id}: source window max confidence differs.`);
    candidateRows.push([
      event.event_id,
      event.video_id,
      event.action,
      formatTime(event.start_ms),
      formatTime(event.end_ms),
      event.start_ms / 1000,
      event.end_ms / 1000,
      event.side,
      event.observed_sides.join("|"),
      event.confidence,
      event.conflict_group_id,
      event.duplicate_group_id,
      event.merge_decision,
      directCandidateIds.join("|"),
      event.source_window_count,
      event.source_window_max_confidence,
      event.review_reason,
      null,
      null,
      null,
      null,
      null,
    ]);
  }

  assert.deepEqual(
    [...referencedCandidateIds].sort(),
    [...allSourceCandidateIds].sort(),
    "Canonical refs must cover every embedded source event exactly once.",
  );
  invariant(sourceRows.length === allSourceCandidateIds.size, "Source row count differs from embedded source event count.");
  return { candidateRows, sourceRows };
}

function buildWorkbook(payload, projection) {
  const workbook = Workbook.create();
  const overview = workbook.worksheets.add("概览");
  const candidates = workbook.worksheets.add("候选动作");
  const sources = workbook.worksheets.add("来源事件");
  const labels = workbook.worksheets.add("标签说明");
  for (const sheet of [overview, candidates, sources, labels]) sheet.showGridLines = false;

  const candidateLastRow = projection.candidateRows.length + 3;
  const sourceLastRow = projection.sourceRows.length + 3;

  addTitle(
    overview,
    "I",
    "Rangitoto 双裁剪动作复核概览",
    "只以“候选动作”页黄色列为人工输入；人工确认动作非空即代表已复核，background 用于拒绝误检。",
  );
  overview.getRange("A4:B7").values = [["总候选", null], ["已复核", null], ["待复核", null], ["冲突候选", null]];
  overview.getRange("B4:B7").formulas = [
    [`=COUNTA('候选动作'!$A$4:$A$${candidateLastRow})`],
    [`=COUNTA('候选动作'!$R$4:$R$${candidateLastRow})`],
    ["=B4-B5"],
    [`=COUNTA('候选动作'!$K$4:$K$${candidateLastRow})`],
  ];
  overview.getRange("D4:F11").values = [["动作", "预测分布", "人工分布"], ...ACTIONS.map((action) => [action, null, null])];
  overview.getRange("E5:F11").formulas = ACTIONS.map((_, index) => [
    `=COUNTIF('候选动作'!$C$4:$C$${candidateLastRow},D${index + 5})`,
    `=COUNTIF('候选动作'!$R$4:$R$${candidateLastRow},D${index + 5})`,
  ]);
  overview.getRange("A4:B7").format = {
    fill: COLORS.grayFill,
    font: { bold: true, color: COLORS.text },
    borders: { preset: "outside", style: "thin", color: COLORS.grayLine },
    verticalAlignment: "center",
  };
  overview.getRange("B4:B7").format = {
    fill: COLORS.white,
    font: { bold: true, color: COLORS.tealDark },
    horizontalAlignment: "center",
  };
  styleHeader(overview.getRange("D4:F4"));
  styleBody(overview.getRange("D5:F11"));
  overview.getRange("E5:F11").format.horizontalAlignment = "center";

  const runRows = ["far", "near"].map((side) => {
    const run = payload.input_runs[side];
    const audit = payload.settings.input_runs[side];
    return [
      side,
      audit.source_file,
      audit.normalized_payload_sha256,
      run.events.length,
      run.windows.length,
      JSON.stringify(run.settings.crop),
      run.model_version,
      run.settings.sampling_contract,
      "自包含格式 2 证据",
    ];
  });
  overview.getRange("A13:I18").values = [
    ["输入", "来源文件", "规范化 SHA256", "事件数", "窗口数", "裁剪", "模型", "采样合同", "说明"],
    ...runRows,
    ["语义", "receive", "仅指对方发球后的接发球", null, null, null, null, null, null],
    ["语义", "dig", "仅指对方进攻后的防守起球", null, null, null, null, null, null],
    ["视角", "far / near", "只表示画面裁剪位置，不表示球队", null, null, null, null, null, null],
  ];
  styleHeader(overview.getRange("A13:I13"), COLORS.navy);
  styleBody(overview.getRange("A14:I18"));
  overview.getRange("B14:C18").format.wrapText = true;
  overview.getRange("14:18").format.rowHeightPx = 38;
  setWidths(overview, { A: 82, B: 300, C: 340, D: 76, E: 76, F: 140, G: 180, H: 170, I: 190 });

  addTitle(
    candidates,
    "V",
    "Rangitoto 候选动作复核表",
    "黄色列可编辑；人工开始/结束时间填写数值秒。预测可读时间仅用于显示。",
  );
  candidates.getRange("A3:V3").values = [CANDIDATE_HEADERS];
  candidates.getRange(`A4:V${candidateLastRow}`).values = projection.candidateRows;
  styleHeader(candidates.getRange("A3:V3"));
  styleBody(candidates.getRange(`A4:V${candidateLastRow}`));
  const candidateTable = candidates.tables.add(`A3:V${candidateLastRow}`, true, "RangitotoCandidateReview");
  candidateTable.style = "TableStyleMedium2";
  candidateTable.showFilterButton = true;
  candidates.getRange(`R4:V${candidateLastRow}`).format = {
    fill: COLORS.yellow,
    font: { color: COLORS.text },
    verticalAlignment: "center",
    borders: { insideHorizontal: { style: "thin", color: "#E8DDA8" } },
  };
  candidates.getRange(`R4:R${candidateLastRow}`).dataValidation = { rule: { type: "list", values: ACTIONS } };
  candidates.getRange(`U4:U${candidateLastRow}`).dataValidation = { rule: { type: "list", values: SIDES } };
  candidates.getRange(`F4:G${candidateLastRow}`).format.numberFormat = "0.000";
  candidates.getRange(`S4:T${candidateLastRow}`).format.numberFormat = "0.000";
  candidates.getRange(`J4:J${candidateLastRow}`).format.numberFormat = "0.000000";
  candidates.getRange(`P4:P${candidateLastRow}`).format.numberFormat = "0.000000";
  candidates.getRange(`K4:K${candidateLastRow}`).conditionalFormats.add("containsText", {
    text: "cg_",
    format: { fill: COLORS.dangerFill, font: { color: COLORS.dangerText, bold: true } },
  });
  candidates.freezePanes.freezeRows(3);
  candidates.freezePanes.freezeColumns(2);
  setWidths(candidates, {
    A: 132, B: 104, C: 86, D: 126, E: 126, F: 98, G: 98, H: 82, I: 96, J: 92, K: 104,
    L: 104, M: 250, N: 320, O: 96, P: 116, Q: 360, R: 132, S: 116, T: 116, U: 94, V: 230,
  });
  candidates.getRange(`M4:N${candidateLastRow}`).format.wrapText = true;
  candidates.getRange(`Q4:Q${candidateLastRow}`).format.wrapText = true;
  candidates.getRange(`V4:V${candidateLastRow}`).format.wrapText = true;
  candidates.getRange(`4:${candidateLastRow}`).format.rowHeightPx = 46;

  addTitle(
    sources,
    "T",
    "来源事件明细",
    "每行对应一个显式 source_event_ref；窗口成员只通过 member_window_indices 直接查找，不按时间推断。",
  );
  sources.getRange("A3:T3").values = [SOURCE_HEADERS];
  sources.getRange(`A4:T${sourceLastRow}`).values = projection.sourceRows;
  styleHeader(sources.getRange("A3:T3"));
  styleBody(sources.getRange(`A4:T${sourceLastRow}`));
  const sourceTable = sources.tables.add(`A3:T${sourceLastRow}`, true, "RangitotoSourceEvents");
  sourceTable.style = "TableStyleMedium2";
  sourceTable.showFilterButton = true;
  sources.getRange(`F4:F${sourceLastRow}`).format.numberFormat = "0.000000";
  sources.getRange(`G4:H${sourceLastRow}`).format.numberFormat = "0.000";
  sources.getRange(`O4:O${sourceLastRow}`).format.numberFormat = "0.000000";
  sources.freezePanes.freezeRows(3);
  sources.freezePanes.freezeColumns(2);
  setWidths(sources, {
    A: 132, B: 216, C: 72, D: 196, E: 86, F: 92, G: 92, H: 92, I: 126, J: 126,
    K: 80, L: 104, M: 104, N: 104, O: 118, P: 148, Q: 190, R: 116, S: 100, T: 132,
  });
  sources.getRange(`4:${sourceLastRow}`).format.rowHeightPx = 32;

  addTitle(
    labels,
    "F",
    "标签说明",
    "当前未指定哪队是我方；far / near 仅代表画面远端/近端裁剪，不代表球队。",
  );
  labels.getRange("A3:F10").values = [
    ["标签", "中文", "定义", "常见边界", "人工侧别", "时间填写"],
    ["background", "无有效动作", "拒绝误检：没有需要保留的有效动作。", "false positive 也必须用此标签完成复核。", "far / near / 不确定", "数值秒"],
    ["serve", "发球", "发球触球动作。", "发球前准备不计。", "far / near / 不确定", "数值秒"],
    ["receive", "接发球", "仅指接对方发球的一传。", "不是所有一传都叫 receive。", "far / near / 不确定", "数值秒"],
    ["set", "二传", "组织进攻的传球动作。", "救球调整可备注说明。", "far / near / 不确定", "数值秒"],
    ["attack", "进攻", "扣球、吊球、推攻等进攻触球。", "与 block 同时出现时分别复核。", "far / near / 不确定", "数值秒"],
    ["block", "拦网", "针对对方进攻的拦网动作。", "拦网尝试未触球可备注。", "far / near / 不确定", "数值秒"],
    ["dig", "防守起球", "仅指对方进攻后的防守起球。", "dig 不是接发球；receive 仅接发球。", "far / near / 不确定", "数值秒"],
  ];
  styleHeader(labels.getRange("A3:F3"));
  styleBody(labels.getRange("A4:F10"));
  labels.getRange("A12:F15").values = [
    ["复核约定", null, null, null, null, null],
    ["复核完成", "人工确认动作非空即代表完成；没有额外状态列、勾选框或操作列。", null, null, null, null],
    ["时间", "人工开始/结束时间填写数值秒；可读时间码是只读显示文本。", null, null, null, null],
    ["来源", "XLSX 只含人工复核行；完整窗口证据保存在已验证的格式 2 JSON。", null, null, null, null],
  ];
  labels.getRange("A12:F12").merge();
  labels.getRange("A12:F12").format = {
    fill: COLORS.yellow,
    font: { bold: true, color: COLORS.yellowDark },
    horizontalAlignment: "center",
  };
  labels.getRange("B13:F15").merge(true);
  styleBody(labels.getRange("A13:F15"));
  labels.getRange("A13:A15").format.font = { bold: true, color: COLORS.text };
  labels.getRange("B13:F15").format.wrapText = true;
  setWidths(labels, { A: 120, B: 300, C: 340, D: 290, E: 168, F: 120 });
  labels.getRange("3:10").format.rowHeightPx = 42;
  labels.getRange("13:15").format.rowHeightPx = 44;

  return workbook;
}

async function renderPreviews(workbook, previewDir, candidateCount, sourceCount) {
  await fs.mkdir(previewDir, { recursive: true });
  const ranges = {
    "概览": "A1:I18",
    "候选动作": `A1:V${Math.min(candidateCount + 3, 10)}`,
    "来源事件": `A1:T${Math.min(sourceCount + 3, 10)}`,
    "标签说明": "A1:F15",
  };
  const paths = [];
  for (const sheetName of SHEET_NAMES) {
    const blob = await workbook.render({ sheetName, range: ranges[sheetName], scale: 1.25, format: "png" });
    const previewPath = path.join(previewDir, `${sheetName}.png`);
    await fs.writeFile(previewPath, new Uint8Array(await blob.arrayBuffer()));
    paths.push(previewPath);
  }
  return paths;
}

async function main() {
  const [, , mergedJsonArg, outputXlsxArg, previewDirArg] = process.argv;
  invariant(
    mergedJsonArg && outputXlsxArg && previewDirArg && process.argv.length === 5,
    "Usage: node tools/build_rangitoto_review.mjs MERGED_JSON OUTPUT_XLSX PREVIEW_DIR",
  );
  const mergedJsonPath = path.resolve(mergedJsonArg);
  const outputXlsxPath = path.resolve(outputXlsxArg);
  const previewDir = path.resolve(previewDirArg);
  const payload = JSON.parse(await fs.readFile(mergedJsonPath, "utf8"));
  validateMergedShape(payload);
  await runPythonVerifier(mergedJsonPath);
  const projection = buildProjection(payload);
  const workbook = buildWorkbook(payload, projection);
  await verifyWorkbook(payload, workbook, "pre-export workbook");
  const previews = await renderPreviews(workbook, previewDir, projection.candidateRows.length, projection.sourceRows.length);

  await fs.mkdir(path.dirname(outputXlsxPath), { recursive: true });
  const temporaryXlsxPath = `${outputXlsxPath}.building-${process.pid}.xlsx`;
  try {
    const exported = await SpreadsheetFile.exportXlsx(workbook);
    await exported.save(temporaryXlsxPath);
    const verification = await verifyWorkbookFile(mergedJsonPath, temporaryXlsxPath);
    await fs.copyFile(temporaryXlsxPath, outputXlsxPath);
    console.log(JSON.stringify({ output: outputXlsxPath, previews, ...verification }));
  } finally {
    await fs.rm(temporaryXlsxPath, { force: true });
    await fs.rm(`${temporaryXlsxPath}.inspect.ndjson`, { force: true });
  }
}

main().then(
  () => process.exit(0),
  (error) => {
    console.error(error);
    process.exit(1);
  },
);
