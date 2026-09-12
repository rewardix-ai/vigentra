"""Indian number-plate grammar: formats, positional character types,
position-aware confusion maps, state/RTO validity priors, colour classes.

Everything downstream (detector filtering, beam decode, ROVER fusion,
confidence prior) imports from here so the rules live in exactly one place.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import yaml

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
LETTERS = set(ALPHABET[:26])
DIGITS = set(ALPHABET[26:])
BLANK = "-"  # CTC blank token used by the readers

_CODES_FILE = Path(__file__).resolve().parent.parent / "config" / "india_codes.yaml"


@lru_cache(maxsize=1)
def _codes() -> dict:
    with open(_CODES_FILE, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def state_codes() -> set[str]:
    return set(_codes()["state_codes"].keys())


def gujarat_rto_codes() -> set[str]:
    return set(_codes()["gujarat_rto"].keys())


def district_range(state: str) -> tuple[int, int] | None:
    r = _codes().get("district_ranges", {}).get(state)
    return (int(r[0]), int(r[1])) if r else None


# ---------------------------------------------------------------------------
# Format grammar.  Template string: A = letter, D = digit, other = literal.
# Every template is fixed-length so a position index has exactly one class.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PlateFormat:
    name: str
    template: str
    prior: float

    @property
    def length(self) -> int:
        return len(self.template)

    def slot_type(self, i: int) -> str:
        return self.template[i]

    def regex(self) -> re.Pattern:
        parts = []
        for ch in self.template:
            if ch == "A":
                parts.append("[A-Z]")
            elif ch == "D":
                parts.append("[0-9]")
            else:
                parts.append(re.escape(ch))
        return re.compile("^" + "".join(parts) + "$")


FORMATS: list[PlateFormat] = []
# Standard BS-1989: SS DD [L{0,3}] N{1,4}. Modern issuance is dominated by
# 2 series letters + 4 digits; priors weighted accordingly.
for _s in (0, 1, 2, 3):
    for _n in (4, 3, 2, 1):
        if _s == 2 and _n == 4:
            _p = 1.0
        elif _s in (1, 3) and _n == 4:
            _p = 0.5
        elif _s == 0:
            _p = 0.15 if _n == 4 else 0.03
        else:
            _p = 0.08
        FORMATS.append(PlateFormat(f"std_s{_s}_n{_n}", "AADD" + "A" * _s + "D" * _n, _p))
# Delhi-style single-digit district: DL 8C AF 5030 / DL 1C O 5334. Delhi issues
# these in volume and they appear in every Delhi feed; measured on the Delhi
# test clip, a prior of 0.12 let the 10-char standard template rewrite
# DL3CCN5712 as DL30CN5712, so they are weighted close to the standard form.
FORMATS += [PlateFormat("dl_short", "AADAAADDDD", 0.6), PlateFormat("dl_short_s1", "AADAADDDD", 0.35)]
# Bharat series: YY BH NNNN LL
FORMATS.append(PlateFormat("bharat", "DDBHDDDDAA", 0.15))
# Diplomatic: DD CD/CC/UN NNN(N)
for _tag in ("CD", "CC", "UN"):
    FORMATS.append(PlateFormat(f"dipl_{_tag}", "DD" + _tag + "DDD", 0.01))
    FORMATS.append(PlateFormat(f"dipl_{_tag}_4", "DD" + _tag + "DDDD", 0.01))
# Vintage: SS VA NN NNNN
FORMATS.append(PlateFormat("vintage", "AAVADDDDDD", 0.005))
# Temporary / dealer: SS DD TC NNNN
FORMATS.append(PlateFormat("temp", "AADDTCDDDD", 0.01))

_MAX_PRIOR = max(f.prior for f in FORMATS)
FORMATS_BY_LEN: dict[int, list[PlateFormat]] = {}
for _f in FORMATS:
    FORMATS_BY_LEN.setdefault(_f.length, []).append(_f)

# ---------------------------------------------------------------------------
# Position-aware confusion maps (spec 5.4)
# ---------------------------------------------------------------------------
TO_LETTER = {"0": "O", "1": "I", "2": "Z", "5": "S", "6": "G", "8": "B", "4": "A", "7": "T"}
TO_DIGIT = {"O": "0", "D": "0", "Q": "0", "U": "0", "I": "1", "L": "1", "T": "1",
            "Z": "2", "S": "5", "G": "6", "B": "8", "A": "4", "E": "8"}
# Confusion pairs used to spread probability mass in beam decode.
CONFUSABLE: dict[str, tuple[str, ...]] = {
    "0": ("O", "D", "Q", "U"), "O": ("0", "D", "Q"), "D": ("0", "O"), "Q": ("0", "O"),
    "1": ("I", "L", "T", "7"), "I": ("1", "L", "T"), "L": ("1", "I"), "T": ("1", "7"), "7": ("1", "T"),
    "2": ("Z",), "Z": ("2", "7"),
    "5": ("S",), "S": ("5", "8"),
    "6": ("G", "B"), "G": ("6", "C"),
    "8": ("B", "3", "S"), "B": ("8", "3", "R"),
    "4": ("A",), "A": ("4",),
    "3": ("8", "B"), "E": ("8", "F"), "F": ("E", "P"), "P": ("F", "R"), "R": ("P", "B"),
    "M": ("N", "H"), "N": ("M", "H"), "H": ("M", "N", "K"), "K": ("H", "X"), "V": ("Y", "W"),
    "Y": ("V",), "W": ("V", "M"), "C": ("G", "0"), "J": ("1", "U"), "U": ("V", "0"),
}
RARE_SERIES_LETTERS = {"I", "O"}


def coerce(ch: str, slot: str) -> str:
    """Map a character into the slot class using the confusion map."""
    if slot == "A" and ch in DIGITS:
        return TO_LETTER.get(ch, ch)
    if slot == "D" and ch in LETTERS:
        return TO_DIGIT.get(ch, ch)
    return ch


def fits_slot(ch: str, slot: str) -> bool:
    if slot == "A":
        return ch in LETTERS
    if slot == "D":
        return ch in DIGITS
    return ch == slot


@dataclass
class GrammarScore:
    valid: bool
    fmt: PlateFormat | None
    prior: float
    reasons: list[str]


def normalise(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def score_string(s: str, preferred_state: str = "GJ") -> GrammarScore:
    """Grammar prior in [0,1] for a candidate string. 0 if no format matches."""
    s = normalise(s)
    best: GrammarScore | None = None
    for fmt in FORMATS_BY_LEN.get(len(s), []):
        if not fmt.regex().match(s):
            continue
        # the single-digit-district layouts are Delhi's own; for any other state they only
        # turned junk into plate-shaped strings (28 of 80 Delhi-clip reads became GJ4UPP7111-like)
        if fmt.name.startswith("dl_short") and s[:2] != "DL":
            continue
        prior = fmt.prior / _MAX_PRIOR
        rs: list[str] = []
        if fmt.template.startswith("AAD"):
            st = s[:2]
            if st not in state_codes():
                prior *= 0.05
                rs.append(f"unknown_state:{st}")
            else:
                if preferred_state and st != preferred_state:   # "" / None: no state preference
                    prior *= 0.7
                if fmt.template.startswith("AADD"):
                    dist = s[2:4]
                    rng = district_range(st)
                    if st == "GJ" and dist not in gujarat_rto_codes():
                        prior *= 0.1
                        rs.append(f"bad_gj_rto:{dist}")
                    elif rng and not (rng[0] <= int(dist) <= rng[1]):
                        prior *= 0.3
                        rs.append(f"district_out_of_range:{st}{dist}")
                    if dist == "00":
                        prior *= 0.05
                        rs.append("district_00")
            series = "".join(ch for ch, t in zip(s[4:], fmt.template[4:]) if t == "A")
            if any(ch in RARE_SERIES_LETTERS for ch in series):
                prior *= 0.35
                rs.append(f"rare_series_letter:{series}")
        elif fmt.name == "bharat":
            yy = int(s[:2])
            if not (19 <= yy <= 35):
                prior *= 0.2
                rs.append("bharat_year_implausible")
        cand = GrammarScore(True, fmt, min(prior, 1.0), rs)
        if best is None or cand.prior > best.prior:
            best = cand
    if best is None:
        return GrammarScore(False, None, 0.0, ["no_format_match"])
    return best


def slot_types(length: int) -> list[str] | None:
    fmts = FORMATS_BY_LEN.get(length)
    if not fmts:
        return None
    return list(max(fmts, key=lambda f: f.prior).template)


# ---------------------------------------------------------------------------
# Colour / class rules (spec 5.2) and geometry (spec 5.3)
# ---------------------------------------------------------------------------
PLATE_CLASSES = {
    ("white", "black"): "private_white",
    ("yellow", "black"): "commercial_yellow",
    ("green", "white"): "ev_commercial",
    ("green", "yellow"): "ev_private",
    ("black", "yellow"): "rental_black",
    ("red", "white"): "temporary_red",
    ("blue", "white"): "diplomatic_blue",
}


def plate_class(ground: str, text: str) -> str:
    return PLATE_CLASSES.get((ground, text), "unknown")


ASPECT_SINGLE_ROW = (2.6, 5.5)
ASPECT_TWO_ROW = (1.2, 2.6)
ASPECT_ANY = (1.0, 6.0)
PLATE_H_FRAC_OF_VEHICLE = (0.04, 0.25)

# Strings that must never be emitted as a plate (overlay regression, spec 6.2.1)
FORBIDDEN_SUBSTRINGS = ("CSITMS", "PTZ", "LIVE", "REC", "CAM0", "CAM1", "CAM2", "CAM3", "BRIDGE", "CHIMAN", "CCTV", "IPC")
# Burned-in timestamps / camera names as they survive normalisation:
#   2026-06-14 06:33 -> 20260614 0633, 1:55 AM -> 155AM, 14/06/2026 -> 14062026
_OVERLAY_RE = re.compile(
    r"(^20[2-3]\d)|(20[2-3]\d$)|(AM$)|(PM$)|(^\d{6,})|(\d{8,})|(^[0-9]{1,2}[0-9]{2}(AM|PM))|(HRS$)")


def looks_like_overlay(s: str) -> bool:
    """True for anything that reads like a timestamp, date, year, clock or camera caption.
    Plates never start with a 4-digit year, never end in AM/PM, and never carry 6+ digit runs."""
    s = normalise(s)
    if any(tok in s for tok in FORBIDDEN_SUBSTRINGS):
        return True
    if _OVERLAY_RE.search(s):
        return True
    digits = sum(ch.isdigit() for ch in s)
    return digits >= 6 and s[:2].isdigit() and not (s[2:4] == "BH")  # 22BH1234AA is the only digit-first plate
