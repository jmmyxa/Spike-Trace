import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
import JSZip from "jszip";

import { loadEvidenceOverrideEnvelope } from "./active_review_evidence_overrides.mjs";
import { assertStableInput, normalizeRepoPath, parseJsonObjectStrict, readInputSnapshot, sha256 } from "./active_review_io.mjs";
import { inspectActionFormulaBlocks } from "./active_review_xlsx_formulas.mjs";
import { createReviewWorkbook } from "./build_active_review_batch.mjs";
import { verifyWorkbookFile } from "./verify_active_review_batch.mjs";

const AGGREGATE_PERMISSION = { sheet: "人工动作", range: "C4:C483", block_size: 12, expected_display_value: "播放" };

function expectedFormula(clipId) {
  return `HYPERLINK("clips/${clipId}.mp4","播放")`;
}

function replaceCellFormula(xml, cell, replacement) {
  const cellPattern = new RegExp(`(<(?:[A-Za-z_][\\w.-]*:)?c\\b[^>]*\\br="${cell}"[^>]*>)([\\s\\S]*?)(</(?:[A-Za-z_][\\w.-]*:)?c>)`);
  const match = cellPattern.exec(xml);
  const marker = xml.indexOf(`r="${cell}"`);
  assert.ok(match, `Fixture cell ${cell} must exist: ${xml.slice(Math.max(0, marker - 80), marker + 240)}`);
  const formulaPattern = /<(?:[A-Za-z_][\w.-]*:)?f(?:\s[^>]*)?(?:\/>|>[\s\S]*?<\/(?:[A-Za-z_][\w.-]*:)?f>)/;
  assert.match(match[2], formulaPattern, `Fixture cell ${cell} must contain a formula.`);
  const body = match[2].replace(formulaPattern, replacement);
  return `${xml.slice(0, match.index)}${match[1]}${body}${match[3]}${xml.slice(match.index + match[0].length)}`;
}

async function rewriteActionSheet(workbookBytes, mutate) {
  const archive = await JSZip.loadAsync(workbookBytes);
  let actionPart = null;
  let xml = null;
  for (const name of Object.keys(archive.files).filter((entry) => /^xl\/worksheets\/[^/]+\.xml$/.test(entry))) {
    const candidate = await archive.file(name).async("string");
    if (candidate.includes('r="C483"')) { actionPart = name; xml = candidate; break; }
  }
  assert.ok(actionPart, "Fixture action worksheet part must exist.");
  archive.file(actionPart, mutate(xml));
  return Buffer.from(await archive.generateAsync({ type: "uint8array" }));
}

async function rewritePart(workbookBytes, part, mutate) {
  const archive = await JSZip.loadAsync(workbookBytes);
  const entry = archive.file(part);
  assert.ok(entry, `Fixture part ${part} must exist.`);
  archive.file(part, mutate(await entry.async("string")));
  return Buffer.from(await archive.generateAsync({ type: "uint8array" }));
}

async function producerWorkbookBytes(expandedBytes) {
  return rewriteActionSheet(expandedBytes, (source) => {
    let xml = replaceCellFormula(source, "C4", `<x:f t="shared" ref="C4:C15" si="0">${expectedFormula("round-01-clip-001")}</x:f>`);
    for (let row = 5; row <= 15; row += 1) xml = replaceCellFormula(xml, `C${row}`, '<x:f t="shared" si="0"/>');
    xml = replaceCellFormula(xml, "C29", `<x:f t="shared" ref="C28:C39" si="2">${expectedFormula("round-01-clip-003")}</x:f>`);
    for (let row = 30; row <= 39; row += 1) xml = replaceCellFormula(xml, `C${row}`, '<x:f t="shared" si="2"/>');
    return xml;
  });
}

async function writeFixture(root) {
  const selection = {
    format_version: 1,
    batch_id: "synthetic-round-01",
    round_id: "round-01",
    video: { path: "source.mp4", sha256: null },
    clips: Array.from({ length: 40 }, (_, index) => ({
      clip_id: `round-01-clip-${String(index + 1).padStart(3, "0")}`,
      ordinal: index + 1,
      duration_seconds: 5,
      start_seconds: index * 5,
      end_seconds: index * 5 + 5,
      time_stratum: null,
      selection_bucket: null,
      selection_reasons: ["fixture"],
      candidate_hints: [],
    })),
  };
  const sourceBytes = Buffer.from("synthetic source video", "utf8");
  selection.video.sha256 = sha256(sourceBytes);
  const selectionPath = path.join(root, "selection.json");
  const sourcePath = path.join(root, selection.video.path);
  const batchDir = path.join(root, "batch");
  const clipsDir = path.join(batchDir, "clips");
  await fs.mkdir(clipsDir, { recursive: true });
  await fs.writeFile(sourcePath, sourceBytes);
  const selectionBytes = Buffer.from(`${JSON.stringify(selection, null, 2)}\n`, "utf8");
  await fs.writeFile(selectionPath, selectionBytes);
  const manifestClips = [];
  for (const clip of selection.clips) {
    const relativePath = `clips/${clip.clip_id}.mp4`;
    const proxyBytes = Buffer.from(`proxy ${clip.clip_id}`, "utf8");
    await fs.writeFile(path.join(batchDir, relativePath), proxyBytes);
    manifestClips.push({ clip_id: clip.clip_id, ordinal: clip.ordinal, path: relativePath, sha256: sha256(proxyBytes) });
  }
  await fs.writeFile(path.join(batchDir, "proxy-manifest.json"), `${JSON.stringify({
    format_version: 1,
    batch_id: selection.batch_id,
    selection: normalizeRepoPath(selectionPath, root),
    selection_sha256: sha256(selectionBytes),
    video: { path: selection.video.path, sha256: selection.video.sha256 },
    clips: manifestClips,
  }, null, 2)}\n`);
  const expandedPath = path.join(batchDir, "expanded.xlsx");
  await (await SpreadsheetFile.exportXlsx(createReviewWorkbook(selection))).save(expandedPath);
  const expandedBytes = await fs.readFile(expandedPath);
  const producerBytes = await producerWorkbookBytes(expandedBytes);
  const producerPath = path.join(batchDir, "producer.xlsx");
  await fs.writeFile(producerPath, producerBytes);
  return { selection, selectionPath, selectionBytes, producerPath, producerBytes };
}

async function boundCompatibility(fixture, workbookBytes, sharedFormulaRanges) {
  const payload = {
    format: "spiketrace.active-review-evidence-overrides",
    format_version: 1,
    review_set_key: "review/round-01",
    batch_id: fixture.selection.batch_id,
    round_id: fixture.selection.round_id,
    selection: { path: normalizeRepoPath(fixture.selectionPath, fixture.root), sha256: sha256(fixture.selectionBytes) },
    workbook: { path: normalizeRepoPath(fixture.producerPath, fixture.root), sha256: sha256(workbookBytes) },
    video: { path: fixture.selection.video.path, sha256: fixture.selection.video.sha256 },
    workbook_compatibility: { trimmed_banner_cells: [], shared_formula_ranges: sharedFormulaRanges, validation_import_gaps: [], read_only_repairs: [] },
    action_overrides: [],
    supplemental_actions: [],
    outcome_observations: [],
    visibility_observations: [],
    action_participants: [],
  };
  const overridePath = path.join(fixture.root, "override.json");
  const overrideBytes = Buffer.from(`${JSON.stringify(payload, null, 2)}\n`, "utf8");
  return loadEvidenceOverrideEnvelope(overridePath, {
    overrideBytes,
    selection: parseJsonObjectStrict(fixture.selectionBytes, "Selection fixture"),
    selectionPath: fixture.selectionPath,
    selectionBytes: fixture.selectionBytes,
    workbookPath: fixture.producerPath,
    workbookBytes,
    repoRoot: fixture.root,
  });
}

async function verify(fixture, workbookBytes, permissions, bound = null) {
  const boundEvidenceOverrides = bound ?? await boundCompatibility(fixture, workbookBytes, permissions);
  return verifyWorkbookFile(fixture.selectionPath, fixture.producerPath, {
    selectionBytes: fixture.selectionBytes,
    workbookBytes,
    boundEvidenceOverrides,
    repoRoot: fixture.root,
  });
}

const root = await fs.mkdtemp(path.join(os.tmpdir(), "active-review-shared-formulas-"));
try {
  const fixture = { root, ...await writeFixture(root) };
  const imported = await SpreadsheetFile.importXlsx(await FileBlob.load(fixture.producerPath));
  const formulas = imported.worksheets.getItem("人工动作").getRange("C4:C39").formulas.flat();
  assert.equal(formulas[0], `=${expectedFormula("round-01-clip-001")}`);
  assert.ok(formulas.slice(1, 12).every((formula) => formula === ""));
  assert.equal(formulas[24], `=${expectedFormula("round-01-clip-003")}`);
  assert.equal(formulas[25], `=${expectedFormula("round-01-clip-003")}`);
  assert.ok(formulas.slice(26, 36).every((formula) => formula === ""));

  await assert.rejects(() => verify(fixture, fixture.producerBytes, []), /shared hyperlink/);
  await verify(fixture, fixture.producerBytes, [AGGREGATE_PERMISSION]);
  await verify(fixture, fixture.producerBytes, [
    { ...AGGREGATE_PERMISSION, range: "C4:C15" },
    { ...AGGREGATE_PERMISSION, range: "C28:C39" },
  ]);

  for (const permission of [
    { ...AGGREGATE_PERMISSION, range: "C5:C483" },
    { ...AGGREGATE_PERMISSION, block_size: 11 },
    { ...AGGREGATE_PERMISSION, range: "C16:C483" },
    { ...AGGREGATE_PERMISSION, sheet: "短片清单" },
  ]) await assert.rejects(() => verify(fixture, fixture.producerBytes, [permission]), /shared hyperlink|compatibility/);

  const mutations = [
    ["missing follower formula", (xml) => replaceCellFormula(xml, "C5", "")],
    ["wrong master hyperlink", (xml) => replaceCellFormula(xml, "C4", '<x:f t="shared" ref="C4:C15" si="0">HYPERLINK("clips/replaced.mp4","播放")</x:f>')],
    ["wrong follower si", (xml) => replaceCellFormula(xml, "C5", '<x:f t="shared" si="99"/>')],
    ["cross-block master ref", (xml) => replaceCellFormula(xml, "C4", `<x:f t="shared" ref="C4:C27" si="0">${expectedFormula("round-01-clip-001")}</x:f>`)],
    ["duplicate master", (xml) => replaceCellFormula(xml, "C5", `<x:f t="shared" ref="C4:C15" si="0">${expectedFormula("round-01-clip-001")}</x:f>`)],
    ["extra shared cell", (xml) => replaceCellFormula(xml, "C16", '<x:f t="shared" si="0"/>')],
  ];
  for (const [name, mutate] of mutations) {
    const workbookBytes = await rewriteActionSheet(fixture.producerBytes, mutate);
    await assert.rejects(() => verify(fixture, workbookBytes, [AGGREGATE_PERMISSION]), /raw formula|shared hyperlink/, name);
  }

  const foreignFormulaBytes = await rewriteActionSheet(fixture.producerBytes, (xml) => replaceCellFormula(xml, "C5", '<evil:f xmlns:evil="urn:evil" t="shared" si="0"/>'));
  await assert.rejects(() => verify(fixture, foreignFormulaBytes, [AGGREGATE_PERMISSION]), /raw formula/, "foreign-namespace formula must not authenticate a follower");
  const counterfeitRelationshipBytes = await rewritePart(fixture.producerBytes, "xl/workbook.xml", (xml) => xml.replace(/(<[^>]*sheet\b[^>]*name="人工动作"[^>]*?)r:id=/, '$1xmlns:evil="urn:evil" evil:id='));
  await assert.rejects(() => verify(fixture, counterfeitRelationshipBytes, [AGGREGATE_PERMISSION]), /raw formula/, "counterfeit relationship attribute must not resolve the action sheet");
  const wrongRelationshipTypeBytes = await rewritePart(fixture.producerBytes, "xl/_rels/workbook.xml.rels", (xml) => xml.replaceAll("http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet", "urn:evil/worksheet"));
  await assert.rejects(() => verify(fixture, wrongRelationshipTypeBytes, [AGGREGATE_PERMISSION]), /raw formula/, "suffix-only worksheet relationship type must be rejected");

  const originalBinding = await boundCompatibility(fixture, fixture.producerBytes, [AGGREGATE_PERMISSION]);
  const changedBytes = await rewriteActionSheet(fixture.producerBytes, (xml) => xml.replace("播放</v>", "已改</v>"));
  await assert.rejects(() => verify(fixture, changedBytes, [AGGREGATE_PERMISSION], originalBinding), /workbook binding does not match/);
} finally {
  await fs.rm(root, { recursive: true, force: true });
}

if (process.argv[2] === "--real") {
  assert.equal(process.argv.length, 6, "Usage: node tools/test_active_review_shared_formulas.mjs --real SELECTION_JSON REVIEW_XLSX EVIDENCE_OVERRIDES_JSON");
  const selectionPath = process.argv[3];
  const workbookPath = process.argv[4];
  const overridePath = process.argv[5];
  const [selectionSnapshot, workbookSnapshot, overrideSnapshot] = await Promise.all([
    readInputSnapshot(selectionPath, "Selection"),
    readInputSnapshot(workbookPath, "Workbook"),
    readInputSnapshot(overridePath, "Evidence override"),
  ]);
  assert.equal(selectionSnapshot.sha256, "c7c9d4c21ae8fb041eece192b9c4f2c66648c863fdf79c278f08ca11e6cfe06c");
  assert.equal(workbookSnapshot.sha256, "3b3baa474bf5d20e24a2e979b389e5d1b6df755b3c8516c993d8cc719b53535b");
  const selection = parseJsonObjectStrict(selectionSnapshot.bytes, "Selection");
  const override = parseJsonObjectStrict(overrideSnapshot.bytes, "Evidence override");
  const aggregateOverrideBytes = Buffer.from(`${JSON.stringify({
    ...override,
    workbook_compatibility: { ...override.workbook_compatibility, shared_formula_ranges: [AGGREGATE_PERMISSION] },
  }, null, 2)}\n`, "utf8");
  const boundEvidenceOverrides = await loadEvidenceOverrideEnvelope(overrideSnapshot.path, {
    overrideBytes: aggregateOverrideBytes,
    selection,
    selectionPath: selectionSnapshot.path,
    selectionBytes: selectionSnapshot.bytes,
    workbookPath: workbookSnapshot.path,
    workbookBytes: workbookSnapshot.bytes,
    repoRoot: process.cwd(),
  });
  const blocks = await inspectActionFormulaBlocks(workbookSnapshot.bytes, selection);
  assert.equal(blocks.length, 40);
  assert.equal(blocks.filter((block) => block.shared && block.ordinaryCount === 0 && block.sharedCellCount === 12).length, 39);
  assert.deepEqual(blocks.find((block) => block.range === "C28:C39"), { range: "C28:C39", shared: true, ordinaryCount: 1, sharedCellCount: 11, masterCell: "C29" });
  await assert.rejects(() => verifyWorkbookFile(selectionSnapshot.path, workbookSnapshot.path, {
    allowManualValues: true,
    selectionBytes: selectionSnapshot.bytes,
    workbookBytes: workbookSnapshot.bytes,
    boundEvidenceOverrides,
    repoRoot: process.cwd(),
  }), /Clip round-01-clip-023 has overlapping timed rows/);
  await assertStableInput(selectionSnapshot.path, selectionSnapshot.bytes, "Selection");
  await assertStableInput(workbookSnapshot.path, workbookSnapshot.bytes, "Workbook");
  await assertStableInput(overrideSnapshot.path, overrideSnapshot.bytes, "Evidence override");
  process.stdout.write(`${JSON.stringify({ shared_blocks: 40, standard_shared_blocks: 39, mixed_block: "C28:C39", public_next_blocker: "Clip round-01-clip-023 has overlapping timed rows", selection_sha256: selectionSnapshot.sha256, workbook_sha256: workbookSnapshot.sha256 })}\n`);
}
