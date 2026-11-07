"""Output and input checks for bilingual reading (Plan H1 QA fixes).

* ``is_echo`` / ``looks_untranslated``: the model sent the source back
  (verbatim or barely reworded) instead of translating it. Placeholder checks
  pass on an echo, so without this it was cached as ``ok`` forever.
* ``is_meta_block``: a byline, affiliation, correspondence, date or DOI line
  (names and addresses, nothing to translate). The PDF paragraph builder
  skips these; the HTML overlay ports the same rule (``htmlTranslation.ts``).
* ``tidy_cjk_spacing``: drop the stray spaces a model puts inside or before
  full-width punctuation ("（ 例如 ）").

Every check is conservative: a block that is legitimately left as is (names,
math only, a DOI, text already in the target language) is never an echo.
"""

from __future__ import annotations

import difflib
import re

from paperhub.translation.language import language_code

_PLACEHOLDER = re.compile(r"⟦(?:\d+|BR)⟧")
_LATIN_WORD = re.compile(r"[A-Za-zÀ-ɏ]+(?:['’-][A-Za-zÀ-ɏ]+)*")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_SCRIPT = {
    "zh": re.compile(r"[㐀-䶿一-鿿豈-﫿]"),
    "ja": re.compile(r"[぀-ヿ㐀-䶿一-鿿]"),
    "ko": re.compile(r"[가-힯]"),
}
_EN_STOPWORDS = frozenset({
    "a", "an", "the", "of", "and", "or", "to", "in", "on", "for", "with", "by",
    "from", "as", "at", "is", "are", "was", "were", "be", "been", "this", "that",
    "these", "those", "which", "who", "we", "our", "it", "its", "their", "they",
    "can", "not", "than", "then", "into", "over", "under", "between", "through",
    "each", "such", "also", "however", "while", "when", "where"
})
# A run of prose: this many Latin words, mostly lowercase (names are not).
_PROSE_MIN_WORDS = 8
_PROSE_LOWER_SHARE = 0.5
_ENGLISH_STOPWORD_SHARE = 0.15
_NEAR_IDENTICAL = 0.9


def _bare(text: str) -> str:
    return _PLACEHOLDER.sub(" ", text)


def _latin_prose(text: str) -> bool:
    words = [w for w in _LATIN_WORD.findall(_bare(text)) if len(w) > 1]
    if len(words) < _PROSE_MIN_WORDS:
        return False
    lower = sum(1 for w in words if w[0].islower())
    return lower / len(words) >= _PROSE_LOWER_SHARE


def _english_prose(text: str) -> bool:
    if not _latin_prose(text):
        return False
    words = [w.casefold() for w in _LATIN_WORD.findall(_bare(text))]
    stop = sum(1 for w in words if w in _EN_STOPWORDS)
    return stop / max(1, len(words)) >= _ENGLISH_STOPWORD_SHARE


def looks_untranslated(output: str, language: str) -> bool:
    """``output`` is Latin-script prose that cannot be a translation into
    ``language``: a CJK target with no character of its script, or English
    prose for any non-English target. Needs no source, so it also audits
    cached rows."""
    code = language_code(language)
    if code == "en":
        return False
    script = _SCRIPT.get(code or "")
    if script is not None:
        return _latin_prose(output) and script.search(output) is None
    return _english_prose(output)


def is_echo(source: str, output: str, language: str) -> bool:
    """The model returned ``source`` (or nearly it) instead of a translation."""
    if language_code(language) == "en":
        return False
    if looks_untranslated(output, language):
        return True
    # Any other target: a near copy of a prose source is not a translation.
    src, out = " ".join(_bare(source).split()), " ".join(_bare(output).split())
    if not _latin_prose(src):
        return False
    return difflib.SequenceMatcher(None, src, out, autojunk=False).ratio() >= _NEAR_IDENTICAL


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_DOI = re.compile(r"(?:\bdoi:\s*)?\b10\.\d{4,9}/\S+", re.I)
_KEYWORDS = re.compile(r"^\W*(?:keywords?|key\s+words|index\s+terms)\b", re.I)
# Lowercase words a name / affiliation line may hold.
_CONNECTORS = frozenset({
    "and", "of", "the", "for", "at", "in", "de", "des", "du", "la", "le", "van",
    "von", "der", "den", "und", "y", "e", "da", "di", "et", "al"
})
_META_MAX_WORDS = 150


def is_meta_block(text: str) -> bool:
    """A byline, affiliation, correspondence, date or DOI/URL/e-mail-only line.

    Its words (after removing e-mails, URLs, DOIs and placeholders) are all
    capitalised (bar a few) apart from connectors such as "of" and "and". Keyword lines
    are content, never meta."""
    bare = _bare(text).strip()
    if not bare or _KEYWORDS.match(bare):
        return False
    stripped = _DOI.sub(" ", _URL.sub(" ", _EMAIL.sub(" ", bare)))
    had_address = stripped != bare
    words = _WORD.findall(stripped)
    if not words:
        return had_address
    if len(words) > _META_MAX_WORDS:
        return False
    content = [w for w in words if w.casefold() not in _CONNECTORS]
    if not content or (len(content) < 2 and not had_address):
        return False  # one capitalised word ("Results") is not a byline
    # A CJK run has no case, so it never reads as a name here. Two words (at
    # most a quarter) may be lowercase ("Google Research, Brain team"); more
    # is a sentence.
    lower = sum(1 for w in content if not w[0].isupper())
    return lower <= min(2, len(content) // 4)


_FW_OPEN = "（「『【《〈〔"
_FW_CLOSE_OR_STOP = "）」』】》〉〕，。、；：！？"
_SPACE = r"[ \t 　]+"
_SPACE_BEFORE = re.compile(rf"{_SPACE}(?=[{_FW_CLOSE_OR_STOP}{_FW_OPEN}])")
_SPACE_AFTER = re.compile(rf"(?<=[{_FW_OPEN}{_FW_CLOSE_OR_STOP}]){_SPACE}")


def tidy_cjk_spacing(text: str) -> str:
    """Remove spaces before and after full-width punctuation. Placeholders and
    spacing between other characters (Latin words, ``⟦n⟧`` next to Han) stay."""
    return _SPACE_AFTER.sub("", _SPACE_BEFORE.sub("", text))
