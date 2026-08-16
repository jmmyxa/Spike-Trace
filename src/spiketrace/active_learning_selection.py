from __future__ import annotations

import os as _os
from collections.abc import Iterable
from pathlib import Path

from . import _active_learning_selection_artifact as _artifact
from . import _active_learning_selection_contract as _contract
from . import _active_learning_selector as _selector
from .dual_crop_review import verify_dual_crop_review

os = _os
MINORITY_ACTIONS = _contract.MINORITY_ACTIONS
ROUND_ONE_QUOTAS = _contract.ROUND_ONE_QUOTAS
SELECTION_ROOT_FIELDS = _contract.SELECTION_ROOT_FIELDS
format_timecode = _contract.format_timecode


def select_review_batch(
    merged_json_path: str | Path,
    output_path: str | Path,
    *,
    repo_root: str | Path,
    round_number: int = 1,
    seed: int = 42,
    preferred_clip_seconds: float = 15.0,
    min_clip_seconds: float = 5.0,
    max_clip_seconds: float = 30.0,
    min_anchor_gap_seconds: float = 5.0,
    time_strata: int = 10,
    previous_selection_paths: Iterable[str | Path] = (),
) -> dict[str, object]:
    return _selector.select_review_batch(
        merged_json_path,
        output_path,
        repo_root=repo_root,
        round_number=round_number,
        seed=seed,
        preferred_clip_seconds=preferred_clip_seconds,
        min_clip_seconds=min_clip_seconds,
        max_clip_seconds=max_clip_seconds,
        min_anchor_gap_seconds=min_anchor_gap_seconds,
        time_strata=time_strata,
        previous_selection_paths=previous_selection_paths,
        _verifier=verify_dual_crop_review,
    )


def validate_merged_review_source(
    merged_json_path: str | Path,
    *,
    repo_root: str | Path,
    require_video: bool = True,
) -> dict[str, object]:
    return _artifact.validate_merged_review_source(
        merged_json_path,
        repo_root=repo_root,
        require_video=require_video,
        _verifier=verify_dual_crop_review,
    )


def write_review_selection(
    payload: object,
    output_path: str | Path,
    *,
    repo_root: str | Path,
) -> dict[str, object]:
    return _artifact.write_review_selection(
        payload,
        output_path,
        repo_root=repo_root,
        _verifier=verify_dual_crop_review,
    )


def load_review_selection(
    selection_path: str | Path,
    *,
    repo_root: str | Path,
    require_video: bool = True,
) -> dict[str, object]:
    return _artifact.load_review_selection(
        selection_path,
        repo_root=repo_root,
        require_video=require_video,
        _verifier=verify_dual_crop_review,
    )


def _relative_posix_path(value: object, description: str) -> str:
    return _artifact._relative_posix_path(value, description)
