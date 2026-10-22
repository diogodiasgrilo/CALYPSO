// Runs HYDRA_Widget.js against stubbed Scriptable globals and returns every
// piece of text it rendered. Scriptable is not available off-device, so the
// alternative to this harness is grepping the source — which proves a token
// exists, never that the widget draws the right thing.
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";

const SRC = path.join(import.meta.dirname, "..", "HYDRA_Widget.js");

export async function render({ token = "tok", httpStatus = 200, body = null,
                               throwOnFetch = false, cache = null,
                               nowISO = null, family = "small",
                               fleetBody = null, logo = { __img: true } } = {}) {
  let src = fs.readFileSync(SRC, "utf8");
  src = src.replace('const WIDGET_TOKEN = "";',
                    `const WIDGET_TOKEN = ${JSON.stringify(token)};`);

  const texts = [];           // {text, color}
  // Two cache slots, mirroring the script's two payload shapes.
  const store = { "hydra-widget-cache": cache === null ? undefined : JSON.stringify(cache),
                  "hydra-widget-fleet": cache === null ? undefined : JSON.stringify(cache) };
  const urls = [];
  const sizes = [];
  const images = [];
  const sparks = [];

  class Stack {
    layoutHorizontally() {} centerAlignContent() {} addSpacer() {}
    set spacing(_v) {}
    // Fixed-width numeric columns and the 1pt hairlines set .size; the fleet
    // layout depends on both, so the stub has to accept them.
    set size(v) { this._size = v; sizes.push(v); }
    get size() { return this._size; }
    set backgroundColor(_c) {}
    addStack() { return new Stack(); }
    addImage(img) { const o = { image: img }; images.push(o);
                    return { set imageSize(v) { o.size = v; },
                             set cornerRadius(v) { o.radius = v; },
                             set imageOpacity(_v) {}, set borderWidth(_v) {},
                             set borderColor(_v) {}, set tintColor(_v) {},
                             set containerRelativeShape(_v) {}, set resizable(_v) {},
                             leftAlignImage() {}, centerAlignImage() {},
                             rightAlignImage() {}, applyFittingContentMode() {},
                             applyFillingContentMode() {} }; }
    addText(t) { const o = { text: t, color: null }; texts.push(o);
                 // unwrap the Color stub: a `class` constructor cannot return
                 // a primitive, so `new Color("#fff")` yields an instance.
                 return { set font(fv) { o.font = fv; },
                          set textColor(c) { o.color = (c && c.hex) || c; },
                          set lineLimit(_l) {},
                          set minimumScaleFactor(_m) {} }; }
  }
  class ListWidget extends Stack {
    setPadding() {} set backgroundColor(_c) {} set url(_u) {}
    set refreshAfterDate(_d) {}
    presentSmall() {} presentMedium() {} presentLarge() {}
  }

  const RealDate = Date;
  const FakeDate = nowISO ? class extends RealDate {
    constructor(...a) { super(...(a.length ? a : [nowISO])); }
    static now() { return new RealDate(nowISO).getTime(); }
  } : RealDate;

  const ctx = {
    ListWidget,
    Color: class { constructor(hex) { this.hex = hex; } },
    // The REAL Scriptable surface. `monospacedSystemFont` is deliberately
    // ABSENT — it does not exist on the device either, and the first fleet
    // rendering silently fell back to the proportional font because of it.
    // The REAL Scriptable Font surface, verified against docs.scriptable.app.
    // `monospacedSystemFont` is deliberately ABSENT — it does not exist on the
    // device either, and the first fleet rendering silently fell back to the
    // proportional font because the code guarded on it.
    Font: { systemFont: () => "systemFont", boldSystemFont: () => 0,
            semiboldSystemFont: () => 0, mediumSystemFont: () => 0,
            boldMonospacedSystemFont: () => "boldMono",
            mediumMonospacedSystemFont: () => "mediumMono",
            regularMonospacedSystemFont: () => 0,
            // Dynamic Type presets
            largeTitle: () => "largeTitle", title1: () => "title1",
            title2: () => "title2", title3: () => "title3",
            headline: () => "headline", subheadline: () => "subheadline",
            body: () => "body", callout: () => "callout",
            footnote: () => "footnote", caption1: () => "caption1",
            caption2: () => "caption2" },
    Size: class { constructor(w2, h2) { this.width = w2; this.height = h2; } },
    Keychain: {
      set: (k, v) => { store[k] = v; },
      get: (k) => { if (store[k] === undefined) throw new Error("missing");
                    return store[k]; },
    },
    Request: class {
      constructor(url) { this.url = url; urls.push(url);
                         this.response = { statusCode: httpStatus }; }
      set headers(h) { this.sentHeaders = h; ctx.__headers = h; }
      set timeoutInterval(_t) {}
      async loadJSON() {
        if (throwOnFetch) throw new Error("network");
        this.response = { statusCode: httpStatus };
        // Serve the fleet payload to the fleet path, so a test cannot pass by
        // the script fetching the wrong endpoint and rendering it anyway.
        return this.url.includes("/api/widget/fleet") ? (fleetBody || body) : body;
      }
    },
    config: { runsInWidget: true, widgetFamily: family },
    // Image + drawing surface, verified against docs.scriptable.app.
    Point: class { constructor(x, y) { this.x = x; this.y = y; } },
    Rect: class { constructor(x, y, w3, h3) { Object.assign(this, { x, y, width: w3, height: h3 }); } },
    Path: class {
      constructor() { this.points = []; }
      move(pt) { this.points.push(pt); }
      addLine(pt) { this.points.push(pt); }
      addLines(pts) { this.points.push(...pts); }
      addRect() {} addEllipse() {} addRoundedRect() {}
      addCurve() {} addQuadCurve() {} closeSubpath() {}
    },
    DrawContext: class {
      constructor() { this.paths = []; }
      set size(v) { this._size = v; } get size() { return this._size; }
      set opaque(_v) {} set respectScreenScale(_v) {}
      setFillColor() {} setStrokeColor(c) { this._stroke = (c && c.hex) || c; }
      setLineWidth() {} addPath(p2) { this.paths.push(p2); }
      strokePath() { sparks.push({ points: this.paths[this.paths.length - 1],
                                   color: this._stroke, size: this._size }); }
      fillPath() {} fill() {} stroke() {} drawImageInRect() {} drawText() {}
      getImage() { return { __spark: true, paths: this.paths.length }; }
    },
    FileManager: { local: () => ({
      joinPath: (a, b) => `${a}/${b}`, cacheDirectory: () => "/cache",
      fileExists: () => logo !== null, readImage: () => logo,
      writeImage: () => {},
    }) },
    Script: { setWidget: () => {}, complete: () => {} },
    console, Intl, Date: FakeDate, JSON, Math,
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  await vm.runInContext(`(async () => { ${src} })()`, ctx);
  return { texts, all: texts.map(t => t.text).join(" | "),
           headers: ctx.__headers, urls, sizes, images, sparks,
           cached: store["hydra-widget-cache"],
           cachedFleet: store["hydra-widget-fleet"] };
}
