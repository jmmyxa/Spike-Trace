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

function fail(message) {
  throw new Error(`Action raw formula verification failed: ${message}`);
}

function attribute(node, name, namespace = "") {
  const match = Object.values(node.attributes).find((entry) => entry.local === name && entry.uri === namespace);
  return match?.value ?? null;
}

function parseXml(xml, label, handlers) {
  const parser = sax.parser(true, { xmlns: true, trim: false, normalize: false });
  const elements = [];
  parser.ondoctype = () => fail(`${label} contains a document type.`);
  parser.onopentag = (node) => {
    const parent = elements.at(-1) ?? null;
    elements.push(node);
    handlers.onOpenTag?.(node, parent);
  };
  parser.ontext = handlers.onText ?? null;
  parser.oncdata = handlers.onText ?? null;
  parser.onclosetag = () => handlers.onCloseTag?.(elements.pop());
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
  parseXml(workbookXml, WORKBOOK_PART, {
    onOpenTag(node) {
      if (!SPREADSHEET_NAMESPACES.has(node.uri) || node.local !== "sheet" || attribute(node, "name") !== "人工动作") return;
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
  parseXml(relationshipsXml, WORKBOOK_RELATIONSHIPS_PART, {
    onOpenTag(node) {
      if (!PACKAGE_RELATIONSHIP_NAMESPACES.has(node.uri) || node.local !== "Relationship" || attribute(node, "Id") !== relationshipId) return;
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
  let currentCell = null;
  let currentFormula = null;
  parseXml(worksheetXml, "人工动作 worksheet", {
    onOpenTag(node, parent) {
      if (SPREADSHEET_NAMESPACES.has(node.uri) && node.local === "c") {
        if (currentCell) fail("worksheet cells are nested.");
        currentCell = { reference: attribute(node, "r"), formula: null };
        if (!currentCell.reference) fail("worksheet cell reference is missing.");
        if (seenCells.has(currentCell.reference)) fail(`cell ${currentCell.reference} is duplicated.`);
        seenCells.add(currentCell.reference);
      } else if (SPREADSHEET_NAMESPACES.has(node.uri) && node.local === "f" && currentCell && SPREADSHEET_NAMESPACES.has(parent?.uri) && parent.local === "c") {
        if (currentFormula || currentCell.formula) fail(`cell ${currentCell.reference} has duplicate formula nodes.`);
        currentFormula = { type: attribute(node, "t"), sharedIndex: attribute(node, "si"), reference: attribute(node, "ref"), text: "" };
      } else if (currentFormula) {
        fail(`formula ${currentCell.reference} contains an element.`);
      }
    },
    onText(value) {
      if (currentFormula) currentFormula.text += value;
    },
    onCloseTag(node) {
      if (SPREADSHEET_NAMESPACES.has(node.uri) && node.local === "f" && currentFormula) {
        currentCell.formula = currentFormula;
        currentFormula = null;
      } else if (SPREADSHEET_NAMESPACES.has(node.uri) && node.local === "c" && currentCell) {
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
  const indexes = new Set(shared.map((entry) => sharedIndex(entry.formula, entry.cell)));
  const masters = shared.filter((entry) => entry.formula.reference !== null);
  if (indexes.size !== 1 || masters.length !== 1) fail(`shared block ${range} does not have one coherent master.`);
  const master = masters[0];
  if (master.formula.reference !== range || master.formula.text !== expected) fail(`shared block ${range} master is invalid.`);
  for (const entry of shared) {
    if (entry === master) continue;
    if (entry.formula.reference !== null || entry.formula.text !== "") fail(`shared follower ${entry.cell} is invalid.`);
  }
  return { range, shared: true, ordinaryCount: entries.length - shared.length, sharedCellCount: shared.length, masterCell: master.cell };
}

export async function inspectActionFormulaBlocks(workbookBytes, selection) {
  if (!(workbookBytes instanceof Uint8Array)) fail("workbook bytes must be a Uint8Array.");
  let archive;
  try {
    archive = await JSZip.loadAsync(workbookBytes);
  } catch {
    fail("workbook bytes are not a readable XLSX package.");
  }
  const relationshipId = actionSheetRelationshipId(await requiredXml(archive, WORKBOOK_PART));
  const part = worksheetPart(await requiredXml(archive, WORKBOOK_RELATIONSHIPS_PART), relationshipId);
  const cells = formulaCells(await requiredXml(archive, part));
  for (const [cell, formula] of cells) {
    if (formula.type !== "shared") continue;
    const match = /^C([1-9][0-9]*)$/.exec(cell);
    if (!match || Number(match[1]) < 4 || Number(match[1]) > 483) fail(`extra shared formula cell ${cell} is invalid.`);
  }
  if (!Array.isArray(selection?.clips) || selection.clips.length !== 40) fail("selection must contain 40 clips.");
  return selection.clips.map((clip, index) => verifyBlock(cells, clip, 4 + index * 12));
}
