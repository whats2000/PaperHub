"""Placeholder and cache-key helpers for bilingual reading (Plan H1)."""

import hashlib
import re
from collections import Counter

from paperhub.translation.language import canonical_language

_PLACEHOLDER_RE = re.compile(r"⟦(\d+|BR)⟧")


def placeholder_ids(text: str) -> list[str]:
    """Ordered ``⟦n⟧`` / ``⟦BR⟧`` token ids found in ``text``."""
    return _PLACEHOLDER_RE.findall(text)


def placeholders_intact(source: str, output: str) -> bool:
    """True when ``output`` keeps exactly the source's tokens (order may change)."""
    return Counter(placeholder_ids(source)) == Counter(placeholder_ids(output))


def normalize_language(s: str) -> str:
    """The cache key for a target-language label: known spellings of one
    language share one canonical label ("Japanese" -> "日本語"); any other
    label keeps its own key, with whitespace collapsed."""
    return canonical_language(s)


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_BR = "⟦BR⟧"


def _has_words(segment: str) -> bool:
    return any(ch.isalnum() for ch in _PLACEHOLDER_RE.sub("", segment))


def br_position_intact(source: str, output: str) -> bool:
    """True unless a mid-paragraph ``⟦BR⟧`` was moved to an edge of ``output``.

    When the source has prose before its first ``⟦BR⟧`` the output must too, and
    likewise after its last one, so a break cannot be parked at the start or end
    of the translation (which would split the PDF card in the wrong place).
    Token counts are ``placeholders_intact``'s job; a missing break passes here.
    """
    if _BR not in source or _BR not in output:
        return True
    s_first, o_first = source.index(_BR), output.index(_BR)
    s_last, o_last = source.rindex(_BR), output.rindex(_BR)
    if _has_words(source[:s_first]) and not _has_words(output[:o_first]):
        return False
    return not (_has_words(source[s_last + len(_BR):]) and not _has_words(output[o_last + len(_BR):]))
