import assert from "node:assert/strict";

const BANNER_CELLS = [
  ["短片清单", "A1", "主动学习短片清单"], ["短片清单", "A2", "只读投影；请用相对链接播放同目录 clips 文件夹中的代理短片。选择理由会自动换行。"],
  ["人工动作", "A1", "人工动作"], ["人工动作", "A2", "只填写浅黄色的五列。每条记录使用当前短片的相对整秒；没有状态列或勾选框。"],
  ["候选提示", "A1", "候选提示"], ["候选提示", "A2", "只读模型提示；它们是参考，不是人工审核结果。"],
  ["标签说明", "A1", "标签说明"], ["标签说明", "A2", "请先阅读再填写“人工动作”页。"],
];
const ACTIONS = ["background", "serve", "receive", "set", "attack", "block", "dig"];
const SIDES = ["far", "near"];
const CLIP_HEADERS = ["序号", "短片ID", "播放短片", "代理文件", "片段长度(秒)", "原视频开始", "原视频结束", "时间分层", "选择桶", "选择原因", "候选提示数"];
const ACTION_HEADERS = ["短片ID", "动作序号", "播放短片", "片段长度(秒)", "人工确认动作", "片段内开始秒", "片段内结束秒", "人工侧别", "备注"];
const HINT_HEADERS = ["短片ID", "候选ID", "相对开始秒", "相对结束秒", "预测动作", "置信度", "观察裁剪", "重复组", "冲突组", "来源候选ID"];
const LABEL_VALUES = [
  ["规则", "说明"],
  ["receive / dig", "receive 仅指接对方发球的一传；dig 仅指对方进攻后的防守起球。"],
  ["相对秒", "片段内开始秒和结束秒必须填写非负整数，单位是相对当前短片的秒。"],
  ["background", "background 必须单独使用，且开始秒与结束秒两个单元格都必须保持空白。"],
  ["人工侧别", "background 仍须选择当前 far 或 near 的球队裁剪；far/near 只表示画面远端/近端裁剪，不表示球队身份。"],
  ["完成方式", "没有状态列、审核列或 checkbox；填写人工确认动作即是人工记录。"],
  ["容量", "每个短片固定 12 个动作槽。若 12 槽都不足，请不要覆盖现有行，应请求扩容版本。"],
  ["代理文件", "代理短片无音频且已降分辨率，只用于便携复核。"],
];

function blank(value) { return value === null || value === undefined || value === ""; }
function normalize(value) { return blank(value) ? null : value; }
function fail(message) { throw new Error(message); }
function expectedHyperlink(clipId) { return `=HYPERLINK("clips/${clipId}.mp4","播放")`; }
function rangeValues(sheet, address) { return sheet.getRange(address).values; }
function rowInRange(range, row) {
  const match = /^C(\d+):C(\d+)$/.exec(range ?? "");
  return Boolean(match && row >= Number(match[1]) && row <= Number(match[2]));
}
function sharedFormula(cell) { return typeof cell === "object" && cell !== null ? cell.sharedFormula : undefined; }
function formulaText(cell) { return typeof cell === "string" ? cell : cell?.formula; }

async function readWorkbookSemanticSnapshot(workbook) {
  const names = [];
  const sheets = [];
  for (let index = 0; index < 20; index += 1) {
    let sheet;
    try { sheet = workbook.worksheets.getItemAt(index); } catch { break; }
    if (!sheet) break;
    names.push(sheet.name);
    const used = sheet.getUsedRange();
    sheets.push({
      name: sheet.name,
      usedRange: [used.rowIndex, used.columnIndex, used.rowCount, used.columnCount],
      values: used.values,
      formulas: used.formulas,
      banners: { A1: rangeValues(sheet, "A1")[0][0], A2: rangeValues(sheet, "A2")[0][0] },
      validations: {
        action: sheet.name === "人工动作" ? {
          E: sheet.getRange("E4:E483").dataValidation,
          FG: sheet.getRange("F4:G483").dataValidation,
          H: sheet.getRange("H4:H483").dataValidation,
        } : null,
      },
    });
  }
  return { names, sheets };
}

function compatibilityPayload(boundEvidenceOverrides) {
  return boundEvidenceOverrides?.payload?.workbook_compatibility ?? null;
}

function allowedBanner(snapshot, sheet, cell, value, compatibility) {
  const expected = BANNER_CELLS.find((entry) => entry[0] === sheet && entry[1] === cell)?.[2];
  if (value === expected) return;
  const permission = cell === "A2" ? compatibility?.trimmed_banner_cells?.find((entry) => entry.sheet === sheet && entry.cell === cell) : null;
  if (!permission || permission.expected_value.trimEnd() !== expected || permission.actual_value !== value || value !== permission.expected_value.trimEnd()) fail(`${sheet} ${cell} banner mismatch.`);
}

function verifyWorkbookSemanticSnapshot(snapshot, { selection, projection, workbookSha256, boundEvidenceOverrides }) {
  const compatibility = compatibilityPayload(boundEvidenceOverrides);
  assert.deepEqual(snapshot.names, ["短片清单", "人工动作", "候选提示", "标签说明"], "Workbook sheet order/count must be exact.");
  const byName = new Map(snapshot.sheets.map((sheet) => [sheet.name, sheet]));
  for (const [sheet, cell] of BANNER_CELLS) allowedBanner(snapshot, sheet, cell, byName.get(sheet)?.banners[cell], compatibility);
  const clips = byName.get("短片清单");
  const actions = byName.get("人工动作");
  const hints = byName.get("候选提示");
  const labels = byName.get("标签说明");
  assert.deepEqual(clips.usedRange, [0, 0, 43, 11], "Clip sheet used range");
  assert.deepEqual(actions.usedRange, [0, 0, 483, 9], "Action sheet used range");
  assert.deepEqual(hints.usedRange, [0, 0, 3 + projection.hintRows.length, 10], "Hint sheet used range");
  assert.deepEqual(labels.usedRange, [0, 0, 10, 2], "Label sheet used range");
  const clipSheet = byName.get("短片清单");
  const actionSheet = byName.get("人工动作");
  const clipValues = clipSheet.values;
  const actionValues = actionSheet.values;
  const normalized = (rows) => rows.map((row) => row.map(normalize));
  assert.deepEqual(clips.values[2], CLIP_HEADERS, "Clip headers");
  assert.deepEqual(actions.values[2], ACTION_HEADERS, "Action headers");
  assert.deepEqual(hints.values[2], HINT_HEADERS, "Hint headers");
  assert.deepEqual(labels.values.slice(2, 10), LABEL_VALUES, "Label instructions");
  assert.deepEqual(normalized(clipValues.slice(3).map((row) => row.slice(0, 2))), projection.clipRows.map((row) => row.slice(0, 2)), "Clip identifiers");
  assert.deepEqual(normalized(clipValues.slice(3).map((row) => row.slice(3, 11))), projection.clipRows.map((row) => row.slice(3)), "Clip read-only values");
  assert.deepEqual(normalized(hints.values.slice(3)), projection.hintRows, "Hint read-only values");
  const sourceRows = actionValues.slice(3, 483);
  const actualActionReadOnly = normalized(sourceRows.map((row) => [row[0], row[1], row[3]]));
  const expectedActionReadOnly = projection.actionRows.map((row) => [row[0], row[1], row[3]]);
  for (let index = 0; index < actualActionReadOnly.length; index += 1) {
    const repair = compatibility?.read_only_repairs?.find((entry) => entry.sheet === "人工动作" && entry.cell === `A${index + 4}`);
    if (repair && actualActionReadOnly[index][0] === null) actualActionReadOnly[index][0] = expectedActionReadOnly[index][0];
  }
  assert.deepEqual(actualActionReadOnly, expectedActionReadOnly, "Action read-only values");
  for (const [index, clip] of selection.clips.entries()) {
    const cell = clipSheet.formulas[index + 3][2];
    assert.equal(formulaText(cell), expectedHyperlink(clip.clip_id), `Clip hyperlink ${clip.clip_id}`);
    assert.equal(clipSheet.values[index + 3][2], "播放", `Clip hyperlink display ${clip.clip_id}`);
  }
  for (let block = 0; block < selection.clips.length; block += 1) {
    const start = 4 + block * 12;
    const range = `C${start}:C${start + 11}`;
    const cells = actionSheet.formulas.slice(start - 1, start + 11).map((row) => row[2]);
    const expected = expectedHyperlink(selection.clips[block].clip_id);
    const expanded = cells.every((cell) => formulaText(cell) === expected && sharedFormula(cell) === undefined);
    if (!expanded) {
      const permission = compatibility?.shared_formula_ranges?.find((entry) => entry.sheet === "人工动作" && entry.range === range && entry.block_size === 12 && entry.expected_display_value === "播放");
      const references = cells.map(sharedFormula);
      const master = references.find((entry) => entry?.ref === range);
      if (!permission || !master || !Number.isInteger(master.index) || references.some((entry) => !entry || entry.index !== master.index || (entry.ref !== null && entry.ref !== range) || entry.text !== "") || cells.some((cell) => formulaText(cell) !== null && formulaText(cell) !== expected)) fail(`Action shared hyperlink block ${range} is invalid.`);
    }
    for (let slot = 0; slot < 12; slot += 1) if (actionSheet.values[start - 1 + slot][2] !== "播放") fail(`Action hyperlink display row ${start + slot} is invalid.`);
  }
  const expectedGaps = new Map((compatibility?.validation_import_gaps ?? []).map((gap) => [gap.range, gap]));
  const validationRules = [["E4:E483", actionSheet.validations.action?.E], ["F4:G483", actionSheet.validations.action?.FG], ["H4:H483", actionSheet.validations.action?.H]];
  for (const [range, validation] of validationRules) {
    if (validation?.rule) {
      const rule = validation.rule;
      if (range === "E4:E483" && (rule.type !== "list" || JSON.stringify(rule.values) !== JSON.stringify(ACTIONS))) fail(`Action ${range} validation changed.`);
      if (range === "F4:G483" && (rule.type !== "whole" || rule.operator !== "greaterThanOrEqual" || Number(rule.formula1) !== 0)) fail(`Action ${range} validation changed.`);
      if (range === "H4:H483" && (rule.type !== "list" || JSON.stringify(rule.values) !== JSON.stringify(SIDES))) fail(`Action ${range} validation changed.`);
      continue;
    }
    const gap = expectedGaps.get(range);
    if (!gap) fail(`Action ${range} validation is missing.`);
  }
  return { snapshot, workbookSha256, compatibility };
}

function canonicalizeWorkbookActionRows(selection, verifiedActionRows, { boundEvidenceOverrides, requirePopulatedSources = true } = {}) {
  const repairs = compatibilityPayload(boundEvidenceOverrides)?.read_only_repairs ?? [];
  const canonicalActionRows = [];
  const normalizationAudit = [];
  for (const [index, row] of verifiedActionRows.entries()) {
    const clipIndex = Math.floor(index / 12);
    const clip = selection.clips[clipIndex];
    const slot = (index % 12) + 1;
    const rawReadOnlyClip = normalize(row[0]);
    const repair = repairs.find((entry) => entry.clip_id === clip.clip_id && entry.source_action_slot === slot);
    if (rawReadOnlyClip !== clip.clip_id) {
      if (!repair || rawReadOnlyClip !== null) fail(`Action row ${index + 4} clip_id does not match selection.`);
      normalizationAudit.push({ sheet: "人工动作", cell: `A${index + 4}`, field: "clip_id", original_value: rawReadOnlyClip, normalized_value: clip.clip_id, source_repair: repair });
    }
    if (repair && rawReadOnlyClip !== null) fail(`Action row ${index + 4} repair requires a blank clip_id.`);
    const values = row.slice(4, 9).map(normalize);
    const populated = values.some((value) => value !== null);
    if (!populated) continue;
    const [reviewLabel, start, end, teamSide, note] = values;
    if (!ACTIONS.includes(reviewLabel)) fail(`Manual row ${index + 4} needs an allowed action.`);
    if (teamSide !== null && !SIDES.includes(teamSide)) fail(`Manual row ${index + 4} needs far or near.`);
    if ((start === null) !== (end === null) || (start !== null && (!Number.isInteger(start) || start < 0 || !Number.isInteger(end) || end <= start || end > clip.duration_seconds))) fail(`Manual row ${index + 4} needs paired non-negative whole-second times within the clip.`);
    if (reviewLabel === "background" && start === null && end === null) { /* clip sentinel checked below */ }
    canonicalActionRows.push({ action_ref: `${clip.clip_id}/action-${String(slot).padStart(3, "0")}`, clip_id: clip.clip_id, source_action_slot: slot, source_row: index + 4, raw_values: { clip_id: rawReadOnlyClip, review_label: reviewLabel, relative_start_seconds: start, relative_end_seconds: end, team_side: teamSide, note }, normalized_values: { clip_id: clip.clip_id, review_label: reviewLabel, relative_start_seconds: start, relative_end_seconds: end, team_side: teamSide, note }, background_scope: reviewLabel === "background" ? (start === null ? "clip_sentinel" : "timed_interval") : null, side_inherited: false, source_repairs: repair ? [repair] : [] });
  }
  const byClip = new Map();
  for (const row of canonicalActionRows) { if (!byClip.has(row.clip_id)) byClip.set(row.clip_id, []); byClip.get(row.clip_id).push(row); }
  for (const clip of selection.clips) {
    const rows = byClip.get(clip.clip_id) ?? [];
    if (rows.length === 0) {
      if (requirePopulatedSources) fail(`Clip ${clip.clip_id} has zero populated source rows.`);
      continue;
    }
    let side = null;
    let sentinel = null;
    for (const row of rows) {
      if (row.normalized_values.team_side !== null) { if (side !== null && side !== row.normalized_values.team_side) fail(`Clip ${clip.clip_id} has conflicting sides.`); side = row.normalized_values.team_side; }
      else { if (side === null) fail(`Clip ${clip.clip_id} has a blank first populated side.`); row.normalized_values.team_side = side; row.side_inherited = true; }
      if (row.review_label === "background" && row.background_scope === "clip_sentinel") { if (sentinel || rows.length !== 1) fail(`Clip ${clip.clip_id} untimed background must be the only populated row.`); sentinel = row; }
    }
    const timed = rows.filter((row) => row.background_scope === "timed_interval" || row.normalized_values.review_label !== "background");
    for (let i = 0; i < timed.length; i += 1) for (let j = i + 1; j < timed.length; j += 1) if (timed[i].normalized_values.team_side === timed[j].normalized_values.team_side && timed[i].normalized_values.relative_start_seconds < timed[j].normalized_values.relative_end_seconds && timed[j].normalized_values.relative_start_seconds < timed[i].normalized_values.relative_end_seconds) fail(`Clip ${clip.clip_id} has overlapping timed rows.`);
  }
  return { canonicalActionRows, normalizationAudit };
}

export async function verifyWorkbookSemantics(workbook, selection, projection, workbookSha256, verifiedActionRows, options = {}) {
  const snapshot = await readWorkbookSemanticSnapshot(workbook);
  const verified = verifyWorkbookSemanticSnapshot(snapshot, { selection, projection, workbookSha256, boundEvidenceOverrides: options.boundEvidenceOverrides });
  const canonical = canonicalizeWorkbookActionRows(selection, verifiedActionRows, options);
  return { ...verified, ...canonical };
}
