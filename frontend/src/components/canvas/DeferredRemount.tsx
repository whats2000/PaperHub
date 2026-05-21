import { Fragment, useEffect, useState, type ReactNode } from "react";

import { PaperLoading } from "@/components/canvas/PaperLoading";

interface Props {
  /** When this changes, the children are unmounted now and the new ones are
   *  mounted on a fresh macrotask — never swapped in place. */
  swapKey: string | number;
  /** Shown for the one task between unmount and remount. */
  fallback?: ReactNode;
  children: ReactNode;
}

const DEFAULT_FALLBACK = <PaperLoading />;

/**
 * Remounts `children` across a task boundary when `swapKey` changes: the old
 * subtree unmounts synchronously (this commit), then the new one mounts in a
 * separate macrotask. This keeps an expensive unmount — e.g. react-pdf / pdf.js
 * worker teardown — from sharing one synchronous render flush with the next
 * mount, which otherwise froze the main thread for ~20s on a PDF↔PDF swap.
 * Mounting the reader asynchronously, in its own frame, is the whole fix.
 */
export function DeferredRemount({ swapKey, fallback, children }: Props) {
  const [committedKey, setCommittedKey] = useState(swapKey);
  const [swapping, setSwapping] = useState(false);

  // Adjust state during render when the key changes (the documented
  // derive-from-props pattern, not an effect): begin the swap, which unmounts
  // the current children on this commit.
  if (swapKey !== committedKey && !swapping) {
    setSwapping(true);
  }

  // After the unmount has committed, mount the new children on the next task.
  useEffect(() => {
    if (!swapping) return;
    const id = window.setTimeout(() => {
      setCommittedKey(swapKey);
      setSwapping(false);
    }, 0);
    return () => window.clearTimeout(id);
  }, [swapping, swapKey]);

  if (swapping) return <>{fallback ?? DEFAULT_FALLBACK}</>;
  // `key` guarantees a fresh mount per committed swapKey.
  return <Fragment key={committedKey}>{children}</Fragment>;
}
