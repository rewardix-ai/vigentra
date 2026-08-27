"""Confusion-weighted matching between a read plate and a watchlist entry.

Exact-match lookup over OCR output is a trap. If the only camera that saw a
vehicle read one character wrong, an exact query returns nothing and the
vehicle looks like it was never there — which, for a stolen-vehicle watchlist,
is the failure that matters.

So matching is fuzzy. But plain edit distance is too blunt: it calls
``GJ01AB1234`` equally far from ``GJ01AB1284`` and ``GJ01AB1734``, when the
first is the classic 3/8 confusion and the second is not. Substitutions are
therefore priced by whether the two glyphs are a pair the OCR layer is known to
swap, which pushes genuine misreads to the top of a ranking and pushes
coincidental look-alikes down.

This module is deliberately free of any CV or ML dependency. The central API
receives characters, never pixels, and this container stays small enough to
deploy anywhere. The confusion tables are the same ones the edge reader uses to
repair a plate in the first place — kept in sync by
``tests/test_plate_matching.py``, which asserts them against the edge package.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Every Indian state and union-territory code. A two-letter string that is not
#: one of these is not a registration number, however plate-shaped it looks.
STATE_CODES: frozenset[str] = frozenset(
    """
    AN AP AR AS BR CG CH DD DL DN GA GJ HP HR JH JK KA KL LA LD MH ML MN MP
    MZ NL OD OR PB PY RJ SK TN TR TS UA UK UP WB
    """.split()
)

#: Non-civilian and special series prefixes: Bharat, diplomatic, UN.
SPECIAL_PREFIXES: frozenset[str] = frozenset({"BH", "CD", "UN", "DC"})

_VALID_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")

#: Glyphs OCR reads as a digit when the slot wants a letter.
_LETTER_TO_DIGIT: dict[str, tuple[str, ...]] = {
    "O": ("0",), "Q": ("0",), "D": ("0",), "U": ("0",),
    "I": ("1",), "L": ("1",), "J": ("1",), "T": ("7", "1"),
    "Z": ("2",), "R": ("2",),
    "A": ("4",), "Y": ("4",),
    "S": ("5",),
    "G": ("6",), "C": ("0", "6"),
    "B": ("8",), "E": ("8",),
    "P": ("9",),
}

#: ...and the inverse. One-to-many on purpose: '0' is genuinely ambiguous
#: between O/D/Q on Indian plates, and 'DL' misread as '0L' is only
#: recoverable by trying 'D'.
_DIGIT_TO_LETTER: dict[str, tuple[str, ...]] = {
    "0": ("O", "D", "Q", "U", "C", "G"),
    "1": ("I", "L", "T", "J"),
    "2": ("Z",),
    "3": ("J", "B"),
    "4": ("A", "Y"),
    "5": ("S",),
    "6": ("G", "C"),
    "7": ("T", "Y"),
    "8": ("B", "E", "S"),
    "9": ("P", "G", "Q"),
}


def _confusion_pairs() -> frozenset[frozenset[str]]:
    """Every glyph pair the OCR layer is known to swap, in both directions."""
    pairs: set[frozenset[str]] = set()
    for letter, digits in _LETTER_TO_DIGIT.items():
        for digit in digits:
            pairs.add(frozenset((letter, digit)))
    for digit, letters in _DIGIT_TO_LETTER.items():
        for letter in letters:
            pairs.add(frozenset((digit, letter)))
    return frozenset(pairs)


CONFUSABLE = _confusion_pairs()

#: Cost of swapping two glyphs OCR routinely confuses.
SUB_CONFUSABLE = 0.35
#: Cost of swapping two unrelated glyphs.
SUB_UNRELATED = 1.0
#: Cost of a missing or extra character.
#:
#: Cheaper than an unrelated substitution, deliberately. Dropping a character
#: is one of the commonest OCR failures — a plate clipped at the frame edge, or
#: a character lost to glare — and it shows up repeatedly in real footage
#: (``GJ21RS344`` for ``GJ21RS3344``). Substituting a *different* digit in
#: place, by contrast, usually means a different vehicle. Pricing both at 1.0
#: makes them indistinguishable by threshold; pricing the indel lower separates
#: "we lost a character" from "this is another car".
INDEL = 0.5

#: Default acceptance threshold for a watchlist hit.
#:
#: One classic misread costs 0.35, so 1.0 tolerates two plausible OCR errors
#: while still rejecting a genuinely different plate. Raising this does not
#: find more stolen cars; it finds more innocent ones.
DEFAULT_MAX_DISTANCE = 1.0

_NOISE = re.compile(r"[^A-Z0-9]")


def clean(raw: str | None) -> str:
    """Upper-case, strip punctuation and drop non-plate glyphs.

    Also removes the ``IND`` country strip that sits on the left of an HSRP
    plate and is frequently picked up as part of the read.
    """
    if not raw:
        return ""
    text = _NOISE.sub("", raw.upper())
    text = re.sub(r"^IND", "", text)
    return "".join(character for character in text if character in _VALID_CHARS)


def sub_cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    return SUB_CONFUSABLE if frozenset((a, b)) in CONFUSABLE else SUB_UNRELATED


def plate_distance(a: str, b: str) -> float:
    """Confusion-weighted edit distance between two plate strings.

    0.0 is identical. A single classic misread scores 0.35.
    """
    a, b = clean(a), clean(b)
    if a == b:
        return 0.0
    if not a or not b:
        return float(max(len(a), len(b)))

    previous = [column * INDEL for column in range(len(b) + 1)]
    for row, char_a in enumerate(a, 1):
        current = [row * INDEL]
        for column, char_b in enumerate(b, 1):
            current.append(
                min(
                    previous[column] + INDEL,                      # delete from a
                    current[column - 1] + INDEL,                   # insert into a
                    previous[column - 1] + sub_cost(char_a, char_b),  # substitute
                )
            )
        previous = current
    return previous[-1]


def similarity(a: str, b: str) -> float:
    """Distance rescaled to 0..1, where 1.0 is an exact match."""
    a, b = clean(a), clean(b)
    longest = max(len(a), len(b), 1)
    return max(0.0, 1.0 - plate_distance(a, b) / longest)


def state_of(plate: str | None) -> str | None:
    """The two-letter state code, when the plate carries a real one."""
    text = clean(plate)
    if len(text) < 2:
        return None
    prefix = text[:2]
    if prefix in STATE_CODES or prefix in SPECIAL_PREFIXES:
        return prefix
    # Bharat, diplomatic and UN series put the series code in slots 3-4, after
    # a numeric prefix: `21 BH 1234 AA`, `11 CD 1234`, `52 UN 0001`. Checking
    # only the first two characters would classify all of them as unplausible
    # and quietly refuse a real registration.
    if len(text) >= 4 and text[:2].isdigit() and text[2:4] in SPECIAL_PREFIXES:
        return text[2:4]
    return None


def is_plausible(plate: str | None) -> bool:
    """Whether a string is shaped like an Indian registration at all.

    Used to refuse a watchlist entry that would never match anything, rather
    than storing it and leaving an operator to wonder why it never fires.
    """
    text = clean(plate)
    if not 6 <= len(text) <= 11:
        return False
    return state_of(text) is not None


@dataclass(frozen=True)
class PlateMatch:
    """One watchlist entry a read plate is close enough to.

    ``exact`` matters to an operator in a way ``distance`` does not: an exact
    hit is something to act on now, a near hit is something to look at.
    """

    watch_plate: str
    seen_plate: str
    distance: float
    exact: bool

    @property
    def similarity(self) -> float:
        return similarity(self.watch_plate, self.seen_plate)


def best_matches(
    seen: str,
    candidates: list[str],
    *,
    max_distance: float = DEFAULT_MAX_DISTANCE,
) -> list[PlateMatch]:
    """Every candidate within *max_distance* of *seen*, closest first.

    Returns all of them rather than only the best: two watchlist entries a
    single character apart is exactly the case where an operator should see
    both and decide, not have one silently chosen for them.
    """
    seen_clean = clean(seen)
    if not seen_clean:
        return []

    matches = []
    for candidate in candidates:
        candidate_clean = clean(candidate)
        if not candidate_clean:
            continue
        distance = plate_distance(seen_clean, candidate_clean)
        if distance <= max_distance:
            matches.append(
                PlateMatch(
                    watch_plate=candidate_clean,
                    seen_plate=seen_clean,
                    distance=distance,
                    exact=distance == 0.0,
                )
            )
    matches.sort(key=lambda match: (match.distance, match.watch_plate))
    return matches
