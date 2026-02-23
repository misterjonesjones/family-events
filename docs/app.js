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
    const res = await fetch("../events.json", { cache: "no-store" });
    if (!res.ok) throw new Error("events.json nicht erreichbar");
    EVENTS = await res.json();

    const metaRes = await fetch("../events.meta.json", { cache: "no-store" });
    if (metaRes.ok) {
      META = await metaRes.json();
    }
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
      const hay = `${e.title} ${e.center} ${e.location} ${e.source}`.toLowerCase();
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
    li.className = "item";

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
      </div>
    `;
    ul.appendChild(li);
  }
}

async function main() {
  const ok = await loadData();
  if (!ok) return;

  EVENTS.sort((a,b)=>a.start.localeCompare(b.start));

  fillSelect("source", uniq(EVENTS.map(e => e.source)), "Quelle");
  fillSelect("center", uniq(EVENTS.map(e => e.center)), "Zentrum");

  ["q","range","source","center","weekday","tod","onlyNew","onlyFree","onlySignup"]
    .forEach(id => {
      $(id).addEventListener("input", render);
      $(id).addEventListener("change", render);
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
    render();
  });

  if (META.updated_at) {
    $("updated").textContent = "Update: " + META.updated_at;
  }

  render();
}

main();
