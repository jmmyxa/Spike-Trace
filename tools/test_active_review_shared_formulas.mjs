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
const PRODUCER_PERMISSIONS = [
  { ...AGGREGATE_PERMISSION, range: "C4:C15" },
  { ...AGGREGATE_PERMISSION, range: "C28:C39" },
];

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

async function rewriteActionSheet(workbookBytes, mutate, generateOptions = {}) {
  const archive = await JSZip.loadAsync(workbookBytes);
  let actionPart = null;
  let xml = null;
  for (const name of Object.keys(archive.files).filter((entry) => /^xl\/worksheets\/[^/]+\.xml$/.test(entry))) {
    const candidate = await archive.file(name).async("string");
    if (candidate.includes('r="C483"')) { actionPart = name; xml = candidate; break; }
  }
  assert.ok(actionPart, "Fixture action worksheet part must exist.");
  archive.file(actionPart, mutate(xml));
  return Buffer.from(await archive.generateAsync({ type: "uint8array", ...generateOptions }));
}

async function rewritePart(workbookBytes, part, mutate) {
  const archive = await JSZip.loadAsync(workbookBytes);
  const entry = archive.file(part);
  assert.ok(entry, `Fixture part ${part} must exist.`);
  archive.file(part, mutate(await entry.async("string")));
  return Buffer.from(await archive.generateAsync({ type: "uint8array" }));
}

async function rewriteActionRelationship(workbookBytes, mutate) {
  const archive = await JSZip.loadAsync(workbookBytes);
  const workbookXml = await archive.file("xl/workbook.xml").async("string");
  const sheet = /<[^>]*sheet\b[^>]*name="人工动作"[^>]*\/>/.exec(workbookXml)?.[0];
  const relationshipId = /(?:^|\s)(?:[A-Za-z_][\w.-]*:)?id="([^"]+)"/.exec(sheet ?? "")?.[1];
  assert.ok(relationshipId, "Fixture action worksheet relationship ID must exist.");
  const part = "xl/_rels/workbook.xml.rels";
  const relationshipsXml = await archive.file(part).async("string");
  archive.file(part, mutate(relationshipsXml, relationshipId));
  return Buffer.from(await archive.generateAsync({ type: "uint8array" }));
}

function duplicateCentralDirectoryEntry(workbookBytes, entryName) {
  const bytes = Buffer.from(workbookBytes);
  let eocdOffset = -1;
  for (let offset = bytes.length - 22; offset >= Math.max(0, bytes.length - 65_557); offset -= 1) {
    if (bytes.readUInt32LE(offset) === 0x06054b50 && offset + 22 + bytes.readUInt16LE(offset + 20) === bytes.length) {
      eocdOffset = offset;
      break;
    }
  }
  assert.notEqual(eocdOffset, -1, "Fixture ZIP end record must exist.");
  const centralOffset = bytes.readUInt32LE(eocdOffset + 16);
  const centralSize = bytes.readUInt32LE(eocdOffset + 12);
  let entryOffset = centralOffset;
  let duplicate = null;
  while (entryOffset < centralOffset + centralSize) {
    assert.equal(bytes.readUInt32LE(entryOffset), 0x02014b50, "Fixture central directory entry must be valid.");
    const nameLength = bytes.readUInt16LE(entryOffset + 28);
    const extraLength = bytes.readUInt16LE(entryOffset + 30);
    const commentLength = bytes.readUInt16LE(entryOffset + 32);
    const recordLength = 46 + nameLength + extraLength + commentLength;
    const name = bytes.subarray(entryOffset + 46, entryOffset + 46 + nameLength).toString("utf8");
    if (name === entryName) duplicate = bytes.subarray(entryOffset, entryOffset + recordLength);
    entryOffset += recordLength;
  }
  assert.ok(duplicate, `Fixture central directory entry ${entryName} must exist.`);
  const eocd = Buffer.from(bytes.subarray(eocdOffset));
  eocd.writeUInt16LE(eocd.readUInt16LE(8) + 1, 8);
  eocd.writeUInt16LE(eocd.readUInt16LE(10) + 1, 10);
  eocd.writeUInt32LE(centralSize + duplicate.length, 12);
  return Buffer.concat([bytes.subarray(0, eocdOffset), duplicate, eocd]);
}

function crc32(bytes) {
  let value = 0xffffffff;
  for (const byte of bytes) {
    value ^= byte;
    for (let bit = 0; bit < 8; bit += 1) value = (value >>> 1) ^ ((value & 1) ? 0xedb88320 : 0);
  }
  return (value ^ 0xffffffff) >>> 0;
}

function unicodePathExtra(rawName, effectiveName) {
  const unicodeName = Buffer.from(effectiveName, "utf8");
  const unicodeExtra = Buffer.alloc(4 + 1 + 4 + unicodeName.length);
  unicodeExtra.writeUInt16LE(0x7075, 0);
  unicodeExtra.writeUInt16LE(1 + 4 + unicodeName.length, 2);
  unicodeExtra[4] = 1;
  unicodeExtra.writeUInt32LE(crc32(rawName), 5);
  unicodeName.copy(unicodeExtra, 9);
  return unicodeExtra;
}

function addUnicodePathOverride(workbookBytes, entryName, effectiveName) {
  const bytes = Buffer.from(workbookBytes);
  let eocdOffset = -1;
  for (let offset = bytes.length - 22; offset >= Math.max(0, bytes.length - 65_557); offset -= 1) {
    if (bytes.readUInt32LE(offset) === 0x06054b50 && offset + 22 + bytes.readUInt16LE(offset + 20) === bytes.length) {
      eocdOffset = offset;
      break;
    }
  }
  assert.notEqual(eocdOffset, -1, "Fixture ZIP end record must exist.");
  const centralOffset = bytes.readUInt32LE(eocdOffset + 16);
  const centralSize = bytes.readUInt32LE(eocdOffset + 12);
  let entryOffset = centralOffset;
  let rewritten = null;
  while (entryOffset < centralOffset + centralSize) {
    assert.equal(bytes.readUInt32LE(entryOffset), 0x02014b50, "Fixture central directory entry must be valid.");
    const nameLength = bytes.readUInt16LE(entryOffset + 28);
    const extraLength = bytes.readUInt16LE(entryOffset + 30);
    const commentLength = bytes.readUInt16LE(entryOffset + 32);
    const recordLength = 46 + nameLength + extraLength + commentLength;
    const rawName = bytes.subarray(entryOffset + 46, entryOffset + 46 + nameLength);
    if (rawName.toString("utf8") === entryName) {
      const unicodeExtra = unicodePathExtra(rawName, effectiveName);
      const record = Buffer.from(bytes.subarray(entryOffset, entryOffset + recordLength));
      record.writeUInt16LE(extraLength + unicodeExtra.length, 30);
      const insertion = 46 + nameLength + extraLength;
      rewritten = Buffer.concat([record.subarray(0, insertion), unicodeExtra, record.subarray(insertion)]);
      break;
    }
    entryOffset += recordLength;
  }
  assert.ok(rewritten, `Fixture central directory entry ${entryName} must exist.`);
  const eocd = Buffer.from(bytes.subarray(eocdOffset));
  const delta = rewritten.length - (46 + bytes.readUInt16LE(entryOffset + 28) + bytes.readUInt16LE(entryOffset + 30) + bytes.readUInt16LE(entryOffset + 32));
  eocd.writeUInt32LE(centralSize + delta, 12);
  const originalLength = rewritten.length - delta;
  return Buffer.concat([bytes.subarray(0, entryOffset), rewritten, bytes.subarray(entryOffset + originalLength, eocdOffset), eocd]);
}

function addLocalUnicodePathOverride(workbookBytes, entryName, effectiveName) {
  const bytes = Buffer.from(workbookBytes);
  let eocdOffset = -1;
  for (let offset = bytes.length - 22; offset >= Math.max(0, bytes.length - 65_557); offset -= 1) {
    if (bytes.readUInt32LE(offset) === 0x06054b50 && offset + 22 + bytes.readUInt16LE(offset + 20) === bytes.length) {
      eocdOffset = offset;
      break;
    }
  }
  assert.notEqual(eocdOffset, -1, "Fixture ZIP end record must exist.");
  const centralOffset = bytes.readUInt32LE(eocdOffset + 16);
  const centralSize = bytes.readUInt32LE(eocdOffset + 12);
  let entryOffset = centralOffset;
  let localOffset = null;
  let rawName = null;
  while (entryOffset < centralOffset + centralSize) {
    const nameLength = bytes.readUInt16LE(entryOffset + 28);
    const extraLength = bytes.readUInt16LE(entryOffset + 30);
    const commentLength = bytes.readUInt16LE(entryOffset + 32);
    const candidate = bytes.subarray(entryOffset + 46, entryOffset + 46 + nameLength);
    if (candidate.toString("utf8") === entryName) {
      localOffset = bytes.readUInt32LE(entryOffset + 42);
      rawName = candidate;
      break;
    }
    entryOffset += 46 + nameLength + extraLength + commentLength;
  }
  assert.notEqual(localOffset, null, `Fixture central directory entry ${entryName} must exist.`);
  const localNameLength = bytes.readUInt16LE(localOffset + 26);
  const localExtraLength = bytes.readUInt16LE(localOffset + 28);
  const insertion = localOffset + 30 + localNameLength + localExtraLength;
  const unicodeExtra = unicodePathExtra(rawName, effectiveName);
  const result = Buffer.concat([bytes.subarray(0, insertion), unicodeExtra, bytes.subarray(insertion)]);
  result.writeUInt16LE(localExtraLength + unicodeExtra.length, localOffset + 28);
  const shiftedCentralOffset = centralOffset + unicodeExtra.length;
  const shiftedEocdOffset = eocdOffset + unicodeExtra.length;
  result.writeUInt32LE(shiftedCentralOffset, shiftedEocdOffset + 16);
  entryOffset = shiftedCentralOffset;
  while (entryOffset < shiftedCentralOffset + centralSize) {
    const nameLength = result.readUInt16LE(entryOffset + 28);
    const extraLength = result.readUInt16LE(entryOffset + 30);
    const commentLength = result.readUInt16LE(entryOffset + 32);
    const candidateOffset = result.readUInt32LE(entryOffset + 42);
    if (candidateOffset > localOffset) result.writeUInt32LE(candidateOffset + unicodeExtra.length, entryOffset + 42);
    entryOffset += 46 + nameLength + extraLength + commentLength;
  }
  return result;
}

async function actionSheetPart(workbookBytes) {
  const archive = await JSZip.loadAsync(workbookBytes);
  for (const name of Object.keys(archive.files).filter((entry) => /^xl\/worksheets\/[^/]+\.xml$/.test(entry))) {
    if ((await archive.file(name).async("string")).includes('r="C483"')) return name;
  }
  assert.fail("Fixture action worksheet part must exist.");
}

async function sharedWorkbookBytes(expandedBytes, sharedBlocks) {
  return rewriteActionSheet(expandedBytes, (source) => {
    let xml = source;
    for (const block of sharedBlocks) {
      const start = 4 + block * 12;
      const masterRow = start === 28 ? 29 : start;
      xml = replaceCellFormula(xml, `C${masterRow}`, `<x:f t="shared" ref="C${start}:C${start + 11}" si="${block}">${expectedFormula(`round-01-clip-${String(block + 1).padStart(3, "0")}`)}</x:f>`);
      for (let row = masterRow + 1; row <= start + 11; row += 1) xml = replaceCellFormula(xml, `C${row}`, `<x:f t="shared" si="${block}"/>`);
    }
    return xml;
  });
}

async function producerWorkbookBytes(expandedBytes) {
  return sharedWorkbookBytes(expandedBytes, [0, 2]);
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
  const fullySharedBytes = await sharedWorkbookBytes(expandedBytes, Array.from({ length: 40 }, (_, index) => index));
  const firstThirtyNineSharedBytes = await sharedWorkbookBytes(expandedBytes, Array.from({ length: 39 }, (_, index) => index));
  const firstThreeSharedBytes = await sharedWorkbookBytes(expandedBytes, [0, 1, 2]);
  const producerPath = path.join(batchDir, "producer.xlsx");
  await fs.writeFile(producerPath, producerBytes);
  return { selection, selectionPath, selectionBytes, producerPath, producerBytes, fullySharedBytes, firstThirtyNineSharedBytes, firstThreeSharedBytes };
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
  await assert.rejects(() => verify(fixture, fixture.producerBytes, [AGGREGATE_PERMISSION]), /shared hyperlink|compatibility/);
  await verify(fixture, fixture.producerBytes, PRODUCER_PERMISSIONS);
  await verify(fixture, fixture.fullySharedBytes, [AGGREGATE_PERMISSION]);

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
    await assert.rejects(() => verify(fixture, workbookBytes, PRODUCER_PERMISSIONS), /raw formula|shared hyperlink/, name);
  }

  const foreignFormulaBytes = await rewriteActionSheet(fixture.producerBytes, (xml) => replaceCellFormula(xml, "C5", '<evil:f xmlns:evil="urn:evil" t="shared" si="0"/>'));
  await assert.rejects(() => verify(fixture, foreignFormulaBytes, PRODUCER_PERMISSIONS), /raw formula/, "foreign-namespace formula must not authenticate a follower");
  const counterfeitRelationshipBytes = await rewritePart(fixture.producerBytes, "xl/workbook.xml", (xml) => xml.replace(/(<[^>]*sheet\b[^>]*name="人工动作"[^>]*?)r:id=/, '$1xmlns:evil="urn:evil" evil:id='));
  await assert.rejects(() => verify(fixture, counterfeitRelationshipBytes, PRODUCER_PERMISSIONS), /raw formula/, "counterfeit relationship attribute must not resolve the action sheet");
  const wrongRelationshipTypeBytes = await rewritePart(fixture.producerBytes, "xl/_rels/workbook.xml.rels", (xml) => xml.replaceAll("http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet", "urn:evil/worksheet"));
  await assert.rejects(() => verify(fixture, wrongRelationshipTypeBytes, PRODUCER_PERMISSIONS), /raw formula/, "suffix-only worksheet relationship type must be rejected");

  const relocatedFormulaBytes = await rewriteActionSheet(fixture.producerBytes, (xml) => {
    const formulaCells = [];
    const stripped = xml.replace(/<x:c\b[^>]*\br="C(?:[4-9]|[1-9][0-9]|[1-3][0-9]{2}|4[0-7][0-9]|48[0-3])"[^>]*>[\s\S]*?<\/x:c>/g, (cell) => {
      formulaCells.push(cell);
      return "";
    });
    assert.equal(formulaCells.length, 480, "Fixture must relocate all 480 action formula cells.");
    return stripped.replace("</x:worksheet>", `<x:extLst><x:ext uri="urn:spiketrace:test">${formulaCells.join("")}</x:ext></x:extLst></x:worksheet>`);
  });
  await assert.rejects(() => verify(fixture, relocatedFormulaBytes, PRODUCER_PERMISSIONS), /raw formula/, "formula cells outside sheetData rows must not authenticate action formulas");

  const foreignWorksheetBytes = await rewriteActionSheet(fixture.producerBytes, (xml) => xml
    .replace('<x:worksheet xmlns:x="http://schemas.openxmlformats.org/spreadsheetml/2006/main"', '<x:worksheet xmlns:x="urn:evil:worksheet"')
    .replace("<x:sheetData>", '<x:sheetData xmlns:x="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'));
  await assert.rejects(() => verify(fixture, foreignWorksheetBytes, PRODUCER_PERMISSIONS), /raw formula/, "foreign worksheet root namespace must be rejected");

  const mixedWorksheetBytes = await rewriteActionSheet(fixture.producerBytes, (xml) => replaceCellFormula(xml, "C5", '<strict:f xmlns:strict="http://purl.oclc.org/ooxml/spreadsheetml/main" t="shared" si="0"/>'));
  await assert.rejects(() => verify(fixture, mixedWorksheetBytes, PRODUCER_PERMISSIONS), /raw formula/, "strict and transitional SpreadsheetML namespaces must not mix");

  const counterfeitSheetBytes = await rewritePart(fixture.producerBytes, "xl/workbook.xml", (xml) => {
    let actionSheet = null;
    const stripped = xml.replace(/<[^>]*sheet\b[^>]*name="人工动作"[^>]*\/>/, (sheet) => {
      actionSheet = sheet;
      return "";
    });
    assert.ok(actionSheet, "Fixture action sheet declaration must exist.");
    return stripped.replace(/<\/[^>]*workbook>/, `<extLst><ext uri="urn:spiketrace:test">${actionSheet}</ext></extLst>$&`);
  });
  await assert.rejects(() => verify(fixture, counterfeitSheetBytes, PRODUCER_PERMISSIONS), /raw formula/, "sheet declarations outside workbook sheets must not resolve the action sheet");

  const counterfeitPackageRelationshipBytes = await rewriteActionRelationship(fixture.producerBytes, (xml, relationshipId) => {
    let actionRelationship = null;
    const pattern = new RegExp(`<(?:[A-Za-z_][\\w.-]*:)?Relationship\\b[^>]*\\bId="${relationshipId}"[^>]*/>`);
    const stripped = xml.replace(pattern, (relationship) => {
      actionRelationship = relationship;
      return "";
    });
    assert.ok(actionRelationship, "Fixture action worksheet relationship must exist.");
    return stripped.replace("</Relationships>", `<ext>${actionRelationship}</ext></Relationships>`);
  });
  await assert.rejects(() => verify(fixture, counterfeitPackageRelationshipBytes, PRODUCER_PERMISSIONS), /raw formula/, "package relationships outside the root relationship list must not resolve the action sheet");

  const mismatchedRowBytes = await rewriteActionSheet(fixture.producerBytes, (xml) => xml.replace('<x:row r="4"', '<x:row r="999"'));
  await assert.rejects(() => verify(fixture, mismatchedRowBytes, PRODUCER_PERMISSIONS), /raw formula/, "cell references must match their containing row");

  const sharedGroupMutations = [
    ["shared si reused across blocks", (xml) => {
      let result = replaceCellFormula(xml, "C29", `<x:f t="shared" ref="C28:C39" si="0">${expectedFormula("round-01-clip-003")}</x:f>`);
      for (let row = 30; row <= 39; row += 1) result = replaceCellFormula(result, `C${row}`, '<x:f t="shared" si="0"/>');
      return result;
    }],
    ["ordinary formula mixed into a standard block", (xml) => {
      let result = replaceCellFormula(xml, "C4", `<x:f>${expectedFormula("round-01-clip-001")}</x:f>`);
      result = replaceCellFormula(result, "C5", `<x:f t="shared" ref="C4:C15" si="0">${expectedFormula("round-01-clip-001")}</x:f>`);
      return result;
    }],
    ["C28 mixed block ordinary cell moved", (xml) => {
      let result = replaceCellFormula(xml, "C28", '<x:f t="shared" si="2"/>');
      result = replaceCellFormula(result, "C30", `<x:f>${expectedFormula("round-01-clip-003")}</x:f>`);
      return result;
    }],
    ["C28 mixed block master moved from C29", (xml) => {
      let result = replaceCellFormula(xml, "C29", '<x:f t="shared" si="2"/>');
      result = replaceCellFormula(result, "C30", `<x:f t="shared" ref="C28:C39" si="2">${expectedFormula("round-01-clip-003")}</x:f>`);
      return result;
    }],
    ["C28 mixed block has two ordinary formulas", (xml) => replaceCellFormula(xml, "C30", `<x:f>${expectedFormula("round-01-clip-003")}</x:f>`)],
  ];
  for (const [name, mutate] of sharedGroupMutations) {
    const workbookBytes = await rewriteActionSheet(fixture.producerBytes, mutate);
    await assert.rejects(() => verify(fixture, workbookBytes, PRODUCER_PERMISSIONS), /raw formula/, name);
  }

  for (const entryName of ["xl/workbook.xml", await actionSheetPart(fixture.producerBytes)]) {
    const duplicateEntryBytes = duplicateCentralDirectoryEntry(fixture.producerBytes, entryName);
    await assert.rejects(() => verify(fixture, duplicateEntryBytes, PRODUCER_PERMISSIONS), /raw formula/, `duplicate ZIP entry ${entryName} must be rejected`);
  }

  const unicodeCollisionBytes = addUnicodePathOverride(fixture.producerBytes, "[Content_Types].xml", "xl/workbook.xml");
  await assert.rejects(() => verify(fixture, unicodeCollisionBytes, PRODUCER_PERMISSIONS), /unsupported Unicode Path extra field/, "central Unicode Path extra fields must be rejected before JSZip");
  const localUnicodeOverrideBytes = addLocalUnicodePathOverride(fixture.producerBytes, "[Content_Types].xml", "xl/workbook.xml");
  await assert.rejects(() => verify(fixture, localUnicodeOverrideBytes, PRODUCER_PERMISSIONS), /unsupported Unicode Path extra field/, "local Unicode Path extra fields must be rejected before JSZip");

  const oversizedXmlBytes = await rewriteActionSheet(
    fixture.producerBytes,
    (xml) => `${" ".repeat(5 * 1024 * 1024)}${xml}`,
    { compression: "DEFLATE", compressionOptions: { level: 9 } },
  );
  await assert.rejects(() => verify(fixture, oversizedXmlBytes, PRODUCER_PERMISSIONS), /raw formula/, "oversized highly compressed XML must be rejected before decompression");

  const permissionMutations = [
    ["full aggregate cannot coexist with a single-block permission", fixture.fullySharedBytes, [AGGREGATE_PERMISSION, { ...AGGREGATE_PERMISSION, range: "C4:C15" }]],
    ["partial 39-block aggregate is not canonical", fixture.firstThirtyNineSharedBytes, [{ ...AGGREGATE_PERMISSION, range: "C4:C471" }]],
    ["overlapping multi-block permissions are invalid", fixture.firstThreeSharedBytes, [{ ...AGGREGATE_PERMISSION, range: "C4:C27" }, { ...AGGREGATE_PERMISSION, range: "C16:C39" }]],
    ["permission must not cover an expanded block", fixture.producerBytes, [{ ...AGGREGATE_PERMISSION, range: "C4:C15" }, { ...AGGREGATE_PERMISSION, range: "C16:C27" }, { ...AGGREGATE_PERMISSION, range: "C28:C39" }]],
    ["multiple permissions must not cover one shared block", fixture.producerBytes, [{ ...AGGREGATE_PERMISSION, range: "C4:C15" }, { ...AGGREGATE_PERMISSION, range: "C4:C15" }, { ...AGGREGATE_PERMISSION, range: "C28:C39" }]],
  ];
  for (const [name, workbookBytes, permissions] of permissionMutations) {
    await assert.rejects(() => verify(fixture, workbookBytes, permissions), /shared hyperlink|compatibility/, name);
  }

  const originalBinding = await boundCompatibility(fixture, fixture.producerBytes, PRODUCER_PERMISSIONS);
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
