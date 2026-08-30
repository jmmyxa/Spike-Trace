import crypto from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";

function digest(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function runPython(repoRoot, args, context) {
  const python = process.env.SPIKETRACE_PYTHON;
  if (!python) throw new Error("SPIKETRACE_PYTHON must point to the supplied synthetic-pipeline Python.");
  const result = spawnSync(python, args, { cwd: repoRoot, encoding: "utf8", env: { ...process.env, PYTHONPATH: path.join(repoRoot, "src"), PYTHONUTF8: "1" } });
  if (result.status !== 0) throw new Error(`${context} failed (${result.status})\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
}

function inferencePayload(side, video, videoHash) {
  const crop = side === "far" ? [0, 0, 1920, 645] : [0, 255, 1920, 1080];
  return { format_version: 2, video, model_version: "synthetic-active-review-v1", settings: { device: "cpu", checkpoint: "runs/synthetic/best.pt", checkpoint_sha256: "a".repeat(64), video_sha256: videoHash, opencv_version: "synthetic", torch_version: "synthetic", torchvision_version: "synthetic", video, num_frames: 4, image_size: 32, window_seconds: 1, stride_seconds: 1, confidence_threshold: 0, merge_gap_seconds: 0.25, min_event_seconds: 0.2, batch_size: 1, crop, sampling_contract: "center-nearest-frame-v1" }, events: [], windows: [] };
}

function addCandidate(run, eventId, start, action, confidence) {
  const roundedConfidence = Math.round(confidence * 1_000_000) / 1_000_000;
  const windowIndex = run.windows.length;
  run.windows.push({ window_index: windowIndex, start_seconds: start, end_seconds: start + 1, action, confidence: roundedConfidence });
  run.events.push({ video_id: "round-01", event_id: eventId, start_ms: start * 1000, end_ms: (start + 1) * 1000, action, confidence: roundedConfidence, team_side: null, player_number: null, status: "predicted", model_version: "synthetic-active-review-v1", source: "sliding_window", source_window_indices: [windowIndex] });
}

function addBackground(run, start) {
  run.windows.push({ window_index: run.windows.length, start_seconds: start, end_seconds: start + 1, action: "background", confidence: 0.99 });
}

export async function createSyntheticSelection(fixtureDir, { repoRoot = process.cwd() } = {}) {
  const root = path.resolve(repoRoot);
  const videoPath = path.join(fixtureDir, "round-01.mp4");
  await fs.writeFile(path.join(fixtureDir, "make_video.py"), ["import cv2, numpy as np, sys", "writer = cv2.VideoWriter(sys.argv[1], cv2.VideoWriter_fourcc(*'mp4v'), 1.0, (1920, 1080))", "assert writer.isOpened()", "for index in range(400):", "    frame = np.full((1080, 1920, 3), index % 255, dtype=np.uint8)", "    writer.write(frame)", "writer.release()"].join("\n"));
  runPython(root, [path.join(fixtureDir, "make_video.py"), videoPath], "synthetic low-resolution video creation");
  const videoBytes = await fs.readFile(videoPath);
  const video = { path: path.relative(root, videoPath).split(path.sep).join("/"), fps: 1, frame_count: 400, width: 1920, height: 1080, duration_seconds: 400 };
  const far = inferencePayload("far", video, digest(videoBytes));
  const near = inferencePayload("near", video, digest(videoBytes));
  const minority = ["receive", "block", "dig"];
  for (let index = 0; index < 20; index += 1) { const start = 5 + index * 6; addCandidate(far, `conflict-far-${index}`, start, minority[index % minority.length], 0.8); addCandidate(near, `conflict-near-${index}`, start, "attack", 0.82); }
  for (let index = 0; index < 8; index += 1) addCandidate(far, `tail-${index}`, 132 + index * 6, "set", 0.95 - index / 100);
  for (let index = 0; index < 4; index += 1) { const start = 184 + index * 6; addCandidate(far, `dual-${index}`, start, "set", 0.6); addCandidate(near, `dual-${index}`, start, "set", 0.6); }
  for (let index = 0; index < 4; index += 1) addCandidate(far, `random-${index}`, 210 + index * 6, "tip", 0.3);
  for (let index = 0; index < 16; index += 1) addCandidate(far, `reserve-${index}`, 270 + index * 6, "serve", 0.5);
  for (let second = 340; second < 400; second += 1) { addBackground(far, second); addBackground(near, second); }
  const farPath = path.join(fixtureDir, "far.json"); const nearPath = path.join(fixtureDir, "near.json");
  await fs.writeFile(farPath, JSON.stringify(far)); await fs.writeFile(nearPath, JSON.stringify(near));
  const mergedDir = path.join(fixtureDir, "merged"); const selectionPath = path.join(fixtureDir, "selection.json");
  runPython(root, ["-c", ["from pathlib import Path", "from spiketrace.dual_crop_review import build_dual_crop_review", "from spiketrace.active_learning_selection import select_review_batch", "root, far, near, merged, selection = map(Path, __import__('sys').argv[1:])", "build_dual_crop_review(far, near, merged, repo_root=root)", "select_review_batch(merged / 'merged_candidates.json', selection, repo_root=root, preferred_clip_seconds=5, min_clip_seconds=5, max_clip_seconds=5)"].join("\n"), root, farPath, nearPath, mergedDir, selectionPath], "synthetic selector pipeline");
  return selectionPath;
}
