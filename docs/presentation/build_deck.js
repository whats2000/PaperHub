// PaperHub — Swiss modernist redesign. Pure white + ink black + cobalt blue.
// Arial Black + Helvetica/Calibri. Thick rules, big numerals, geometric blocks.
// Deliberately NOT Anthropic-coded (no cream, no terracotta, no Georgia italic).

const pptxgen = require("pptxgenjs");
const SHOT = "/sessions/practical-charming-pascal/mnt/PaperHub/docs/screenshots";

// ---------- Palette: Wired / IBM / Pentagram modernism ----------
const C = {
  white:      "FFFFFF",
  ink:        "0A0A0A",   // not pure black — slightly softer for screens
  inkSoft:    "1A1A1A",
  body:       "262626",
  muted:      "737373",   // neutral cool gray
  ruleSoft:   "D4D4D4",
  // ONE strong accent — cobalt blue (IBM/Wired). NOT teal, NOT terracotta.
  cobalt:     "0047AB",
  cobaltSoft: "DBEAFE",
  // Red used SPARINGLY for danger/pain only
  red:        "DC2626",
  // For ✓ / ✗ — keep tonal
  good:       "166534",
  // Dark slide
  dark:       "0A0A0A",
  ondark:     "FFFFFF",
  darkmuted:  "8A8A8A",
};

// Fonts: bold geometric sans for display, mono for technical, Chinese sans only.
const F = {
  display:  "Arial Black",         // huge English title moments
  bold:     "Arial Black",
  sans:     "Helvetica",           // body Latin
  zh:       "Microsoft JhengHei",  // Chinese body / display (with bold)
  mono:     "Consolas",
};

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
const W = 13.333, H = 7.5;
pres.author = "whats2000";
pres.title = "PaperHub — 專案介紹";
const TOTAL = 17;

// ---------- Helpers ----------

// Heavy black rule — the workhorse divider in Swiss style
function bar(slide, x, y, w, opts = {}) {
  slide.addShape(pres.shapes.LINE, {
    x, y, w, h: 0,
    line: { color: opts.color || C.ink, width: opts.width || 1.5 },
  });
}

// Page header — thick black bar + grid coordinates (1/14) + section name
function pageHeader(slide, pageNum, section, opts = {}) {
  const onDark = !!opts.onDark;
  const fg = onDark ? C.ondark : C.ink;
  const sub = onDark ? C.darkmuted : C.muted;
  // Top thick rule
  slide.addShape(pres.shapes.LINE, {
    x: 0.6, y: 0.42, w: W - 1.2, h: 0,
    line: { color: fg, width: 2 },
  });
  // Grid coords + section in a single line
  slide.addText(String(pageNum).padStart(2, "0") + " / " + TOTAL, {
    x: 0.6, y: 0.5, w: 1.5, h: 0.3,
    fontFace: F.mono, fontSize: 10, bold: true, color: fg, margin: 0,
  });
  if (section) {
    slide.addText(section, {
      x: 2.2, y: 0.52, w: 8, h: 0.3,
      fontFace: F.bold, fontSize: 9, color: fg, charSpacing: 4, margin: 0,
    });
  }
  // Right side: brand
  slide.addText("PAPERHUB", {
    x: W - 3.5, y: 0.52, w: 3, h: 0.3,
    fontFace: F.bold, fontSize: 9, color: fg, charSpacing: 6, align: "right", margin: 0,
  });
}

// Page footer — thin baseline + meta
function pageFooter(slide, pageNum, opts = {}) {
  const onDark = !!opts.onDark;
  const fg = onDark ? C.darkmuted : C.muted;
  slide.addShape(pres.shapes.LINE, {
    x: 0.6, y: H - 0.5, w: W - 1.2, h: 0,
    line: { color: onDark ? C.darkmuted : C.ruleSoft, width: 0.5 },
  });
  slide.addText("PAPERHUB · 專案介紹", {
    x: 0.6, y: H - 0.4, w: 8, h: 0.3,
    fontFace: F.mono, fontSize: 8.5, color: fg, charSpacing: 1, margin: 0,
  });
  slide.addText("PG. " + String(pageNum).padStart(2, "0"), {
    x: W - 2, y: H - 0.4, w: 1.4, h: 0.3,
    fontFace: F.mono, fontSize: 8.5, color: fg, align: "right", margin: 0,
  });
}

// ============================================================================
// SLIDE 1 — COVER (black, massive display sans)
// ============================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.dark };

  // Top thin bar — Swiss grid signature
  s.addShape(pres.shapes.LINE, {
    x: 0.6, y: 0.6, w: W - 1.2, h: 0, line: { color: C.ondark, width: 1.5 },
  });
  s.addText("01 / 14", {
    x: 0.6, y: 0.7, w: 2, h: 0.3, fontFace: F.mono, fontSize: 11, bold: true, color: C.ondark, margin: 0,
  });
  s.addText("COVER", {
    x: 2.2, y: 0.72, w: 6, h: 0.3, fontFace: F.bold, fontSize: 9, color: C.ondark, charSpacing: 5, margin: 0,
  });
  s.addText("2026.06.10", {
    x: W - 3, y: 0.72, w: 2.5, h: 0.3, fontFace: F.mono, fontSize: 11, bold: true, color: C.ondark, align: "right", margin: 0,
  });

  // Massive Arial Black title — Swiss / Wired feel
  s.addText("PAPER", {
    x: 0.6, y: 1.5, w: 11, h: 1.45,
    fontFace: F.display, fontSize: 128, color: C.ondark,
    charSpacing: -4, margin: 0,
  });
  s.addText("HUB.", {
    x: 0.6, y: 2.95, w: 11, h: 1.45,
    fontFace: F.display, fontSize: 128, color: C.cobalt,
    charSpacing: -4, margin: 0,
  });

  // Cobalt accent block — a thick solid block, not a thin line
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.6, y: 4.65, w: 0.5, h: 0.18,
    fill: { color: C.cobalt }, line: { color: C.cobalt, width: 0 },
  });

  // Chinese subtitle — clean sans, no italic
  s.addText("帶溯源的論文研究 AI 工作站", {
    x: 0.6, y: 4.95, w: 12, h: 0.55,
    fontFace: F.zh, fontSize: 26, bold: true, color: C.ondark, margin: 0,
  });
  s.addText("A PAPER-AWARE CHAT CLIENT WHERE EVERY CITED SENTENCE TRACES BACK TO ITS SOURCE.", {
    x: 0.6, y: 5.55, w: 12, h: 0.35,
    fontFace: F.bold, fontSize: 10, color: C.darkmuted, charSpacing: 3, margin: 0,
  });

  // Bottom thick rule + metadata in mono grid
  s.addShape(pres.shapes.LINE, {
    x: 0.6, y: 6.5, w: W - 1.2, h: 0, line: { color: C.ondark, width: 1.5 },
  });
  const meta = [
    { lab: "PRESENTER",  val: "whats2000" },
    { lab: "REPOSITORY", val: "github.com/whats2000/PaperHub" },
    { lab: "LICENSE",    val: "Apache 2.0" },
  ];
  const mw = (W - 1.2) / 3;
  for (let i = 0; i < meta.length; i++) {
    const mx = 0.6 + i * mw;
    s.addText(meta[i].lab, {
      x: mx, y: 6.65, w: mw, h: 0.25,
      fontFace: F.bold, fontSize: 8, color: C.darkmuted, charSpacing: 4, margin: 0,
    });
    s.addText(meta[i].val, {
      x: mx, y: 6.92, w: mw, h: 0.4,
      fontFace: F.mono, fontSize: 13, bold: true, color: C.ondark, margin: 0,
    });
  }

  s.addNotes("Swiss modernist cover：純黑背景、160pt Arial Black 巨型 PAPER/HUB.、鈷藍 accent block。\n\n刻意對比 Anthropic 的奶油 + 焦糖 + 襯線 italic 美學 — 我們選擇 Wired / IBM / Pentagram 那條線：冷峻、幾何、grid 感。\n\n開場：「研究者最大的不安是 AI 給的答案查不到出處 — 今天 10 分鐘示範 PaperHub 怎麼解。」");
}

// ============================================================================
// SLIDE 2 — PROBLEM (3 pain statements as numbered modernist stack)
// ============================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.white };
  pageHeader(s, 2, "PROBLEM · 我們要解的問題");

  // Big title — Microsoft JhengHei bold for Chinese display
  s.addText("研究者用 AI 整理論文，", {
    x: 0.6, y: 1.2, w: W - 1.2, h: 0.8,
    fontFace: F.zh, fontSize: 36, bold: true, color: C.ink, margin: 0,
  });
  s.addText([
    { text: "最大的", options: { fontFace: F.zh, fontSize: 36, bold: true, color: C.ink } },
    { text: "不安", options: { fontFace: F.zh, fontSize: 36, bold: true, color: C.cobalt } },
    { text: "是什麼？", options: { fontFace: F.zh, fontSize: 36, bold: true, color: C.ink } },
  ], { x: 0.6, y: 1.95, w: W - 1.2, h: 0.8, margin: 0 });

  // Subtitle
  s.addText("我們訪談了研究生、博士後與技術 reviewer — 三個痛點反覆出現。", {
    x: 0.6, y: 2.9, w: W - 1.2, h: 0.4,
    fontFace: F.zh, fontSize: 14, color: C.muted, margin: 0,
  });

  // Heavy bar separating header from content
  bar(s, 0.6, 3.5, W - 1.2, { width: 2 });

  // Three pain entries — compressed to fit above footer
  const pains = [
    { num: "01", tag: "TRUST GAP",
      t: "「我不知道這個答案從哪來。」",
      d: "AI 說「根據第三節」 — 但沒有連結回去。交給教授前都要再人工核對一次。" },
    { num: "02", tag: "OPAQUE ROUTING",
      t: "「不知道 AI 用哪個工具回我。」",
      d: "答錯時，到底是模型、提示、還是路由錯？黑盒讓研究者不敢用在重要場景。" },
    { num: "03", tag: "NOT REPRODUCIBLE",
      t: "「上次的對話沒辦法重現。」",
      d: "Demo 一次成功不算成功；可交付的研究流程必須可重複、可稽核、可給別人 review。" },
  ];
  let py = 3.65;
  for (const p of pains) {
    s.addText(p.num, {
      x: 0.6, y: py - 0.05, w: 1.6, h: 0.85,
      fontFace: F.display, fontSize: 40, color: C.cobalt, margin: 0,
    });
    s.addText(p.tag, {
      x: 2.3, y: py, w: 5, h: 0.25,
      fontFace: F.bold, fontSize: 9, color: C.muted, charSpacing: 4, margin: 0,
    });
    s.addText(p.t, {
      x: 2.3, y: py + 0.22, w: W - 2.9, h: 0.4,
      fontFace: F.zh, fontSize: 17, bold: true, color: C.ink, margin: 0,
    });
    s.addText(p.d, {
      x: 2.3, y: py + 0.65, w: W - 2.9, h: 0.32,
      fontFace: F.zh, fontSize: 11, color: C.body, margin: 0,
    });
    py += 1.02;
    bar(s, 0.6, py, W - 1.2, { width: 0.5 });
  }

  pageFooter(s, 2);
  s.addNotes("三個痛點以大鈷藍 Arial Black 數字 + 細小 ALL CAPS tag + 中文粗體引言 + 細節呈現。\n\nSwiss modernist 設計指紋：thick rules、cobalt 強色、孟非西斯式 grid 排版。\n\n敘事重點：強調這是「訪談得到的真實痛點」 — 不是工程想像。");
}

// ============================================================================
// SLIDE 3 — USE CASES (modernist horizontal ribbon — square markers, big nums)
// ============================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.white };
  pageHeader(s, 3, "JOURNEY · 使用者旅程");

  s.addText("研究流程，", {
    x: 0.6, y: 1.2, w: W - 1.2, h: 0.7,
    fontFace: F.zh, fontSize: 36, bold: true, color: C.ink, margin: 0,
  });
  s.addText([
    { text: "四個", options: { fontFace: F.zh, fontSize: 36, bold: true, color: C.cobalt } },
    { text: "明確的階段。", options: { fontFace: F.zh, fontSize: 36, bold: true, color: C.ink } },
  ], { x: 0.6, y: 1.9, w: W - 1.2, h: 0.7, margin: 0 });

  s.addText("每階段對「信任」的要求都不同 — 我們把它分開設計。", {
    x: 0.6, y: 2.75, w: W - 1.2, h: 0.4,
    fontFace: F.zh, fontSize: 13, color: C.muted, margin: 0,
  });

  // The ribbon — a thick black bar with 4 square station marks
  const ribbonY = 4.5;
  bar(s, 0.6, ribbonY, W - 1.2, { width: 2 });

  const stages = [
    { num: "01", en: "SEARCH",   t: "搜尋", d: "從 arXiv / Semantic Scholar 找論文，AI 看「能不能接上你已有的」" },
    { num: "02", en: "CURATE",   t: "收藏", d: "感興趣的留下；跨對話自動去重，永不重複下載或處理" },
    { num: "03", en: "DISCUSS",  t: "討論", d: "多論文一起問；每個引用都能反查到原文 — HTML 與 PDF 同步" },
    { num: "04", en: "GENERATE", t: "產出", d: "一句話「做投影片」→ 研討會等級 Beamer；講稿可獨立中英文" },
  ];
  const stepW = (W - 1.2) / 4;
  for (let i = 0; i < stages.length; i++) {
    const x = 0.6 + i * stepW;
    const st = stages[i];
    // Square station mark — cobalt filled, sits ON the bar
    s.addShape(pres.shapes.RECTANGLE, {
      x: x + 0.05 - 0.1, y: ribbonY - 0.1, w: 0.2, h: 0.2,
      fill: { color: C.cobalt }, line: { color: C.cobalt, width: 0 },
    });
    // BIG Arial Black number above
    s.addText(st.num, {
      x: x, y: ribbonY - 1.4, w: 1.5, h: 1.0,
      fontFace: F.display, fontSize: 60, color: C.ink, charSpacing: -2, margin: 0,
    });
    // English label — all caps small
    s.addText(st.en, {
      x: x, y: ribbonY + 0.2, w: stepW - 0.3, h: 0.3,
      fontFace: F.bold, fontSize: 10, color: C.cobalt, charSpacing: 5, margin: 0,
    });
    // Chinese stage name — big bold
    s.addText(st.t, {
      x: x, y: ribbonY + 0.5, w: stepW - 0.3, h: 0.45,
      fontFace: F.zh, fontSize: 22, bold: true, color: C.ink, margin: 0,
    });
    // Description
    s.addText(st.d, {
      x: x, y: ribbonY + 1.05, w: stepW - 0.3, h: 1.2,
      fontFace: F.zh, fontSize: 11, color: C.body, valign: "top", margin: 0,
    });
  }

  // Bottom: target users line
  bar(s, 0.6, H - 0.95, W - 1.2, { width: 0.5, color: C.ruleSoft });
  s.addText("FOR", {
    x: 0.6, y: H - 0.85, w: 0.6, h: 0.25,
    fontFace: F.bold, fontSize: 9, color: C.cobalt, charSpacing: 4, margin: 0,
  });
  s.addText("研究生   ·   博士後   ·   技術 reviewer   ·   企業 R&D / IP 團隊   ·   圖書館 / 教育機構", {
    x: 1.2, y: H - 0.85, w: W - 2.0, h: 0.25,
    fontFace: F.zh, fontSize: 12, color: C.ink, margin: 0,
  });

  pageFooter(s, 3);
  s.addNotes("Swiss modernist ribbon：thick black bar + cobalt 方塊 station + 60pt Arial Black 數字。\n\n比 v2 的編輯式 circular ribbon 更冷峻、更幾何。\n\n強調：把流程分四階段是因為每階段的「信任要求」不同。");
}

// ============================================================================
// SLIDE 4 — FOUR PROMISES (big "4" + numbered list, modernist grid)
// ============================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.white };
  pageHeader(s, 4, "SOLUTION · 解決方案");

  // LEFT: massive "4" + label — sized so glyph + descender fit cleanly above the label
  s.addText("4", {
    x: 0.6, y: 1.4, w: 5, h: 4.3,
    fontFace: F.display, fontSize: 320, color: C.ink, charSpacing: -10, margin: 0, valign: "top",
  });

  // Cobalt accent block
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.6, y: 5.95, w: 0.6, h: 0.18,
    fill: { color: C.cobalt }, line: { color: C.cobalt, width: 0 },
  });
  s.addText("FOUR PROMISES", {
    x: 0.6, y: 6.2, w: 5, h: 0.3,
    fontFace: F.bold, fontSize: 11, color: C.ink, charSpacing: 5, margin: 0,
  });
  s.addText("PaperHub 給研究者的四個承諾", {
    x: 0.6, y: 6.55, w: 5, h: 0.4,
    fontFace: F.zh, fontSize: 16, bold: true, color: C.ink, margin: 0,
  });

  // Vertical thick rule separator
  s.addShape(pres.shapes.LINE, {
    x: 6.0, y: 1.2, w: 0, h: 5.6, line: { color: C.ink, width: 1.5 },
  });

  // RIGHT: 4 promises in modernist numbered list
  const promises = [
    { i: "01", t: "看得到根據",   en: "CITATION CANVAS",         d: "答案中每個 [chunk:N] 都能點 — 自動跳到 HTML 與 PDF 對應段落，同步 highlight。" },
    { i: "02", t: "讀得懂全文",   en: "AGENTIC HIERARCHICAL QA", d: "每篇論文派一位「閱讀員」沿章節大綱讀；旗艦模型只看被引用段落做跨論文綜合。" },
    { i: "03", t: "做得出簡報",   en: "BEAMER SLIDE PIPELINE",   d: "「做一份 20 分鐘演講」→ 真實圖表的研討會簡報；講稿與投影片解耦，可單頁編輯。" },
    { i: "04", t: "記得住偏好",   en: "LIBRARY & MEMORY",        d: "自然語言查資料庫並顯示 SQL；「以後用繁中回我」跨對話記住，且能反悔。" },
  ];
  let py = 1.3;
  for (const p of promises) {
    s.addText(p.i, {
      x: 6.3, y: py, w: 0.85, h: 0.55,
      fontFace: F.display, fontSize: 28, color: C.cobalt, margin: 0,
    });
    s.addText(p.t, {
      x: 7.25, y: py - 0.05, w: 5.5, h: 0.45,
      fontFace: F.zh, fontSize: 20, bold: true, color: C.ink, margin: 0,
    });
    s.addText(p.en, {
      x: 7.25, y: py + 0.4, w: 5.5, h: 0.25,
      fontFace: F.bold, fontSize: 9, color: C.muted, charSpacing: 4, margin: 0,
    });
    s.addText(p.d, {
      x: 7.25, y: py + 0.68, w: 5.5, h: 0.55,
      fontFace: F.zh, fontSize: 11.5, color: C.body, valign: "top", margin: 0,
    });
    py += 1.4;
    if (p !== promises[promises.length - 1]) {
      bar(s, 6.3, py - 0.08, 6.6, { color: C.ruleSoft, width: 0.5 });
    }
  }

  pageFooter(s, 4);
  s.addNotes("極端 modernist 排版：左邊是一個 480pt 的「4」幾乎佔整個左欄 — Wired magazine cover 等級的 typographic moment。\n\n右邊四個 numbered promise — Arial Black 數字 + Chinese bold 標題 + ALL CAPS 英文標籤 + 描述。\n\n沒有 card、沒有彩色 fill、只有 hairline rule 分隔。");
}

// ============================================================================
// SLIDE 5 — SYSTEM ARCHITECTURE (六層架構)
// ============================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.white };
  pageHeader(s, 5, "ARCHITECTURE · 系統架構");

  s.addText([
    { text: "不只是 chat — ", options: { fontFace: F.zh, fontSize: 30, bold: true, color: C.ink } },
    { text: "六層架構，各司其職。", options: { fontFace: F.zh, fontSize: 30, bold: true, color: C.cobalt } },
  ], { x: 0.6, y: 1.2, w: W - 1.2, h: 0.7, margin: 0 });
  s.addText("SYSTEM ARCHITECTURE — EVERY LAYER REPLACEABLE · EVERY CROSS-LAYER CALL AUDITED", {
    x: 0.6, y: 2.0, w: W - 1.2, h: 0.3,
    fontFace: F.bold, fontSize: 10, color: C.muted, charSpacing: 3, margin: 0,
  });

  bar(s, 0.6, 2.55, W - 1.2, { width: 1.5 });

  // ────────────────────────────────────────────────────────────────────────
  // SYSTEM ARCHITECTURE DIAGRAM — box-and-arrows
  // ────────────────────────────────────────────────────────────────────────
  // Helpers: a labeled box, an arrow (line + arrowhead via LINE end-arrow)
  const drawBox = (x, y, w, h, opts) => {
    const fill = opts.fill || C.white;
    const border = opts.border || C.ink;
    s.addShape(pres.shapes.RECTANGLE, {
      x, y, w, h, fill: { color: fill }, line: { color: border, width: opts.borderW || 1 },
    });
    if (opts.tag) {
      s.addText(opts.tag, {
        x: x + 0.08, y: y + 0.06, w: w - 0.16, h: 0.22,
        fontFace: F.bold, fontSize: 8, color: opts.tagColor || C.muted, charSpacing: 3, margin: 0,
      });
    }
    if (opts.title) {
      s.addText(opts.title, {
        x: x + 0.08, y: y + (opts.tag ? 0.28 : 0.08), w: w - 0.16, h: opts.titleH || 0.32,
        fontFace: opts.titleFont || F.bold, fontSize: opts.titleSize || 13,
        color: opts.titleColor || C.ink, valign: "middle", margin: 0,
      });
    }
    if (opts.sub) {
      s.addText(opts.sub, {
        x: x + 0.08, y: y + h - 0.32, w: w - 0.16, h: 0.28,
        fontFace: opts.subFont || F.mono, fontSize: opts.subSize || 8.5,
        color: opts.subColor || C.cobalt, valign: "middle", margin: 0,
      });
    }
  };
  const arrow = (x1, y1, x2, y2, color) => {
    s.addShape(pres.shapes.LINE, {
      x: x1, y: y1, w: x2 - x1, h: y2 - y1,
      line: { color: color || C.ink, width: 1.2, endArrowType: "triangle" },
    });
  };

  // === ROW 1: User → UI ===================================================
  const userX = 0.6, userY = 2.85, userW = 1.3, userH = 0.55;
  drawBox(userX, userY, userW, userH, {
    fill: C.ink, border: C.ink,
    title: "USER", titleColor: C.white, titleFont: F.bold, titleSize: 11,
  });
  // arrow user → UI
  arrow(userX + userW, userY + userH/2, userX + userW + 0.5, userY + userH/2);

  // UI Layer — wide box right of user
  const uiX = 2.5, uiY = 2.85, uiW = W - 0.6 - uiX, uiH = 0.95;
  drawBox(uiX, uiY, uiW, uiH, {
    tag: "I · UI LAYER", tagColor: C.cobalt,
    title: "React · Vite · Tailwind · Zustand", titleSize: 12,
    sub: "Composer  ·  Routing Badge  ·  Trace Panel  ·  Citation Canvas  ·  Slides Panel  ·  Memory Manager",
  });

  // === ARROW: UI → Orchestrator ==========================================
  arrow(uiX + uiW/2, uiY + uiH, uiX + uiW/2, uiY + uiH + 0.35, C.cobalt);
  s.addText("SSE / REST  ·  /chat", {
    x: uiX + uiW/2 + 0.08, y: uiY + uiH + 0.05, w: 2, h: 0.25,
    fontFace: F.mono, fontSize: 8, color: C.cobalt, margin: 0,
  });

  // === ROW 2: Orchestrator (big central box containing Router + 6 agents) =
  const orchX = 0.6, orchY = 4.18, orchW = 9.0, orchH = 1.55;
  drawBox(orchX, orchY, orchW, orchH, {
    tag: "II · ORCHESTRATOR · FASTAPI + LANGGRAPH", tagColor: C.cobalt,
  });
  // Router pill inside orchestrator
  const rtX = orchX + 0.25, rtY = orchY + 0.4, rtW = 1.5, rtH = 0.4;
  s.addShape(pres.shapes.RECTANGLE, {
    x: rtX, y: rtY, w: rtW, h: rtH,
    fill: { color: C.ink }, line: { color: C.ink, width: 0 },
  });
  s.addText("ROUTER", {
    x: rtX, y: rtY, w: rtW, h: rtH,
    fontFace: F.bold, fontSize: 11, color: C.white, charSpacing: 3,
    align: "center", valign: "middle", margin: 0,
  });
  s.addText("intent classifier · history-aware", {
    x: rtX, y: rtY + rtH + 0.02, w: rtW, h: 0.22,
    fontFace: F.mono, fontSize: 7.5, color: C.muted, align: "center", margin: 0,
  });
  // 6 agent pills in a row
  const agents = ["paper_qa", "paper_search", "slides", "library_stats", "memory", "chitchat"];
  const agX0 = rtX + rtW + 0.4, agY = orchY + 0.4;
  const agW = (orchW - (agX0 - orchX) - 0.25) / agents.length - 0.06;
  for (let i = 0; i < agents.length; i++) {
    const ax = agX0 + i * (agW + 0.06);
    s.addShape(pres.shapes.RECTANGLE, {
      x: ax, y: agY, w: agW, h: 0.4,
      fill: { color: C.cobaltSoft }, line: { color: C.cobalt, width: 0.75 },
    });
    s.addText(agents[i], {
      x: ax, y: agY, w: agW, h: 0.4,
      fontFace: F.mono, fontSize: 8.5, bold: true, color: C.cobalt,
      align: "center", valign: "middle", margin: 0,
    });
  }
  s.addText("6 SPECIALIST AGENTS · DISJOINT TOOL PALETTES", {
    x: agX0, y: agY + 0.45, w: orchW - (agX0 - orchX) - 0.25, h: 0.22,
    fontFace: F.bold, fontSize: 7.5, color: C.muted, charSpacing: 3, align: "center", margin: 0,
  });
  // small arrow from router to first agent
  arrow(rtX + rtW + 0.05, rtY + rtH/2, agX0 - 0.05, rtY + rtH/2);

  // === SIDE: SQLite/audit (vertical block on the right) ==================
  const dbX = orchX + orchW + 0.3, dbY = orchY, dbW = W - 0.6 - dbX, dbH = orchH;
  drawBox(dbX, dbY, dbW, dbH, {
    fill: C.ink, border: C.ink,
    tag: "VI · DATABASE", tagColor: C.cobalt,
    title: "SQLite", titleColor: C.white, titleSize: 16,
    sub: "11 tables  ·  audit log  ·  replayable",
    subColor: C.cobaltSoft,
  });
  // Bidirectional arrow Orchestrator ↔ DB
  s.addShape(pres.shapes.LINE, {
    x: orchX + orchW + 0.02, y: orchY + orchH/2, w: dbX - orchX - orchW - 0.04, h: 0,
    line: { color: C.ink, width: 1.2, beginArrowType: "triangle", endArrowType: "triangle" },
  });

  // === ROW 3: Three downstream layers — LLM / RAG / MCP ==================
  const dsY = 5.93, dsH = 0.95;
  const dsW = (orchW - 0.4) / 3;
  const dsCells = [
    { tag: "III · LLM",       title: "LiteLLM Adapter",   sub: "GEMINI · OPENAI · ANTHROPIC · OLLAMA" },
    { tag: "IV · RAG / KB",   title: "Chroma + Reranker", sub: "BGE-SMALL · CROSS-ENCODER · sibling proc." },
    { tag: "V · MCP LAYER",   title: "Scope-Gated Tools", sub: "papers · sql · memory · websearch · marker" },
  ];
  for (let i = 0; i < 3; i++) {
    const dx = orchX + i * (dsW + 0.2);
    drawBox(dx, dsY, dsW, dsH, {
      tag: dsCells[i].tag, tagColor: C.cobalt,
      title: dsCells[i].title, titleSize: 11,
      sub: dsCells[i].sub, subSize: 8,
    });
    // arrow from orchestrator to this cell
    arrow(dx + dsW/2, orchY + orchH, dx + dsW/2, dsY, C.ink);
  }

  // Footer principle line (replaces the previous bar+text)
  s.addText("DESIGN PRINCIPLE", {
    x: 0.6, y: H - 0.45, w: 2.5, h: 0.25,
    fontFace: F.bold, fontSize: 9, color: C.cobalt, charSpacing: 4, margin: 0,
  });
  s.addText("每個跨層呼叫都先寫 tool_calls — 整段對話可單純從 SQLite 重播。", {
    x: 3.0, y: H - 0.45, w: W - 3.6, h: 0.25,
    fontFace: F.zh, fontSize: 10.5, bold: true, color: C.ink, margin: 0,
  });

  // pageFooter omitted: custom DESIGN PRINCIPLE line serves as footer
  s.addNotes("【AI 系統設計完整性】這一頁說明系統設計。\n\n六層架構強調：\n1. 每一層都可獨立替換（換 LLM provider、換 vector store、換 MCP 都不動其他層）\n2. Tiered LLM 策略 — 小模型跑 subagent，旗艦模型做最後綜合，成本與品質平衡\n3. MCP 是所有外部呼叫的統一入口，scope 檢查在 orchestrator 而不是 server 內\n4. SQLite 同時當 application state + audit log — 一個檔案完整重現任何對話\n\nDesign principle 強調 audit-first 設計 — 這是 Anthropic 風格的 spec-driven engineering，不是 prototype quality。");
}

// ============================================================================
// SLIDE 6 — COMPARISON TABLE (modernist with thick rules, cobalt accent column)
// ============================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.white };
  pageHeader(s, 6, "POSITIONING · 市場定位");

  s.addText("與市面工具相比，", {
    x: 0.6, y: 1.2, w: W - 1.2, h: 0.7,
    fontFace: F.zh, fontSize: 32, bold: true, color: C.ink, margin: 0,
  });
  s.addText([
    { text: "我們的", options: { fontFace: F.zh, fontSize: 32, bold: true, color: C.ink } },
    { text: "差異", options: { fontFace: F.zh, fontSize: 32, bold: true, color: C.cobalt } },
    { text: "在哪？", options: { fontFace: F.zh, fontSize: 32, bold: true, color: C.ink } },
  ], { x: 0.6, y: 1.85, w: W - 1.2, h: 0.7, margin: 0 });
  s.addText("並非取代 ChatGPT — 而是把它沒做好的「研究級信任」補上。", {
    x: 0.6, y: 2.7, w: W - 1.2, h: 0.4,
    fontFace: F.zh, fontSize: 13, color: C.muted, margin: 0,
  });

  // Table — modernist: thick top/bottom rules, no cell fills except PaperHub column tinted
  const tx = 0.6, ty = 3.4;
  const colWs = [5.3, 1.5, 1.7, 1.5, 1.7];
  const totalW = colWs.reduce((a, b) => a + b, 0);
  const rowH = 0.36;

  // Cobalt tint behind PaperHub column (full height of table)
  const tableTotalRows = 8;
  s.addShape(pres.shapes.RECTANGLE, {
    x: tx + colWs[0] + colWs[1] + colWs[2] + colWs[3], y: ty - 0.04,
    w: colWs[4], h: rowH * tableTotalRows + 0.16,
    fill: { color: C.cobaltSoft }, line: { color: C.cobaltSoft, width: 0 },
  });

  // Top thick rule
  bar(s, tx, ty - 0.04, totalW, { width: 2 });

  // Headers
  const headers = ["", "ChatGPT", "NotebookLM", "PaperQA", "PAPERHUB"];
  let hx = tx;
  for (let i = 0; i < headers.length; i++) {
    const isUs = i === headers.length - 1;
    s.addText(headers[i], {
      x: hx, y: ty + 0.05, w: colWs[i], h: rowH,
      fontFace: isUs ? F.display : F.bold,
      fontSize: isUs ? 13 : 11,
      color: isUs ? C.cobalt : C.ink,
      align: i === 0 ? "left" : "center",
      valign: "middle", margin: 0,
      charSpacing: isUs ? 2 : 3,
    });
    hx += colWs[i];
  }
  bar(s, tx, ty + rowH + 0.08, totalW, { width: 1 });

  // Rows
  const rows = [
    ["引用可反查到原文段落",       "✗", "△", "△", "✓✓"],
    ["HTML + PDF 雙視圖 highlight", "✗", "✗", "✗", "✓"],
    ["每篇論文獨立深讀（非 top-k）","✗", "△", "△", "✓"],
    ["可重播的對話稽核紀錄",        "✗", "✗", "✗", "✓"],
    ["研討會級投影片 + 講稿產出",   "△", "✗", "✗", "✓"],
    ["跨對話記憶 + 衝突自動覆寫",   "△", "✗", "✗", "✓"],
    ["本機跑、無雲端依賴",          "✗", "✗", "✓", "✓"],
  ];
  let ry = ty + rowH + 0.12;
  for (let r = 0; r < rows.length; r++) {
    let cx = tx;
    for (let c = 0; c < rows[r].length; c++) {
      const v = rows[r][c];
      const isUs = c === rows[r].length - 1;
      let color = C.body;
      let bold = false;
      if (c > 0) {
        color = v === "✗" ? "BBBBBB" : (v === "△" ? "A16207" : (isUs ? C.cobalt : C.good));
        bold = isUs;
      }
      s.addText(v, {
        x: cx, y: ry, w: colWs[c], h: rowH,
        fontFace: c === 0 ? F.zh : F.display,
        fontSize: c === 0 ? 12 : 15,
        bold: bold || (c === 0),
        color: c === 0 ? C.ink : color,
        align: c === 0 ? "left" : "center",
        valign: "middle", margin: 0,
      });
      cx += colWs[c];
    }
    ry += rowH;
    if (r < rows.length - 1) bar(s, tx, ry, totalW, { width: 0.4, color: C.ruleSoft });
  }
  bar(s, tx, ry, totalW, { width: 2 });

  // Conclusion
  s.addText("結論", {
    x: 0.6, y: H - 0.95, w: 0.7, h: 0.3,
    fontFace: F.bold, fontSize: 10, color: C.cobalt, charSpacing: 4, margin: 0,
  });
  s.addText("在「可信任 × 學術級 × 隱私」這個交集，市面上沒有同等對手。", {
    x: 1.4, y: H - 0.95, w: W - 2.0, h: 0.3,
    fontFace: F.zh, fontSize: 13, bold: true, color: C.ink, margin: 0,
  });

  pageFooter(s, 6);
  s.addNotes("Modernist 表格：thick rules + 鈷藍 PaperHub 欄背景 tint + Arial Black header。\n\n為什麼鈷藍襯？比厚黑 fill 更精緻；對比 v2 的綠色 tint 也更專業。\n\n強調「PaperHub」用 ARIAL BLACK 與其他競品的 helvetica regular 形成 typography contrast。");
}

// ============================================================================
// SLIDE 6 — CITATION CANVAS (modernist demo spread)
// ============================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.white };
  pageHeader(s, 7, "DEMO I · CITATION CANVAS");

  s.addText([
    { text: "點任意引用，", options: { fontFace: F.zh, fontSize: 30, bold: true, color: C.ink } },
    { text: "回到原文那一段。", options: { fontFace: F.zh, fontSize: 30, bold: true, color: C.cobalt } },
  ], { x: 0.6, y: 1.2, w: W - 1.2, h: 0.7, margin: 0 });
  s.addText("CITATION CANVAS · HTML 與 PDF 雙視圖同步 HIGHLIGHT", {
    x: 0.6, y: 2.0, w: W - 1.2, h: 0.3,
    fontFace: F.bold, fontSize: 10, color: C.muted, charSpacing: 3, margin: 0,
  });

  bar(s, 0.6, 2.45, W - 1.2, { width: 1.5 });

  // Two screenshots — slightly smaller so captions fit cleanly above footer
  const imgW = 5.7, imgH = 3.4;
  s.addImage({
    path: SHOT + "/04-citation-canvas-html.png",
    x: 0.6, y: 2.7, w: imgW, h: imgH, sizing: { type: "contain", w: imgW, h: imgH },
  });
  s.addImage({
    path: SHOT + "/05-citation-canvas-pdf.png",
    x: 0.6 + imgW + 0.4, y: 2.7, w: imgW, h: imgH, sizing: { type: "contain", w: imgW, h: imgH },
  });

  // Figure captions — combined into one tight row for each side
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.6, y: 6.25, w: 0.3, h: 0.05,
    fill: { color: C.cobalt }, line: { color: C.cobalt, width: 0 },
  });
  s.addText("FIG. 01 — RENDERED HTML", {
    x: 0.95, y: 6.2, w: imgW, h: 0.25,
    fontFace: F.bold, fontSize: 9, color: C.ink, charSpacing: 3, margin: 0,
  });
  s.addText("精準 highlight 至段落級。", {
    x: 0.6, y: 6.45, w: imgW, h: 0.3,
    fontFace: F.zh, fontSize: 12, color: C.body, margin: 0,
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.6 + imgW + 0.4, y: 6.25, w: 0.3, h: 0.05,
    fill: { color: C.cobalt }, line: { color: C.cobalt, width: 0 },
  });
  s.addText("FIG. 02 — SOURCE PDF", {
    x: 0.95 + imgW + 0.4, y: 6.2, w: imgW, h: 0.25,
    fontFace: F.bold, fontSize: 9, color: C.ink, charSpacing: 3, margin: 0,
  });
  s.addText("原檔同步，座標精準對齊。", {
    x: 0.6 + imgW + 0.4, y: 6.45, w: imgW, h: 0.3,
    fontFace: F.zh, fontSize: 12, color: C.body, margin: 0,
  });

  pageFooter(s, 7);
  s.addNotes("Demo I：旗艦功能 Citation Canvas。雙圖 + cobalt 方塊標記 + Arial Black 全大寫 FIG. 標籤。\n\nDemo 流程：點任意 [chunk:N] → 右側 panel 自動跳到段落並 highlight → 切到 PDF tab 同段也亮。\n\n強調：不是 substring search，是 ingest 時注入的精準 ID。");
}

// ============================================================================
// SLIDE 7 — AGENTIC QA (process steps + screenshot)
// ============================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.white };
  pageHeader(s, 8, "DEMO II · AGENTIC QA");

  s.addText([
    { text: "讀懂全文，", options: { fontFace: F.zh, fontSize: 30, bold: true, color: C.ink } },
    { text: "不是猜片段。", options: { fontFace: F.zh, fontSize: 30, bold: true, color: C.cobalt } },
  ], { x: 0.6, y: 1.2, w: W - 1.2, h: 0.7, margin: 0 });
  s.addText("AGENTIC HIERARCHICAL RETRIEVAL — 模仿真人研究員閱讀流程", {
    x: 0.6, y: 2.0, w: W - 1.2, h: 0.3,
    fontFace: F.bold, fontSize: 10, color: C.muted, charSpacing: 3, margin: 0,
  });

  bar(s, 0.6, 2.55, W - 1.2, { width: 1.5 });

  // LEFT — process steps with Arial Black numbers
  const lx = 0.6, lw = 5.6;
  const steps = [
    { i: "01", t: "派員",     d: "每篇 enabled 論文派一位「閱讀員」（小模型）" },
    { i: "02", t: "看目錄",   d: "閱讀員不亂翻 — 先列章節大綱，再挑相關段落" },
    { i: "03", t: "標證據",   d: "閱讀員回報「這問題用到第 3、7、12 段」+ chunk ID" },
    { i: "04", t: "跨篇綜合", d: "旗艦模型只讀被標的段落，做跨論文比較" },
  ];
  let py = 2.85;
  for (const st of steps) {
    s.addText(st.i, {
      x: lx, y: py, w: 1.0, h: 0.55,
      fontFace: F.display, fontSize: 26, color: C.cobalt, margin: 0,
    });
    s.addText(st.t, {
      x: lx + 1.0, y: py - 0.02, w: lw - 1.0, h: 0.45,
      fontFace: F.zh, fontSize: 19, bold: true, color: C.ink, margin: 0,
    });
    s.addText(st.d, {
      x: lx + 1.0, y: py + 0.42, w: lw - 1.0, h: 0.4,
      fontFace: F.zh, fontSize: 11.5, color: C.body, margin: 0,
    });
    py += 0.95;
    if (st !== steps[steps.length - 1]) bar(s, lx, py - 0.04, lw, { color: C.ruleSoft, width: 0.5 });
  }

  // RIGHT — screenshot (slightly shorter so figure caption fits above footer)
  s.addImage({
    path: SHOT + "/06-language-adherence.png",
    x: 6.6, y: 2.85, w: 6.2, h: 3.7, sizing: { type: "contain", w: 6.2, h: 3.7 },
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 6.6, y: 6.65, w: 0.3, h: 0.05,
    fill: { color: C.cobalt }, line: { color: C.cobalt, width: 0 },
  });
  s.addText("FIG. 03 — 中文問題 / 中文回答，CITATION MARKERS 原樣保留", {
    x: 6.95, y: 6.6, w: 6, h: 0.25,
    fontFace: F.bold, fontSize: 9, color: C.ink, charSpacing: 2, margin: 0,
  });

  pageFooter(s, 8);
  s.addNotes("Demo II：階層式檢索。左邊四步驟（Arial Black 數字 + Chinese bold 標題 + 描述），右邊截圖證明跨語言保真。\n\n強調對 v.s. 一般 top-k RAG：我們模仿真人研究員流程（看大綱 → 挑段落 → 跨篇綜合），不是盲目 cosine similarity。");
}

// ============================================================================
// SLIDE 8 — SLIDE PIPELINE
// ============================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.white };
  pageHeader(s, 9, "DEMO III · SLIDE PIPELINE");

  s.addText([
    { text: "從論文到簡報，", options: { fontFace: F.zh, fontSize: 30, bold: true, color: C.ink } },
    { text: "一句話。", options: { fontFace: F.zh, fontSize: 30, bold: true, color: C.cobalt } },
  ], { x: 0.6, y: 1.2, w: W - 1.2, h: 0.7, margin: 0 });
  s.addText("BEAMER SLIDE PIPELINE — 投影片 / 講稿解耦、可單頁編輯", {
    x: 0.6, y: 2.0, w: W - 1.2, h: 0.3,
    fontFace: F.bold, fontSize: 10, color: C.muted, charSpacing: 3, margin: 0,
  });

  bar(s, 0.6, 2.45, W - 1.2, { width: 1.5 });

  const imgW = 5.7, imgH = 3.0;
  s.addImage({
    path: SHOT + "/11-slides-generate.png",
    x: 0.6, y: 2.7, w: imgW, h: imgH, sizing: { type: "contain", w: imgW, h: imgH },
  });
  s.addImage({
    path: SHOT + "/12-slides-notes-added.png",
    x: 0.6 + imgW + 0.4, y: 2.7, w: imgW, h: imgH, sizing: { type: "contain", w: imgW, h: imgH },
  });

  s.addShape(pres.shapes.RECTANGLE, { x: 0.6, y: 5.8, w: 0.3, h: 0.05, fill: { color: C.cobalt }, line: { color: C.cobalt, width: 0 } });
  s.addText("FIG. 04 — 「做 20 分鐘演講」→ 直接產出 BEAMER", {
    x: 0.95, y: 5.75, w: imgW, h: 0.25,
    fontFace: F.bold, fontSize: 9, color: C.ink, charSpacing: 2, margin: 0,
  });
  s.addShape(pres.shapes.RECTANGLE, { x: 0.6 + imgW + 0.4, y: 5.8, w: 0.3, h: 0.05, fill: { color: C.cobalt }, line: { color: C.cobalt, width: 0 } });
  s.addText("FIG. 05 — 「加講稿用繁中」→ 只重寫講稿，不動投影片", {
    x: 0.95 + imgW + 0.4, y: 5.75, w: imgW, h: 0.25,
    fontFace: F.bold, fontSize: 9, color: C.ink, charSpacing: 2, margin: 0,
  });

  // Three contracts in modernist inline numbered row (fits above footer)
  bar(s, 0.6, 6.1, W - 1.2, { color: C.ruleSoft, width: 0.5 });
  const contracts = [
    { n: "01", t: "簡潔投影片 / 豐富講稿", d: "符合真實會議標準" },
    { n: "02", t: "絕不引用不存在的圖表",   d: "DETERMINISTIC VERIFY 自動替換" },
    { n: "03", t: "排版錯誤自動修",         d: "OVERFULL 觸發 REVISE 重編譯" },
  ];
  let cx2 = 0.6;
  const ccw = (W - 1.2) / 3;
  for (const ct of contracts) {
    s.addText(ct.n, {
      x: cx2, y: 6.25, w: 0.8, h: 0.4,
      fontFace: F.display, fontSize: 20, color: C.cobalt, margin: 0,
    });
    s.addText(ct.t, {
      x: cx2 + 0.8, y: 6.22, w: ccw - 0.8, h: 0.3,
      fontFace: F.zh, fontSize: 12.5, bold: true, color: C.ink, margin: 0,
    });
    s.addText(ct.d, {
      x: cx2 + 0.8, y: 6.5, w: ccw - 0.8, h: 0.25,
      fontFace: F.zh, fontSize: 9.5, color: C.muted, margin: 0,
    });
    cx2 += ccw;
  }

  pageFooter(s, 9);
  s.addNotes("Demo III：投影片 pipeline。兩張對比 demo + 三大硬約束。\n\nv2.21 重點：生成與講稿解耦 — GENERATE 只產投影片；NOTES 是 opt-in；EDIT 只動目標 frame，不重編譯。");
}

// ============================================================================
// SLIDE 9 — LIBRARY + MEMORY
// ============================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.white };
  pageHeader(s, 10, "DEMO IV · LIBRARY & MEMORY");

  s.addText([
    { text: "圖書館自己整理，", options: { fontFace: F.zh, fontSize: 30, bold: true, color: C.ink } },
    { text: "記憶會學你。", options: { fontFace: F.zh, fontSize: 30, bold: true, color: C.cobalt } },
  ], { x: 0.6, y: 1.2, w: W - 1.2, h: 0.7, margin: 0 });
  s.addText("LIBRARY INTELLIGENCE & MEMORY GOVERNANCE", {
    x: 0.6, y: 2.0, w: W - 1.2, h: 0.3,
    fontFace: F.bold, fontSize: 10, color: C.muted, charSpacing: 3, margin: 0,
  });

  bar(s, 0.6, 2.45, W - 1.2, { width: 1.5 });

  const imgW = 5.7, imgH = 3.2;
  s.addImage({
    path: SHOT + "/09-library-stats-sql.png",
    x: 0.6, y: 2.7, w: imgW, h: imgH, sizing: { type: "contain", w: imgW, h: imgH },
  });
  s.addImage({
    path: SHOT + "/10-memory-manager.png",
    x: 0.6 + imgW + 0.4, y: 2.7, w: imgW, h: imgH, sizing: { type: "contain", w: imgW, h: imgH },
  });

  s.addShape(pres.shapes.RECTANGLE, { x: 0.6, y: 6.0, w: 0.3, h: 0.05, fill: { color: C.cobalt }, line: { color: C.cobalt, width: 0 } });
  s.addText("FIG. 06 — NL → SQL：答案 + 真實執行的 SQL", {
    x: 0.95, y: 5.95, w: imgW, h: 0.25,
    fontFace: F.bold, fontSize: 9, color: C.ink, charSpacing: 2, margin: 0,
  });
  s.addShape(pres.shapes.RECTANGLE, { x: 0.6 + imgW + 0.4, y: 6.0, w: 0.3, h: 0.05, fill: { color: C.cobalt }, line: { color: C.cobalt, width: 0 } });
  s.addText("FIG. 07 — MEMORY MANAGER：SCOPE + SUPERSEDE 鏈", {
    x: 0.95 + imgW + 0.4, y: 5.95, w: imgW, h: 0.25,
    fontFace: F.bold, fontSize: 9, color: C.ink, charSpacing: 2, margin: 0,
  });

  bar(s, 0.6, 6.3, W - 1.2, { color: C.ruleSoft, width: 0.5 });
  s.addText("TWO SAFETY GATES", {
    x: 0.6, y: 6.42, w: 3, h: 0.3,
    fontFace: F.bold, fontSize: 9, color: C.cobalt, charSpacing: 4, margin: 0,
  });
  s.addText("AI 寫的 SQL 受 sqlglot AST + verb/table allowlist 規則檢查；記憶系統會主動拒絕 API KEY / 密碼 / PII 寫入，即使使用者明確要求。", {
    x: 0.6, y: 6.7, w: W - 1.2, h: 0.35,
    fontFace: F.zh, fontSize: 11, color: C.ink, margin: 0,
  });

  pageFooter(s, 10);
  s.addNotes("Demo IV：圖書館智慧與記憶治理。\n\n強調「規則 100% 不是 LLM 自查」這個技術判斷 — 對 enterprise audit / compliance 是關鍵差異化。");
}

// ============================================================================
// SLIDE 11 — AI TECH DEPTH (4 key design decisions)
// ============================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.white };
  pageHeader(s, 11, "DEPTH · AI 技術深度");

  s.addText([
    { text: "不只是用 LLM — ", options: { fontFace: F.zh, fontSize: 30, bold: true, color: C.ink } },
    { text: "知道何時不用。", options: { fontFace: F.zh, fontSize: 30, bold: true, color: C.cobalt } },
  ], { x: 0.6, y: 1.2, w: W - 1.2, h: 0.7, margin: 0 });
  s.addText("FOUR DESIGN DECISIONS THAT MATTER — AGENTIC · TOPOLOGICAL · TIERED · RULED", {
    x: 0.6, y: 2.0, w: W - 1.2, h: 0.3,
    fontFace: F.bold, fontSize: 10, color: C.muted, charSpacing: 3, margin: 0,
  });

  bar(s, 0.6, 2.55, W - 1.2, { width: 1.5 });

  // 2x2 grid of 4 tech depth points
  const techs = [
    { i: "01", t: "Agentic Hierarchical RAG",
      en: "VS. NAIVE TOP-K DENSE RETRIEVAL",
      d: "每篇論文派一位 subagent 讀章節 TOC（list_sections → read_section），標出 chunks.id；旗艦模型只讀被標的原始片段，做跨論文 synthesis。",
      tag: "不是盲目向量檢索" },
    { i: "02", t: "Four-Stage Paper Search Subgraph",
      en: "DISJOINT TOOL PALETTES PER STAGE",
      d: "Parser → Processor (Discover ⇄ Resolve) → Finalizer → Synthesizer。每階段只看到該看到的 tools，路由錯誤率大幅下降，trace 變成四個明確 step。",
      tag: "v2.7 從 mega-agent 拆解" },
    { i: "03", t: "Model Tiering",
      en: "SMALL SUBAGENTS + FLAGSHIP FINALIZER",
      d: "Per-paper subagent 用便宜小模型（gemini-flash），跨論文 synthesis 用旗艦模型。Embedder + reranker 跑在 sibling process 倖存 uvicorn reload。",
      tag: "成本 × 品質平衡" },
    { i: "04", t: "Rules-vs-LLM Boundary",
      en: "WHERE RULES OWN, WHERE LLMs FREE",
      d: "chunk id resolution / SQL allowlist（sqlglot AST）/ MCP scope / API key redactor / memory safety gate — 全部規則 100% deterministic；LLM 只做語意決定。",
      tag: "SRS §II-1 設計原則" },
  ];

  const gx0 = 0.6, gy0 = 2.85;
  const gw = (W - 1.2 - 0.3) / 2;
  const gh = (H - 0.5 - gy0 - 0.3) / 2;
  for (let i = 0; i < techs.length; i++) {
    const col = i % 2, row = Math.floor(i / 2);
    const x = gx0 + col * (gw + 0.3);
    const y = gy0 + row * (gh + 0.2);
    const t = techs[i];
    // index — cobalt
    s.addText(t.i, {
      x: x, y: y, w: 0.9, h: 0.5,
      fontFace: F.display, fontSize: 28, color: C.cobalt, margin: 0,
    });
    // technique title — bold English Arial Black
    s.addText(t.t, {
      x: x + 0.95, y: y + 0.05, w: gw - 1.0, h: 0.4,
      fontFace: F.bold, fontSize: 14, color: C.ink, margin: 0,
    });
    // english tagline
    s.addText(t.en, {
      x: x + 0.95, y: y + 0.5, w: gw - 1.0, h: 0.25,
      fontFace: F.bold, fontSize: 8.5, color: C.muted, charSpacing: 3, margin: 0,
    });
    // hairline rule
    bar(s, x, y + 0.85, gw, { color: C.ruleSoft, width: 0.5 });
    // description — Chinese
    s.addText(t.d, {
      x: x, y: y + 0.95, w: gw, h: gh - 1.35,
      fontFace: F.zh, fontSize: 11, color: C.body, valign: "top", margin: 0,
    });
    // bottom tag — cobalt italic
    s.addText("→ " + t.tag, {
      x: x, y: y + gh - 0.35, w: gw, h: 0.3,
      fontFace: F.zh, fontSize: 10, bold: true, color: C.cobalt, valign: "middle", margin: 0,
    });
  }

  pageFooter(s, 11);
  s.addNotes("【AI 技術應用深度】這一頁說明技術深度。\n\n四個關鍵技術決策：\n01 Agentic Hierarchical RAG — 不是 top-k 向量相似度。每篇論文派 subagent 跟章節大綱走，這是模仿真人研究員流程。\n02 4-Stage Subgraph — 早期 mega-agent 在多 tools 環境下常選錯。v2.7 拆成四階段子圖，disjoint tool palette 是關鍵設計判斷。\n03 Model Tiering — 不是「都用旗艦模型」也不是「都用小模型」。成本曲線與品質曲線之間找平衡點。\n04 Rules-vs-LLM — SRS §II-1 的核心原則：硬邊界用規則保證 100%，語意決定才交給 LLM。這是讓系統可信的關鍵。\n\n如果 judge 問「跟其他 RAG 系統的根本差異」，這頁是答案。");
}

// ============================================================================
// SLIDE 12 — TRUST INTERLUDE (BLACK, single huge statement)
// ============================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.dark };
  pageHeader(s, 12, "TRUST · 為什麼可信", { onDark: true });

  // Single big modernist statement
  s.addText("信任", {
    x: 0.6, y: 1.5, w: W - 1.2, h: 1.5,
    fontFace: F.zh, fontSize: 120, bold: true, color: C.ondark, charSpacing: -2, margin: 0,
  });
  s.addText([
    { text: "不是說的　— ", options: { fontFace: F.zh, fontSize: 60, bold: true, color: C.darkmuted } },
    { text: "是設計出來的。", options: { fontFace: F.zh, fontSize: 60, bold: true, color: C.cobalt } },
  ], { x: 0.6, y: 3.1, w: W - 1.2, h: 1.0, margin: 0 });

  bar(s, 0.6, 4.7, W - 1.2, { color: C.darkmuted, width: 1 });

  // Three single-line evidence statements with Arial Black numbers
  const evidences = [
    { i: "01", t: "每一次回答都看得見路由 — ROUTING BADGE 顯示 intent · 模型 · 信心 %" },
    { i: "02", t: "每一個引用都點得回原文 — CITATION CANVAS，HTML 與 PDF 雙視圖" },
    { i: "03", t: "每一次對話都能重播 — TRACE PANEL + paperhub-replay CLI" },
  ];
  let py = 4.9;
  for (const e of evidences) {
    s.addText(e.i, {
      x: 0.6, y: py, w: 0.9, h: 0.5,
      fontFace: F.display, fontSize: 24, color: C.cobalt, margin: 0,
    });
    s.addText(e.t, {
      x: 1.55, y: py + 0.02, w: W - 2.15, h: 0.45,
      fontFace: F.zh, fontSize: 17, bold: true, color: C.ondark, margin: 0,
    });
    py += 0.62;
    bar(s, 0.6, py - 0.03, W - 1.2, { color: C.darkmuted, width: 0.5 });
  }

  s.addText("DESIGN PRINCIPLE · 規則保證硬邊界（100% REJECT），LLM 只做語意工作。", {
    x: 0.6, y: H - 0.85, w: W - 1.2, h: 0.3,
    fontFace: F.bold, fontSize: 10, color: C.darkmuted, charSpacing: 3, margin: 0,
  });

  pageFooter(s, 12, { onDark: true });
  s.addNotes("Dark interlude — 用 120pt 巨型「信任」+ 60pt 對比聲明，創造 chapter break。\n\nSwiss 風格的暗色頁不是「裝飾」 — 是讓 audience 視覺得到 reset，記住三個 evidence。");
}

// ============================================================================
// SLIDE 11 — REAL-WORLD FIT (audience + Docker deployment)
// ============================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.white };
  pageHeader(s, 13, "FIT · 落地場景");

  s.addText([
    { text: "誰會用？", options: { fontFace: F.zh, fontSize: 32, bold: true, color: C.ink } },
    { text: " 怎麼自架？", options: { fontFace: F.zh, fontSize: 32, bold: true, color: C.cobalt } },
  ], { x: 0.6, y: 1.2, w: W - 1.2, h: 0.7, margin: 0 });
  s.addText("AUDIENCE · DEPLOYMENT · ROADMAP", {
    x: 0.6, y: 2.0, w: W - 1.2, h: 0.3,
    fontFace: F.bold, fontSize: 10, color: C.muted, charSpacing: 4, margin: 0,
  });

  bar(s, 0.6, 2.55, W - 1.2, { width: 1.5 });

  // LEFT
  s.addText("AUDIENCE", {
    x: 0.6, y: 2.8, w: 4, h: 0.3,
    fontFace: F.bold, fontSize: 11, color: C.cobalt, charSpacing: 4, margin: 0,
  });
  const groups = [
    { i: "01", t: "個人研究者",         d: "研究生、博士後、獨立學者   ·   Lab meeting / survey / 論文閱讀" },
    { i: "02", t: "企業 R&D / IP 團隊", d: "技術調研、競品分析、專利策略   ·   需要可審計的調研報告" },
    { i: "03", t: "圖書館 / 教育機構",  d: "輔助研究、教學示範、館際整理   ·   本機部署、隱私友善" },
  ];
  let py = 3.25;
  for (const g of groups) {
    s.addText(g.i, {
      x: 0.6, y: py, w: 0.9, h: 0.5,
      fontFace: F.display, fontSize: 22, color: C.ink, margin: 0,
    });
    s.addText(g.t, {
      x: 1.55, y: py - 0.02, w: 5.4, h: 0.4,
      fontFace: F.zh, fontSize: 17, bold: true, color: C.ink, margin: 0,
    });
    s.addText(g.d, {
      x: 1.55, y: py + 0.4, w: 5.4, h: 0.3,
      fontFace: F.zh, fontSize: 10.5, color: C.body, margin: 0,
    });
    py += 1.0;
    bar(s, 0.6, py - 0.1, 6.5, { color: C.ruleSoft, width: 0.5 });
  }

  // Vertical separator
  s.addShape(pres.shapes.LINE, { x: 7.2, y: 2.8, w: 0, h: 3.8, line: { color: C.ink, width: 1.5 } });

  // RIGHT — Docker deployment block
  const rx = 7.5;
  s.addText("DEPLOYMENT", {
    x: rx, y: 2.8, w: 4, h: 0.3,
    fontFace: F.bold, fontSize: 11, color: C.cobalt, charSpacing: 4, margin: 0,
  });
  s.addText("Docker, 一行起跑。", {
    x: rx, y: 3.2, w: W - rx - 0.6, h: 0.5,
    fontFace: F.zh, fontSize: 22, bold: true, color: C.ink, margin: 0,
  });
  // The command — in a solid black bar
  s.addShape(pres.shapes.RECTANGLE, {
    x: rx, y: 3.8, w: W - rx - 0.6, h: 0.55,
    fill: { color: C.ink }, line: { color: C.ink, width: 0 },
  });
  s.addText("$ docker compose up", {
    x: rx + 0.2, y: 3.8, w: W - rx - 0.8, h: 0.55,
    fontFace: F.mono, fontSize: 16, bold: true, color: C.cobalt, valign: "middle", margin: 0,
  });

  // Service list
  s.addText("SERVICES", {
    x: rx, y: 4.6, w: 4, h: 0.3,
    fontFace: F.bold, fontSize: 9, color: C.muted, charSpacing: 4, margin: 0,
  });
  const services = [
    ["backend",     "FastAPI · LangGraph", ":8000"],
    ["modelserver", "embedder + reranker", ":8001"],
    ["marker",      "PDF ingestion",       ":8002"],
    ["websearch",   "discovery MCP",       ":3000"],
    ["frontend",    "React SPA · nginx",   ":8080"],
  ];
  let sy = 4.95;
  for (const [name, desc, port] of services) {
    s.addText(name, {
      x: rx, y: sy, w: 1.6, h: 0.28,
      fontFace: F.mono, fontSize: 10.5, bold: true, color: C.ink, margin: 0,
    });
    s.addText(desc, {
      x: rx + 1.6, y: sy, w: 2.5, h: 0.28,
      fontFace: F.mono, fontSize: 10, color: C.body, margin: 0,
    });
    s.addText(port, {
      x: rx + 4.1, y: sy, w: 1.0, h: 0.28,
      fontFace: F.mono, fontSize: 10, bold: true, color: C.cobalt, align: "right", margin: 0,
    });
    sy += 0.28;
  }
  s.addText("GPU 切換用 docker-compose.gpu.yml override，零 host 設定。", {
    x: rx, y: H - 0.85, w: W - rx - 0.6, h: 0.3,
    fontFace: F.zh, fontSize: 11, color: C.muted, margin: 0,
  });

  pageFooter(s, 13);
  s.addNotes("落地頁：左 audience，右 Docker 部署。\n\n部署區用一個 SOLID BLACK 區塊裝 $ 指令 — terminal aesthetic，對工程師 judge 有共鳴。\n\n服務清單三欄對齊：名稱 + 描述 + 端口 — 像 docker ps 的輸出。");
}

// ============================================================================
// SLIDE 12 — METRICS (three giant numbers)
// ============================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.white };
  pageHeader(s, 14, "METRICS · 成效與承諾");

  s.addText([
    { text: "用數字證明 — ", options: { fontFace: F.zh, fontSize: 30, bold: true, color: C.ink } },
    { text: "工程化交付。", options: { fontFace: F.zh, fontSize: 30, bold: true, color: C.cobalt } },
  ], { x: 0.6, y: 1.2, w: W - 1.2, h: 0.7, margin: 0 });
  s.addText("從規格 → 計畫 → TDD → 真 API 端對端 — 每個 plan 都跑過驗證。", {
    x: 0.6, y: 2.0, w: W - 1.2, h: 0.3,
    fontFace: F.zh, fontSize: 13, color: C.muted, margin: 0,
  });

  bar(s, 0.6, 2.65, W - 1.2, { width: 1.5 });

  // Three oversized Arial Black numbers
  const nums = [
    { n: "30",     lab: "真實論文已 ingest",   sub: "26 ARXIV  ·  4 PDF UPLOAD" },
    { n: "19/20",  lab: "Benchmark 答題正確", sub: "20 CASES  ·  MANUAL SCORED" },
    { n: "1,036",  lab: "後端 + 前端測試數",  sub: "PYTEST + VITEST  ·  EACH PR" },
  ];
  const nw = (W - 1.2) / 3;
  for (let i = 0; i < nums.length; i++) {
    const x = 0.6 + i * nw;
    // Vertical hairline between numbers
    if (i > 0) {
      s.addShape(pres.shapes.LINE, { x: x - 0.1, y: 3.0, w: 0, h: 1.7, line: { color: C.ruleSoft, width: 0.5 } });
    }
    s.addText(nums[i].n, {
      x: x, y: 3.0, w: nw - 0.3, h: 1.3,
      fontFace: F.display, fontSize: 80, color: C.ink, charSpacing: -3, margin: 0,
    });
    s.addText(nums[i].lab, {
      x: x, y: 4.3, w: nw - 0.3, h: 0.3,
      fontFace: F.zh, fontSize: 14, bold: true, color: C.ink, margin: 0,
    });
    s.addText(nums[i].sub, {
      x: x, y: 4.6, w: nw - 0.3, h: 0.25,
      fontFace: F.bold, fontSize: 9, color: C.cobalt, charSpacing: 3, margin: 0,
    });
  }
  bar(s, 0.6, 5.0, W - 1.2, { width: 1.5 });

  // Three column lists below
  const cols = [
    { tag: "DONE",   t: "已驗證成效",
      pts: ["26 篇 arXiv + 4 篇 PDF — 真實論文 ingest 與檢索測試", "20-case benchmark：人工 19/20、LLM 判斷 17/20", "真 API 端對端：QA / 產生 / 加講稿 / 改語言 / 編輯"] },
    { tag: "WIP",    t: "已知限制",
      pts: ["部分 PDF 解析會產生公式錯誤", "受限於 ArXiv / Semantic Scholar 免費 API 限流，深度檢索 5–20 min", "模型比較需重啟後端，即時切換尚未實作"] },
    { tag: "OPS",    t: "Docker 部署",
      pts: ["5 服務各自 Dockerfile，可獨立 scale", "GPU 走 override file，零 host 污染", "可換任何 LiteLLM provider"] },
  ];
  let cx = 0.6;
  for (const c of cols) {
    // Tag — solid block
    s.addShape(pres.shapes.RECTANGLE, {
      x: cx, y: 5.2, w: 0.7, h: 0.3,
      fill: { color: C.ink }, line: { color: C.ink, width: 0 },
    });
    s.addText(c.tag, {
      x: cx, y: 5.2, w: 0.7, h: 0.3,
      fontFace: F.bold, fontSize: 9, color: C.white, charSpacing: 2,
      align: "center", valign: "middle", margin: 0,
    });
    s.addText(c.t, {
      x: cx + 0.85, y: 5.18, w: nw - 1.05, h: 0.35,
      fontFace: F.zh, fontSize: 15, bold: true, color: C.ink, valign: "middle", margin: 0,
    });
    let py2 = 5.65;
    for (const pt of c.pts) {
      s.addText("·   " + pt, {
        x: cx, y: py2, w: nw - 0.3, h: 0.32,
        fontFace: F.zh, fontSize: 10.5, color: C.body, valign: "top", margin: 0,
      });
      py2 += 0.35;
    }
    cx += nw;
  }

  pageFooter(s, 14);
  s.addNotes("Modernist 數字頁：80pt Arial Black 數字並排 + 三欄列表。\n\n三大數字皆為真實可驗：\n- 30 篇真實論文 ingest（26 arxiv + 4 PDF）— ls backend/workspace/papers_cache/ 可驗\n- 20-case benchmark 人工 19/20 (95%)、LLM-judge 17/20 (85%)— 結果在 backend/benchmark/results/\n- 1,036 測試（後端 pytest + 前端 vitest）— 每次 PR 都跑\n\n誠實列限制是 A-rank 要求 — 三條都是真實的操作 / 設計限制，不是空頭未實作清單。");
}

// ============================================================================
// SLIDE 13 — SUMMARY (three numbered statements)
// ============================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.white };
  pageHeader(s, 15, "SUMMARY · 總結");

  s.addText("WHY PAPERHUB MATTERS", {
    x: 0.6, y: 1.2, w: W - 1.2, h: 0.3,
    fontFace: F.bold, fontSize: 11, color: C.cobalt, charSpacing: 5, margin: 0,
  });
  s.addText("為什麼 PaperHub 值得業界注意。", {
    x: 0.6, y: 1.55, w: W - 1.2, h: 0.8,
    fontFace: F.zh, fontSize: 34, bold: true, color: C.ink, margin: 0,
  });

  bar(s, 0.6, 2.75, W - 1.2, { width: 2 });

  const lines = [
    { i: "01", t: "解決真實的研究痛點。",       d: "訪談得到的三大痛點 — 引用無依據、路由不透明、無法重現 — 用三個 UI 元件直接解掉。" },
    { i: "02", t: "建立可信任的 AI 介面。",     d: "規則保證硬邊界、LLM 只做語意工作；每個答案附證據鏈、每次對話都可重播。" },
    { i: "03", t: "可落地、可擴張、可賺錢。",   d: "個人研究者 → 企業 R&D → 垂直 SKU；本機隱私 + Docker + MCP 標準介面，是天然的 B2B 切入點。" },
  ];
  let py = 3.1;
  for (const ln of lines) {
    s.addText(ln.i, {
      x: 0.6, y: py, w: 1.4, h: 0.85,
      fontFace: F.display, fontSize: 44, color: C.cobalt, charSpacing: -1, margin: 0,
    });
    s.addText(ln.t, {
      x: 2.1, y: py - 0.05, w: W - 2.7, h: 0.55,
      fontFace: F.zh, fontSize: 24, bold: true, color: C.ink, margin: 0,
    });
    s.addText(ln.d, {
      x: 2.1, y: py + 0.55, w: W - 2.7, h: 0.5,
      fontFace: F.zh, fontSize: 12, color: C.body, margin: 0,
    });
    py += 1.3;
    bar(s, 0.6, py - 0.12, W - 1.2, { color: C.ruleSoft, width: 0.5 });
  }

  pageFooter(s, 15);
  s.addNotes("結尾總結 — 三句 numbered statement：\n01 = problem-solution fit\n02 = AI 設計 + 治理\n03 = 落地與商業\n\n用 44pt Arial Black 數字 + 24pt Chinese bold 標題 + 細節描述。");
}

// ============================================================================
// SLIDE 14 — TEAM CREDITS (6 people, 4 roles)
// ============================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.white };
  pageHeader(s, 16, "TEAM · 團隊分工");

  s.addText([
    { text: "六人團隊，", options: { fontFace: F.zh, fontSize: 32, bold: true, color: C.ink } },
    { text: "四個角色。", options: { fontFace: F.zh, fontSize: 32, bold: true, color: C.cobalt } },
  ], { x: 0.6, y: 1.2, w: W - 1.2, h: 0.7, margin: 0 });
  s.addText("ROLE DISTRIBUTION · SPEC-DRIVEN DEVELOPMENT REQUIRES CLEAR OWNERSHIP", {
    x: 0.6, y: 2.0, w: W - 1.2, h: 0.3,
    fontFace: F.bold, fontSize: 10, color: C.muted, charSpacing: 3, margin: 0,
  });

  bar(s, 0.6, 2.55, W - 1.2, { width: 1.5 });

  // LEFT — giant "06" as the headline number (sized to fit cleanly)
  s.addText("06", {
    x: 0.6, y: 2.85, w: 4.7, h: 2.8,
    fontFace: F.display, fontSize: 200, color: C.ink, charSpacing: -6, margin: 0, valign: "top",
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.6, y: 5.55, w: 0.5, h: 0.18,
    fill: { color: C.cobalt }, line: { color: C.cobalt, width: 0 },
  });
  s.addText("TEAM MEMBERS", {
    x: 0.6, y: 5.8, w: 4.5, h: 0.3,
    fontFace: F.bold, fontSize: 11, color: C.ink, charSpacing: 4, margin: 0,
  });
  s.addText("六人團隊，四個角色分工", {
    x: 0.6, y: 6.1, w: 4.5, h: 0.35,
    fontFace: F.zh, fontSize: 15, bold: true, color: C.ink, margin: 0,
  });
  s.addText("FOUR ROLES · TWO PAIRED · TWO SOLO", {
    x: 0.6, y: 6.45, w: 4.5, h: 0.3,
    fontFace: F.bold, fontSize: 9, color: C.cobalt, charSpacing: 3, margin: 0,
  });

  // Vertical thick rule
  s.addShape(pres.shapes.LINE, {
    x: 5.4, y: 2.85, w: 0, h: 3.9, line: { color: C.ink, width: 1.5 },
  });

  // RIGHT — 4 role entries in a vertical numbered list
  const roles = [
    {
      i: "01", count: "×2",
      t: "問題定義與簡報製作",
      en: "PROBLEM FRAMING & PITCH",
      d: "需求訪談、痛點梳理、敘事設計、簡報視覺化，對外發表的把關。",
    },
    {
      i: "02", count: "×2",
      t: "規格實作與程式開發",
      en: "SPEC IMPLEMENTATION & CODE",
      d: "依 SRS 將計畫拆任務、TDD 寫測試、實作前後端與 RAG / Agent 流程。",
    },
    {
      i: "03", count: "×1",
      t: "使用操作與系統測試",
      en: "OPERATIONS & QA",
      d: "真 API 端對端情境測試、缺陷回報、Docker 部署驗證、UX 走查。",
    },
    {
      i: "04", count: "×1",
      t: "專案管理與進度追查",
      en: "PROJECT MANAGEMENT",
      d: "排程、Plan A-G 進度追蹤、跨角色協作、風險控管與時程調度。",
    },
  ];
  const rx = 5.7;
  let py = 2.85;
  const rowH = 0.95;
  for (let i = 0; i < roles.length; i++) {
    const r = roles[i];
    // index — small cobalt num
    s.addText(r.i, {
      x: rx, y: py + 0.08, w: 0.6, h: 0.4,
      fontFace: F.display, fontSize: 18, color: C.cobalt, margin: 0,
    });
    // count — bold black Arial
    s.addText(r.count, {
      x: rx + 0.55, y: py - 0.1, w: 1.0, h: 0.7,
      fontFace: F.display, fontSize: 36, color: C.ink, margin: 0,
    });
    // role name — Chinese bold
    s.addText(r.t, {
      x: rx + 1.65, y: py - 0.02, w: W - rx - 1.65 - 0.6, h: 0.4,
      fontFace: F.zh, fontSize: 17, bold: true, color: C.ink, margin: 0,
    });
    // english tag
    s.addText(r.en, {
      x: rx + 1.65, y: py + 0.35, w: W - rx - 1.65 - 0.6, h: 0.25,
      fontFace: F.bold, fontSize: 8.5, color: C.muted, charSpacing: 3, margin: 0,
    });
    // description
    s.addText(r.d, {
      x: rx + 1.65, y: py + 0.6, w: W - rx - 1.65 - 0.6, h: 0.32,
      fontFace: F.zh, fontSize: 10.5, color: C.body, margin: 0,
    });
    py += rowH;
    if (i < roles.length - 1) bar(s, rx, py - 0.05, W - rx - 0.6, { color: C.ruleSoft, width: 0.5 });
  }

  pageFooter(s, 16);
  s.addNotes("團隊分工頁 — Swiss modernist credits / colophon 風格。\n\n左邊 280pt「06」對應總人數，右邊四個角色 numbered list。\n\n四角色配比：\n01 問題定義與簡報製作 ×2 — 訪談、敘事、視覺\n02 規格實作與程式開發 ×2 — TDD、前後端、agent\n03 使用操作與系統測試 ×1 — 真 API 測試、Docker 驗證\n04 專案管理與進度追查 ×1 — Plan A-G 進度、跨角色協作\n\n強調 spec-driven workflow 與角色清晰是這個案子能 ship 7 plans 的關鍵。");
}

// ============================================================================
// SLIDE 15 — Q&A CLOSE (was SLIDE 14)
// ============================================================================
{
  const s = pres.addSlide();
  s.background = { color: C.dark };

  // Top bar
  s.addShape(pres.shapes.LINE, { x: 0.6, y: 0.6, w: W - 1.2, h: 0, line: { color: C.ondark, width: 1.5 } });
  s.addText("17 / 17", { x: 0.6, y: 0.7, w: 2, h: 0.3, fontFace: F.mono, fontSize: 11, bold: true, color: C.ondark, margin: 0 });
  s.addText("CLOSING", { x: 2.2, y: 0.72, w: 6, h: 0.3, fontFace: F.bold, fontSize: 9, color: C.ondark, charSpacing: 5, margin: 0 });
  s.addText("THANK YOU", { x: W - 3.5, y: 0.72, w: 3, h: 0.3, fontFace: F.bold, fontSize: 9, color: C.ondark, charSpacing: 5, align: "right", margin: 0 });

  // Massive Q&A — sized so glyph fits in the top half cleanly
  s.addText("Q&A", {
    x: 0.6, y: 1.3, w: 13, h: 3.2,
    fontFace: F.display, fontSize: 240, color: C.ondark, charSpacing: -8, margin: 0, valign: "top",
  });

  // Cobalt accent block
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.6, y: 4.7, w: 0.5, h: 0.18,
    fill: { color: C.cobalt }, line: { color: C.cobalt, width: 0 },
  });
  s.addText("謝謝聆聽 — 歡迎挑戰問題。", {
    x: 0.6, y: 5.0, w: 12, h: 0.55,
    fontFace: F.zh, fontSize: 28, bold: true, color: C.ondark, margin: 0,
  });
  s.addText("LET RESEARCHERS TRUST AI AGAIN — BECAUSE EVERY SENTENCE TRACES BACK.", {
    x: 0.6, y: 5.6, w: 12, h: 0.35,
    fontFace: F.bold, fontSize: 11, color: C.cobalt, charSpacing: 3, margin: 0,
  });

  // Bottom meta
  s.addShape(pres.shapes.LINE, { x: 0.6, y: 6.5, w: W - 1.2, h: 0, line: { color: C.ondark, width: 1.5 } });
  const meta = [
    { lab: "REPOSITORY", val: "github.com/whats2000/PaperHub" },
    { lab: "DEPLOYMENT", val: "$ docker compose up" },
    { lab: "LICENSE",    val: "Apache 2.0" },
  ];
  const mw = (W - 1.2) / 3;
  for (let i = 0; i < meta.length; i++) {
    const mx = 0.6 + i * mw;
    s.addText(meta[i].lab, {
      x: mx, y: 6.65, w: mw, h: 0.25,
      fontFace: F.bold, fontSize: 8, color: C.darkmuted, charSpacing: 4, margin: 0,
    });
    s.addText(meta[i].val, {
      x: mx, y: 6.92, w: mw, h: 0.4,
      fontFace: F.mono, fontSize: 13, bold: true, color: C.ondark, margin: 0,
    });
  }

  s.addNotes("收尾 — 360pt 巨型 Q&A 是整場最大 typographic moment。\n\n刻意 Swiss/Pentagram 美學：純黑底、巨型 Arial Black、cobalt accent block、mono 等寬字 metadata。\n\n預期問題：\nQ1「跟 NotebookLM 差在哪？」→ chunk-level highlight + replayable audit + 跨對話記憶治理\nQ2「商業模式？」→ short OSS / mid enterprise self-host / long vertical SKU\nQ3「為什麼用 Gemini？」→ LiteLLM adapter，可換任何 provider\nQ4「資料安全？」→ 本機部署 + redactor + Docker isolation");
}

// =================== WRITE ===================
const outPath = "/sessions/practical-charming-pascal/mnt/PaperHub/PaperHub_專案介紹.pptx";
pres.writeFile({ fileName: outPath }).then(p => console.log("Wrote:", p));
