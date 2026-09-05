from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from .models import NumberObservation, NumberResolution


def normalize_number(raw_text: str) -> str | None:
    """Return a digits-only OCR candidate, preserving leading zeroes."""
    if not isinstance(raw_text, str):
        raise TypeError("raw_text must be a string.")
    digits = "".join(re.findall(r"\d", raw_text))
    return digits or None


def aggregate_number_candidates(
    observations: Iterable[NumberObservation],
    *,
    roster: Iterable[str] | None = None,
    min_confidence: float = 0.55,
    margin: float = 0.15,
) -> NumberResolution:
    """Aggregate OCR across time without guessing when evidence conflicts."""
    if not 0 <= min_confidence <= 1 or not 0 <= margin <= 1:
        raise ValueError("min_confidence and margin must be between 0 and 1.")
    roster_set = None if roster is None else {str(item) for item in roster}
    scores: defaultdict[str, float] = defaultdict(float)
    for observation in observations:
        candidate = normalize_number(observation.raw_text)
        if candidate is None or observation.visibility_state in {"occluded", "not_visible"}:
            continue
        if roster_set is not None and candidate not in roster_set:
            continue
        scores[candidate] += float(observation.confidence)
    ranked = tuple(sorted(scores.items(), key=lambda item: (-item[1], item[0])))
    if not ranked:
        return NumberResolution(None, "not_visible", 0.0)
    total = sum(score for _, score in ranked)
    winner, winner_score = ranked[0]
    normalized_confidence = winner_score / total if total else 0.0
    if len(ranked) > 1 and winner_score - ranked[1][1] < margin * total:
        return NumberResolution(None, "candidate", normalized_confidence, ranked)
    if normalized_confidence < min_confidence:
        return NumberResolution(None, "unreadable", normalized_confidence, ranked)
    return NumberResolution(winner, "confirmed", normalized_confidence, ranked)


resolve_number_observations = aggregate_number_candidates
