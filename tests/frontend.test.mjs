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
ok(fnCount === 112, "112 documented JS functions present", "found " + fnCount);
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

// =======================================================================
// v4.2 — archive, export SIG, géocodage, identité, export HD, manuel
// =======================================================================
console.log("\n=== 8. v4.2 : nouveaux onglets et copyright ===");
for (const id of ["tab-history", "tab-geocode", "tab-identity", "tab-manual"]) {
  ok(!!w.document.getElementById(id), `pane #${id} exists`);
}
ok(/PratiSIG Consulting Services/.test(w.document.body.innerHTML), "copyright names PratiSIG Consulting Services");
ok(/Youssoupha MBODJI/.test(w.document.body.innerHTML), "copyright names Youssoupha MBODJI");
ok(/pratisg\.consulting@gmail\.com/.test(w.document.body.innerHTML), "copyright carries the contact address");
ok(/Dakar, Sénégal/.test(w.document.body.innerHTML), "copyright carries the Dakar address");
ok(w.document.querySelectorAll(".map-export-bar button").length >= 7, "map export bar offers HD + 4 GIS formats");

console.log("\n=== 9. v4.2 : computeTileRange (projection Web Mercator) ===");
const tileRange = (b, z) => w.eval(`computeTileRange(${JSON.stringify(b)}, ${z})`);
const worldZ0 = tileRange({ south: -85, west: -180, north: 85, east: 180 }, 0);
ok(worldZ0.minX === 0 && worldZ0.maxX === 0 && worldZ0.minY === 0 && worldZ0.maxY === 0,
   "zoom 0 → a single tile covers the world", JSON.stringify(worldZ0));
const worldZ3 = tileRange({ south: -85, west: -180, north: 85, east: 180 }, 3);
ok(worldZ3.minX === 0 && worldZ3.maxX === 7 && worldZ3.minY === 0 && worldZ3.maxY === 7 && worldZ3.count === 64,
   "zoom 3 → the full 8x8 grid", JSON.stringify(worldZ3));
// An independent implementation of the same projection, written with the tan
// form instead of the asinh form, used as the oracle.
const oracle = (lat, lon, z) => {
  const n = Math.pow(2, z);
  const x = Math.floor(n * ((lon + 180) / 360));
  const rad = lat * Math.PI / 180;
  const y = Math.floor(n / 2 - n / (2 * Math.PI) * Math.log(Math.tan(Math.PI / 4 + rad / 2)));
  return { x, y };
};
const spots = [[14.7167, -17.4677, "Dakar"], [0, 0, "null island"], [48.8566, 2.3522, "Paris"],
               [35.6762, 139.6503, "Tokyo"], [-33.8688, 151.2093, "Sydney"], [64.1466, -21.9426, "Reykjavik"]];
let projOk = true, projDetail = "";
for (const [lat, lon, name] of spots) {
  for (const z of [1, 5, 10, 14]) {
    const got = tileRange({ south: lat, west: lon, north: lat, east: lon }, z);
    const exp = oracle(lat, lon, z);
    if (got.minX !== exp.x || got.minY !== exp.y) {
      projOk = false; projDetail = `${name} z${z}: got ${got.minX}/${got.minY}, oracle ${exp.x}/${exp.y}`;
    }
  }
}
ok(projOk, "computeTileRange matches an independent slippy-map implementation (24 cases)", projDetail);
ok(tileRange({ south: 10, west: 10, north: 20, east: 20 }, 8).count > 1, "a real area spans several tiles");

console.log("\n=== 10. v4.2 : export SIG côté client ===");
const rows = [
  { id: 1, title: "Séisme à Dakar", summary: "Secousse; magnitude 5,2", link: "http://x/1", source: "USGS",
    source_type: "earthquake", category: "catastrophe", region: "afrique", country: "Sénégal",
    severity: "moderate", risk_level: 4, published_at: "2026-09-10T08:00:00Z", latitude: 14.7167, longitude: -17.4677,
    actors: ["Croix-Rouge"], needs: ["abris"] },
  { id: 2, title: "Sans coordonnées", summary: "x", link: "http://x/2", source: "BBC", source_type: "rss",
    category: "conflit", region: "afrique", country: "Mali", severity: "low", risk_level: 2,
    published_at: "2026-09-11T08:00:00Z", latitude: 0, longitude: 0, actors: [], needs: [] }
];
const gj = w.eval(`buildGeoJSON(${JSON.stringify(rows)})`);
ok(gj.type === "FeatureCollection", "client GeoJSON is a FeatureCollection");
ok(gj.features.length === 1, "a row without coordinates is skipped, not emitted at 0,0");
ok(gj.features[0].geometry.coordinates[0] === -17.4677 && gj.features[0].geometry.coordinates[1] === 14.7167,
   "GeoJSON coordinates are longitude-first (CRS84)");
ok(gj.crs.properties.name === "urn:ogc:def:crs:OGC:1.3:CRS84", "GeoJSON declares CRS84");
ok(gj.metadata.with_coordinates === 1 && gj.metadata.without_coordinates === 1, "GeoJSON metadata counts coordinates");
const csv = w.eval(`buildCSV(${JSON.stringify(rows)})`);
ok(csv.split("\n").length === 3, "client CSV has a header plus one row per incident");
ok(csv.includes('"Secousse; magnitude 5,2"'), "CSV quotes values containing a semicolon");
const kml = w.eval(`buildKML(${JSON.stringify(rows)})`);
ok(kml.includes("<kml") && (kml.match(/<Placemark>/g) || []).length === 1, "client KML has one placemark for the geolocated row");
ok(kml.includes("<coordinates>-17.4677,14.7167,0</coordinates>"), "KML coordinates are lon,lat,alt");
const gpx = w.eval(`buildGPX(${JSON.stringify(rows)})`);
ok(gpx.includes("<gpx") && (gpx.match(/<wpt /g) || []).length === 1, "client GPX has one waypoint");
ok(gpx.includes('lat="14.7167" lon="-17.4677"'), "GPX carries lat/lon attributes");

console.log("\n=== 11. v4.2 : manuel utilisateur (SEARCH / ENGINE / DORK) ===");
w.eval("renderManual()");
const manBody = w.document.getElementById("manual-body").innerHTML;
const manNav = w.document.getElementById("manual-nav").innerHTML;
for (const k of ["search", "engines", "dorks"]) {
  ok(!!w.document.getElementById("man-" + k), `manual has a "${k}" section`);
  ok(new RegExp(k, "i").test(manNav), `manual nav links "${k}"`);
}
ok(/site:|inurl:|intitle:|filetype:/.test(manBody), "SEARCH section explains the operators it generates");
ok(/Shodan|Censys/.test(manBody), "ENGINES section names real engines");
ok(/critical|high|medium|low/.test(manBody), "DORKS section explains the severity scale");
ok(/légal|licite|loi/i.test(manBody), "manual states the legal framing for dorks");
ok(manBody.includes("PratiSIG Consulting Services") && manBody.includes("pratisg.consulting@gmail.com"),
   "manual ends with the copyright and contact");
ok(w.document.querySelectorAll(".manual-sec").length >= 10, "manual covers every documented area");

console.log("\n=== 12. v4.2 : formulaire d'identité adaptatif ===");
const formFor = (kind) => {
  w.document.getElementById("identity-kind").value = kind;
  w.eval("renderIdentityForm()");
  return w.document.getElementById("identity-form").innerHTML;
};
ok(/id="id-email"/.test(formFor("email")) && /id="id-mx"/.test(formFor("email")), "email form asks for the address and the MX check");
ok(/id="id-phone"/.test(formFor("phone")) && /id="id-region"/.test(formFor("phone")), "phone form asks for the number and a default country");
ok(/id="id-username"/.test(formFor("username")), "username form asks for the handle");
ok(/id="id-name"/.test(formFor("person")) && /id="id-employer"/.test(formFor("person")), "person form asks for a name and optional employer");
ok(!/id="id-username"/.test(formFor("email")), "switching tool replaces the previous form");

console.log("\n=== 13. v4.2 : rendu du résultat d'identité ===");
w.eval(`renderIdentityResult("email", ${JSON.stringify({
  valid: true, local_part: "info.contact", domain: "mail.mailinator.com", is_disposable: true,
  is_role_account: true, is_free_provider: true, gravatar_md5: "abc123",
  derived_usernames: ["info", "contact"], mx: { domain: "mail.mailinator.com", exists: true,
    records: [{ host: "mx1.mailinator.com", preference: 10 }], provider_guess: "Mailinator" },
  gravatar_url: "https://example.invalid/a.png",
  engines: [{ name: "Epieos", url: "https://epieos.com/?q=x", deep_link: true },
            { name: "Truecaller", url: "https://www.truecaller.com", deep_link: false, copy: "+221771234567" }]
})})`);
const idOut = w.document.getElementById("identity-result").innerHTML;
ok(/domaine jetable/.test(idOut), "identity result flags a disposable domain");
ok(/compte générique/.test(idOut), "identity result flags a role account");
ok(/mx1\.mailinator\.com/.test(idOut), "identity result shows the MX records");
ok(idOut.includes('class="ident-link nolink"'), "engines without a deep link are marked as paste-the-value");
ok(!/\{[a-z0-9_]+\}/.test(idOut), "no unfilled placeholder leaks into the rendered links");
w.eval(`renderIdentityResult("phone", ${JSON.stringify({
  valid: true, e164: "+221771234567", national: "77 123 45 67", country_code: 221,
  region: "SN", type: "MOBILE", carrier_guess: "Orange Sénégal",
  engines: [{ name: "WhatsApp", url: "https://wa.me/221771234567", deep_link: true }]
})})`);
const phOut = w.document.getElementById("identity-result").innerHTML;
ok(/\+221771234567/.test(phOut), "phone result shows the E.164 number");
ok(/wa\.me\/221771234567/.test(phOut), "phone result offers the WhatsApp link");
w.eval(`renderIdentityResult("email", ${JSON.stringify({ error: "Adresse e-mail vide." })})`);
ok(/Entrée refusée/.test(w.document.getElementById("identity-result").innerHTML), "an invalid input is reported, not silently accepted");

console.log("\n=== 14. v4.2 : archive — rendu, pagination et export ===");
w.eval(`renderHistory(${JSON.stringify(rows)})`);
ok(w.document.querySelectorAll("#history-container .hist-row").length === 2, "archive list renders one row per incident");
w.eval(`renderHistory([])`);
ok(/Aucun événement/.test(w.document.getElementById("history-container").innerHTML), "an empty archive says so explicitly");
w.document.getElementById("hist-category").value = "catastrophe";
w.document.getElementById("hist-search").value = "séisme";
w.document.getElementById("hist-from").value = "2026-09-01";
w.document.getElementById("hist-to").value = "2026-09-30";
const exportedUrl = w.eval(`(function(){ let u=null; const orig=HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click=function(){ u=this.href; };
  exportHistory("geojson"); HTMLAnchorElement.prototype.click=orig; return u; })()`);
ok(/\/api\/export\/incidents\.geojson/.test(exportedUrl), "export targets the GeoJSON endpoint");
ok(/category=catastrophe/.test(exportedUrl), "export carries the category filter");
ok(/search=s%C3%A9isme/.test(exportedUrl), "export carries the free-text filter");
ok(/date_from=2026-09-01/.test(exportedUrl) && /date_to=2026-09-30/.test(exportedUrl), "export carries the date range");
ok(/limit=5000/.test(exportedUrl), "export asks for the whole archive, not just one page");
w.eval(`pickHistoryDay("2026-09-10")`);
ok(w.document.getElementById("hist-from").value === "2026-09-10" &&
   w.document.getElementById("hist-to").value === "2026-09-10", "clicking a day chip narrows the range to that day");

console.log("\n=== 15. v4.2 : géocodage ===");
let geoQ = null;
const realFetch = w.fetch;
w.fetch = (url) => { geoQ = url; return Promise.resolve({ ok: true, json: () => Promise.resolve({ count: 0, results: [] }) }); };
w.document.getElementById("geocode-q").value = "Dakar, Plateau";
w.eval("runGeocode()");
await new Promise(r => setTimeout(r, 80));
ok(/\/api\/geocode\?q=Dakar%2C%20Plateau/.test(geoQ || ""), "a place name goes to the forward geocoder", String(geoQ));
geoQ = null;
w.document.getElementById("geocode-q").value = "14.7167, -17.4677";
w.eval("runGeocode()");
await new Promise(r => setTimeout(r, 80));
ok(/\/api\/geocode\/reverse\?lat=14\.7167&lon=-17\.4677/.test(geoQ || ""),
   "a pasted 'lat, lon' pair is auto-routed to the reverse geocoder", String(geoQ));
w.eval(`renderGeocodeResults(${JSON.stringify({ count: 1, results: [{
  latitude: 14.7167, longitude: -17.4677, display_name: "Dakar, Sénégal", type: "city",
  country: "Sénégal", boundingbox: ["14.6", "14.8", "-17.5", "-17.3"] }] })})`);
ok(/Dakar, Sénégal/.test(w.document.getElementById("geocode-results").innerHTML), "geocoding candidates are listed");
ok(/14\.71670, -17\.46770/.test(w.document.getElementById("geocode-results").innerHTML), "candidates show 5-decimal coordinates");
w.eval(`renderGeocodeResults(${JSON.stringify({ error: "Nominatim injoignable" })})`);
ok(/Nominatim injoignable/.test(w.document.getElementById("geocode-results").innerHTML),
   "a geocoding failure is reported with the backend's reason");
w.fetch = realFetch;   // restore the real client before the live sections

console.log("\n=== 16. v4.2 : export HD refuse une zone trop grande ===");
w.eval(`activeMapMode = "2D"; cesiumViewer = null;
  leafletMap = { getBounds: () => ({ getSouth: () => -80, getWest: () => -179,
    getNorth: () => 80, getEast: () => 179 }), getZoom: () => 6 };`);
w.eval("downloadBasemapImage(3)");
await new Promise(r => setTimeout(r, 150));
const hdStatus = w.document.getElementById("export-map-status").textContent;
ok(/zone trop grande/.test(hdStatus), "an over-large HD export is refused with the tile count", hdStatus);
ok(w.document.getElementById("export-map-status").style.display === "block", "the refusal is visible to the user");


if (API_URL) {
  console.log(`\n=== 17. v4.2 : intégration live (${API_URL}) ===`);
  w.document.getElementById("hist-category").value = "all";
  w.document.getElementById("hist-search").value = "";
  w.document.getElementById("hist-from").value = "";
  w.document.getElementById("hist-to").value = "";
  await w.eval("loadHistory(0)");
  const info = w.document.getElementById("history-page-info").textContent;
  ok(/sur \d+ événement/.test(info), "loadHistory() against the live backend fills the pager", info);
  ok(w.document.querySelectorAll("#history-container .hist-row").length > 0, "the live archive renders rows");
  ok(/backend (sqlite|postgres)/.test(info), "the backend in use is shown to the operator", info);
  await w.eval("loadHistoryStats()");
  ok(/événements archivés/.test(w.document.getElementById("history-summary").textContent), "the archive summary loads");
  const r = await fetch(API_URL.replace(/\/$/, "") + "/api/osint/identity/phone", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ number: "771234567", default_region: "SN" })
  });
  const ph = await r.json();
  ok(ph.e164 === "+221771234567", "the backend normalises a bare Senegalese number to E.164", ph.e164);
  ok(ph.engines.some(e => /wa\.me\/221771234567/.test(e.url)), "the backend produces the WhatsApp link");
  w.eval(`renderIdentityResult("phone", ${JSON.stringify(ph)})`);
  ok(/WhatsApp/.test(w.document.getElementById("identity-result").innerHTML), "the WhatsApp engine renders in the UI");
} else skipped("v4.2 live integration (set API_URL to enable)");

console.log(`\n──────── RESULT: ${pass} passed, ${fail} failed, ${skip} skipped ────────\n`);
process.exit(fail === 0 ? 0 : 1);
