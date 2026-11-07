"""Target-language labels for bilingual reading (Plan H1).

The target language is free text, and it is the translation cache key. Known
spellings of one language ("Japanese", "ja", "日本語") map to one canonical
label so they share one cache; any other label is kept as its own key (only
its whitespace is collapsed). Canonical labels are the frontend's UI-locale
endonyms where one exists, so the pill's choices are already canonical.
"""

from __future__ import annotations

import re
import unicodedata

# code -> (canonical label, aliases). Aliases are matched after NFKC + casefold
# + whitespace collapse, so "Ｊａｐａｎｅｓｅ" and "chinese  (traditional)" match.
_LANGUAGES: dict[str, tuple[str, tuple[str, ...]]] = {
    "en": ("English", ("english", "en", "en-us", "en-gb", "英文", "英語", "英语")),
    "zh-Hant": ("繁體中文", (
        "繁體中文", "繁体中文", "正體中文", "繁中", "中文(繁體)", "中文(繁体)",
        "traditional chinese", "chinese (traditional)", "chinese traditional",
        "zh-tw", "zh_tw", "zh-hant",
    )),
    "zh-Hans": ("简体中文", (
        "简体中文", "簡體中文", "简中", "中文(简体)", "中文(簡體)",
        "simplified chinese", "chinese (simplified)", "chinese simplified",
        "zh-cn", "zh_cn", "zh-hans",
    )),
    "ja": ("日本語", ("日本語", "japanese", "ja", "ja-jp", "日文", "日语", "日語")),
    "ko": ("한국어", ("한국어", "korean", "ko", "ko-kr", "韓文", "韓語", "韩语", "韓国語")),
    "fr": ("Français", ("français", "francais", "french", "fr", "法文", "法語")),
    "de": ("Deutsch", ("deutsch", "german", "de", "德文", "德語")),
    "es": ("Español", ("español", "espanol", "spanish", "es", "西班牙文")),
}

_ALIAS: dict[str, str] = {
    alias: code for code, (_, aliases) in _LANGUAGES.items() for alias in aliases
}

_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯]")


def _collapse(label: str) -> str:
    return " ".join(label.split())


def _match_key(label: str) -> str:
    return _collapse(unicodedata.normalize("NFKC", label)).casefold()


def _known_code(label: str) -> str | None:
    return _ALIAS.get(_match_key(label))


def canonical_language(label: str) -> str:
    """The cache key for ``label``: a known language's canonical label, else
    ``label`` with its whitespace collapsed."""
    code = _known_code(label)
    return _LANGUAGES[code][0] if code else _collapse(label)


def language_code(label: str) -> str | None:
    """A coarse code for script checks: ``en``, ``zh``, ``ja``, ``ko``, ``fr``,
    ... A free-text label is classified by its own script (a label written in
    Han, kana or Hangul names a language written in it), else ``None``."""
    code = _known_code(label)
    if code is not None:
        return "zh" if code.startswith("zh") else code
    if _KANA.search(label):
        return "ja"
    if _HANGUL.search(label):
        return "ko"
    if _HAN.search(label):
        return "zh"
    if "english" in _match_key(label):
        return "en"
    return None
