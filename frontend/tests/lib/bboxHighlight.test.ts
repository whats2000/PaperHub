/**
 * F2.1 A2' — bbox geometric highlight tests
 *
 * Marker chunks carry an EXACT region: `page` (0-based absolute page index) +
 * `bbox` ([x0,y0,x1,y1] in PDF points, origin top-left, native page space).
 * The Citation Canvas draws the PDF highlight from this geometry — exact, no
 * text search. These tests pin the scaling math + the selection rule.
 */
import { describe, expect, it } from "vitest";
import {
  bboxToRect,
  hasGeometricRegion,
  markerPageToPageNumber,
} from "@/lib/bboxHighlight";

describe("bboxToRect", () => {
  it("scales a bbox from native PDF points to the rendered width (origin top-left)", () => {
    // originalWidth=612 (Letter), rendered=918 → scale=1.5
    // bbox [100,70,500,185] → left=150, top=105, width=600, height=172.5
    const rect = bboxToRect([100, 70, 500, 185], 612, 918);
    expect(rect.left).toBeCloseTo(150);
    expect(rect.top).toBeCloseTo(105);
    expect(rect.width).toBeCloseTo(600);
    expect(rect.height).toBeCloseTo(172.5);
  });

  it("is identity at scale 1 (rendered === original)", () => {
    const rect = bboxToRect([10, 20, 30, 50], 612, 612);
    expect(rect).toEqual({ left: 10, top: 20, width: 20, height: 30 });
  });

  it("handles a half-scale render", () => {
    const rect = bboxToRect([100, 200, 300, 400], 1000, 500); // scale 0.5
    expect(rect).toEqual({ left: 50, top: 100, width: 100, height: 100 });
  });
});

describe("markerPageToPageNumber", () => {
  it("converts a 0-based Marker page index to a 1-based react-pdf page number", () => {
    expect(markerPageToPageNumber(0)).toBe(1);
    expect(markerPageToPageNumber(4)).toBe(5);
  });
});

describe("hasGeometricRegion", () => {
  it("is true for a chunk with a length-4 bbox and a non-null page", () => {
    expect(hasGeometricRegion({ page: 0, bbox: [1, 2, 3, 4] })).toBe(true);
  });

  it("is false when bbox is null/undefined", () => {
    expect(hasGeometricRegion({ page: 0, bbox: null })).toBe(false);
    expect(hasGeometricRegion({ page: 0 })).toBe(false);
  });

  it("is false when page is null/undefined", () => {
    expect(hasGeometricRegion({ page: null, bbox: [1, 2, 3, 4] })).toBe(false);
    expect(hasGeometricRegion({ bbox: [1, 2, 3, 4] })).toBe(false);
  });

  it("is false when bbox is not length 4 (malformed)", () => {
    expect(hasGeometricRegion({ page: 0, bbox: [1, 2, 3] })).toBe(false);
    expect(hasGeometricRegion({ page: 0, bbox: [] })).toBe(false);
  });
});
