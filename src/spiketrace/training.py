from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from .constants import ACTION_LABELS
from .domain import AnnotationRecord
from .manifest import load_manifest, summarize_manifest
from .metrics import classification_metrics
from .ml import (
    create_model,
    frames_to_tensor,
    make_checkpoint,
    require_torch,
    resolve_device,
    save_checkpoint,
    seed_everything,
)
from .video import inspect_video, sample_video_clip


class VideoClipDataset:
    def __init__(
        self,
        records: Sequence[AnnotationRecord],
        *,
        labels: Sequence[str],
        num_frames: int,
        image_size: int,
    ) -> None:
        self.records = list(records)
        self.labels = list(labels)
        self.label_to_index = {label: index for index, label in enumerate(labels)}
        self.num_frames = num_frames
        self.image_size = image_size

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int):
        record = self.records[index]
        frames = sample_video_clip(
            record.video_path,
            record.start_seconds,
            record.end_seconds,
            num_frames=self.num_frames,
            image_size=self.image_size,
            crop=record.crop,
        )
        return frames_to_tensor(frames), self.label_to_index[record.label]


def _validate_annotation_bounds(records: Sequence[AnnotationRecord]) -> None:
    metadata_by_path = {
        video_path: inspect_video(video_path)
        for video_path in {record.video_path for record in records}
    }
    for record in records:
        metadata = metadata_by_path[record.video_path]
        tolerance = 1.0 / metadata.fps
        if record.end_seconds > metadata.duration_seconds + tolerance:
            raise ValueError(
                f"Annotation ends after the video: {record.video_path} "
                f"ends at {metadata.duration_seconds:.3f}s, annotation ends at "
                f"{record.end_seconds:.3f}s."
            )


def _run_epoch(model, loader, criterion, device: str, optimizer=None):
    torch = require_torch()
    training = optimizer is not None
    model.train(training)
    total_loss = 0.0
    targets: list[int] = []
    predictions: list[int] = []

    context = torch.enable_grad() if training else torch.no_grad()
    with context:
        for clips, target in loader:
            clips = clips.to(device)
            target = target.to(device)
            if training:
                optimizer.zero_grad(set_to_none=True)
            logits = model(clips)
            loss = criterion(logits, target)
            if training:
                loss.backward()
                optimizer.step()
            total_loss += float(loss.detach().cpu()) * clips.shape[0]
            targets.extend(target.detach().cpu().tolist())
            predictions.extend(logits.argmax(dim=1).detach().cpu().tolist())

    average_loss = total_loss / len(targets) if targets else 0.0
    return average_loss, targets, predictions


def train_action_model(
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    video_root: str | Path | None = None,
    model_name: str = "r3d18",
    model_version: str = "action-r3d18-v0.1",
    pretrained: bool = False,
    epochs: int = 10,
    batch_size: int = 4,
    learning_rate: float = 1e-4,
    weight_decay: float = 1e-4,
    num_frames: int = 16,
    image_size: int = 112,
    window_seconds: float = 1.0,
    device: str = "auto",
    seed: int = 42,
    num_workers: int = 0,
) -> dict[str, object]:
    torch = require_torch()
    if epochs <= 0 or batch_size <= 0 or learning_rate <= 0:
        raise ValueError("epochs, batch_size, and learning_rate must be positive.")
    if num_frames <= 0 or image_size <= 0 or window_seconds <= 0:
        raise ValueError("num_frames, image_size, and window_seconds must be positive.")

    records = load_manifest(manifest_path, video_root=video_root)
    _validate_annotation_bounds(records)
    train_records = [record for record in records if record.split == "train"]
    val_records = [record for record in records if record.split == "val"]
    if not train_records:
        raise ValueError("The manifest must contain at least one train record.")
    if not val_records:
        raise ValueError("The manifest must contain at least one val record.")

    labels = list(ACTION_LABELS)
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    selected_device = resolve_device(device)
    seed_everything(seed)

    train_dataset = VideoClipDataset(
        train_records, labels=labels, num_frames=num_frames, image_size=image_size
    )
    val_dataset = VideoClipDataset(
        val_records, labels=labels, num_frames=num_frames, image_size=image_size
    )
    train_loader = torch.utils.data.DataLoader(
        train_dataset,
        batch_size=batch_size,
        shuffle=True,
        num_workers=num_workers,
        pin_memory=selected_device == "cuda",
    )
    val_loader = torch.utils.data.DataLoader(
        val_dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        pin_memory=selected_device == "cuda",
    )

    model = create_model(model_name, len(labels), pretrained=pretrained).to(
        selected_device
    )
    counts = Counter(record.label for record in train_records)
    class_weights = [
        len(train_records) / (len(labels) * counts[label]) if counts[label] else 0.0
        for label in labels
    ]
    criterion = torch.nn.CrossEntropyLoss(
        weight=torch.tensor(class_weights, dtype=torch.float32, device=selected_device)
    )
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=learning_rate, weight_decay=weight_decay
    )

    config = {
        "manifest": str(Path(manifest_path).expanduser().resolve()),
        "manifest_summary": summarize_manifest(records),
        "model_name": model_name,
        "model_version": model_version,
        "pretrained": pretrained,
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "weight_decay": weight_decay,
        "num_frames": num_frames,
        "image_size": image_size,
        "window_seconds": window_seconds,
        "device": selected_device,
        "seed": seed,
        "labels": labels,
    }
    (output / "training_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    history: list[dict[str, object]] = []
    best_macro_f1 = -1.0
    for epoch in range(1, epochs + 1):
        train_loss, train_targets, train_predictions = _run_epoch(
            model, train_loader, criterion, selected_device, optimizer
        )
        val_loss, val_targets, val_predictions = _run_epoch(
            model, val_loader, criterion, selected_device
        )
        train_metrics = classification_metrics(train_targets, train_predictions, labels)
        val_metrics = classification_metrics(val_targets, val_predictions, labels)
        epoch_result = {
            "epoch": epoch,
            "train_loss": round(train_loss, 6),
            "val_loss": round(val_loss, 6),
            "train": train_metrics,
            "val": val_metrics,
        }
        history.append(epoch_result)
        print(json.dumps(epoch_result, ensure_ascii=False))

        checkpoint = make_checkpoint(
            model=model,
            model_name=model_name,
            labels=labels,
            model_version=model_version,
            num_frames=num_frames,
            image_size=image_size,
            window_seconds=window_seconds,
            epoch=epoch,
            metrics=epoch_result,
        )
        save_checkpoint(checkpoint, output / "latest.pt")
        if float(val_metrics["macro_f1"]) > best_macro_f1:
            best_macro_f1 = float(val_metrics["macro_f1"])
            save_checkpoint(checkpoint, output / "best.pt")

    report = {
        "config": config,
        "best_macro_f1": best_macro_f1,
        "history": history,
        "best_checkpoint": str(output / "best.pt"),
        "latest_checkpoint": str(output / "latest.pt"),
    }
    (output / "metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
