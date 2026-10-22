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
                               fleetBody = null } = {}) {
  let src = fs.readFileSync(SRC, "utf8");
  src = src.replace('const WIDGET_TOKEN = "";',
                    `const WIDGET_TOKEN = ${JSON.stringify(token)};`);

  const texts = [];           // {text, color}
  // Two cache slots, mirroring the script's two payload shapes.
  const store = { "hydra-widget-cache": cache === null ? undefined : JSON.stringify(cache),
                  "hydra-widget-fleet": cache === null ? undefined : JSON.stringify(cache) };
  const urls = [];

  class Stack {
    layoutHorizontally() {} centerAlignContent() {} addSpacer() {}
    set spacing(_v) {}
    addStack() { return new Stack(); }
    addText(t) { const o = { text: t, color: null }; texts.push(o);
                 // unwrap the Color stub: a `class` constructor cannot return
                 // a primitive, so `new Color("#fff")` yields an instance.
                 return { set font(_f) {},
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
    Font: { systemFont: () => 0, boldSystemFont: () => 0,
            mediumSystemFont: () => 0, boldMonospacedSystemFont: () => 0,
            monospacedSystemFont: () => 0 },
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
    Script: { setWidget: () => {}, complete: () => {} },
    console, Intl, Date: FakeDate, JSON, Math,
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  await vm.runInContext(`(async () => { ${src} })()`, ctx);
  return { texts, all: texts.map(t => t.text).join(" | "),
           headers: ctx.__headers, urls,
           cached: store["hydra-widget-cache"],
           cachedFleet: store["hydra-widget-fleet"] };
}
