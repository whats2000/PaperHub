"""The response language for a tool's direct answer (H4).

When ``query_library`` answers the user directly (``answer_user``), the SQL
agent writes the user-facing text, but it does not see the conversation. It
gets this explicit language instead. A remembered language preference still
wins: the SQL agent's prompt honours a remembered fact over this value, and
its question carries the active-memory block.
"""
from __future__ import annotations

import re

from paperhub.agents.router_policy import _chinese_script

_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯]")
_LETTER = re.compile(r"[^\W\d_]")
# A run of letters outside the CJK scripts: one Latin (Cyrillic, ...) word.
_WORD = re.compile(r"[^\W\d_぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]+")

_UI_LANGUAGES = {
    "en": "English",
    "zh-TW": "Traditional Chinese",
    "zh-CN": "Simplified Chinese",
    "ja": "Japanese",
}
SAME_AS_MESSAGE = "the language of the user's message"


def direct_answer_language(user_message: str, ui_language: str | None) -> str:
    """The language to answer ``user_message`` in.

    CJK decides only when it dominates: at least two CJK characters (about
    one CJK word) per word in another script, so a CJK term or title inside
    an English sentence stays "the language of the user's message". Then:
    kana -> Japanese, Hangul -> Korean; Han-only text is Japanese under a
    Japanese UI (kanji overlap Traditional forms), else the Chinese script
    its Traditional/Simplified-only characters show, else the UI's Chinese
    script. A message with no letters takes the UI language (English when
    unset)."""
    cjk = (len(_HAN.findall(user_message)) + len(_KANA.findall(user_message))
           + len(_HANGUL.findall(user_message)))
    words = len(_WORD.findall(user_message))
    if cjk and cjk >= 2 * words:
        if _KANA.search(user_message):
            return "Japanese"
        if _HANGUL.search(user_message):
            return "Korean"
        if ui_language == "ja":
            return "Japanese"
        script = _chinese_script(user_message)
        if script == "traditional" or (script is None and ui_language == "zh-TW"):
            return "Traditional Chinese"
        if script == "simplified" or (script is None and ui_language == "zh-CN"):
            return "Simplified Chinese"
        return "Chinese, in the script of the user's message"
    if _LETTER.search(user_message):
        return SAME_AS_MESSAGE
    return _UI_LANGUAGES.get(ui_language or "", "English")
