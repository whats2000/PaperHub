import { Menu } from "@base-ui/react/menu";
import { Check, Monitor, Moon, Settings as SettingsIcon, Sun, User } from "lucide-react";
import { useTheme } from "next-themes";
import { useTranslation } from "react-i18next";

import {
  LANGUAGE_ENDONYMS,
  SUPPORTED_LANGUAGES,
  type SupportedLanguage,
} from "../../lib/i18n";
import { useVersionStore } from "@/store/version";

interface Props {
  collapsed: boolean;
  onOpenSettings: () => void;
}

const THEME_OPTIONS = [
  { value: "light", icon: Sun, key: "themeLight" },
  { value: "dark", icon: Moon, key: "themeDark" },
  { value: "system", icon: Monitor, key: "themeSystem" },
] as const;

// Shared menu styling (no `.menu-item` utility exists in this codebase, so the
// classes are inlined to match the popover/button Tailwind conventions).
const ITEM_CLASS =
  "flex w-full cursor-default select-none items-center rounded-sm px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-50";
const POPUP_CLASS =
  "z-50 min-w-48 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md outline-none";
const LABEL_CLASS =
  "px-2 py-1 text-xs font-medium uppercase tracking-wide text-muted-foreground";

// App version for the About line — injected at build time from package.json
// via the `__APP_VERSION__` Vite `define` (see vite.config.ts).
const APP_VERSION = __APP_VERSION__;

export function AccountMenu({ collapsed, onOpenSettings }: Props) {
  const { t, i18n } = useTranslation("common");
  const { theme, setTheme } = useTheme();
  const openChangelog = useVersionStore((s) => s.openChangelog);
  const updateAvailable = useVersionStore((s) => s.info?.update_available ?? false);

  return (
    <Menu.Root>
      <Menu.Trigger
        aria-label={t("account")}
        className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-sm outline-none hover:bg-accent/50"
      >
        <span className="relative grid size-7 shrink-0 place-items-center rounded-full bg-muted">
          <User className="size-4" />
          {updateAvailable && (
            <span className="absolute -right-0.5 -top-0.5 size-2 rounded-full bg-amber-500"
                  aria-label={t("updateBadge")} />
          )}
        </span>
        {!collapsed && <span className="truncate">{t("account")}</span>}
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner side="top" align="start" className="isolate z-50">
          <Menu.Popup className={POPUP_CLASS}>
            {/* Language — a single-select radio group so each endonym renders
                as role="menuitemradio" with real aria-checked state. */}
            <Menu.Group>
              <Menu.GroupLabel className={LABEL_CLASS}>{t("language")}</Menu.GroupLabel>
              <Menu.RadioGroup
                value={i18n.language}
                onValueChange={(value) => void i18n.changeLanguage(value as string)}
              >
                {SUPPORTED_LANGUAGES.map((lng: SupportedLanguage) => (
                  <Menu.RadioItem
                    key={lng}
                    value={lng}
                    className={`${ITEM_CLASS} justify-between`}
                  >
                    <span>{LANGUAGE_ENDONYMS[lng]}</span>
                    <Menu.RadioItemIndicator>
                      <Check className="size-4" />
                    </Menu.RadioItemIndicator>
                  </Menu.RadioItem>
                ))}
              </Menu.RadioGroup>
            </Menu.Group>

            <Menu.Separator className="my-1 h-px bg-border" />

            {/* Theme — a single-select radio group, same shape as Language. */}
            <Menu.Group>
              <Menu.GroupLabel className={LABEL_CLASS}>{t("theme")}</Menu.GroupLabel>
              <Menu.RadioGroup
                value={theme}
                onValueChange={(value) => setTheme(value as string)}
              >
                {THEME_OPTIONS.map(({ value, icon: Icon, key }) => (
                  <Menu.RadioItem
                    key={value}
                    value={value}
                    className={`${ITEM_CLASS} justify-between`}
                  >
                    <span className="flex items-center gap-2">
                      <Icon className="size-4" />
                      {t(key)}
                    </span>
                    <Menu.RadioItemIndicator>
                      <Check className="size-4" />
                    </Menu.RadioItemIndicator>
                  </Menu.RadioItem>
                ))}
              </Menu.RadioGroup>
            </Menu.Group>

            <Menu.Separator className="my-1 h-px bg-border" />

            <Menu.Item className={`${ITEM_CLASS} gap-2`} onClick={onOpenSettings}>
              <SettingsIcon className="size-4" />
              {t("settings")}
            </Menu.Item>

            <Menu.Item className={`${ITEM_CLASS} gap-2`} onClick={openChangelog}>
              {t("about")} · v{APP_VERSION}
            </Menu.Item>
            {updateAvailable && (
              <Menu.Item className={`${ITEM_CLASS} gap-2 text-amber-600 dark:text-amber-400`} onClick={openChangelog}>
                {t("about:update")}
              </Menu.Item>
            )}
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}
