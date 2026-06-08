import { Moon, Sun, Monitor } from "lucide-react";
import { useTheme } from "next-themes";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

type ThemeChoice = "light" | "dark" | "system";

const NEXT: Record<ThemeChoice, ThemeChoice> = {
  light: "dark",
  dark: "system",
  system: "light",
};

const LABEL_KEY: Record<ThemeChoice, string> = {
  light: "theme.light",
  dark: "theme.dark",
  system: "theme.system",
};

export function ThemeToggle() {
  const { t } = useTranslation("states");
  const { theme, setTheme } = useTheme();
  const current = (theme as ThemeChoice | undefined) ?? "system";
  const Icon =
    current === "light" ? Sun : current === "dark" ? Moon : Monitor;
  const label = t(LABEL_KEY[current]);

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="icon"
              aria-label={t("theme.buttonAria", { label })}
              onClick={() => setTheme(NEXT[current])}
            />
          }
        >
          <Icon className="h-4 w-4" />
        </TooltipTrigger>
        <TooltipContent>
          <p className="text-sm">{t("theme.tooltip", { label })}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
