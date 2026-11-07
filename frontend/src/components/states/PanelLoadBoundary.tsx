import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RefreshCw, RotateCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";

interface BoundaryProps {
  children: ReactNode;
  /** Called before the boundary re-renders its children on "Try again". */
  onRetry?: () => void;
  className?: string;
}

interface BoundaryState {
  error: Error | null;
}

/**
 * Contains a failed lazy panel (see `retryableLazy`: Slides, Citation Canvas, Memory) so the
 * error stays inside the panel instead of unmounting the whole app.
 */
export class PanelLoadBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Panel failed to load", error, info.componentStack);
  }

  private retry = (): void => {
    this.props.onRetry?.();
    this.setState({ error: null });
  };

  render(): ReactNode {
    if (this.state.error) {
      return <PanelLoadError onRetry={this.retry} className={this.props.className} />;
    }
    return this.props.children;
  }
}

function PanelLoadError({
  onRetry,
  className,
}: {
  onRetry: () => void;
  className?: string;
}) {
  const { t } = useTranslation("states");
  return (
    <div
      role="alert"
      className={
        "flex h-full w-full flex-col items-center justify-center gap-3 p-6 text-center " +
        "animate-in fade-in-0 zoom-in-95 duration-200 motion-reduce:animate-none " +
        (className ?? "")
      }
    >
      <AlertTriangle className="h-8 w-8 text-destructive" aria-hidden />
      <p className="text-sm font-medium">
        {t("panelLoad.title", "This panel couldn't load")}
      </p>
      <p className="max-w-xs text-xs text-muted-foreground">
        {t(
          "panelLoad.body",
          "Part of the app failed to download, often after an update. The rest of the app still works.",
        )}
      </p>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" onClick={onRetry}>
          <RotateCw className="mr-1 h-3.5 w-3.5" aria-hidden />
          {t("panelLoad.retry", "Try again")}
        </Button>
        <Button size="sm" onClick={() => window.location.reload()}>
          <RefreshCw className="mr-1 h-3.5 w-3.5" aria-hidden />
          {t("panelLoad.reload", "Reload page")}
        </Button>
      </div>
    </div>
  );
}
