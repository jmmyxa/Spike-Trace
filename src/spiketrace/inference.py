from __future__ import annotations

import hashlib
import importlib.metadata
from pathlib import Path

import cv2

from .constants import SAMPLING_CONTRACT
from .domain import ActionWindow
from .events import merge_action_windows_with_provenance
from .ml import frames_to_tensor, load_checkpoint, require_torch, resolve_device
from .outputs import write_inference_outputs
from .video import (
    inspect_video,
    iter_sequential_video_clip_batches,
    iter_window_times,
)


def infer_video(
    video_path: str | Path,
    checkpoint_path: str | Path,
    output_dir: str | Path,
    *,
    stride_seconds: float = 0.4,
    confidence_threshold: float = 0.5,
    merge_gap_seconds: float = 0.25,
    min_event_seconds: float = 0.2,
    batch_size: int = 8,
    device: str = "auto",
    crop: tuple[int, int, int, int] | None = None,
) -> dict[str, object]:
    torch = require_torch()
    if batch_size <= 0:
        raise ValueError("batch_size must be positive.")
    selected_device = resolve_device(device)
    model, checkpoint = load_checkpoint(checkpoint_path, device=selected_device)
    metadata = inspect_video(video_path)
    num_frames = int(checkpoint["num_frames"])
    image_size = int(checkpoint["image_size"])
    window_seconds = float(checkpoint["window_seconds"])
    labels = list(checkpoint["labels"])
    model_version = str(checkpoint["model_version"])

    times = list(
        iter_window_times(
            metadata.duration_seconds,
            window_seconds=window_seconds,
            stride_seconds=stride_seconds,
        )
    )
    windows: list[ActionWindow] = []
    completed = 0
    for batch_times, clips in iter_sequential_video_clip_batches(
        metadata.path,
        times,
        num_frames=num_frames,
        image_size=image_size,
        batch_size=batch_size,
        crop=crop,
    ):
        tensors = [frames_to_tensor(clip) for clip in clips]
        batch = torch.stack(tensors).to(selected_device)
        with torch.no_grad():
            probabilities = torch.softmax(model(batch), dim=1).detach().cpu()
        scores, indices = probabilities.max(dim=1)
        for (start, end), score, index in zip(batch_times, scores, indices):
            windows.append(
                ActionWindow(
                    start_seconds=round(start, 6),
                    end_seconds=round(end, 6),
                    action=labels[int(index)],
                    confidence=round(float(score), 6),
                )
            )
        completed += len(batch_times)
        print(f"Processed {completed}/{len(times)} windows", flush=True)

    events, event_window_indices = merge_action_windows_with_provenance(
        windows,
        video_id=metadata.path.stem,
        model_version=model_version,
        confidence_threshold=confidence_threshold,
        merge_gap_seconds=merge_gap_seconds,
        min_event_seconds=min_event_seconds,
    )
    settings = {
        "device": selected_device,
        "checkpoint": str(Path(checkpoint_path).expanduser().resolve()),
        "checkpoint_sha256": _file_sha256(checkpoint_path),
        "video_sha256": _file_sha256(metadata.path),
        "opencv_version": str(cv2.__version__),
        "torch_version": str(torch.__version__),
        "torchvision_version": importlib.metadata.version("torchvision"),
        "video": metadata.to_dict(),
        "num_frames": num_frames,
        "image_size": image_size,
        "window_seconds": window_seconds,
        "stride_seconds": stride_seconds,
        "confidence_threshold": confidence_threshold,
        "merge_gap_seconds": merge_gap_seconds,
        "min_event_seconds": min_event_seconds,
        "batch_size": batch_size,
        "crop": list(crop) if crop is not None else None,
        "sampling_contract": checkpoint.get("sampling_contract", SAMPLING_CONTRACT),
    }
    json_path, csv_path = write_inference_outputs(
        output_dir,
        metadata=metadata,
        model_version=model_version,
        events=events,
        windows=windows,
        settings=settings,
        event_window_indices=event_window_indices,
    )
    return {
        "video": metadata.to_dict(),
        "model_version": model_version,
        "window_count": len(windows),
        "event_count": len(events),
        "events_json": str(json_path),
        "events_csv": str(csv_path),
    }


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().resolve().open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
