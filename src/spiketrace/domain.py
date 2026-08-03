from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class AnnotationRecord:
    video_path: Path
    start_seconds: float
    end_seconds: float
    label: str
    split: str
    team_side: str | None = None
    player_number: str | None = None
    crop: tuple[int, int, int, int] | None = None

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    path: Path
    fps: float
    frame_count: int
    width: int
    height: int
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path)
        return data


@dataclass(frozen=True, slots=True)
class ActionWindow:
    start_seconds: float
    end_seconds: float
    action: str
    confidence: float


@dataclass(frozen=True, slots=True)
class ActionEvent:
    video_id: str
    event_id: str
    start_ms: int
    end_ms: int
    action: str
    confidence: float
    team_side: str | None
    player_number: str | None
    status: str
    model_version: str
    source: str = "sliding_window"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
