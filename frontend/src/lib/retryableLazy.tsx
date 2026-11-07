import {
  lazy,
  type ComponentProps,
  type ComponentType,
  type LazyExoticComponent,
  type ReactNode,
} from "react";

// Same constraint React.lazy uses; props are recovered via ComponentProps<T>.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyComponent = ComponentType<any>;

export interface RetryableLazy<T extends AnyComponent> {
  (props: ComponentProps<T>): ReactNode;
  /** Drop the cached (rejected) import so the next render imports again. */
  reset: () => void;
}

/**
 * `React.lazy` caches a rejected import forever, so a panel whose chunk
 * failed once (a stale deploy chunk, a dev-server dep re-optimize) could
 * never load again without a page reload. This wrapper can be `reset()`
 * to retry the import; pair it with `PanelLoadBoundary`'s `onRetry`.
 */
export function retryableLazy<T extends AnyComponent>(
  load: () => Promise<{ default: T }>,
): RetryableLazy<T> {
  let Lazy: LazyExoticComponent<T> = lazy(load);
  const Wrapper = ((props: ComponentProps<T>) => <Lazy {...props} />) as RetryableLazy<T>;
  Wrapper.reset = () => {
    Lazy = lazy(load);
  };
  return Wrapper;
}
