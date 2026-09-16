/**
 * Functional tests for the HUMAN-OSINT single-page front-end.
 *
 * The page is loaded in jsdom with the third-party map libraries (Leaflet,
 * Cesium, turf, ExifReader) replaced by stubs. The Cesium stub records the
 * options handed to `Cesium.Viewer`, which is what lets these tests prove the
 * 3D globe is built with the supported `baseLayer` API instead of the
 * `imageryProvider` option that CesiumJS removed in 1.107 — the root cause of
 * the "blue globe with white borders" bug.
 *
 * Run:
 *     cd tests && npm install && npm test
 *
 * Set API_URL to also exercise the live backend round-trip, e.g.
 *     API_URL=http://127.0.0.1:8000 npm test
 */
import { JSDOM, VirtualConsole } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PAGE = path.resolve(HERE, "..", "index.html");
const ANDROID_PAGE = path.resolve(HERE, "..", "app", "src", "main", "assets", "osint", "index.html");
const API_URL = process.env.API_URL || "";

const html = fs.readFileSync(PAGE, "utf8");

/* ------------------------------------------------------------------ *
 * Stubs injected BEFORE the application's inline script.
 * ------------------------------------------------------------------ */
const STUB = `<script>
window.__cesiumCalls = {viewerOptions:null, layersAdded:0, layersRemoved:0, providers:[]};
(function(){
  function ImgLayer(p){ this.imageryProvider=p; window.__cesiumCalls.layersAdded++; }
  function UrlTpl(o){ this.url=o.url; this.maximumLevel=o.maximumLevel; window.__cesiumCalls.providers.push(o.url); }
  function Viewer(el, opts){
    window.__cesiumCalls.viewerOptions = opts;
    this.scene={globe:{enableLighting:false,showGroundAtmosphere:false,baseColor:null,
      tileLoadProgressEvent:{addEventListener:function(){}}},backgroundColor:null,skyAtmosphere:{}};
    this.imageryLayers={removeAll:function(){window.__cesiumCalls.layersRemoved++;},
      add:function(){window.__cesiumCalls.layersAdded++;},
      addImageryProvider:function(){window.__cesiumCalls.layersAdded++;}};
    this.camera={flyTo:function(){}};
    this.entities={removeAll:function(){},add:function(){}};
  }
  window.Cesium={Viewer:Viewer,ImageryLayer:ImgLayer,UrlTemplateImageryProvider:UrlTpl,
    EllipsoidTerrainProvider:function(){},Ion:{defaultAccessToken:"x"},
    Cartesian3:{fromDegrees:function(){return{};}},Cartesian2:function(){return{};},
    Color:{fromCssColorString:function(){return{};},BLACK:{},WHITE:{}},
    LabelStyle:{FILL_AND_OUTLINE:0},VerticalOrigin:{BOTTOM:0}};
  // Universal chainable stub for Leaflet / turf / ExifReader.
  function mk(){ const h={get(t,p){ if(p==="then") return undefined;
      if(p===Symbol.toPrimitive) return function(){return "";};
      return mk(); }, apply(){ return mk(); }, construct(){ return mk(); }};
    return new Proxy(function(){}, h); }
  window.L=mk(); window.turf=mk(); window.ExifReader=mk();
})();
</scr` + `ipt>`;

const idx = html.lastIndexOf("<script>");
const patched = html.slice(0, idx) + STUB + html.slice(idx);

const vc = new VirtualConsole();
const errors = [];
vc.on("jsdomError", e => errors.push("jsdomError: " + e.message));
vc.on("error", (...a) => errors.push("console.error: " + a.join(" ")));

const dom = new JSDOM(patched, {
  url: API_URL ? API_URL.replace(/\/$/, "") + "/" : "http://localhost/",
  runScripts: "dangerously",
  pretendToBeVisual: true,
  virtualConsole: vc,
});
const w = dom.window;
if (typeof fetch === "function") w.fetch = (...a) => fetch(...a);

/** EventSource stub that records the URL and lets the test push server frames. */
class ES { constructor(url){ this.url = url; ES.instances.push(this); } close(){ this.closed = true; } }
ES.instances = [];
w.EventSource = ES;

await new Promise(r => setTimeout(r, 3500));

let pass = 0, fail = 0, skip = 0;
const ok = (cond, label, extra = "") => {
  if (cond) { pass++; console.log("  \u2705 " + label); }
  else { fail++; console.log("  \u274C " + label + (extra ? "  -> " + extra : "")); }
};
const skipped = label => { skip++; console.log("  \u23ED  " + label + " (skipped)"); };
/** `let`/`const` at script scope are not on `window`; read them via eval. */
const read = expr => w.eval(expr);

console.log("\n=== 1. Cesium 3D globe basemap ===");
w.switchMapMode("3D");
await new Promise(r => setTimeout(r, 400));
const vo = w.__cesiumCalls.viewerOptions;
ok(vo !== null, "Cesium.Viewer was constructed");
ok(vo && vo.baseLayer !== undefined, "Viewer received `baseLayer` (CesiumJS >= 1.107 API)",
  vo ? "options keys: " + Object.keys(vo).join(",") : "no viewer");
ok(vo && !("imageryProvider" in vo), "Viewer does NOT use the removed `imageryProvider` option",
  vo && ("imageryProvider" in vo) ? "still passed - the globe would render blue" : "");
ok(vo && vo.baseLayer instanceof w.Cesium.ImageryLayer, "baseLayer is an ImageryLayer wrapping a provider");
ok(w.__cesiumCalls.providers.some(u => u.includes("World_Imagery")), "Esri World Imagery tile URL registered");
ok(vo && vo.baseLayer.imageryProvider.url.includes("{z}/{y}/{x}"), "ArcGIS tile order is {z}/{y}/{x}");
ok(read("typeof cesiumViewer!=='undefined' && cesiumViewer && cesiumViewer.scene.globe.baseColor!==null"),
  "globe.baseColor is set (a loading globe is dark, not blue)");

console.log("\n=== 2. 3D basemap switching ===");
const before = w.__cesiumCalls.layersAdded;
w.switchBaseLayer("osm");
await new Promise(r => setTimeout(r, 200));
ok(read("currentCesiumBaseKey") === "osm", "switchBaseLayer('osm') switches the 3D globe to OSM",
  "got " + read("currentCesiumBaseKey"));
ok(w.__cesiumCalls.layersRemoved > 0, "previous imagery layers removed first");
ok(w.__cesiumCalls.layersAdded > before, "new imagery layer added");
ok(w.__cesiumCalls.providers.some(u => u.includes("tile.openstreetmap.org")), "OpenStreetMap tile URL registered");
w.switchBaseLayer("sat");
await new Promise(r => setTimeout(r, 200));
ok(read("currentCesiumBaseKey") === "sat", "switching back to satellite works");
ok(read("currentBaseLayerKey") === "sat", "2D and 3D basemap keys stay in sync");

console.log("\n=== 3. Tool hints and documentation UI ===");
ok(w.document.querySelectorAll(".tool-help-bar").length === 12, "hint bar injected into all 12 tool panels",
  "found " + w.document.querySelectorAll(".tool-help-bar").length);
ok(w.document.querySelectorAll(".help-qbtn").length === 12, "each hint bar has a help button");
ok(w.document.querySelectorAll("[data-hint]").length >= 10, "controls carry hover hints",
  "found " + w.document.querySelectorAll("[data-hint]").length);
ok(!!w.document.getElementById("btn-help"), "global help button present in the header");
w.showToolHelp();
const modal = w.document.getElementById("help-modal");
ok(modal && modal.classList.contains("open"), "help panel opens");
const body = w.document.getElementById("hm-body").textContent;
ok(body.includes("LIVE 70+") && body.includes("DORKS 80+") && body.includes("AI AGENT"), "help panel lists the tools");
ok(body.includes("Mise \u00e0 jour automatique"), "help panel documents the auto-update mechanism");
ok(body.includes("/api/auto-update/status"), "help panel references the diagnostic endpoint");
w.closeToolHelp();
ok(!w.document.getElementById("help-modal").classList.contains("open"), "help panel closes");

console.log("\n=== 4. Documentation coverage ===");
const fnCount = (html.match(/^\s*(?:async\s+)?function\s+\w+/gm) || []).length;
ok(fnCount === 86, "86 documented JS functions present", "found " + fnCount);
const undoc = (() => {
  const ls = html.split("\n");
  const pat = /^\s*(?:async\s+)?function\s+(\w+)/;
  let n = 0;
  for (let i = 0; i < ls.length; i++) {
    const m = ls[i].match(pat);
    if (!m) continue;
    let j = i - 1;
    while (j >= 0 && ls[j].trim() === "") j--;
    if (!(j >= 0 && ls[j].trim().endsWith("*/"))) n++;
  }
  return n;
})();
ok(undoc === 0, "every JS function has a JSDoc block above it", undoc + " undocumented");

console.log("\n=== 5. Live auto-update wiring ===");
ok(ES.instances.length >= 1, "an SSE connection was opened");
ok(ES.instances[0] && ES.instances[0].url.includes("/api/live/stream"), "SSE targets /api/live/stream");
const es = ES.instances[0];
let fetchCalled = false;
const origFetch = w.fetch;
w.fetch = (...a) => { fetchCalled = true; return origFetch ? origFetch(...a) : Promise.reject(new Error("no fetch")); };
es.onmessage({ data: JSON.stringify({ type: "update", count: 25, content_hash: "hashA", last_updated: "x" }) });
await new Promise(r => setTimeout(r, 800));
ok(fetchCalled, "SSE update with the SAME count but a NEW hash triggers a refetch (regression guard)");
ok(read("lastContentHash") === "hashA", "the new content hash is remembered");
fetchCalled = false;
es.onmessage({ data: JSON.stringify({ type: "update", count: 25, content_hash: "hashA", last_updated: "x" }) });
await new Promise(r => setTimeout(r, 400));
ok(!fetchCalled, "an identical hash does not cause a redundant refetch");
ok(read("typeof lastFetchAt") === "number", "lastFetchAt tracked for the age badge");
ok(!!w.document.getElementById("update-age-badge"), "age badge present");
ok(!!w.document.getElementById("sse-badge"), "SSE status badge present");

console.log("\n=== 6. Android asset copy is in sync ===");
if (fs.existsSync(ANDROID_PAGE)) {
  ok(fs.readFileSync(ANDROID_PAGE, "utf8") === html,
    "app/src/main/assets/osint/index.html matches index.html",
    "the Android WebView would load a stale copy");
} else skipped("Android asset copy not found");

if (API_URL) {
  console.log("\n=== 7. Live backend round-trip (" + API_URL + ") ===");
  const r = await fetch(API_URL.replace(/\/$/, "") + "/api/health");
  const h = await r.json();
  ok(h.auto_update === true, "backend reports auto_update=true");
  ok(typeof h.content_hash === "string", "backend exposes a content_hash");
  ok(typeof h.refresh_interval_sec === "number", "backend exposes refresh_interval_sec");
  const st = await (await fetch(API_URL.replace(/\/$/, "") + "/api/auto-update/status")).json();
  ok(st.auto_update === true && typeof st.sse_heartbeat_sec === "number", "/api/auto-update/status responds");
} else skipped("live backend round-trip (set API_URL to enable)");

if (errors.length) {
  console.log("\nJS errors captured during load:");
  errors.slice(0, 10).forEach(e => console.log("  " + e));
}
console.log(`\n──────── RESULT: ${pass} passed, ${fail} failed, ${skip} skipped ────────`);
process.exit(fail ? 1 : 0);
