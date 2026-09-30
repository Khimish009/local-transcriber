"""Check the alignment thresholds against a real recording (Phase 10 calibration).

`SPEAKER_MATCH_TOLERANCE_MS`, `BLOCK_SILENCE_GAP_MS` and `MAX_BLOCK_SECONDS` were picked by
reasoning, not measurement (AGENTS.md). This module measures what the recording actually
contains, so the numbers can be confirmed or moved on evidence.

Usage, from `backend/` — no ML dependencies needed:

    PYTHONPATH=. .venv/bin/python -m benchmark.thresholds \\
        --run ../data/benchmark/runs/zasedanie --model v3_e2e_rnnt
"""

import argparse
import json
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from statistics import median


def percentile(values: Sequence[float], share: float) -> float:
    """Nearest-rank percentile. Enough for calibration; avoids a numpy dependency."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, ceil(share * len(ordered)) - 1))
    return ordered[index]


@dataclass(frozen=True)
class Span:
    start: float
    end: float

    def distance_to(self, point: float) -> float:
        """0 when the point is inside, otherwise the gap to the nearest edge."""
        if self.start <= point < self.end:
            return 0.0
        return self.start - point if point < self.start else point - self.end


def nearest_distance(spans: Sequence[Span], point: float) -> float:
    """Distance from a point to the closest span. Spans must be sorted and disjoint."""
    if not spans:
        return float("inf")
    starts = [s.start for s in spans]
    index = bisect_left(starts, point)
    candidates = [spans[i] for i in (index - 1, index) if 0 <= i < len(spans)]
    return min(c.distance_to(point) for c in candidates)


def word_gaps(words: Sequence[dict]) -> list[float]:
    """Silence between consecutive words of the same speaker, in seconds."""
    gaps = []
    for previous, current in zip(words, words[1:], strict=False):
        if previous.get("speaker_id") != current.get("speaker_id"):
            continue
        gap = current["start"] - previous["end"]
        if gap > 0:
            gaps.append(gap)
    return gaps


def report(run_dir: Path, model: str) -> str:
    transcript = json.loads((run_dir / f"transcript.{model}.json").read_text(encoding="utf-8"))
    diarization = json.loads((run_dir / "diarization.json").read_text(encoding="utf-8"))

    words = transcript["words"]
    segments = transcript["segments"]
    raw_spans = diarization.get("exclusive") or diarization["segments"]
    exclusive = sorted((Span(s["start"], s["end"]) for s in raw_spans), key=lambda s: s.start)

    lines = [f"# Пороги выравнивания на `{run_dir.name}` (`{model}`)", ""]

    # --- SPEAKER_MATCH_TOLERANCE_MS -----------------------------------------
    distances = [nearest_distance(exclusive, (w["start"] + w["end"]) / 2) * 1000 for w in words]
    outside = [d for d in distances if d > 0]
    unassigned = sum(1 for w in words if w.get("speaker_id") is None)

    lines += [
        "## SPEAKER_MATCH_TOLERANCE_MS",
        "",
        f"Слов всего: {len(words)}. Середина слова попала внутрь интервала спикера сразу: "
        f"{len(words) - len(outside)} ({(len(words) - len(outside)) / len(words):.1%}).",
        f"Понадобился допуск: {len(outside)} слов. Осталось без спикера: {unassigned}.",
        "",
    ]
    if outside:
        lines += [
            "Расстояние до ближайшего интервала у тех, кому допуск понадобился (мс):",
            "",
            f"- медиана {median(outside):.0f}",
            f"- 90-й перцентиль {percentile(outside, 0.90):.0f}",
            f"- 99-й перцентиль {percentile(outside, 0.99):.0f}",
            f"- максимум {max(outside):.0f}",
            "",
            f"Порог 250 мс покрыл бы {sum(1 for d in outside if d <= 250) / len(outside):.1%} "
            "из них.",
            "",
        ]

    # --- BLOCK_SILENCE_GAP_MS -----------------------------------------------
    gaps = word_gaps(words)
    lines += [
        "## BLOCK_SILENCE_GAP_MS",
        "",
        f"Пауз между словами одного спикера: {len(gaps)}.",
        "",
        f"- медиана {median(gaps) * 1000:.0f} мс",
        f"- 90-й перцентиль {percentile(gaps, 0.90) * 1000:.0f} мс",
        f"- 99-й перцентиль {percentile(gaps, 0.99) * 1000:.0f} мс",
        f"- максимум {max(gaps) * 1000:.0f} мс" if gaps else "- пауз нет",
        "",
    ]
    for candidate in (800, 1000, 1500, 2000, 3000):
        splits = sum(1 for g in gaps if g * 1000 >= candidate)
        lines.append(f"- порог {candidate} мс → {splits} разрывов внутри реплики одного спикера")
    lines.append("")

    # --- MAX_BLOCK_SECONDS ---------------------------------------------------
    lengths = [s["end"] - s["start"] for s in segments]
    at_cap = [length for length in lengths if length >= 39.0]
    lines += [
        "## MAX_BLOCK_SECONDS",
        "",
        f"Блоков: {len(segments)}. Медиана {median(lengths):.1f} с, "
        f"90-й перцентиль {percentile(lengths, 0.90):.1f} с, максимум {max(lengths):.1f} с.",
        f"Упёрлись в потолок 40 с: {len(at_cap)} блоков ({len(at_cap) / len(segments):.1%}).",
        "",
        "Блок, упёршийся в потолок, режется по времени, а не по смыслу — чем их больше, "
        "тем чаще фраза рвётся посередине.",
        "",
    ]
    words_per_block = [len(s["text"].split()) for s in segments]
    lines.append(
        f"Слов в блоке: медиана {median(words_per_block):.0f}, максимум {max(words_per_block)}."
    )

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--model", default="v3_e2e_rnnt")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    text = report(args.run, args.model)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"written: {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
