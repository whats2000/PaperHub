import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

interface PageHeaderProps {
  icon: LucideIcon;
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
}

/** Page title row; shares `--header-h` with the Sidebar header so their bottom rules align. */
export function PageHeader({ icon: Icon, title, meta, actions }: PageHeaderProps) {
  return (
    <div className="shrink-0 h-(--header-h) flex items-center gap-2.5 px-5 border-b border-border">
      <Icon className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
      <h1 className="text-lg font-semibold">{title}</h1>
      {meta && <div className="text-xs text-muted-foreground">{meta}</div>}
      <div className="flex-1" />
      {actions}
    </div>
  );
}
