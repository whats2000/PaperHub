import { Menu } from "@base-ui/react/menu";
import { Check, Monitor, Moon, Settings as SettingsIcon, Sun, User } from "lucide-react";
import { useTheme } from "next-themes";
import { useTranslation } from "react-i18next";

import {
  LANGUAGE_ENDONYMS,
  SUPPORTED_LANGUAGES,
  type SupportedLanguage,
} from "../../lib/i18n";

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

// App version for the About line. A build-time env override is preferred;
// otherwise fall back to a static string so typecheck never depends on an
// undefined global.
const APP_VERSION =
  (import.meta.env.VITE_APP_VERSION as string | undefined) ?? "2.30.0";

export function AccountMenu({ collapsed, onOpenSettings }: Props) {
  const { t, i18n } = useTranslation("common");
  const { theme, setTheme } = useTheme();

  return (
    <Menu.Root>
      <Menu.Trigger
        aria-label={t("account")}
        className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-sm outline-none hover:bg-accent/50"
      >
        <span className="grid size-7 shrink-0 place-items-center rounded-full bg-muted">
          <User className="size-4" />
        </span>
        {!collapsed && <span className="truncate">{t("account")}</span>}
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner side="top" align="start" className="isolate z-50">
          <Menu.Popup className={POPUP_CLASS}>
            {/* Language — inline group so each endonym is a directly
                selectable menuitem (keeps the menu shallow + accessible). */}
            <Menu.Group>
              <Menu.GroupLabel className={LABEL_CLASS}>{t("language")}</Menu.GroupLabel>
              {SUPPORTED_LANGUAGES.map((lng: SupportedLanguage) => (
                <Menu.Item
                  key={lng}
                  className={`${ITEM_CLASS} justify-between`}
                  closeOnClick={false}
                  onClick={() => void i18n.changeLanguage(lng)}
                >
                  <span>{LANGUAGE_ENDONYMS[lng]}</span>
                  {i18n.language === lng && <Check className="size-4" />}
                </Menu.Item>
              ))}
            </Menu.Group>

            <Menu.Separator className="my-1 h-px bg-border" />

            {/* Theme — inline group, same shape as Language. */}
            <Menu.Group>
              <Menu.GroupLabel className={LABEL_CLASS}>{t("theme")}</Menu.GroupLabel>
              {THEME_OPTIONS.map(({ value, icon: Icon, key }) => (
                <Menu.Item
                  key={value}
                  className={`${ITEM_CLASS} justify-between`}
                  closeOnClick={false}
                  onClick={() => setTheme(value)}
                >
                  <span className="flex items-center gap-2">
                    <Icon className="size-4" />
                    {t(key)}
                  </span>
                  {theme === value && <Check className="size-4" />}
                </Menu.Item>
              ))}
            </Menu.Group>

            <Menu.Separator className="my-1 h-px bg-border" />

            <Menu.Item className={`${ITEM_CLASS} gap-2`} onClick={onOpenSettings}>
              <SettingsIcon className="size-4" />
              {t("settings")}
            </Menu.Item>

            <Menu.Item className={`${ITEM_CLASS} gap-2`} disabled>
              {t("about")} · v{APP_VERSION}
            </Menu.Item>
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}
