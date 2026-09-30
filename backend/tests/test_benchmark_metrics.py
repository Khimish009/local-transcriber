"""Phase 10 — the comparison logic behind the ASR benchmark."""

from benchmark.metrics import (
    Interval,
    Word,
    compare,
    long_monologues,
    merge_intervals,
    normalize,
    numeric_disagreements,
    overlapping_speech,
    profile,
    short_replies,
    term_hits,
    within,
)


def words(*items: tuple[str, float, float]) -> list[Word]:
    return [Word(text, start, end) for text, start, end in items]


def test_normalize_folds_case_punctuation_and_yo() -> None:
    assert normalize("Шерёметьев,") == "шереметьев"
    assert normalize("«500»") == "500"
    assert normalize("какие-либо") == "какие-либо"
    assert normalize("...") == ""


def test_identical_streams_agree_completely() -> None:
    a = words(("Да", 0.0, 0.3), ("конечно", 0.3, 0.9))
    b = words(("да", 0.0, 0.3), ("конечно.", 0.3, 0.9))

    result = compare(a, b)

    assert result.ratio == 1.0
    assert result.disagreements == []


def test_replacement_is_reported_with_its_timestamp() -> None:
    a = words(("статья", 1.0, 1.5), ("159", 1.5, 2.0))
    b = words(("статья", 1.0, 1.5), ("сто", 1.5, 1.8), ("пятьдесят", 1.8, 2.0))

    [disagreement] = compare(a, b).disagreements

    assert disagreement.kind == "replace"
    assert disagreement.a == "159"
    assert disagreement.b == "сто пятьдесят"
    assert (disagreement.start, disagreement.end) == (1.5, 2.0)


def test_word_only_in_one_model_is_labelled() -> None:
    a = words(("суд", 0.0, 0.4), ("указал", 0.4, 1.0))
    b = words(("суд", 0.0, 0.4))

    [disagreement] = compare(a, b).disagreements

    assert disagreement.kind == "only_a"
    assert disagreement.a == "указал"
    assert disagreement.b == ""


def test_agreement_ratio_counts_matched_words_of_the_reference() -> None:
    a = words(("один", 0.0, 0.5), ("два", 0.5, 1.0), ("три", 1.0, 1.5), ("четыре", 1.5, 2.0))
    b = words(("один", 0.0, 0.5), ("два", 0.5, 1.0), ("пять", 1.0, 1.5), ("четыре", 1.5, 2.0))

    assert compare(a, b).ratio == 0.75


def test_numeric_disagreements_catch_digits_and_spelled_numbers() -> None:
    a = words(("500", 0.0, 0.5), ("рублей", 0.5, 1.0), ("суд", 1.0, 1.4))
    b = words(("пятьсот", 0.0, 0.5), ("рублей", 0.5, 1.0), ("сад", 1.0, 1.4))

    disagreements = compare(a, b).disagreements
    numeric = numeric_disagreements(disagreements)

    # Both words differ, but only one of the two differences is about a number.
    assert len(disagreements) == 2
    assert [d.a for d in numeric] == ["500"]


def test_merge_intervals_joins_touching_spans() -> None:
    merged = merge_intervals([Interval(0, 2), Interval(1.5, 3), Interval(10, 11)])

    assert [(i.start, i.end) for i in merged] == [(0, 3), (10, 11)]


def test_overlapping_speech_finds_only_cross_speaker_overlap() -> None:
    segments = [
        ("SPEAKER_00", 0.0, 5.0),
        ("SPEAKER_00", 5.0, 7.0),  # same speaker: continuation, not overlap
        ("SPEAKER_01", 4.0, 6.0),
    ]

    [overlap] = overlapping_speech(segments)

    assert (overlap.start, overlap.end) == (4.0, 6.0)


def test_short_replies_need_to_be_both_brief_and_few_words() -> None:
    segments = [
        (0.0, 1.5, "Да, конечно."),
        (2.0, 40.0, "Да."),  # two words but a 38-second block: not a quick exchange
        (41.0, 44.0, "Ваша честь, сторона защиты возражает против продления меры пресечения."),
    ]

    assert [(i.start, i.end) for i in short_replies(segments)] == [(0.0, 1.5)]


def test_long_monologues_use_the_duration_threshold() -> None:
    segments = [(0.0, 29.0, "почти"), (30.0, 75.0, "монолог")]

    assert [(i.start, i.end) for i in long_monologues(segments)] == [(30.0, 75.0)]


def test_within_matches_a_disagreement_straddling_a_boundary() -> None:
    a = words(("суд", 9.5, 10.5))
    b = words(("сад", 9.5, 10.5))
    disagreements = compare(a, b).disagreements

    assert within(disagreements, [Interval(10.0, 20.0)]) == disagreements
    assert within(disagreements, [Interval(20.0, 30.0)]) == []


def test_term_hits_survive_declension() -> None:
    counts = term_hits(["Ходатайство", "ходатайства", "суд"], ["ходатайств", "залог"])

    assert counts == {"ходатайств": 2, "залог": 0}


def test_term_hits_match_multi_word_phrases() -> None:
    """Normalizing a phrase as one string would glue its words together and match nothing."""
    words = ["избрать", "меру", "пресечения", "в", "виде", "залога"]

    assert term_hits(words, ["меру пресечения", "мера пресечения"]) == {
        "меру пресечения": 1,
        "мера пресечения": 0,
    }


def test_profile_separates_formatted_output_from_a_raw_stream() -> None:
    formatted = profile(["Суд", "указал,", "что", "статья", "159", "УК."])
    raw = profile(["суд", "указал", "что", "статья", "сто", "пятьдесят", "девятой"])

    assert formatted.is_formatted
    assert formatted.digits == 1
    assert formatted.commas == 1
    assert not raw.is_formatted
    assert raw.capitalized == 0


# --- threshold calibration helpers (Phase 10) ---------------------------------


def test_percentile_uses_nearest_rank() -> None:
    from benchmark.thresholds import percentile

    values = [10, 20, 30, 40, 50]

    assert percentile(values, 0.0) == 10
    assert percentile(values, 0.5) == 30
    assert percentile(values, 1.0) == 50
    assert percentile([], 0.5) == 0.0


def test_span_distance_is_zero_inside_and_grows_outside() -> None:
    from benchmark.thresholds import Span

    span = Span(10.0, 20.0)

    assert span.distance_to(15.0) == 0.0
    assert span.distance_to(9.5) == 0.5
    assert span.distance_to(20.5) == 0.5
    assert span.distance_to(20.0) == 0.0, "half-open: the end belongs to the next span"


def test_nearest_distance_checks_both_neighbours() -> None:
    from benchmark.thresholds import Span, nearest_distance

    spans = [Span(0.0, 5.0), Span(10.0, 15.0)]

    assert nearest_distance(spans, 3.0) == 0.0
    assert nearest_distance(spans, 6.0) == 1.0  # closer to the left span
    assert nearest_distance(spans, 9.0) == 1.0  # closer to the right one
    assert nearest_distance([], 1.0) == float("inf")


def test_word_gaps_ignore_speaker_changes() -> None:
    from benchmark.thresholds import word_gaps

    words = [
        {"start": 0.0, "end": 1.0, "speaker_id": "SPEAKER_00"},
        {"start": 1.5, "end": 2.0, "speaker_id": "SPEAKER_00"},
        {"start": 5.0, "end": 6.0, "speaker_id": "SPEAKER_01"},  # change: not a pause
    ]

    assert word_gaps(words) == [0.5]
