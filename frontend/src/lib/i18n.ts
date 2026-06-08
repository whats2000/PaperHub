import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import enChat from "../locales/en/chat.json";
import enCommon from "../locales/en/common.json";
import enMemory from "../locales/en/memory.json";
import enReferences from "../locales/en/references.json";
import enSettings from "../locales/en/settings.json";
import jaChat from "../locales/ja/chat.json";
import jaCommon from "../locales/ja/common.json";
import jaMemory from "../locales/ja/memory.json";
import jaReferences from "../locales/ja/references.json";
import jaSettings from "../locales/ja/settings.json";
import zhCNChat from "../locales/zh-CN/chat.json";
import zhCNCommon from "../locales/zh-CN/common.json";
import zhCNMemory from "../locales/zh-CN/memory.json";
import zhCNReferences from "../locales/zh-CN/references.json";
import zhCNSettings from "../locales/zh-CN/settings.json";
import zhTWChat from "../locales/zh-TW/chat.json";
import zhTWCommon from "../locales/zh-TW/common.json";
import zhTWMemory from "../locales/zh-TW/memory.json";
import zhTWReferences from "../locales/zh-TW/references.json";
import zhTWSettings from "../locales/zh-TW/settings.json";

export const SUPPORTED_LANGUAGES = ["en", "zh-TW", "zh-CN", "ja"] as const;
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];

export const LANGUAGE_ENDONYMS: Record<SupportedLanguage, string> = {
  en: "English",
  "zh-TW": "繁體中文",
  "zh-CN": "简体中文",
  ja: "日本語",
};

// English is the source-of-truth catalog. New namespaces are added here and
// to each locale folder as the string-extraction pass progresses (Task D1).
const resources = {
  en: {
    common: enCommon,
    settings: enSettings,
    chat: enChat,
    memory: enMemory,
    references: enReferences,
  },
  "zh-TW": {
    common: zhTWCommon,
    settings: zhTWSettings,
    chat: zhTWChat,
    memory: zhTWMemory,
    references: zhTWReferences,
  },
  "zh-CN": {
    common: zhCNCommon,
    settings: zhCNSettings,
    chat: zhCNChat,
    memory: zhCNMemory,
    references: zhCNReferences,
  },
  ja: {
    common: jaCommon,
    settings: jaSettings,
    chat: jaChat,
    memory: jaMemory,
    references: jaReferences,
  },
} as const;

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: "en",
    supportedLngs: [...SUPPORTED_LANGUAGES],
    ns: ["common", "settings", "chat", "memory", "references"],
    defaultNS: "common",
    interpolation: { escapeValue: false },
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: "paperhub-lang",
      caches: ["localStorage"],
    },
  });

export default i18n;
