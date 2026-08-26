"""Indian licence-plate grammar, character disambiguation and validation.

This module is deliberately free of heavy dependencies so it can be unit
tested on its own.  It is the single largest source of accuracy gain in the
whole pipeline: OCR engines confuse a small, *known* set of glyph pairs, and
the position of a character inside an Indian plate tells us unambiguously
whether that character must be a letter or a digit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------

#: State / union-territory codes that may open a standard plate.
STATE_CODES: frozenset[str] = frozenset(
    """
    AN AP AR AS BR CG CH DD DL DN GA GJ HP HR JH JK KA KL LA LD MH ML MN MP
    MZ NL OD OR PB PY RJ SK TN TR TS UA UK UP WB
    """.split()
)

#: Codes used by non-civilian / special series plates.
SPECIAL_PREFIXES: frozenset[str] = frozenset({"BH", "CD", "UN", "DC"})

#: States this deployment expects to see.  A reading that lands on one of
#: these wins ties against an equally-cheap repair onto some other state,
#: and earns a small confidence bonus.  This is a *soft* prior only: plates
#: from every other state still validate normally.
PREFERRED_STATES: set[str] = {"GJ"}

#: Highest RTO district number currently issued per state.  Used as a soft
#: sanity signal - a plate outside the range is still accepted, just scored
#: lower, because rejecting a real plate is far worse than passing a fake one.
RTO_MAX: dict[str, int] = {"GJ": 39}
DEFAULT_RTO_MAX = 99

#: Tie-break discount applied to repairs that land on a preferred state.
#: Sized to let a preferred state overtake one rank in the confusion list,
#: never two.
PREFERRED_STATE_DISCOUNT = 0.4


def set_preferred_states(codes: Iterable[str]) -> None:
    """Re-point the regional prior at run time (driven by config.yaml)."""
    global PREFERRED_STATES
    PREFERRED_STATES = {c.strip().upper() for c in codes if c.strip()}

VALID_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")

# Glyphs that OCR engines routinely swap.  Read as: "when we know this slot
# must be a DIGIT, the key may be rewritten to any of these values".  The
# tuples are ordered most-likely first; that order becomes the repair cost,
# so 'O'->'0' is preferred over 'C'->'0' when both would satisfy the format.
LETTER_TO_DIGIT: dict[str, tuple[str, ...]] = {
    "O": ("0",), "Q": ("0",), "D": ("0",), "U": ("0",),
    "I": ("1",), "L": ("1",), "J": ("1",), "T": ("7", "1"),
    "Z": ("2",), "R": ("2",),
    "A": ("4",), "Y": ("4",),
    "S": ("5",),
    "G": ("6",), "C": ("0", "6"),
    "B": ("8",), "E": ("8",),
    "P": ("9",),
}

# ...and the inverse, for slots that must hold a LETTER.  '0' is genuinely
# ambiguous between O/D/Q on Indian plates, which is why this must be
# one-to-many: 'DL' misread as '0L' is only recoverable by trying 'D'.
DIGIT_TO_LETTER: dict[str, tuple[str, ...]] = {
    # 'G' is last but must be present: a blurred 'GJ' reads as '0J' often
    # enough that dropping it would cost real Gujarat plates.
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

#: Hard ceiling on how many repair combinations we will evaluate per format.
#: Keeps the search bounded when a reading is mostly garbage.
MAX_REPAIR_COMBOS = 512

#: Characters never present on an Indian plate but often hallucinated from
#: the plate border, mounting screws, or the IND / emblem strip.
NOISE_CHARS = str.maketrans("", "", " .,:;-_|/\\[](){}<>*#'\"`~^+=?!@$%&")


# --------------------------------------------------------------------------
# Plate format templates
# --------------------------------------------------------------------------
# Slot language:  'A' = letter, 'N' = digit.  Every template carries both a
# concrete regex and a per-index slot map used for confusion-aware repair.

@dataclass(frozen=True)
class PlateFormat:
    name: str
    regex: re.Pattern[str]
    #: one entry per character index: 'A' letter, 'N' digit
    slots: tuple[str, ...]
    #: extra confidence awarded when a candidate matches this format
    prior: float = 1.0
    #: True when characters 0:2 must be a real state code
    state_checked: bool = True


def _fmt(name: str, pattern: str, slots: str, prior: float = 1.0,
         state_checked: bool = True) -> PlateFormat:
    return PlateFormat(name, re.compile(pattern), tuple(slots), prior, state_checked)


#: Ordered by specificity - the first match wins when scores tie.
PLATE_FORMATS: tuple[PlateFormat, ...] = (
    # MH 12 DE 1433 - the modern standard, by far the most common
    _fmt("standard_2L", r"^[A-Z]{2}\d{2}[A-Z]{2}\d{4}$", "AANNAANNNN", 1.00),
    # MH 01 A 1234
    _fmt("standard_1L", r"^[A-Z]{2}\d{2}[A-Z]\d{4}$", "AANNANNNN", 0.96),
    # KA 05 MAB 1234
    _fmt("standard_3L", r"^[A-Z]{2}\d{2}[A-Z]{3}\d{4}$", "AANNAAANNNN", 0.94),
    # 21 BH 1234 AA - Bharat series
    _fmt("bharat", r"^\d{2}BH\d{4}[A-Z]{1,2}$", "NNAANNNNAA", 0.98, False),
    # DL 8C AF 5030 - Delhi does not zero-pad its district code, so a single
    # digit is followed by a vehicle-class letter and a 1-2 letter series.
    _fmt("delhi_3L", r"^DL\d[A-Z]{3}\d{4}$", "AANAAANNNN", 0.93),
    _fmt("delhi_2L", r"^DL\d[A-Z]{2}\d{4}$", "AANAANNNN", 0.90),
    # UP 16 1234 - older / some commercial plates
    _fmt("standard_0L", r"^[A-Z]{2}\d{6}$", "AANNNNNN", 0.80),
    # 11 CD 1234 - diplomatic corps
    _fmt("diplomatic", r"^\d{2}(CD|UN|DC)\d{4}$", "NNAANNNN", 0.85, False),
    # MH 12 A 123 - 3-digit tail, older registrations
    _fmt("short_tail", r"^[A-Z]{2}\d{2}[A-Z]{2}\d{3}$", "AANNAANNN", 0.72),
)

#: Plates seen in the wild range 6..11 characters.
MIN_PLATE_LEN, MAX_PLATE_LEN = 6, 11


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------

@dataclass
class PlateCandidate:
    """A single normalised reading of a plate."""
    text: str                      #: repaired / normalised plate string
    raw: str                       #: what the OCR engine actually returned
    confidence: float              #: 0..1 OCR confidence, engine reported
    score: float = 0.0             #: combined grammar-aware score, 0..1
    fmt: str | None = None         #: matched PlateFormat.name, None if free-form
    valid: bool = False            #: True when it matches a known format
    state: str | None = None       #: decoded state code, when applicable
    repairs: list[str] = field(default_factory=list)   #: human-readable fixes
    engine: str = ""               #: which OCR engine produced it
    variant: str = ""              #: which enhancement variant it came from

    def as_dict(self) -> dict:
        return {
            "text": self.text, "raw": self.raw,
            "confidence": round(self.confidence, 4), "score": round(self.score, 4),
            "format": self.fmt, "valid": self.valid, "state": self.state,
            "repairs": self.repairs, "engine": self.engine, "variant": self.variant,
        }


# --------------------------------------------------------------------------
# Cleaning and repair
# --------------------------------------------------------------------------

def clean(raw: str) -> str:
    """Upper-case, strip punctuation/whitespace and drop non-plate glyphs."""
    if not raw:
        return ""
    s = raw.upper().translate(NOISE_CHARS)
    # Drop the 'IND' country strip that sits on the left of HSRP plates.
    s = re.sub(r"^IND", "", s)
    return "".join(c for c in s if c in VALID_CHARS)


def _options(ch: str, want: str) -> tuple[tuple[str, float], ...]:
    """Candidate glyphs for *ch* in a slot of class *want*, with repair cost.

    Cost 0 means the character already belongs to the required class.  Every
    substitution costs more the further down the confusion list it sits.
    """
    if want == "N":
        if ch.isdigit():
            return ((ch, 0.0),)
        alts = LETTER_TO_DIGIT.get(ch, ())
    elif want == "A":
        if ch.isalpha():
            return ((ch, 0.0),)
        alts = DIGIT_TO_LETTER.get(ch, ())
    else:
        return ((ch, 0.0),)
    # No known confusion for this glyph: keep it and let the regex reject it.
    return tuple((a, 1.0 + 0.35 * i) for i, a in enumerate(alts)) or ((ch, 9.0),)


def _fit(text: str, fmt: PlateFormat) -> tuple[str, list[str], float] | None:
    """Try to bend *text* into *fmt*.

    Explores the confusion alternatives for every mismatched slot and returns
    the cheapest repair that satisfies both the format regex and the state
    whitelist, as ``(repaired_text, repair_log, penalty)``.  Returns ``None``
    when no repair works or the length can never fit.
    """
    slots = fmt.slots
    # A couple of formats have an optional trailing letter, so allow the text
    # to be one slot shorter than the template.
    if len(text) == len(slots):
        pass
    elif len(text) == len(slots) - 1:
        slots = slots[:-1]
    else:
        return None

    per_slot = [_options(ch, want) for ch, want in zip(text, slots)]

    # Bound the search: trim the least likely alternatives from the most
    # ambiguous slots until the product of the branching factors is sane.
    while True:
        combos = 1
        for opts in per_slot:
            combos *= len(opts)
        if combos <= MAX_REPAIR_COMBOS:
            break
        widest = max(range(len(per_slot)), key=lambda i: len(per_slot[i]))
        if len(per_slot[widest]) <= 1:
            break
        per_slot[widest] = per_slot[widest][:-1]

    best: tuple[float, str] | None = None
    for combo in _product(per_slot):
        cost = sum(c for _, c in combo)
        # Cheapest possible adjusted cost for this combo; skip early when it
        # cannot beat the incumbent even with the preferred-state discount.
        if best is not None and cost - PREFERRED_STATE_DISCOUNT >= best[0]:
            continue
        repaired = "".join(ch for ch, _ in combo)
        if not fmt.regex.match(repaired):
            continue
        # Reject impossible state codes rather than silently inventing one.
        if fmt.state_checked and repaired[:2] not in STATE_CODES:
            continue
        if fmt.state_checked and repaired[:2] in PREFERRED_STATES:
            cost -= PREFERRED_STATE_DISCOUNT
        if best is None or cost < best[0]:
            best = (cost, repaired)

    if best is None:
        return None

    cost, repaired = best
    cost = max(0.0, cost)
    repairs = [f"{i}:{a}->{b}" for i, (a, b) in enumerate(zip(text, repaired)) if a != b]
    return repaired, repairs, cost * 0.06


def _product(per_slot: list[tuple[tuple[str, float], ...]]):
    """itertools.product, but yielding cheapest-first within each slot."""
    from itertools import product
    return product(*per_slot)


def normalise(raw: str, confidence: float = 1.0, *, engine: str = "",
              variant: str = "") -> PlateCandidate:
    """Clean, repair and validate a raw OCR string into a PlateCandidate."""
    text = clean(raw)
    cand = PlateCandidate(text=text, raw=raw, confidence=confidence,
                          engine=engine, variant=variant)
    if not (MIN_PLATE_LEN <= len(text) <= MAX_PLATE_LEN):
        cand.score = confidence * 0.25          # keep it, but rank it low
        return cand

    # Selection is lexicographic: fewest repairs first, then the more likely
    # format.  Ordering by a blended score instead would let a high-prior
    # format outrank a *cleaner* fit and rewrite a plate that was already
    # correct - e.g. 'DL8CAF5030' being forced into the 2-digit standard.
    best: tuple[tuple[float, float], PlateFormat, str, list[str], float] | None = None
    for fmt in PLATE_FORMATS:
        fit = _fit(text, fmt)
        if fit is None:
            continue
        repaired, repairs, penalty = fit
        key = (penalty, -fmt.prior)
        if best is None or key < best[0]:
            best = (key, fmt, repaired, repairs, penalty)

    if best is None:
        # Nothing matched.  Still usable (partially occluded plate, foreign
        # vehicle) but heavily discounted so a valid reading always wins.
        cand.score = confidence * 0.35
        return cand

    _key, fmt, repaired, repairs, penalty = best
    quality = max(0.0, fmt.prior - penalty)
    cand.text = repaired
    cand.fmt = fmt.name
    cand.valid = True
    cand.repairs = repairs
    cand.state = repaired[:2] if fmt.state_checked else None

    # Regional prior: a plate from a state this site actually sees is more
    # likely to be a correct reading than one from across the country.
    bonus = 0.0
    if cand.state:
        if cand.state in PREFERRED_STATES:
            bonus += 0.05
        rto = _rto_number(repaired, fmt)
        if rto is not None and not (1 <= rto <= RTO_MAX.get(cand.state, DEFAULT_RTO_MAX)):
            bonus -= 0.08          # out-of-range district: suspicious, not fatal

    # Grammar validity is worth a lot, but never enough on its own to promote
    # a reading the OCR engine itself had no faith in.
    cand.score = min(1.0, max(0.0,
        confidence * (0.55 + 0.45 * quality) + 0.12 * quality + bonus))
    return cand


def _rto_number(text: str, fmt: PlateFormat) -> int | None:
    """District number that follows the state code, when the format has one."""
    m = re.match(r"^[A-Z]{2}(\d{1,2})", text)
    if not m or not fmt.state_checked:
        return None
    return int(m.group(1))


def best_of(candidates: Iterable[PlateCandidate]) -> PlateCandidate | None:
    """Pick the highest scoring candidate, preferring grammar-valid ones."""
    cands = list(candidates)
    if not cands:
        return None
    return max(cands, key=lambda c: (c.valid, c.score, c.confidence))


def plausible(text: str) -> bool:
    """Cheap gate used before we spend effort on a string."""
    t = clean(text)
    if not (MIN_PLATE_LEN <= len(t) <= MAX_PLATE_LEN):
        return False
    # A plate always mixes letters and digits; a run of one class is noise.
    return any(c.isdigit() for c in t) and any(c.isalpha() for c in t)


def format_pretty(text: str, fmt: str | None) -> str:
    """Insert conventional spacing: 'MH12DE1433' -> 'MH 12 DE 1433'."""
    if not fmt or not text:
        return text
    if fmt.startswith("standard"):
        m = re.match(r"^([A-Z]{2})(\d{2})([A-Z]{0,3})(\d{3,4})$", text)
        if m:
            return " ".join(p for p in m.groups() if p)
    if fmt == "bharat":
        m = re.match(r"^(\d{2})(BH)(\d{4})([A-Z]{1,2})$", text)
        if m:
            return " ".join(m.groups())
    return text


def char_classes(fmt_name: str | None, length: int) -> Sequence[str]:
    """Slot classes for a format, used by the positional voter."""
    for f in PLATE_FORMATS:
        if f.name == fmt_name:
            return f.slots[:length]
    return tuple("?" * length)
