from __future__ import annotations

import random
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .constants import (
    ACTION_LABEL_SCHEMA_VERSION,
    CHECKPOINT_FORMAT_VERSION,
    SAMPLING_CONTRACT,
)
from .errors import CheckpointError

KINETICS_MEAN = (0.43216, 0.394666, 0.37645)
KINETICS_STD = (0.22803, 0.22145, 0.216989)
SUPPORTED_MODELS = ("r3d18", "tiny3d")


def require_torch():
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "PyTorch is required for training and inference. Install project dependencies first."
        ) from exc
    return torch


def resolve_device(requested: str = "auto") -> str:
    torch = require_torch()
    if requested != "auto":
        if requested == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is not available.")
        if requested == "mps" and not (
            hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
        ):
            raise RuntimeError("MPS was requested but is not available.")
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def seed_everything(seed: int) -> None:
    torch = require_torch()
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def frames_to_tensor(frames: np.ndarray):
    """Convert [T, H, W, C] uint8 RGB frames to normalized [C, T, H, W]."""
    torch = require_torch()
    if frames.ndim != 4 or frames.shape[-1] != 3:
        raise ValueError("Expected RGB frames with shape [T, H, W, 3].")
    tensor = torch.from_numpy(np.ascontiguousarray(frames)).permute(3, 0, 1, 2)
    tensor = tensor.to(dtype=torch.float32).div_(255.0)
    mean = torch.tensor(KINETICS_MEAN, dtype=tensor.dtype).view(3, 1, 1, 1)
    std = torch.tensor(KINETICS_STD, dtype=tensor.dtype).view(3, 1, 1, 1)
    return (tensor - mean) / std


def create_model(model_name: str, num_classes: int, *, pretrained: bool = False):
    torch = require_torch()
    if num_classes < 2:
        raise ValueError("At least two action classes are required.")

    if model_name == "r3d18":
        try:
            from torchvision.models.video import R3D_18_Weights, r3d_18
        except ImportError as exc:
            raise RuntimeError("torchvision is required for the R3D-18 model.") from exc
        weights = R3D_18_Weights.DEFAULT if pretrained else None
        model = r3d_18(weights=weights)
        model.fc = torch.nn.Linear(model.fc.in_features, num_classes)
        return model

    if model_name == "tiny3d":
        return torch.nn.Sequential(
            torch.nn.Conv3d(3, 8, kernel_size=3, padding=1),
            torch.nn.BatchNorm3d(8),
            torch.nn.ReLU(inplace=True),
            torch.nn.MaxPool3d(kernel_size=(1, 2, 2)),
            torch.nn.Conv3d(8, 16, kernel_size=3, padding=1),
            torch.nn.BatchNorm3d(16),
            torch.nn.ReLU(inplace=True),
            torch.nn.AdaptiveAvgPool3d((1, 1, 1)),
            torch.nn.Flatten(),
            torch.nn.Linear(16, num_classes),
        )

    raise ValueError(
        f"Unknown model '{model_name}'. Supported models: {', '.join(SUPPORTED_MODELS)}"
    )


def make_checkpoint(
    *,
    model,
    model_name: str,
    labels: Sequence[str],
    model_version: str,
    num_frames: int,
    image_size: int,
    window_seconds: float,
    epoch: int,
    metrics: dict[str, object],
    sampling_contract: str = SAMPLING_CONTRACT,
) -> dict[str, Any]:
    return {
        "format_version": CHECKPOINT_FORMAT_VERSION,
        "model_name": model_name,
        "model_version": model_version,
        "labels": list(labels),
        "action_label_schema_version": ACTION_LABEL_SCHEMA_VERSION,
        "sampling_contract": sampling_contract,
        "num_frames": num_frames,
        "image_size": image_size,
        "window_seconds": window_seconds,
        "epoch": epoch,
        "metrics": metrics,
        "normalization": {"mean": list(KINETICS_MEAN), "std": list(KINETICS_STD)},
        "model_state": model.state_dict(),
    }


def save_checkpoint(checkpoint: dict[str, Any], destination: str | Path) -> Path:
    torch = require_torch()
    path = Path(destination).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    torch.save(checkpoint, temporary)
    temporary.replace(path)
    return path


def load_checkpoint(checkpoint_path: str | Path, *, device: str):
    torch = require_torch()
    path = Path(checkpoint_path).expanduser().resolve()
    if not path.is_file():
        raise CheckpointError(f"Checkpoint does not exist: {path}")
    try:
        try:
            checkpoint = torch.load(path, map_location=device, weights_only=True)
        except TypeError:
            checkpoint = torch.load(path, map_location=device)
    except Exception as exc:
        raise CheckpointError(f"Could not load checkpoint: {path}") from exc

    required = {
        "format_version",
        "model_name",
        "model_version",
        "labels",
        "num_frames",
        "image_size",
        "window_seconds",
        "model_state",
    }
    if not isinstance(checkpoint, dict) or required - set(checkpoint):
        missing = required - set(checkpoint if isinstance(checkpoint, dict) else {})
        raise CheckpointError(
            "Checkpoint is missing required fields: " + ", ".join(sorted(missing))
        )
    if checkpoint["format_version"] != CHECKPOINT_FORMAT_VERSION:
        raise CheckpointError(
            f"Unsupported checkpoint format: {checkpoint['format_version']}"
        )
    checkpoint = dict(checkpoint)
    sampling_contract = checkpoint.setdefault("sampling_contract", SAMPLING_CONTRACT)
    if sampling_contract != SAMPLING_CONTRACT:
        raise CheckpointError(f"Unsupported sampling contract: {sampling_contract}")

    labels = checkpoint["labels"]
    model = create_model(checkpoint["model_name"], len(labels), pretrained=False)
    model.load_state_dict(checkpoint["model_state"])
    model.to(device)
    model.eval()
    return model, checkpoint
