import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

function invariant(condition, message) {
  if (!condition) throw new Error(message);
}

export function sha256(bytes) {
  invariant(bytes instanceof Uint8Array, "SHA-256 input must be a Uint8Array.");
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

export async function sha256File(filePath, { open = fs.open, chunkBytes = 1024 * 1024 } = {}) {
  invariant(Number.isInteger(chunkBytes) && chunkBytes > 0, "SHA-256 chunk size must be a positive integer.");
  const hash = crypto.createHash("sha256");
  const handle = await open(filePath, "r");
  const buffer = Buffer.allocUnsafe(chunkBytes);
  let position = 0;
  try {
    while (true) {
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, position);
      if (bytesRead === 0) break;
      hash.update(buffer.subarray(0, bytesRead));
      position += bytesRead;
    }
  } finally {
    await handle.close();
  }
  return hash.digest("hex");
}

export async function readInputSnapshot(filePath, label) {
  const absolutePath = path.resolve(filePath);
  const bytes = await fs.readFile(absolutePath);
  return { path: absolutePath, bytes, sha256: sha256(bytes) };
}

export function normalizeRepoPath(value, repoRoot = process.cwd()) {
  return path.relative(path.resolve(repoRoot), path.resolve(value)).split(path.sep).join("/");
}

export async function assertStableInput(filePath, expectedBytes, label) {
  const actualBytes = await fs.readFile(filePath);
  invariant(Buffer.compare(actualBytes, expectedBytes) === 0, `${label} changed during extraction.`);
}

function isWhitespace(character) {
  return character === " " || character === "\n" || character === "\r" || character === "\t";
}

export function parseJsonObjectStrict(bytes, label) {
  invariant(bytes instanceof Uint8Array, `${label} must be UTF-8 bytes.`);
  const text = Buffer.from(bytes).toString("utf8");
  let index = 0;
  const fail = () => { throw new Error(`${label} contains invalid JSON.`); };
  const skipWhitespace = () => { while (isWhitespace(text[index])) index += 1; };
  const parseString = () => {
    if (text[index] !== '"') fail();
    const start = index;
    index += 1;
    while (index < text.length) {
      const character = text[index++];
      if (character === "\\") {
        if (index >= text.length) fail();
        index += 1;
      } else if (character === '"') {
        const token = text.slice(start, index);
        try { return JSON.parse(token); } catch { fail(); }
      } else if (character < " ") fail();
    }
    fail();
  };
  const parseValue = () => {
    skipWhitespace();
    if (text[index] === "{") return parseObject();
    if (text[index] === "[") return parseArray();
    if (text[index] === '"') { parseString(); return; }
    if (text.startsWith("true", index)) { index += 4; return; }
    if (text.startsWith("false", index)) { index += 5; return; }
    if (text.startsWith("null", index)) { index += 4; return; }
    const match = text.slice(index).match(/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/);
    if (match) { index += match[0].length; return; }
    fail();
  };
  const parseObject = () => {
    index += 1;
    const keys = new Set();
    skipWhitespace();
    if (text[index] === "}") { index += 1; return; }
    while (index < text.length) {
      skipWhitespace();
      const key = parseString();
      if (keys.has(key)) throw new Error(`${label} contains duplicate key "${key}"`);
      keys.add(key);
      skipWhitespace();
      if (text[index++] !== ":") fail();
      parseValue();
      skipWhitespace();
      if (text[index] === "}") { index += 1; return; }
      if (text[index++] !== ",") fail();
    }
    fail();
  };
  const parseArray = () => {
    index += 1;
    skipWhitespace();
    if (text[index] === "]") { index += 1; return; }
    while (index < text.length) {
      parseValue();
      skipWhitespace();
      if (text[index] === "]") { index += 1; return; }
      if (text[index++] !== ",") fail();
    }
    fail();
  };
  skipWhitespace();
  if (text[index] !== "{") fail();
  parseObject();
  skipWhitespace();
  if (index !== text.length) fail();
  try { return JSON.parse(text); } catch { fail(); }
}

export async function publishJsonNoReplace(outputPath, payload, { io = {}, beforePublish } = {}) {
  const open = io.open ?? fs.open;
  const link = io.link ?? fs.link;
  const unlink = io.unlink ?? fs.unlink;
  const readFile = io.readFile ?? fs.readFile;
  const unique = io.randomUUID ?? crypto.randomUUID;
  const absoluteOutput = path.resolve(outputPath);
  const temporary = path.join(path.dirname(absoluteOutput), `.${path.basename(absoluteOutput)}.tmp-${process.pid}-${unique()}`);
  const bytes = Buffer.from(`${JSON.stringify(payload, null, 2)}\n`, "utf8");
  let handle;
  try {
    handle = await open(temporary, "wx");
    await handle.writeFile(bytes);
    await handle.sync();
    await handle.close();
    handle = undefined;
    const publishedBytes = await readFile(temporary);
    invariant(Buffer.compare(publishedBytes, bytes) === 0, "Temporary draft bytes changed before publication.");
    if (beforePublish) await beforePublish();
    await link(temporary, absoluteOutput);
    return bytes;
  } finally {
    if (handle) await handle.close().catch(() => undefined);
    await unlink(temporary).catch((error) => {
      if (error?.code !== "ENOENT") throw error;
    });
  }
}
