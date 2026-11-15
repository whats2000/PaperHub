"""Verbatim-span detection against the turn's private material (Plan H5a).

One window generator serves the checkpoint's guard and the eval's automatic
``must_not_contain`` set. Text is NFKC-normalised and case-folded, punctuation
and whitespace dropped. Windows are every 8-word window, plus - when the text
contains CJK - every 12-character window over the character stream of all
word characters (``\w``: CJK, Latin, digits and other scripts; punctuation and
whitespace dropped, so they do not break a run); only windows holding at least
one CJK character count, so a common long Latin word never matches by itself.
"""
from __future__ import annotations

import re
import unicodedata

WORD_WINDOW = 8
CJK_WINDOW = 12
_CJK = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")


def normalise(text: str) -> str:
    """NFKC, case-folded, punctuation to spaces, whitespace collapsed."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", folded)).strip()


def windows(text: str, words: int = WORD_WINDOW, chars: int = CJK_WINDOW) -> list[str]:
    norm = normalise(text)
    toks = norm.split()
    out = [" ".join(toks[i:i + words]) for i in range(max(0, len(toks) - words + 1))]
    if _CJK.search(norm):
        stream = re.sub(r"[\s_]+", "", norm)
        out += [w for i in range(max(0, len(stream) - chars + 1))
                if _CJK.search(w := stream[i:i + chars])]
    return out


def shares_span(text: str, private_texts: list[str] | tuple[str, ...]) -> bool:
    """True when ``text`` shares a window with any of ``private_texts``."""
    mine = set(windows(text))
    return bool(mine) and any(mine.intersection(windows(p)) for p in private_texts)


#: A private document title shorter than this is too generic to guard.
MIN_TITLE_WORDS = 3
MIN_TITLE_CHARS = 12


def _phrase_norm(text: str) -> str:
    """``normalise`` with ``_`` as a separator too (the term matcher's rule)."""
    return re.sub(r"\s+", " ", normalise(text).replace("_", " ")).strip()


def _stream(norm: str) -> str:
    return re.sub(r"\s+", "", norm)


def guarded_title(title: str) -> str | None:
    """The normalised form of a private document title the checkpoint guards,
    or None when it is too short to be specific: fewer than 3 words or 12
    characters (a title with CJK counts characters of its stream only, since
    CJK has no word separators)."""
    norm = _phrase_norm(title)
    if _CJK.search(norm):
        return norm if len(_stream(norm)) >= MIN_TITLE_CHARS else None
    if len(norm.split()) < MIN_TITLE_WORDS or len(norm) < MIN_TITLE_CHARS:
        return None
    return norm


def contains_phrase(text: str, phrase: str) -> bool:
    """``text`` contains the normalised ``phrase`` on word boundaries (a
    phrase with CJK: anywhere in the character stream)."""
    norm = _phrase_norm(text)
    if _CJK.search(phrase):
        return _stream(phrase) in _stream(norm)
    return f" {phrase} " in f" {norm} "


#: Function words left out when a title is matched word by word.
_TITLE_STOPWORDS = frozenset(
    ["a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on", "or", "the", "to", "via", "with", "without"])


def _content_words(norm: str) -> set[str]:
    return {w for w in norm.split() if w not in _TITLE_STOPWORDS}


#: Any-order matching needs this many content words (fewer are too generic).
MIN_ANY_ORDER_WORDS = 4
#: From this many content words on, all but one still name the title.
ALL_BUT_ONE_WORDS = 5


def contains_title(text: str, title: str, *, any_order: bool = False) -> bool:
    """``text`` names the guarded (normalised) ``title`` as a phrase. With
    ``any_order`` (the review's rewrite, where reordering was seen), a Latin
    title of at least 4 content words also matches word by word in any
    order: all of them, or all but one from 5 on, so a reordered title is
    caught but a text sharing only its topic is not."""
    if contains_phrase(text, title):
        return True
    if not any_order or _CJK.search(title):
        return False
    words = _content_words(title)
    if len(words) < MIN_ANY_ORDER_WORDS:
        return False
    have = len(words & set(_phrase_norm(text).split()))
    return have >= (len(words) - 1 if len(words) >= ALL_BUT_ONE_WORDS else len(words))
