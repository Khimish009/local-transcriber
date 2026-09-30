"""Pure comparison logic for the ASR benchmark (T10.3).

No ML imports and no I/O, so this runs in the plain dev venv and is unit-tested like the
rest of the deterministic pipeline logic.

There is no reference transcript, so nothing here measures WER. What it measures is where
two models disagree and what kind of material they disagree on — names, numbers, jargon,
overlapping speech, short replies, long monologues. That is what T10.3 asks for and it is
all that is honestly available without a hand-made ground truth.
"""

import difflib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

# Keeps letters, digits and inner hyphens; drops punctuation and case so that the diff
# reflects wording rather than formatting. `ё` folds to `е`: the models spell it differently
# and that is not a recognition error.
_STRIP = re.compile(r"[^\w\-]+", re.UNICODE)

# A short exchange ("Да.", "Не возражаю.") stresses a model differently from a monologue.
SHORT_REPLY_MAX_WORDS = 5
SHORT_REPLY_MAX_SECONDS = 3.0
LONG_MONOLOGUE_MIN_SECONDS = 30.0


def normalize(word: str) -> str:
    return _STRIP.sub("", word.lower().replace("ё", "е"))


def normalize_all(words: Iterable[str]) -> list[str]:
    return [n for n in (normalize(w) for w in words) if n]


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Disagreement:
    """One place where the two models produced different wording."""

    kind: str  # replace | only_a | only_b
    a: str
    b: str
    start: float
    end: float

    @property
    def pair(self) -> tuple[str, str]:
        return (self.a, self.b)


@dataclass
class Agreement:
    matched: int
    total_a: int
    total_b: int
    disagreements: list[Disagreement] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        """Share of model A's words that model B reproduced verbatim."""
        return self.matched / self.total_a if self.total_a else 0.0


def _span(words: Sequence[Word], lo: int, hi: int) -> tuple[float, float]:
    """Time span of words[lo:hi]; a zero-length slice collapses onto its neighbour."""
    if not words:
        return (0.0, 0.0)
    if lo >= hi:
        anchor = words[min(lo, len(words) - 1)]
        return (anchor.start, anchor.start)
    return (words[lo].start, words[hi - 1].end)


def compare(a: Sequence[Word], b: Sequence[Word]) -> Agreement:
    """Align two word streams and collect every place they differ."""
    na, nb = [normalize(w.text) for w in a], [normalize(w.text) for w in b]
    matcher = difflib.SequenceMatcher(a=na, b=nb, autojunk=False)

    matched = 0
    found: list[Disagreement] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            matched += i2 - i1
            continue
        text_a = " ".join(w.text for w in a[i1:i2])
        text_b = " ".join(w.text for w in b[j1:j2])
        start, end = _span(a, i1, i2) if i2 > i1 else _span(b, j1, j2)
        kind = {"replace": "replace", "delete": "only_a", "insert": "only_b"}[tag]
        found.append(Disagreement(kind, text_a, text_b, start, end))

    return Agreement(matched=matched, total_a=len(na), total_b=len(nb), disagreements=found)


@dataclass(frozen=True)
class Interval:
    start: float
    end: float

    def contains(self, start: float, end: float) -> bool:
        """True when the two spans overlap at all — a word may straddle a boundary."""
        return start < self.end and end > self.start


def merge_intervals(intervals: Iterable[Interval]) -> list[Interval]:
    ordered = sorted(intervals, key=lambda i: i.start)
    merged: list[Interval] = []
    for interval in ordered:
        if merged and interval.start <= merged[-1].end:
            merged[-1] = Interval(merged[-1].start, max(merged[-1].end, interval.end))
        else:
            merged.append(interval)
    return merged


def overlapping_speech(segments: Sequence[tuple[str, float, float]]) -> list[Interval]:
    """Stretches where two speakers talk at once — the closest available proxy for
    "noisy speech" (TASKS.md T10.3) that does not require listening to the audio.

    Takes the regular (non-exclusive) diarization, where segments of different speakers may
    overlap; the exclusive variant has those conflicts already resolved away.
    """
    found: list[Interval] = []
    ordered = sorted(segments, key=lambda s: s[1])
    for index, (speaker, start, end) in enumerate(ordered):
        for other, other_start, other_end in ordered[index + 1 :]:
            if other_start >= end:
                break
            if other != speaker:
                found.append(Interval(max(start, other_start), min(end, other_end)))
    return merge_intervals(found)


def short_replies(segments: Sequence[tuple[float, float, str]]) -> list[Interval]:
    """Brief turns: few words and little time. Models tend to drop or invent these."""
    return [
        Interval(start, end)
        for start, end, text in segments
        if len(text.split()) <= SHORT_REPLY_MAX_WORDS and (end - start) <= SHORT_REPLY_MAX_SECONDS
    ]


def long_monologues(segments: Sequence[tuple[float, float, str]]) -> list[Interval]:
    return [
        Interval(start, end)
        for start, end, _ in segments
        if (end - start) >= LONG_MONOLOGUE_MIN_SECONDS
    ]


def within(
    disagreements: Iterable[Disagreement], regions: Sequence[Interval]
) -> list[Disagreement]:
    return [d for d in disagreements if any(r.contains(d.start, d.end) for r in regions)]


def has_digit(text: str) -> bool:
    return any(c.isdigit() for c in text)


def numeric_disagreements(disagreements: Iterable[Disagreement]) -> list[Disagreement]:
    """Cases where at least one side wrote a number — as digits or spelled out."""
    spelled = re.compile(
        r"\b(ноль|один|одна|одного|два|две|двух|три|трёх|трех|четыре|пять|шесть|семь|восемь|"
        r"девять|десять|одиннадцат|двенадцат|тринадцат|четырнадцат|пятнадцат|шестнадцат|"
        r"семнадцат|восемнадцат|девятнадцат|двадцат|тридцат|сорок|пятьдесят|шестьдесят|"
        r"семьдесят|восемьдесят|девяност|сто|двест|трист|четырест|пятьсот|тысяч|миллион)",
        re.IGNORECASE,
    )

    def touches_number(text: str) -> bool:
        return has_digit(text) or bool(spelled.search(text))

    return [d for d in disagreements if touches_number(d.a) or touches_number(d.b)]


def normalize_phrase(term: str) -> str:
    """Normalize a term word by word, so a multi-word phrase keeps its spaces.

    `normalize` alone would glue `мера пресечения` into `мерапресечения`, which matches
    nothing — every multi-word term silently scored zero.
    """
    return " ".join(normalize_all(term.split()))


def term_hits(words: Iterable[str], terms: Iterable[str]) -> dict[str, int]:
    """How often each domain term appears. Terms are matched on normalized word stems, so
    a term survives declension: `ходатайств` matches `ходатайство` and `ходатайства`."""
    joined = " ".join(normalize_all(words))
    return {term: joined.count(normalize_phrase(term)) for term in terms}


@dataclass
class FormattingProfile:
    """What the model emits beyond bare words: punctuation, case, digits.

    This is what separates `v3_e2e_rnnt` from `v3_rnnt` far more than accuracy does.
    """

    words: int
    capitalized: int
    digits: int
    periods: int
    commas: int
    questions: int
    hyphenated: int

    @property
    def capitalized_share(self) -> float:
        return self.capitalized / self.words if self.words else 0.0

    @property
    def is_formatted(self) -> bool:
        """True when the model produces readable text rather than a raw word stream."""
        return self.periods > 0 and self.capitalized > 0


def profile(words: Sequence[str]) -> FormattingProfile:
    joined = " ".join(words)
    return FormattingProfile(
        words=len(words),
        capitalized=sum(1 for w in words if w[:1].isupper()),
        digits=sum(1 for w in words if has_digit(w)),
        periods=joined.count("."),
        commas=joined.count(","),
        questions=joined.count("?"),
        hyphenated=sum(1 for w in words if "-" in w.strip("-")),
    )
