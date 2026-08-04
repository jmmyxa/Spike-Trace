from __future__ import annotations


def format_video_time(seconds: float) -> str:
    total_centiseconds = round(seconds * 100)
    hours, remainder = divmod(total_centiseconds, 60 * 60 * 100)
    minutes, remainder = divmod(remainder, 60 * 100)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"
