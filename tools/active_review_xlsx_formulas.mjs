import path from "node:path";

import JSZip from "jszip";
import sax from "sax";

const WORKBOOK_PART = "xl/workbook.xml";
const WORKBOOK_RELATIONSHIPS_PART = "xl/_rels/workbook.xml.rels";
const SPREADSHEET_NAMESPACES = new Set([
  "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
  "http://purl.oclc.org/ooxml/spreadsheetml/main",
]);
const OFFICE_RELATIONSHIP_NAMESPACES = new Set([
  "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
  "http://purl.oclc.org/ooxml/officeDocument/relationships",
]);
const PACKAGE_RELATIONSHIP_NAMESPACES = new Set([
  "http://schemas.openxmlformats.org/package/2006/relationships",
  "http://purl.oclc.org/ooxml/package/relationships",
]);
const WORKSHEET_RELATIONSHIP_TYPES = new Set([
  "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
  "http://purl.oclc.org/ooxml/officeDocument/relationships/worksheet",
]);
const MAX_ARCHIVE_BYTES = 64 * 1024 * 1024;
const MAX_ENTRY_COUNT = 2048;
const MAX_ENTRY_COMPRESSED_BYTES = 16 * 1024 * 1024;
const MAX_ENTRY_UNCOMPRESSED_BYTES = 16 * 1024 * 1024;
const MAX_XML_UNCOMPRESSED_BYTES = 4 * 1024 * 1024;
const MAX_TOTAL_UNCOMPRESSED_BYTES = 64 * 1024 * 1024;
const MAX_COMPRESSION_RATIO = 200;

function fail(message) {
  throw new Error(`Action raw formula verification failed: ${message}`);
}

function attribute(node, name, namespace = "") {
  const match = Object.values(node.attributes).find((entry) => entry.local === name && entry.uri === namespace);
  return match?.value ?? null;
}

function zipView(bytes) {
  return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
}

function zipName(bytes, offset, length) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(offset, offset + length));
  } catch {
    fail("XLSX package contains an invalid entry name.");
  }
}

function inspectZipExtras(bytes, offset, length) {
  const view = zipView(bytes);
  const end = offset + length;
  if (end > bytes.byteLength) fail("XLSX package contains invalid ZIP extra data.");
  const ids = new Set();
  let hasZip64 = false;
  while (offset < end) {
    if (offset + 4 > end) fail("XLSX package contains invalid ZIP extra data.");
    const id = view.getUint16(offset, true);
    const size = view.getUint16(offset + 2, true);
    offset += 4;
    if (offset + size > end) fail("XLSX package contains invalid ZIP extra data.");
    if (ids.has(id)) fail("XLSX package contains duplicate ZIP extra fields.");
    ids.add(id);
    if (id === 0x7075) fail("XLSX package contains an unsupported Unicode Path extra field.");
    if (id === 0x0001) hasZip64 = true;
    offset += size;
  }
  return hasZip64;
}

function admitZip(workbookBytes) {
  if (workbookBytes.byteLength > MAX_ARCHIVE_BYTES || workbookBytes.byteLength < 22) fail("XLSX package size is invalid.");
  const view = zipView(workbookBytes);
  let eocd = -1;
  for (let offset = workbookBytes.byteLength - 22; offset >= Math.max(0, workbookBytes.byteLength - 65_557); offset -= 1) {
    if (view.getUint32(offset, true) === 0x06054b50 && offset + 22 + view.getUint16(offset + 20, true) === workbookBytes.byteLength) {
      eocd = offset;
      break;
    }
  }
  if (eocd < 0) fail("XLSX package has no valid ZIP end record.");
  const disk = view.getUint16(eocd + 4, true);
  const centralDisk = view.getUint16(eocd + 6, true);
  const diskEntries = view.getUint16(eocd + 8, true);
  const entryCount = view.getUint16(eocd + 10, true);
  const centralSize = view.getUint32(eocd + 12, true);
  const centralOffset = view.getUint32(eocd + 16, true);
  if (disk !== 0 || centralDisk !== 0 || diskEntries !== entryCount) fail("XLSX package uses unsupported multi-disk ZIP data.");
  if (entryCount === 0xffff || centralSize === 0xffffffff || centralOffset === 0xffffffff) fail("XLSX package uses unsupported ZIP64 data.");
  if (entryCount > MAX_ENTRY_COUNT || centralOffset + centralSize !== eocd) fail("XLSX package central directory is invalid.");
  const names = new Set();
  let totalUncompressed = 0;
  let offset = centralOffset;
  for (let index = 0; index < entryCount; index += 1) {
    if (offset + 46 > eocd || view.getUint32(offset, true) !== 0x02014b50) fail("XLSX package central directory entry is invalid.");
    const flags = view.getUint16(offset + 8, true);
    const method = view.getUint16(offset + 10, true);
    const crc = view.getUint32(offset + 16, true);
    const compressedSize = view.getUint32(offset + 20, true);
    const uncompressedSize = view.getUint32(offset + 24, true);
    const nameLength = view.getUint16(offset + 28, true);
    const extraLength = view.getUint16(offset + 30, true);
    const commentLength = view.getUint16(offset + 32, true);
    const entryDisk = view.getUint16(offset + 34, true);
    const localOffset = view.getUint32(offset + 42, true);
    const entryEnd = offset + 46 + nameLength + extraLength + commentLength;
    if (entryEnd > eocd || entryDisk !== 0) fail("XLSX package central directory entry is invalid.");
    if ((flags & 0x0001) !== 0) fail("XLSX package contains an encrypted entry.");
    if ((flags & 0x0008) !== 0) fail("XLSX package contains an unsupported data descriptor.");
    if (method !== 0 && method !== 8) fail("XLSX package contains an unsupported compression method.");
    if (compressedSize === 0xffffffff || uncompressedSize === 0xffffffff || localOffset === 0xffffffff || inspectZipExtras(workbookBytes, offset + 46 + nameLength, extraLength)) fail("XLSX package uses unsupported ZIP64 data.");
    if (compressedSize > MAX_ENTRY_COMPRESSED_BYTES || uncompressedSize > MAX_ENTRY_UNCOMPRESSED_BYTES || (compressedSize === 0 && uncompressedSize !== 0) || uncompressedSize / Math.max(compressedSize, 1) > MAX_COMPRESSION_RATIO) fail("XLSX package entry size is invalid.");
    totalUncompressed += uncompressedSize;
    if (totalUncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES) fail("XLSX package expands beyond the allowed size.");
    const name = zipName(workbookBytes, offset + 46, nameLength);
    const segments = name.split("/");
    if (!name || name.includes("\\") || name.includes("\0") || name.startsWith("/") || /^[A-Za-z]:/.test(name) || segments.includes("..") || segments.includes(".")) fail("XLSX package contains an unsafe entry name.");
    const normalized = path.posix.normalize(name);
    if (names.has(normalized)) fail(`XLSX package entry ${normalized} is duplicated.`);
    names.add(normalized);
    if ((normalized.endsWith(".xml") || normalized.endsWith(".rels")) && uncompressedSize > MAX_XML_UNCOMPRESSED_BYTES) fail(`XLSX XML part ${normalized} is too large.`);
    if (localOffset + 30 > centralOffset || view.getUint32(localOffset, true) !== 0x04034b50) fail("XLSX package local entry is invalid.");
    const localFlags = view.getUint16(localOffset + 6, true);
    const localMethod = view.getUint16(localOffset + 8, true);
    const localCrc = view.getUint32(localOffset + 14, true);
    const localCompressedSize = view.getUint32(localOffset + 18, true);
    const localUncompressedSize = view.getUint32(localOffset + 22, true);
    const localNameLength = view.getUint16(localOffset + 26, true);
    const localExtraLength = view.getUint16(localOffset + 28, true);
    const localDataEnd = localOffset + 30 + localNameLength + localExtraLength + compressedSize;
    if (inspectZipExtras(workbookBytes, localOffset + 30 + localNameLength, localExtraLength)) fail("XLSX package uses unsupported ZIP64 data.");
    if (localDataEnd > centralOffset || localFlags !== flags || localMethod !== method || localCrc !== crc || localCompressedSize !== compressedSize || localUncompressedSize !== uncompressedSize || zipName(workbookBytes, localOffset + 30, localNameLength) !== name) fail("XLSX package local entry does not match its central directory entry.");
    offset = entryEnd;
  }
  if (offset !== eocd) fail("XLSX package central directory size is invalid.");
}

function parseXml(xml, label, handlers) {
  const parser = sax.parser(true, { xmlns: true, trim: false, normalize: false });
  const elements = [];
  parser.ondoctype = () => fail(`${label} contains a document type.`);
  parser.onopentag = (node) => {
    const parent = elements.at(-1) ?? null;
    elements.push(node);
    handlers.onOpenTag?.(node, parent, elements.slice(0, -1));
  };
  parser.ontext = handlers.onText ?? null;
  parser.oncdata = handlers.onText ?? null;
  parser.onclosetag = () => {
    const node = elements.pop();
    handlers.onCloseTag?.(node);
  };
  try {
    parser.write(xml).close();
  } catch (error) {
    if (String(error?.message ?? error).startsWith("Action raw formula verification failed:")) throw error;
    fail(`${label} is invalid XML.`);
  }
}

async function requiredXml(archive, part) {
  const entry = archive.file(part);
  if (!entry || entry.dir) fail(`XLSX part ${part} is missing.`);
  try {
    return await entry.async("string");
  } catch {
    fail(`XLSX part ${part} cannot be read.`);
  }
}

function actionSheetRelationshipId(workbookXml) {
  const ids = [];
  let root = null;
  parseXml(workbookXml, WORKBOOK_PART, {
    onOpenTag(node, _parent, ancestors) {
      if (ancestors.length === 0) {
        if (!SPREADSHEET_NAMESPACES.has(node.uri) || node.local !== "workbook") fail(`${WORKBOOK_PART} has an invalid root element.`);
        root = node;
        return;
      }
      if (ancestors.length !== 2 || ancestors[0] !== root || ancestors[1].uri !== root.uri || ancestors[1].local !== "sheets" || node.uri !== root.uri || node.local !== "sheet" || attribute(node, "name") !== "人工动作") return;
      const relationshipIds = Object.values(node.attributes).filter((entry) => entry.local === "id" && OFFICE_RELATIONSHIP_NAMESPACES.has(entry.uri));
      const id = relationshipIds.length === 1 ? relationshipIds[0].value : null;
      if (!id) fail("人工动作 worksheet relationship is missing.");
      ids.push(id);
    },
  });
  if (ids.length !== 1) fail("人工动作 worksheet relationship is not unique.");
  return ids[0];
}

function worksheetPart(relationshipsXml, relationshipId) {
  const targets = [];
  let root = null;
  parseXml(relationshipsXml, WORKBOOK_RELATIONSHIPS_PART, {
    onOpenTag(node, _parent, ancestors) {
      if (ancestors.length === 0) {
        if (!PACKAGE_RELATIONSHIP_NAMESPACES.has(node.uri) || node.local !== "Relationships") fail(`${WORKBOOK_RELATIONSHIPS_PART} has an invalid root element.`);
        root = node;
        return;
      }
      if (ancestors.length !== 1 || ancestors[0] !== root || node.uri !== root.uri || node.local !== "Relationship" || attribute(node, "Id") !== relationshipId) return;
      const type = attribute(node, "Type");
      const target = attribute(node, "Target");
      if (!WORKSHEET_RELATIONSHIP_TYPES.has(type) || !target || attribute(node, "TargetMode") !== null) fail("人工动作 worksheet relationship is invalid.");
      targets.push(target);
    },
  });
  if (targets.length !== 1) fail("人工动作 worksheet target is not unique.");
  const target = targets[0];
  if (target.includes("\\")) fail("人工动作 worksheet target is not a POSIX part name.");
  const resolved = target.startsWith("/")
    ? path.posix.normalize(target.slice(1))
    : path.posix.normalize(path.posix.join(path.posix.dirname(WORKBOOK_PART), target));
  if (!resolved.startsWith("xl/") || resolved.includes("../") || resolved === "xl") fail("人工动作 worksheet target escapes the XLSX package.");
  return resolved;
}

function formulaCells(worksheetXml) {
  const cells = new Map();
  const seenCells = new Set();
  let root = null;
  let currentCell = null;
  let currentFormula = null;
  parseXml(worksheetXml, "人工动作 worksheet", {
    onOpenTag(node, _parent, ancestors) {
      if (ancestors.length === 0) {
        if (!SPREADSHEET_NAMESPACES.has(node.uri) || node.local !== "worksheet") fail("人工动作 worksheet has an invalid root element.");
        root = node;
        return;
      }
      if (SPREADSHEET_NAMESPACES.has(node.uri) && node.uri !== root.uri) fail("人工动作 worksheet mixes SpreadsheetML namespaces.");
      const row = ancestors.length === 2 && ancestors[0] === root && ancestors[1].uri === root.uri && ancestors[1].local === "sheetData" && node.uri === root.uri && node.local === "row";
      const cell = ancestors.length === 3 && ancestors[0] === root && ancestors[1].uri === root.uri && ancestors[1].local === "sheetData" && ancestors[2].uri === root.uri && ancestors[2].local === "row" && node.uri === root.uri && node.local === "c";
      const formula = ancestors.length === 4 && ancestors[0] === root && ancestors[1].uri === root.uri && ancestors[1].local === "sheetData" && ancestors[2].uri === root.uri && ancestors[2].local === "row" && ancestors[3] === currentCell?.node && node.uri === root.uri && node.local === "f";
      if (row && !/^[1-9][0-9]*$/.test(attribute(node, "r") ?? "")) fail("worksheet row reference is invalid.");
      if (cell) {
        currentCell = { node, reference: attribute(node, "r"), formula: null };
        if (!currentCell.reference) fail("worksheet cell reference is missing.");
        const rowReference = attribute(ancestors[2], "r");
        const cellReference = /^[A-Z]{1,3}([1-9][0-9]*)$/.exec(currentCell.reference);
        if (!cellReference || cellReference[1] !== rowReference) fail(`cell ${currentCell.reference} does not match its containing row.`);
        if (seenCells.has(currentCell.reference)) fail(`cell ${currentCell.reference} is duplicated.`);
        seenCells.add(currentCell.reference);
      } else if (formula) {
        if (currentFormula || currentCell.formula) fail(`cell ${currentCell.reference} has duplicate formula nodes.`);
        currentFormula = { node, type: attribute(node, "t"), sharedIndex: attribute(node, "si"), reference: attribute(node, "ref"), text: "" };
      } else if (currentFormula) {
        fail(`formula ${currentCell.reference} contains an element.`);
      }
    },
    onText(value) {
      if (currentFormula) currentFormula.text += value;
    },
    onCloseTag(node) {
      if (node === currentFormula?.node) {
        currentCell.formula = currentFormula;
        currentFormula = null;
      } else if (node === currentCell?.node) {
        if (currentCell.formula) cells.set(currentCell.reference, currentCell.formula);
        currentCell = null;
      }
    },
  });
  return cells;
}

function expectedFormula(clipId) {
  return `HYPERLINK("clips/${clipId}.mp4","播放")`;
}

function sharedIndex(formula, cell) {
  if (!/^(0|[1-9][0-9]*)$/.test(formula.sharedIndex ?? "")) fail(`cell ${cell} has invalid shared si.`);
  return Number(formula.sharedIndex);
}

function verifyBlock(cells, clip, start) {
  const range = `C${start}:C${start + 11}`;
  const expected = expectedFormula(clip.clip_id);
  const entries = [];
  for (let row = start; row < start + 12; row += 1) {
    const cell = `C${row}`;
    const formula = cells.get(cell);
    if (!formula) fail(`cell ${cell} has no <f> node.`);
    if (formula.type === null) {
      if (formula.sharedIndex !== null || formula.reference !== null || formula.text !== expected) fail(`ordinary formula ${cell} is invalid.`);
      entries.push({ cell, kind: "ordinary", formula });
    } else if (formula.type === "shared") {
      sharedIndex(formula, cell);
      entries.push({ cell, kind: "shared", formula });
    } else fail(`cell ${cell} has unsupported formula type ${formula.type}.`);
  }
  const shared = entries.filter((entry) => entry.kind === "shared");
  if (shared.length === 0) return { range, shared: false, ordinaryCount: 12, sharedCellCount: 0, masterCell: null };
  const mixed = start === 28;
  const expectedMasterCell = mixed ? "C29" : `C${start}`;
  const expectedSharedCells = new Set(Array.from(
    { length: mixed ? 11 : 12 },
    (_, index) => `C${start + (mixed ? 1 : 0) + index}`,
  ));
  if (entries.some((entry) => (entry.kind === "shared") !== expectedSharedCells.has(entry.cell))) fail(`shared block ${range} has an invalid formula shape.`);
  const indexes = new Set(shared.map((entry) => sharedIndex(entry.formula, entry.cell)));
  const masters = shared.filter((entry) => entry.formula.reference !== null);
  if (indexes.size !== 1 || masters.length !== 1) fail(`shared block ${range} does not have one coherent master.`);
  const master = masters[0];
  if (master.cell !== expectedMasterCell || master.formula.reference !== range || master.formula.text !== expected) fail(`shared block ${range} master is invalid.`);
  for (const entry of shared) {
    if (entry === master) continue;
    if (entry.formula.reference !== null || entry.formula.text !== "") fail(`shared follower ${entry.cell} is invalid.`);
  }
  return { range, shared: true, ordinaryCount: entries.length - shared.length, sharedCellCount: shared.length, masterCell: master.cell };
}

function verifySharedGroups(cells) {
  const groups = new Map();
  for (const [cell, formula] of cells) {
    if (formula.type !== "shared") continue;
    const index = sharedIndex(formula, cell);
    const group = groups.get(index) ?? { blocks: new Set(), masters: 0 };
    const match = /^C([1-9][0-9]*)$/.exec(cell);
    if (!match || Number(match[1]) < 4 || Number(match[1]) > 483) fail(`extra shared formula cell ${cell} is invalid.`);
    group.blocks.add(Math.floor((Number(match[1]) - 4) / 12));
    if (formula.reference !== null) group.masters += 1;
    groups.set(index, group);
  }
  for (const [index, group] of groups) {
    if (group.masters !== 1 || group.blocks.size !== 1) fail(`shared si ${index} is not one coherent block-local group.`);
  }
}

export async function inspectActionFormulaBlocks(workbookBytes, selection) {
  if (!(workbookBytes instanceof Uint8Array)) fail("workbook bytes must be a Uint8Array.");
  admitZip(workbookBytes);
  let archive;
  try {
    archive = await JSZip.loadAsync(workbookBytes);
  } catch {
    fail("workbook bytes are not a readable XLSX package.");
  }
  const relationshipId = actionSheetRelationshipId(await requiredXml(archive, WORKBOOK_PART));
  const part = worksheetPart(await requiredXml(archive, WORKBOOK_RELATIONSHIPS_PART), relationshipId);
  const cells = formulaCells(await requiredXml(archive, part));
  verifySharedGroups(cells);
  if (!Array.isArray(selection?.clips) || selection.clips.length !== 40) fail("selection must contain 40 clips.");
  return selection.clips.map((clip, index) => verifyBlock(cells, clip, 4 + index * 12));
}
