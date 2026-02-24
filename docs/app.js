const $ = (id) => document.getElementById(id);

let ITEMS = [];
let META = {};

function uniq(arr){ return [...new Set(arr)].sort((a,b)=>a.localeCompare(b, "de")); }
function pad2(n){ return String(n).padStart(2,"0"); }
function dayKey(d){ return `${d.getFullYear()}-${pad2(d.getMonth()+1)}-${pad2(d.getDate())}`; }
function fmtDay(d){ return new Intl.DateTimeFormat("de-CH",{weekday:"long",year:"numeric",month:"2-digit",day:"2-digit"}).format(d); }
function fmtTime(d){ return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`; }

function todBucket(dtStr){
  const h = new Date(dtStr).getHours();
  if (h < 12) return "morning";
  if (h < 18) return "afternoon";
  return "evening";
}

async function loadData(){
  const res = await fetch("./events.json", { cache: "no-store" });
  if (!res.ok) throw new Error("events.json nicht erreichbar");
  ITEMS = await res.json();

  try {
    const m = await fetch("./events.meta.json", { cache: "no-store" });
    if (m.ok) META = await m.json();
  } catch {}
}

function fillSelect(id, values, placeholder){
  const el = $(id);
  el.innerHTML = `<option value="">${placeholder}</option>`;
  for (const v of values){
    const o = document.createElement("option");
    o.value = v; o.textContent = v;
    el.appendChild(o);
  }
}

function getFiltersFromUrl(){
  const u = new URL(location.href);
  return {
    q: u.searchParams.get("q") || "",
    r: u.searchParams.get("r") || "14",       // 7|14|30|all
    s: u.searchParams.get("s") || "",
    c: u.searchParams.get("c") || "",
    w: u.searchParams.get("w") || "",         // weekday 0..6
    t: u.searchParams.get("t") || "",         // morning|afternoon|evening
    n: u.searchParams.get("n") === "1",
    f: u.searchParams.get("f") === "1",
    a: u.searchParams.get("a") === "1",
    k: u.searchParams.get("k") || "once",     // once|recurring|both
  };
}

function setUrlFromFilters(){
  const u = new URL(location.href);
  const v = {
    q: $("q").value.trim(),
    r: $("range").value,
    s: $("source").value,
    c: $("center").value,
    w: $("weekday").value,
    t: $("tod").value,
    n: $("onlyNew").checked ? "1" : "",
    f: $("onlyFree").checked ? "1" : "",
    a: $("onlySignup").checked ? "1" : "",
    k: $("kindFilter").value || "once",
  };

  ["q","r","s","c","w","t","n","f","a","k"].forEach(k=>u.searchParams.delete(k));
  if (v.q) u.searchParams.set("q", v.q);
  if (v.r && v.r !== "14") u.searchParams.set("r", v.r);
  if (v.s) u.searchParams.set("s", v.s);
  if (v.c) u.searchParams.set("c", v.c);
  if (v.w) u.searchParams.set("w", v.w);
  if (v.t) u.searchParams.set("t", v.t);
  if (v.n) u.searchParams.set("n", v.n);
  if (v.f) u.searchParams.set("f", v.f);
  if (v.a) u.searchParams.set("a", v.a);
  if (v.k && v.k !== "once") u.searchParams.set("k", v.k);

  history.replaceState(null, "", u.toString());
  $("share").href = u.toString();
}

function rangeEndDate(rangeVal){
  if (rangeVal === "all") return null;
  const now = new Date();
  const days = parseInt(rangeVal, 10);
  const end = new Date(now.getTime() + days * 86400000);
  end.setHours(23,59,59,999);
  return end;
}

function withinRange(start, rangeVal){
  const now = new Date();
  if (start < now) return false;
  const end = rangeEndDate(rangeVal);
  if (!end) return true;
  return start <= end;
}

function matchesKind(item, k){
  if (k === "both") return true;
  if (k === "once") return item.kind === "dated";
  if (k === "recurring") return item.kind === "recurring";
  return item.kind === "dated";
}

function applyFilters(){
  const q = $("q").value.trim().toLowerCase();
  const range = $("range").value;
  const source = $("source").value;
  const center = $("center").value;
  const weekday = $("weekday").value;
  const tod = $("tod").value;
  const onlyNew = $("onlyNew").checked;
  const onlyFree = $("onlyFree").checked;
  const onlySignup = $("onlySignup").checked;
  const kindFilter = $("kindFilter").value || "once";

  return ITEMS.filter(e => {
    if (!matchesKind(e, kindFilter)) return false;

    if (source && e.source !== source) return false;
    if (center && e.center !== center) return false;

    if (onlyNew && !e.is_new) return false;
    if (onlyFree && !(e.flags && e.flags.gratis)) return false;
    if (onlySignup && !(e.flags && e.flags.anmeldung)) return false;

    if (e.kind === "dated" && e.start){
      const start = new Date(e.start);
      if (!withinRange(start, range)) return false;
      if (weekday !== "" && start.getDay().toString() !== weekday) return false;
      if (tod && todBucket(e.start) !== tod) return false;
    }

    if (e.kind === "recurring") {
      // recurring has no concrete dates -> only show in "all" and without weekday/tod filters
      if (weekday !== "" || tod) return false;
      if (range !== "all") return false;
    }

    if (q){
      const hay = `${e.title} ${e.center} ${e.location} ${e.source} ${(e.tags||[]).join(" ")} ${e.schedule_text||""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function render(){
  const filtered = applyFilters();

  const dated = filtered.filter(x => x.kind === "dated" && x.start)
    .sort((a,b)=>a.start.localeCompare(b.start));
  const recurring = filtered.filter(x => x.kind === "recurring")
    .sort((a,b)=> (a.center + " " + a.title).localeCompare(b.center + " " + b.title, "de"));

  const kindFilter = $("kindFilter").value || "once";
  const showRecurringSection = (kindFilter === "recurring" || kindFilter === "both");

  $("meta").textContent =
    `${dated.length} Termine` + (showRecurringSection ? ` · ${recurring.length} laufend` : "");

  // dated grouped by day
  const byDay = new Map();
  for (const e of dated){
    const d = new Date(e.start);
    const k = dayKey(d);
    if (!byDay.has(k)) byDay.set(k, []);
    byDay.get(k).push(e);
  }

  const datedRoot = $("dated");
  datedRoot.innerHTML = "";

  const keys = [...byDay.keys()].sort();
  for (const k of keys){
    const dayWrap = document.createElement("div");
    dayWrap.className = "day";
    const d = new Date(k + "T00:00:00");
    dayWrap.innerHTML = `<h2>${fmtDay(d)}</h2>`;

    for (const e of byDay.get(k)){
      const start = new Date(e.start);
      const time = fmtTime(start);

      const flags = [];
      if (e.is_new) flags.push("neu");
      if (e.flags?.gratis) flags.push("gratis");
      if (e.flags?.anmeldung) flags.push("anmeldung");

      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML = `
        <div class="time">${time}<span>${start.toLocaleDateString("de-CH",{day:"2-digit",month:"2-digit"})}</span></div>
        <div class="body">
          <div class="title">
            <a href="${e.url}" target="_blank" rel="noopener">${e.title}</a>
            <div class="badges">
              ${flags.map(f=>`<span class="badge soft">${f}</span>`).join("")}
              <span class="badge">${e.center}</span>
              <span class="badge soft">${e.source}</span>
            </div>
          </div>
          <div class="line2"><span>📍 ${e.location}</span></div>
        </div>
      `;
      dayWrap.appendChild(card);
    }

    datedRoot.appendChild(dayWrap);
  }

  // recurring
  const recRoot = $("recurring");
  recRoot.innerHTML = "";
  if (showRecurringSection && recurring.length){
    const h = document.createElement("div");
    h.className = "sectionTitle";
    h.textContent = "Laufende Angebote";
    recRoot.appendChild(h);

    const list = document.createElement("div");
    list.className = "offerList";

    for (const e of recurring){
      const item = document.createElement("div");
      item.className = "offer";
      item.innerHTML = `
        <div>
          <a href="${e.url}" target="_blank" rel="noopener">${e.title}</a>
          <div class="small">${e.center} · ${e.source}${e.schedule_text ? " · " + e.schedule_text : ""}</div>
        </div>
        <div class="badges">${e.is_new ? `<span class="badge soft">neu</span>` : ""}</div>
      `;
      list.appendChild(item);
    }
    recRoot.appendChild(list);
  }

  setUrlFromFilters();
}

function wire(){
  ["q","range","source","center","weekday","tod","onlyNew","onlyFree","onlySignup","kindFilter"]
    .forEach(id=>{
      $(id).addEventListener("input", render);
      $(id).addEventListener("change", render);
    });

  $("reset").addEventListener("click", ()=>{
    $("q").value = "";
    $("range").value = "14";
    $("source").value = "";
    $("center").value = "";
    $("weekday").value = "";
    $("tod").value = "";
    $("onlyNew").checked = false;
    $("onlyFree").checked = false;
    $("onlySignup").checked = false;
    $("kindFilter").value = "once";
    render();
  });
}

async function main(){
  await loadData();

  fillSelect("center", uniq(ITEMS.map(e=>e.center)), "Zentrum");
  fillSelect("source", uniq(ITEMS.map(e=>e.source)), "Quelle");

  const f = getFiltersFromUrl();
  $("q").value = f.q;
  $("range").value = f.r;
  $("source").value = f.s;
  $("center").value = f.c;
  $("weekday").value = f.w;
  $("tod").value = f.t;
  $("onlyNew").checked = f.n;
  $("onlyFree").checked = f.f;
  $("onlySignup").checked = f.a;
  $("kindFilter").value = f.k || "once";

  if (META.updated_at) $("updated").textContent = `Update: ${META.updated_at}`;

  wire();
  render();
}

main().catch(err=>{
  console.error(err);
  $("meta").textContent = "Fehler beim Laden.";
});
