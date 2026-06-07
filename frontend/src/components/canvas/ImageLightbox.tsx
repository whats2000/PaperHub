import { useEffect, useRef } from "react";
import type { RefObject } from "react";
import { createPortal } from "react-dom";
import { Maximize2, Minus, Plus, X } from "lucide-react";
import {
  TransformWrapper,
  TransformComponent,
  useControls,
} from "react-zoom-pan-pinch";

interface Props {
  /** Resolved (absolute) image URL — `img.currentSrc` from the iframe figure. */
  src: string;
  /** The figure's alt text, for the accessible name. */
  alt: string;
  onClose: () => void;
}

// Zoom delta per wheel notch / control-button press. Small so one scroll nudges
// the zoom rather than jumping (the library's own `wheel.step` is broken
// upstream — bettertyped/react-zoom-pan-pinch#495 — so we drive zoom ourselves).
const ZOOM_STEP = 0.2;

/** The floating control bar. Lives INSIDE TransformWrapper so it can reach the
 *  zoom handlers via `useControls`. `stopPropagation` keeps a control click from
 *  bubbling to the backdrop dismiss handler. */
function Controls({ onClose }: { onClose: () => void }) {
  const { zoomIn, zoomOut, resetTransform } = useControls();
  const btn =
    "rounded p-1.5 text-white/90 hover:bg-white/15 hover:text-white";
  return (
    <div
      data-lightbox-controls
      onClick={(e) => e.stopPropagation()}
      className="absolute right-3 top-3 z-10 flex items-center gap-1 rounded-lg bg-black/60 p-1 backdrop-blur"
    >
      <button
        type="button"
        onClick={() => zoomIn(ZOOM_STEP)}
        aria-label="Zoom in"
        title="Zoom in"
        className={btn}
      >
        <Plus className="h-4 w-4" />
      </button>
      <button
        type="button"
        onClick={() => zoomOut(ZOOM_STEP)}
        aria-label="Zoom out"
        title="Zoom out"
        className={btn}
      >
        <Minus className="h-4 w-4" />
      </button>
      <button
        type="button"
        onClick={() => resetTransform()}
        aria-label="Reset zoom"
        title="Fit to screen"
        className={btn}
      >
        <Maximize2 className="h-4 w-4" />
      </button>
      <div className="mx-0.5 h-5 w-px bg-white/20" />
      <button
        type="button"
        onClick={onClose}
        aria-label="Close image preview"
        title="Close (Esc)"
        className={btn}
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

// Zoom level a double-click toggles into (from fit). Toggle back to fit on the
// next double-click — the standard image-lightbox behaviour (iOS Photos etc.).
const TOGGLE_SCALE = 2.5;

/** Drives a gentle, fixed-step wheel zoom on the overlay. The library's
 *  `wheel.step` doesn't actually change sensitivity (upstream bug #495), so we
 *  disable its wheel and call `zoomIn`/`zoomOut` per notch ourselves — one
 *  scroll = one small step, never a jump to max. */
function WheelZoom({ targetRef }: { targetRef: RefObject<HTMLElement | null> }) {
  const { zoomIn, zoomOut } = useControls();
  useEffect(() => {
    const el = targetRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      if (e.deltaY < 0) zoomIn(ZOOM_STEP, 0);
      else zoomOut(ZOOM_STEP, 0);
    };
    // passive:false so preventDefault can stop the page/pinch from scrolling.
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [targetRef, zoomIn, zoomOut]);
  return null;
}

/** Double-click TOGGLE zoom: if at fit, zoom in to TOGGLE_SCALE centred on the
 *  clicked point (so that point stays put); if already zoomed, return to fit.
 *  The library's own `doubleClick` modes can't do a zoom-to-point toggle
 *  (upstream #125), so we drive it via `setTransform`/`resetTransform`. */
function DoubleClickZoom({
  targetRef,
}: {
  targetRef: RefObject<HTMLElement | null>;
}) {
  const { instance, setTransform, resetTransform } = useControls();
  useEffect(() => {
    const el = targetRef.current;
    if (!el) return;
    const onDblClick = (e: MouseEvent) => {
      const { scale, positionX, positionY } = instance.state;
      const wrapper = instance.wrapperComponent;
      if (!wrapper) return;
      if (scale > 1.01) {
        resetTransform(200); // already zoomed → back to fit
        return;
      }
      const rect = wrapper.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      // The content point currently under the cursor; keep it fixed as we scale.
      const contentX = (x - positionX) / scale;
      const contentY = (y - positionY) / scale;
      setTransform(
        x - contentX * TOGGLE_SCALE,
        y - contentY * TOGGLE_SCALE,
        TOGGLE_SCALE,
        200,
        "easeOut",
      );
    };
    el.addEventListener("dblclick", onDblClick);
    return () => el.removeEventListener("dblclick", onDblClick);
  }, [instance, setTransform, resetTransform, targetRef]);
  return null;
}

/**
 * Full-screen lightbox for inspecting a Citation-Canvas figure at full
 * resolution: scroll to zoom, drag to pan, plus a control bar (zoom in/out,
 * fit, close). Portalled to `document.body` so it covers the whole viewport
 * regardless of the canvas column's width or any transformed ancestor (the
 * push-layout animates with `transform`, which would otherwise re-root a
 * `position:fixed` child). Esc, the close button, or a click on the dark
 * backdrop (anywhere that is NOT the figure) dismisses; clicking the figure
 * itself never dismisses.
 *
 * Zoom/pan mechanics are delegated to `react-zoom-pan-pinch` (pan, pinch,
 * bounds); only the wheel is hand-driven for a predictable step (see WheelZoom).
 */
export function ImageLightbox({ src, alt, onClose }: Props) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  // react-zoom-pan-pinch pans with pointer events and RETARGETS the trailing
  // click/pointer off the <img>, so the event's DOM target is unreliable for
  // "did the user hit the figure?". The one thing it can't fake is GEOMETRY:
  // record, on pointer-down, whether the press was inside the image's bounding
  // box and where it started; on pointer-up, dismiss only when the press began
  // OUTSIDE the figure AND barely moved (a click, not a pan). Pointer-up always
  // fires (unlike click, which the library may swallow), and capture phase runs
  // regardless of any propagation the library stops.
  const downRef = useRef<{ x: number; y: number; onImage: boolean } | null>(
    null,
  );

  // Esc closes; lock body scroll while open so wheel-zoom doesn't bleed through
  // to the page behind the overlay.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  return createPortal(
    <div
      ref={overlayRef}
      role="dialog"
      aria-modal="true"
      aria-label={alt ? `Image preview: ${alt}` : "Image preview"}
      className="fixed inset-0 z-[100] bg-black/80 backdrop-blur-sm"
      onPointerDownCapture={(e) => {
        // A press on a control (zoom/close) is handled by the control itself.
        if ((e.target as HTMLElement).closest("[data-lightbox-controls]")) {
          downRef.current = null;
          return;
        }
        const r = imgRef.current?.getBoundingClientRect();
        const onImage =
          !!r &&
          e.clientX >= r.left &&
          e.clientX <= r.right &&
          e.clientY >= r.top &&
          e.clientY <= r.bottom;
        downRef.current = { x: e.clientX, y: e.clientY, onImage };
      }}
      onPointerUpCapture={(e) => {
        const down = downRef.current;
        downRef.current = null;
        if (!down || down.onImage) return; // started on the figure → keep open
        const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
        if (moved > 6) return; // a drag / pan → keep open
        onClose(); // a genuine click on the backdrop → dismiss
      }}
    >
      <TransformWrapper
        initialScale={1}
        minScale={1}
        maxScale={8}
        centerOnInit
        wheel={{ disabled: true }}
        // Double-click is handled by DoubleClickZoom (zoom-to-point toggle); the
        // library's built-in modes can't do that, so disable its handler.
        doubleClick={{ disabled: true }}
      >
        <Controls onClose={onClose} />
        <WheelZoom targetRef={overlayRef} />
        <DoubleClickZoom targetRef={overlayRef} />
        <TransformComponent
          wrapperStyle={{ width: "100vw", height: "100vh" }}
          contentStyle={{ width: "100vw", height: "100vh" }}
        >
          <div className="flex h-full w-full items-center justify-center">
            <img
              ref={imgRef}
              src={src}
              alt={alt}
              className="max-h-[92vh] max-w-[92vw] select-none object-contain"
              draggable={false}
            />
          </div>
        </TransformComponent>
      </TransformWrapper>
    </div>,
    document.body,
  );
}
