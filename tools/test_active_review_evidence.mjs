import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import {
  assertStableInput,
  normalizeRepoPath,
  parseJsonObjectStrict,
  publishJsonNoReplace,
  readInputSnapshot,
  sha256,
} from "./active_review_io.mjs";

const root = await fs.mkdtemp(path.join(os.tmpdir(), "active-review-evidence-"));
try {
  const inputPath = path.join(root, "selection.json");
  const inputBytes = Buffer.from('{"selection":{"path":"a"}}\n', "utf8");
  await fs.writeFile(inputPath, inputBytes);
  const snapshot = await readInputSnapshot(inputPath, "Selection");
  assert.equal(snapshot.path, inputPath);
  assert.deepEqual(snapshot.bytes, inputBytes);
  assert.equal(snapshot.sha256, sha256(inputBytes));
  await assertStableInput(inputPath, inputBytes, "Selection");
  assert.equal(normalizeRepoPath(inputPath, root), "selection.json");

  const duplicate = Buffer.from(
    '{"selection":{"path":"a"},"selection":{"path":"b"}}',
    "utf8",
  );
  assert.throws(
    () => parseJsonObjectStrict(duplicate, "override"),
    /override contains duplicate key "selection"/,
  );
  assert.throws(() => parseJsonObjectStrict(Buffer.from("[]"), "array"), /array contains invalid JSON/);
  assert.deepEqual(parseJsonObjectStrict(Buffer.from('{"items":[{"x":1},{"x":1}]}'), "valid"), { items: [{ x: 1 }, { x: 1 }] });

  const expectedDraft = { format_version: 1, selection: { path: "selection.json" }, clips: [] };
  const outputPath = path.join(root, "draft.json");
  const expectedBytes = Buffer.from(`${JSON.stringify(expectedDraft, null, 2)}\n`, "utf8");
  assert.deepEqual(await publishJsonNoReplace(outputPath, expectedDraft), expectedBytes);
  assert.deepEqual(await fs.readFile(outputPath), expectedBytes);

  const failureCases = [
    ["open", { open: async () => { throw new Error("open failure"); } }],
    ["write", { open: async (...args) => { const handle = await fs.open(...args); return { writeFile: async () => { throw new Error("write failure"); }, sync: handle.sync.bind(handle), close: handle.close.bind(handle) }; } }],
    ["sync", { open: async (...args) => { const handle = await fs.open(...args); return { writeFile: handle.writeFile.bind(handle), sync: async () => { throw new Error("sync failure"); }, close: handle.close.bind(handle) }; } }],
    ["reread", { readFile: async () => Buffer.from("changed") }],
    ["publish", { link: async () => { throw new Error("publish failure"); } }],
  ];
  for (const [name, io] of failureCases) {
    const target = path.join(root, `${name}.json`);
    await assert.rejects(() => publishJsonNoReplace(target, expectedDraft, { io }));
    assert.equal(await fs.stat(target).then(() => true).catch(() => false), false, `${name} target must not publish`);
    assert.equal((await fs.readdir(root)).some((entry) => entry.startsWith(`.${name}.json.tmp-`)), false, `${name} temp must be removed`);
  }

  const competingPath = path.join(root, "competing.json");
  const competingBytes = Buffer.from("competing writer", "utf8");
  await assert.rejects(() => publishJsonNoReplace(competingPath, expectedDraft, {
    beforePublish: async () => fs.writeFile(competingPath, competingBytes, { flag: "wx" }),
  }));
  assert.deepEqual(await fs.readFile(competingPath), competingBytes);
  assert.equal((await fs.readdir(root)).some((entry) => entry.startsWith(".competing.json.tmp-")), false);
} finally {
  await fs.rm(root, { recursive: true, force: true });
}
