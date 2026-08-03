from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path

from .constants import ACTION_LABELS
from .domain import AnnotationRecord
from .errors import ManifestError

REQUIRED_COLUMNS = {
    "video_path",
    "start_seconds",
    "end_seconds",
    "label",
    "split",
}
ALLOWED_SPLITS = {"train", "val", "test"}


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _optional_crop(
    row: dict[str, str | None], row_number: int
) -> tuple[int, int, int, int] | None:
    columns = ("crop_x1", "crop_y1", "crop_x2", "crop_y2")
    raw_values = [(row.get(column) or "").strip() for column in columns]
    if not any(raw_values):
        return None
    if not all(raw_values):
        raise ManifestError(
            f"Row {row_number} must provide all four crop coordinates or none."
        )
    try:
        x1, y1, x2, y2 = (int(value) for value in raw_values)
    except ValueError as exc:
        raise ManifestError(
            f"Row {row_number} crop coordinates must be integers."
        ) from exc
    if min(x1, y1, x2, y2) < 0 or x2 <= x1 or y2 <= y1:
        raise ManifestError(f"Row {row_number} has invalid crop coordinates.")
    return x1, y1, x2, y2


def load_manifest(
    manifest_path: str | Path,
    *,
    video_root: str | Path | None = None,
    allowed_labels: Sequence[str] = ACTION_LABELS,
    require_files: bool = True,
) -> list[AnnotationRecord]:
    manifest = Path(manifest_path).expanduser().resolve()
    if not manifest.is_file():
        raise ManifestError(f"Annotation manifest does not exist: {manifest}")

    root = Path(video_root).expanduser().resolve() if video_root else manifest.parent
    labels = set(allowed_labels)
    records: list[AnnotationRecord] = []

    with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or ())
        missing = REQUIRED_COLUMNS - fieldnames
        if missing:
            raise ManifestError(
                "Annotation manifest is missing columns: " + ", ".join(sorted(missing))
            )

        for row_number, row in enumerate(reader, start=2):
            try:
                raw_path = (row.get("video_path") or "").strip()
                start = float(row.get("start_seconds") or "")
                end = float(row.get("end_seconds") or "")
            except ValueError as exc:
                raise ManifestError(
                    f"Row {row_number} has invalid start_seconds or end_seconds."
                ) from exc

            if not raw_path:
                raise ManifestError(f"Row {row_number} has an empty video_path.")
            video_path = Path(raw_path).expanduser()
            if not video_path.is_absolute():
                video_path = root / video_path
            video_path = video_path.resolve()

            label = (row.get("label") or "").strip()
            split = (row.get("split") or "").strip().lower()
            if label not in labels:
                raise ManifestError(
                    f"Row {row_number} has unknown label '{label}'. "
                    f"Allowed labels: {', '.join(allowed_labels)}"
                )
            if split not in ALLOWED_SPLITS:
                raise ManifestError(
                    f"Row {row_number} has invalid split '{split}'. "
                    "Use train, val, or test."
                )
            if start < 0 or end <= start:
                raise ManifestError(
                    f"Row {row_number} must satisfy 0 <= start_seconds < end_seconds."
                )
            if require_files and not video_path.is_file():
                raise ManifestError(
                    f"Row {row_number} video does not exist: {video_path}"
                )

            records.append(
                AnnotationRecord(
                    video_path=video_path,
                    start_seconds=start,
                    end_seconds=end,
                    label=label,
                    split=split,
                    team_side=_optional_text(row.get("team_side")),
                    player_number=_optional_text(row.get("player_number")),
                    crop=_optional_crop(row, row_number),
                )
            )

    if not records:
        raise ManifestError("Annotation manifest contains no data rows.")

    splits_by_video: dict[Path, set[str]] = {}
    for record in records:
        splits_by_video.setdefault(record.video_path, set()).add(record.split)
    leaking_videos = {
        path: splits for path, splits in splits_by_video.items() if len(splits) > 1
    }
    if leaking_videos:
        details = "; ".join(
            f"{path.name}: {', '.join(sorted(splits))}"
            for path, splits in sorted(
                leaking_videos.items(), key=lambda item: str(item[0])
            )
        )
        raise ManifestError(
            "A video cannot appear in multiple dataset splits. "
            f"Split leakage found in: {details}"
        )
    return records


def summarize_manifest(records: Iterable[AnnotationRecord]) -> dict[str, object]:
    items = list(records)
    split_counts = Counter(record.split for record in items)
    label_counts = Counter(record.label for record in items)
    matches_by_split = {
        split: len({record.video_path for record in items if record.split == split})
        for split in sorted(ALLOWED_SPLITS)
    }
    return {
        "records": len(items),
        "videos": len({record.video_path for record in items}),
        "duration_seconds": round(sum(record.duration_seconds for record in items), 3),
        "records_by_split": dict(sorted(split_counts.items())),
        "videos_by_split": matches_by_split,
        "records_by_label": dict(sorted(label_counts.items())),
    }
