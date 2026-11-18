"""The user's mark colours: a named palette row per colour (Plan H5b.1).

Each row keeps the user's light-mode ``hex``; the dark-mode colour is
derived (the four defaults keep their hand-validated dark values). A colour
is refused when it would read as the citation yellow, as grey, or would not
show on the page; the reason is a short sentence the UI can show. Mirrors
the frontend's ``lib/marks/palette.ts``.
"""
from __future__ import annotations

import math
import re

DEFAULTS: tuple[tuple[str, str, str, str], ...] = (
    # key, name, light hex, dark hex (validated with the dataviz checker)
    ("green", "Key result", "#2f9e63", "#36a067"),
    ("blue", "Method", "#3b7dd8", "#4b86dc"),
    ("pink", "Question", "#d6457e", "#d9578a"),
    ("purple", "Disagree", "#8b5cd6", "#9670dc"),
)
BUILTIN = {k for k, *_ in DEFAULTS}
DEFAULT_DARK = {k: d for k, _, _, d in DEFAULTS}
NAME_MAX = 40
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# Citations are yellow (SRS UI rules): hues in this OKLCH band read as it.
YELLOW_HUE = (70.0, 120.0)
CHROMA_MIN = 0.07
LIGHT_MIN, LIGHT_MAX = 0.40, 0.80
DARK_L = (0.55, 0.70)


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _unlin(c: float) -> float:
    c = min(1.0, max(0.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def oklch(hex_: str) -> tuple[float, float, float]:
    n = int(hex_[1:], 16)
    r, g, b = (_lin(((n >> s) & 255) / 255) for s in (16, 8, 0))
    lc = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    mc = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    sc = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in (lc, mc, sc))
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def _hex_from_oklch(L: float, C: float, h: float) -> str:
    a, bb = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
    l_ = (L + 0.3963377774 * a + 0.2158037573 * bb) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * bb) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * bb) ** 3
    r = 4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_
    g = -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_
    b = -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_
    return "#" + "".join(f"{round(_unlin(v) * 255):02x}" for v in (r, g, b))


def color_problem(hex_: str) -> str | None:
    """Why ``hex_`` cannot be a mark colour, or None when it can."""
    if not _HEX.match(hex_ or ""):
        return "not a #rrggbb colour"
    L, C, h = oklch(hex_.lower())
    if C < CHROMA_MIN:
        return "too grey to tell apart from an uncoloured mark"
    if YELLOW_HUE[0] <= h <= YELLOW_HUE[1]:
        return "too close to yellow, which is reserved for citations"
    if L < LIGHT_MIN:
        return "too dark to read text over"
    if L > LIGHT_MAX:
        return "too pale to see on the page"
    return None


def dark_variant(key: str, hex_: str) -> str:
    """The colour drawn in the dark theme: lightness brought into the band
    that reads on a dark page, hue and chroma kept."""
    if key in DEFAULT_DARK:
        return DEFAULT_DARK[key]
    L, C, h = oklch(hex_.lower())
    return _hex_from_oklch(min(DARK_L[1], max(DARK_L[0], L + 0.03)), C, h)


def clean_name(name: str) -> str:
    n = " ".join(name.split())
    if not n:
        raise ValueError("a colour needs a label")
    if len(n) > NAME_MAX:
        raise ValueError(f"a label is at most {NAME_MAX} characters")
    return n
