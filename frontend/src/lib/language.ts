/** Target-language labels for bilingual reading, as the backend keys them
 * (`backend/src/paperhub/translation/language.py`): known spellings of one
 * language ("Japanese", "ja", "日本語") are one language, its canonical label
 * the UI-locale endonym; any other label is its own, whitespace collapsed.
 * Keep the two tables in step. */
import i18n from "@/lib/i18n";

/** code -> [canonical label, aliases (matched after NFKC + lower case +
 * whitespace collapse)]. Mirrors the backend's `_LANGUAGES`. */
const LANGUAGES: Record<string, [string, string[]]> = {
  en: ["English", ["english", "en", "en-us", "en-gb", "英文", "英語", "英语"]],
  "zh-Hant": [
    "繁體中文",
    [
      "繁體中文", "繁体中文", "正體中文", "繁中", "中文(繁體)", "中文(繁体)",
      "traditional chinese", "chinese (traditional)", "chinese traditional",
      "zh-tw", "zh_tw", "zh-hant",
    ],
  ],
  "zh-Hans": [
    "简体中文",
    [
      "简体中文", "簡體中文", "简中", "中文(简体)", "中文(簡體)",
      "simplified chinese", "chinese (simplified)", "chinese simplified",
      "zh-cn", "zh_cn", "zh-hans",
    ],
  ],
  ja: ["日本語", ["日本語", "japanese", "ja", "ja-jp", "日文", "日语", "日語"]],
  ko: ["한국어", ["한국어", "korean", "ko", "ko-kr", "韓文", "韓語", "韩语", "韓国語"]],
  fr: ["Français", ["français", "francais", "french", "fr", "法文", "法語"]],
  de: ["Deutsch", ["deutsch", "german", "de", "德文", "德語"]],
  es: ["Español", ["español", "espanol", "spanish", "es", "西班牙文"]],
};

const ALIAS = new Map<string, string>(
  Object.entries(LANGUAGES).flatMap(([code, [, aliases]]) => aliases.map((a) => [a, code] as [string, string])),
);

const collapse = (s: string) => s.split(/\s+/).filter(Boolean).join(" ");

/** The language code of a known label (null: free text). */
export function languageCode(label: string): string | null {
  return ALIAS.get(collapse(label.normalize("NFKC")).toLowerCase()) ?? null;
}

/** The label a translation is keyed by (the backend's `normalize_language`). */
export function normalizeLanguage(label: string): string {
  const code = languageCode(label);
  return code ? LANGUAGES[code]![0] : collapse(label);
}

/** Whether two labels name the same translation language. */
export const sameLanguage = (a: string | null | undefined, b: string | null | undefined) =>
  !!a && !!b && normalizeLanguage(a) === normalizeLanguage(b);

/** Short names of the known languages (a free-text one keeps its name). */
const SHORT_DEFAULTS: Record<string, string> = {
  en: "English",
  "zh-Hant": "繁中",
  "zh-Hans": "简中",
  ja: "日本語",
  ko: "한국어",
  fr: "Français",
  de: "Deutsch",
  es: "Español",
};

/** A language's short name in the UI language ("繁中"); free text as typed. */
export function shortLanguage(label: string): string {
  const code = languageCode(label);
  if (!code) return collapse(label);
  return i18n.t(`notes:langShort.${code}`, SHORT_DEFAULTS[code] ?? LANGUAGES[code]![0]);
}
