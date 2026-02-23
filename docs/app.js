const $ = (id) => document.getElementById(id);

let EVENTS = [];
let META = {};

function fmtStart(dtStr) {
  const d = new Date(dtStr);
  return new Intl.DateTimeFormat("de-CH", {
    weekday: "short",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(d);
}

function todBucket(dtStr) {
  const h = new Date(dtStr).getHours();
  if (h < 12) return "morning";
  if (h < 18) return "afternoon";
  return "evening";
}

function uniq(arr) {
  return [...new Set(arr)].sort((a,b)=>a.localeCompare(b, "de"));
}

async function loadData() {
  try {
    const res = await fetch("./events.json", { cache: "no-store" });
    if (!res.ok) throw new Error("docs/events.json nicht erreichbar");
    EVENTS = await res.json();

    const metaRes = await fetch("./events.meta.json", { cache: "no-store" });
    if (metaRes.ok) META = await metaRes.json();
  } catch (err) {
    console.error(err);
    $("meta").textContent = "Fehler beim Laden der Events.";
    return false;
  }
  return true;
}

function fillSelect(id, values, placeholder) {
  const el = $(id);
  el.innerHTML = `<option value="">${placeholder}</option>`;
  for (const v of values) {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = v;
    el.appendChild(o);
  }
}

function applyFilters() {
  const q = $("q").value.trim().toLowerCase();
  const range = $("range").value;
  const source = $("source").value;
  const center = $("center").value;
  const weekday = $("weekday").value;
  const tod = $("tod").value;
  const onlyNew = $("onlyNew").checked;
  const onlyFree = $("onlyFree").checked;
  const onlySignup = $("onlySignup").checked;

  const now = new Date();
  const maxDate = (range === "all") ? null : new Date(now.getTime() + parseInt(range,10) * 86400000);

  return EVENTS.filter(e => {
    if (source && e.source !== source) return false;
    if (center && e.center !== center) return false;

    const start = new Date(e.start);
    if (maxDate && (start < now || start > maxDate)) return false;

    if (weekday !== "" && start.getDay().toString() !== weekday) return false;
    if (tod && todBucket(e.start) !== tod) return false;

    if (onlyNew && !e.is_new) return false;
    if (onlyFree && !(e.flags && e.flags.gratis)) return false;
    if (onlySignup && !(e.flags && e.flags.anmeldung)) return false;

    if (q) {
      const hay = `${e.title} ${e.center} ${e.location} ${e.source} ${(e.tags||[]).join(" ")}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function render() {
  const items = applyFilters();
  $("meta").textContent = `${items.length} Events (von ${EVENTS.length})`;

  const ul = $("list");
  ul.innerHTML = "";

  for (const e of items) {
    const li = document.createElement("li");
    li.className = "item" + (e.is_new ? " new" : "");

    const tags = (e.tags || []).slice(0, 6).map(t => `<span class="tag">${t}</span>`).join("");

    li.innerHTML = `
      <div class="row">
        <div class="when">${fmtStart(e.start)}</div>
        <div class="badges">
          ${e.is_new ? `<span class="badge">neu</span>` : ""}
          <span class="badge">${e.center}</span>
          <span class="badge">${e.source}</span>
        </div>
      </div>
      <div class="title">
        <a href="${e.url}" target="_blank" rel="noopener">${e.title}</a>
      </div>
      <div class="sub">
        <span>📍 ${e.location}</span>
        ${(e.flags && e.flags.gratis) ? `<span class="badge">gratis</span>` : ""}
        ${(e.flags && e.flags.anmeldung) ? `<span class="badge">anmeldung</span>` : ""}
        ${tags}
      </div>
    `;
    ul.appendChild(li);
  }
}

function setShareLink() {
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
  };

  ["q","r","s","c","w","t","n","f","a"].forEach(k => u.searchParams.delete(k));
  if (v.q) u.searchParams.set("q", v.q);
  if (v.r && v.r !== "14") u.searchParams.set("r", v.r);
  if (v.s) u.searchParams.set("s", v.s);
  if (v.c) u.searchParams.set("c", v.c);
  if (v.w) u.searchParams.set("w", v.w);
  if (v.t) u.searchParams.set("t", v.t);
  if (v.n) u.searchParams.set("n", v.n);
  if (v.f) u.searchParams.set("f", v.f);
  if (v.a) u.searchParams.set("a", v.a);

  history.replaceState(null, "", u.toString());
  $("share").href = u.toString();
}

function getFiltersFromUrl() {
  const u = new URL(location.href);
  return {
    q: u.searchParams.get("q") || "",
    range: u.searchParams.get("r") || "14",
    source: u.searchParams.get("s") || "",
    center: u.searchParams.get("c") || "",
    weekday: u.searchParams.get("w") || "",
    tod: u.searchParams.get("t") || "",
    onlyNew: u.searchParams.get("n") === "1",
    onlyFree: u.searchParams.get("f") === "1",
    onlySignup: u.searchParams.get("a") === "1",
  };
}

async function main() {
  const ok = await loadData();
  if (!ok) return;

  EVENTS.sort((a,b)=>a.start.localeCompare(b.start));

  fillSelect("source", uniq(EVENTS.map(e => e.source)), "Quelle");
  fillSelect("center", uniq(EVENTS.map(e => e.center)), "Zentrum");

  // apply URL filters
  const f = getFiltersFromUrl();
  $("q").value = f.q;
  $("range").value = f.range;
  $("source").value = f.source;
  $("center").value = f.center;
  $("weekday").value = f.weekday;
  $("tod").value = f.tod;
  $("onlyNew").checked = f.onlyNew;
  $("onlyFree").checked = f.onlyFree;
  $("onlySignup").checked = f.onlySignup;

  if (META.updated_at) $("updated").textContent = "Update: " + META.updated_at;

  const rerender = () => { render(); setShareLink(); };
  ["q","range","source","center","weekday","tod","onlyNew","onlyFree","onlySignup"].forEach(id => {
    $(id).addEventListener("input", rerender);
    $(id).addEventListener("change", rerender);
  });

  $("reset").addEventListener("click", () => {
    $("q").value = "";
    $("range").value = "14";
    $("source").value = "";
    $("center").value = "";
    $("weekday").value = "";
    $("tod").value = "";
    $("onlyNew").checked = false;
    $("onlyFree").checked = false;
    $("onlySignup").checked = false;
    rerender();
  });

  rerender();
}

main();
