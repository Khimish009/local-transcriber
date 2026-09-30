"""Turn benchmark runs into a readable Markdown comparison (T10.3).

Usage, from `backend/` with the plain dev venv — no ML dependencies are needed:

    PYTHONPATH=. .venv/bin/python -m benchmark.report --runs ../data/benchmark/runs

The first model listed is the reference the others are compared against; that is the
current default `ASR_MODEL`, not a claim that it is correct.
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from benchmark.metrics import (
    Agreement,
    Disagreement,
    Interval,
    Word,
    compare,
    long_monologues,
    numeric_disagreements,
    overlapping_speech,
    profile,
    short_replies,
    term_hits,
    within,
)

EXAMPLES_PER_CATEGORY = 6


def timestamp(seconds: float) -> str:
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def load_transcript(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def words_of(transcript: dict[str, Any]) -> list[Word]:
    return [Word(w["text"], w["start"], w["end"]) for w in transcript["words"]]


def segments_of(transcript: dict[str, Any]) -> list[tuple[float, float, str]]:
    return [(s["start"], s["end"], s["text"]) for s in transcript["segments"]]


def discover(run_dir: Path) -> dict[str, Path]:
    """Model name → transcript path, for every model present in this run."""
    return {
        path.name[len("transcript.") : -len(".json")]: path
        for path in sorted(run_dir.glob("transcript.*.json"))
    }


def category_regions(transcript: dict[str, Any], run_dir: Path) -> dict[str, list[Interval]]:
    """The stretches of audio each T10.3 category covers, measured on the reference run."""
    segments = segments_of(transcript)
    regions = {
        "короткие реплики": short_replies(segments),
        "длинные монологи": long_monologues(segments),
    }

    diarization_path = run_dir / "diarization.json"
    if diarization_path.is_file():
        diarization = json.loads(diarization_path.read_text(encoding="utf-8"))
        raw = [(s["speaker_id"], s["start"], s["end"]) for s in diarization.get("segments", [])]
        regions["перекрывающаяся речь"] = overlapping_speech(raw)
    return regions


def format_examples(disagreements: list[Disagreement], limit: int = EXAMPLES_PER_CATEGORY) -> str:
    if not disagreements:
        return "_расхождений нет_\n"
    lines = []
    for d in disagreements[:limit]:
        left = d.a or "—"
        right = d.b or "—"
        lines.append(f"- `{timestamp(d.start)}` {left} → {right}")
    if len(disagreements) > limit:
        lines.append(f"- _…ещё {len(disagreements) - limit}_")
    return "\n".join(lines) + "\n"


def repeated_pairs(disagreements: list[Disagreement], limit: int = 12) -> str:
    counter = Counter(d.pair for d in disagreements if len(d.a.split()) <= 3)
    repeated = [(pair, n) for pair, n in counter.most_common(limit) if n > 1]
    if not repeated:
        return "_систематических расхождений нет_\n"
    return "\n".join(f"- {n}× {a or '—'} → {b or '—'}" for (a, b), n in repeated) + "\n"


def timing_table(timing: dict[str, Any] | None, models: list[str]) -> str:
    if not timing:
        return "_тайминг не сохранён (прогон был взят из кэша)_\n"
    audio = timing.get("audio_seconds") or 0.0
    rows = ["| Модель | ASR, с | RTF | Загрузка модели, с |", "|---|---|---|---|"]
    for model in models:
        asr = timing.get("asr_seconds", {}).get(model)
        load = timing.get("model_load_seconds", {}).get(model)
        if asr is None:
            rows.append(f"| `{model}` | из кэша | — | — |")
            continue
        rtf = asr / audio if audio else 0.0
        rows.append(
            f"| `{model}` | {asr:.0f} | {rtf:.3f} | {load:.0f} |"
            if load
            else f"| `{model}` | {asr:.0f} | {rtf:.3f} | — |"
        )
    diarization = timing.get("diarization_seconds")
    if diarization:
        rows.append(f"| _диаризация (общая)_ | {diarization:.0f} | {diarization / audio:.3f} | — |")
    return "\n".join(rows) + "\n"


def report_sample(run_dir: Path, models: list[str], terms: list[str]) -> str:
    available = discover(run_dir)
    missing = [m for m in models if m not in available]
    if missing:
        raise SystemExit(f"{run_dir.name}: no transcript for {', '.join(missing)}")

    transcripts = {model: load_transcript(available[model]) for model in models}
    reference, *others = models
    ref_transcript = transcripts[reference]
    ref_words = words_of(ref_transcript)

    timing_path = run_dir / "timing.json"
    timing = json.loads(timing_path.read_text(encoding="utf-8")) if timing_path.is_file() else None
    duration = (timing or {}).get("audio_seconds") or ref_transcript["source"].get(
        "duration_seconds"
    )

    out = [f"## Запись `{run_dir.name}`", ""]
    if duration:
        out.append(
            f"Длительность: {timestamp(duration)} · спикеров: {len(ref_transcript['speakers'])}"
        )
        out.append("")

    out.append("### Что выдала каждая модель")
    out.append("")
    out.append("| Модель | Слов | Блоков | Заглавных | Цифрами | Точек | Запятых | Читаемый |")
    out.append("|---|---|---|---|---|---|---|---|")
    for model in models:
        texts = [w["text"] for w in transcripts[model]["words"]]
        p = profile(texts)
        out.append(
            f"| `{model}` | {p.words} | {len(transcripts[model]['segments'])} | "
            f"{p.capitalized} ({p.capitalized_share:.0%}) | {p.digits} | {p.periods} | "
            f"{p.commas} | {'да' if p.is_formatted else '**нет**'} |"
        )
    out.append("")

    out.append("### Скорость")
    out.append("")
    out.append(timing_table(timing, models))

    regions = category_regions(ref_transcript, run_dir)

    for other in others:
        agreement: Agreement = compare(ref_words, words_of(transcripts[other]))
        out.append(f"### `{reference}` против `{other}`")
        out.append("")
        out.append(
            f"Совпадение по словам (без регистра и пунктуации): **{agreement.ratio:.1%}** "
            f"({agreement.matched} из {agreement.total_a})."
        )
        kinds = Counter(d.kind for d in agreement.disagreements)
        out.append(
            f"Расхождений: замен {kinds['replace']}, "
            f"только в `{reference}` {kinds['only_a']}, только в `{other}` {kinds['only_b']}."
        )
        out.append("")
        out.append("**Систематические расхождения**")
        out.append("")
        out.append(repeated_pairs(agreement.disagreements))

        out.append("**Числа**")
        out.append("")
        numeric = numeric_disagreements(agreement.disagreements)
        out.append(f"Расхождений, затрагивающих числа: {len(numeric)}.")
        out.append("")
        out.append(format_examples(numeric))

        for name, intervals in regions.items():
            hits = within(agreement.disagreements, intervals)
            covered = sum(i.end - i.start for i in intervals)
            out.append(f"**{name.capitalize()}**")
            out.append("")
            out.append(
                f"Участков: {len(intervals)}, суммарно {covered:.0f} с. "
                f"Расхождений внутри них: {len(hits)}."
            )
            out.append("")
            out.append(format_examples(hits))

        if terms:
            out.append("**Терминология**")
            out.append("")
            out.append("| Термин | " + " | ".join(f"`{m}`" for m in models) + " |")
            out.append("|---" * (len(models) + 1) + "|")
            counts = {
                model: term_hits([w["text"] for w in transcripts[model]["words"]], terms)
                for model in models
            }
            for term in terms:
                row = " | ".join(str(counts[model][term]) for model in models)
                out.append(f"| {term} | {row} |")
            out.append("")

    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, default=Path("/data/benchmark/runs"))
    parser.add_argument(
        "--models",
        default="v3_e2e_rnnt,v3_rnnt",
        help="comma-separated; the first one is the reference the others are compared to",
    )
    parser.add_argument(
        "--terms",
        type=Path,
        default=None,
        help="optional file with one domain term per line, to check jargon coverage",
    )
    parser.add_argument("--out", type=Path, default=None, help="write Markdown here")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    terms = (
        [
            line.strip()
            for line in args.terms.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if args.terms
        else []
    )

    run_dirs = sorted(d for d in args.runs.iterdir() if d.is_dir()) if args.runs.is_dir() else []
    if not run_dirs:
        raise SystemExit(f"No benchmark runs in {args.runs}")

    parts = [
        "# Бенчмарк качества ASR",
        "",
        "Сравнение моделей GigaAM на одной и той же диаризации: различается только `ASR_MODEL`.",
        "",
        "Эталонной расшифровки нет, поэтому здесь **нет WER**. Измеряется расхождение моделей "
        "между собой и то, на каком материале оно возникает. Какая модель права в конкретном "
        "случае — вопрос к человеку, читающему примеры ниже.",
        "",
        f"Записей: {len(run_dirs)}.",
        "",
    ]
    for run_dir in run_dirs:
        parts.append(report_sample(run_dir, models, terms))

    text = "\n".join(parts)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"written: {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
