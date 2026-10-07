"use strict";
// Freezer Tracker front end. Plain JS, no build step.

const ROWS = "ABCDEFGHIJ";
const KIND_LABEL = { grid9: "9×9", grid10: "10×10", list: "No grid" };
const KIND_SIZE = { grid9: 9, grid10: 10, list: null };
const SAMPLE_TYPES = ["Virus stock", "Plasmid", "RNA", "cDNA", "gDNA", "Cell pellet", "Serum", "Primer", "Antibody", "Protein", "Other"];
const TYPE_COLORS = ["#2a78d6", "#d94f70", "#2e9e6a", "#c27c0e", "#8a56c9", "#0f9aa8", "#b5532a", "#5f6b7a", "#a33ea1", "#3b7d23", "#888"];
const REFRESH_MS = 15000;

const state = {
  tab: "boxes",
  tree: null,
  members: [],
  boxId: null,
  box: null,
  colorBy: load("colorBy", "type"),
  onlyOwner: "",
  moving: null,      // sample being moved
  selecting: false,  // label selection mode
  selected: new Set(),
  flashId: null,
  searchResults: null,
  needRoom: "",
  history: { rows: [], filters: { action: "", user: "", q: "" }, done: false },
};

// ---------- small helpers ----------

function load(key, dflt) {
  try { const v = localStorage.getItem("ft." + key); return v === null ? dflt : v; } catch (e) { return dflt; }
}
function save(key, value) {
  try { localStorage.setItem("ft." + key, value); } catch (e) { /* private mode: fine */ }
}
function $(sel, root) { return (root || document).querySelector(sel); }

// h("div.cls", {attrs}, ...children) -> element. Text children are escaped by the DOM.
function h(tag, attrs, ...kids) {
  const [name, ...classes] = tag.split(".");
  const el = document.createElement(name || "div");
  if (classes.length) el.className = classes.join(" ");
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "style" && typeof v === "object") {
        for (const [p, val] of Object.entries(v)) {
          if (p.startsWith("--")) el.style.setProperty(p, val); else el.style[p] = val;
        }
      }
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k in el && k !== "list") el[k] = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}

function pos(row, col) { return row === null || row === undefined ? "" : ROWS[row] + (col + 1); }
function member(id) { return state.members.find(m => m.id === id) || null; }
function memberName(id) { const m = member(id); return m ? m.name : "—"; }
function memberColor(id) { const m = member(id); return m ? m.color : "#aaa"; }
function me() { return state.members.find(m => m.name === load("user", "")) || null; }
function typeColor(t) {
  const i = SAMPLE_TYPES.indexOf(t);
  return TYPE_COLORS[i >= 0 ? i : TYPE_COLORS.length - 1];
}
function soft(hex) { return hex + "33"; }
function dot(color) { return h("span.dot", { style: { background: color } }); }
function today() { return new Date().toISOString().slice(0, 10); }
function fmtTime(ts) {
  const d = new Date(ts);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) + " " +
    d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

let toastTimer;
function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = isErr ? "err" : "";
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, isErr ? 5000 : 2500);
}

// ---------- API ----------

class Confirm extends Error {
  constructor(warnings) { super(warnings.join("\n")); this.warnings = warnings; }
}

async function api(method, path, body) {
  const headers = { "X-User": encodeURIComponent(load("user", "")), "X-Passcode": load("passcode", "") };
  if (body) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) {
    await askPasscode();
    return api(method, path, body);
  }
  if (res.status === 409 && data.needs_confirm) throw new Confirm(data.warnings);
  if (!res.ok) throw new Error(data.error || "Request failed (" + res.status + ")");
  return data;
}

// Run a write; if the server warns about someone else's claim/reservation, ask, then retry with force.
async function write(method, path, body) {
  try {
    return await api(method, path, body);
  } catch (e) {
    if (!(e instanceof Confirm)) throw e;
    const ok = await confirmDialog("Heads up", e.warnings, "Continue anyway");
    if (!ok) return null;
    return api(method, path, Object.assign({}, body, { force: true }));
  }
}

// ---------- dialogs ----------

const dialog = $("#dialog");
let dialogResolve = null;

function openDialog(content) {
  const body = $("#dialog-body");
  body.replaceChildren(content);
  if (!dialog.open) dialog.showModal();
  const first = body.querySelector("input:not([type=hidden]), select, textarea");
  if (first) setTimeout(() => first.focus(), 30);
}
function closeDialog(value) {
  if (dialog.open) dialog.close();
  if (dialogResolve) { const r = dialogResolve; dialogResolve = null; r(value); }
}
dialog.addEventListener("close", () => { if (!dialog.open && dialogResolve) { const r = dialogResolve; dialogResolve = null; r(null); } });

// Confirmations use a second dialog so they can sit on top of an open form.
function confirmDialog(title, lines, okLabel, danger) {
  const box = $("#confirm");
  return new Promise(resolve => {
    let answer = false;
    box.replaceChildren(h("div", { id: "confirm-body" },
      h("h3", null, title),
      [].concat(lines).map(l => h("div.warn", null, l)),
      h("div.actions", null,
        h("button", { type: "button", onclick: () => box.close() }, "Cancel"),
        h("button" + (danger ? ".danger" : ".primary"), { type: "button", onclick: () => { answer = true; box.close(); } }, okLabel || "OK"))));
    box.addEventListener("close", () => resolve(answer), { once: true });
    box.showModal();
  });
}

// Generic form dialog. fields: [{name, label, type, value, options, required}]
function formDialog(title, sub, fields, opts) {
  opts = opts || {};
  return new Promise(resolve => {
    dialogResolve = resolve;
    const err = h("div.error");
    const inputs = {};
    const fieldEl = f => {
      let input;
      if (f.type === "select") {
        input = h("select", { name: f.name },
          f.options.map(o => h("option", { value: o.value, selected: String(o.value) === String(f.value ?? "") }, o.label)));
      } else if (f.type === "textarea") {
        input = h("textarea", { name: f.name, value: f.value || "" });
      } else {
        input = h("input", { name: f.name, type: f.type || "text", value: f.value ?? "", required: f.required,
                             list: f.list, placeholder: f.placeholder || "" });
      }
      inputs[f.name] = input;
      return h("div.field", null, h("label", null, f.label), input);
    };
    const layout = fields.map(f => Array.isArray(f) ? h("div.field-row", null, f.map(fieldEl)) : fieldEl(f));
    const form = h("form", {
      onsubmit: async ev => {
        ev.preventDefault();
        const values = {};
        for (const [k, el] of Object.entries(inputs)) values[k] = el.value;
        try {
          const out = opts.onSubmit ? await opts.onSubmit(values) : values;
          if (out === undefined) return;   // keep dialog open
          closeDialog(out);
        } catch (e) { err.textContent = e.message; }
      },
    },
      h("h3", null, title), sub ? h("div.sub", null, sub) : null,
      layout,
      opts.extra || null,
      err,
      h("div.actions", null,
        opts.danger ? h("button.danger.left", { type: "button", onclick: async () => {
          try { const out = await opts.danger.run(); if (out !== undefined) closeDialog(out); } catch (e) { err.textContent = e.message; }
        } }, opts.danger.label) : null,
        h("button", { type: "button", onclick: () => closeDialog(null) }, "Cancel"),
        h("button.primary", { type: "submit" }, opts.okLabel || "Save")));
    openDialog(form);
  });
}

function memberOptions(includeNone, noneLabel) {
  const opts = state.members.filter(m => m.active).map(m => ({ value: m.id, label: m.name }));
  return includeNone ? [{ value: "", label: noneLabel || "— nobody —" }].concat(opts) : opts;
}

// ---------- passcode & identity ----------

async function askPasscode() {
  const out = await formDialog("Lab passcode", "Ask a lab mate for the passcode.",
    [{ name: "passcode", label: "Passcode", type: "password" }], { okLabel: "Enter" });
  if (out === null) throw new Error("Passcode needed");
  save("passcode", out.passcode);
}

async function askWho(force) {
  if (!force && me()) return;
  const datalist = h("datalist", { id: "member-names" }, state.members.map(m => h("option", { value: m.name })));
  const out = await formDialog("Who are you?", "Your name is recorded on every change you make. Pick yourself, or type a new name to join the list.",
    [{ name: "name", label: "Your name", value: load("user", ""), list: "member-names", required: true }],
    { okLabel: "Continue", extra: datalist });
  if (!out) return;
  const name = out.name.trim();
  if (!name) return;
  if (!state.members.some(m => m.name === name)) {
    const colors = ["#2a78d6", "#d94f70", "#2e9e6a", "#c27c0e", "#8a56c9", "#0f9aa8", "#b5532a", "#a33ea1", "#3b7d23", "#5f6b7a"];
    save("user", name);
    await api("POST", "/api/members", { name, color: colors[state.members.length % colors.length] });
  }
  save("user", name);
  await refresh();
}

function renderWho() {
  const m = me();
  $("#whoami").replaceChildren(m ? dot(m.color) : "", m ? m.name : "Who are you?");
}

// ---------- data loading ----------

async function refresh() {
  const tree = await api("GET", "/api/tree");
  state.tree = tree;
  state.members = tree.members;
  if (state.boxId) {
    try { state.box = await api("GET", "/api/boxes/" + state.boxId); }
    catch (e) { state.boxId = null; state.box = null; }
  }
  if (state.tab === "whos") state.whos = await api("GET", "/api/whos-where");
  renderWho();
  render();
}

async function openBox(id, flashSampleId) {
  state.tab = "boxes";
  state.searchResults = null;
  state.boxId = id;
  state.flashId = flashSampleId || null;
  state.selected.clear();
  save("box", id);
  state.box = await api("GET", "/api/boxes/" + id);
  render();
}

// ---------- rendering ----------

function render() {
  document.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === state.tab));
  renderSidebar();
  const main = $("#main");
  if (state.searchResults) main.replaceChildren(renderSearch());
  else if (state.tab === "whos") main.replaceChildren(renderWhos());
  else if (state.tab === "history") main.replaceChildren(renderHistory());
  else main.replaceChildren(renderBoxView());
  renderBanner();
}

function claimBadge(memberId, note, prefix) {
  if (!memberId) return null;
  return h("span.claim-badge", { title: (prefix || "Claimed by ") + memberName(memberId) + (note ? " — " + note : "") },
    dot(memberColor(memberId)), memberName(memberId));
}

function renderSidebar() {
  const sb = $("#sidebar");
  if (!state.tree) return;
  const roomFor = parseInt(state.needRoom, 10);
  const mine = me();
  const kids = [h("div.tree-head", null, h("h3", null, "Freezers"),
    h("button.link", { onclick: addFreezer, title: "Add a freezer" }, "+ Freezer"))];
  for (const f of state.tree.freezers) {
    const fr = h("div.freezer", null,
      h("div.row", null, h("span.grow", { title: [f.location, f.temperature].filter(Boolean).join(" · ") }, f.name),
        h("span.tools", null,
          h("button", { title: "Add rack", onclick: () => addRack(f) }, "+ rack"),
          h("button", { title: "Edit freezer", onclick: () => editContainer("freezers", f) }, "✎"))));
    for (const r of f.racks) {
      const rk = h("div.rack", null,
        h("div.row", null, h("span.grow", null, r.name), claimBadge(r.claimed_by, r.claim_note),
          h("span.tools", null,
            h("button", { title: "Add box", onclick: () => addBox(r) }, "+ box"),
            h("button", { title: "Claim rack", onclick: () => claimDialog("racks", r) }, "⚑"),
            h("button", { title: "Edit rack", onclick: () => editContainer("racks", r) }, "✎"))));
      for (const b of r.boxes) {
        const free = b.capacity ? b.capacity - b.used - b.reserved : null;
        const claimer = b.claimed_by || r.claimed_by;
        const hasRoom = roomFor > 0 && free !== null && free >= roomFor && (!claimer || (mine && claimer === mine.id));
        rk.append(h("div.row.box-row" + (state.boxId === b.id && state.tab === "boxes" && !state.searchResults ? ".active" : "") + (hasRoom ? ".room" : ""),
          { onclick: () => openBox(b.id) },
          h("span.grow", null, b.name),
          b.claimed_by ? dot(memberColor(b.claimed_by)) : null,
          h("span.people", { title: b.people.map(p => memberName(p.member_id) + ": " + p.count).join("\n") },
            b.people.slice(0, 4).map(p => h("span.dot", { style: { background: memberColor(p.member_id), width: "7px", height: "7px" } }))),
          h("span.fill", null, b.capacity ? b.used + "/" + b.capacity : b.used + " items")));
      }
      fr.append(rk);
    }
    kids.push(fr);
  }
  if (!state.tree.freezers.length) kids.push(h("div.muted", { style: { padding: "8px" } }, "No freezers yet."));
  sb.replaceChildren(...kids);
}

function renderBanner() {
  const b = $("#banner");
  if (state.moving) {
    const s = state.moving;
    const inList = state.box && state.box.kind === "list" && state.tab === "boxes" && !state.searchResults;
    b.replaceChildren(
      h("span", null, "Moving ", h("b", null, s.name), " from ", s.location, ". ",
        inList ? "Move it into this box?" : "Click an empty spot, or open another box."),
      inList && state.box.id !== s.box_id ? h("button", { onclick: () => doMove({ box_id: state.box.id }) }, "Move here") : null,
      h("button", { onclick: cancelMove }, "Cancel (Esc)"));
    b.hidden = false;
  } else if (state.selecting) {
    b.replaceChildren(
      h("span", null, state.selected.size + " selected for labels. Click samples to add or remove."),
      h("button", { onclick: () => printLabels([...state.selected]), disabled: !state.selected.size }, "Print"),
      h("button", { onclick: () => { state.selecting = false; state.selected.clear(); render(); } }, "Done"));
    b.hidden = false;
  } else {
    b.hidden = true;
  }
}

function renderBoxView() {
  const box = state.box;
  if (!box) {
    return h("div.empty-state", null, state.tree && state.tree.freezers.length
      ? "Pick a box on the left." : "Start by adding a freezer on the left.");
  }
  const owners = [...new Set(box.samples.map(s => s.owner_id).filter(Boolean))];
  const wrap = h("div" + (state.moving ? ".moving" : ""));
  wrap.append(h("div.box-header", null,
    h("div", null,
      h("div.crumbs", null, box.freezer + " / " + box.rack),
      h("h2", null, box.name, " ", h("span.muted", { style: { fontSize: "13px", fontWeight: 400 } },
        KIND_LABEL[box.kind] + " · " + box.samples.length + (box.size ? " / " + box.size * box.size : "") + " samples")),
      h("div.claims", null,
        box.claimed_by ? claimBadge(box.claimed_by, box.claim_note, "Box claimed by ") : null,
        box.rack_claimed_by ? h("span.muted", { style: { fontSize: "12px" } }, "Rack: ") : null,
        claimBadge(box.rack_claimed_by, box.rack_claim_note, "Rack claimed by "),
        box.claim_note ? h("span.muted", { style: { fontSize: "12px" } }, box.claim_note) : null)),
    h("div.spacer"),
    h("div.toolbar", null,
      h("button", { onclick: () => claimDialog("boxes", box) }, box.claimed_by ? "Change claim" : "Claim box"),
      box.kind === "list" ? h("button.primary", { onclick: () => sampleForm(null, { box_id: box.id }) }, "+ Add sample") : null,
      h("button", { onclick: () => { state.selecting = true; state.moving = null; render(); } }, "Select labels"),
      h("button", { onclick: () => printLabels(box.samples.map(s => s.id)), disabled: !box.samples.length }, "Print box labels"),
      h("button", { onclick: () => downloadCsv(box.id), disabled: !box.samples.length }, "Export box CSV"))));

  if (box.kind !== "list") {
    wrap.append(h("div.toolbar", null,
      h("span.muted", null, "Color by"),
      h("div.seg", null,
        ["type", "owner"].map(k => h("button" + (state.colorBy === k ? ".on" : ""),
          { onclick: () => { state.colorBy = k; save("colorBy", k); render(); } }, k === "type" ? "Sample type" : "Owner"))),
      h("span.muted", { style: { marginLeft: "8px" } }, "Show only"),
      h("select", { onchange: e => { state.onlyOwner = e.target.value; render(); } },
        h("option", { value: "" }, "Everyone"),
        state.members.map(m => h("option", { value: m.id, selected: String(m.id) === state.onlyOwner }, m.name)))));
    wrap.append(renderGrid(box));
    wrap.append(renderLegend(box, owners));
  } else {
    wrap.append(renderListBox(box));
  }
  if (state.flashId) {
    const id = state.flashId;
    state.flashId = null;
    setTimeout(() => {
      const el = document.querySelector('[data-sample="' + id + '"]');
      if (el) { el.classList.add("flash"); el.scrollIntoView({ block: "center", behavior: "smooth" }); }
    }, 50);
  }
  return wrap;
}

function renderGrid(box) {
  const n = box.size;
  const grid = h("div.grid", { style: { "--n": n } });
  grid.append(h("div.hdr"));
  for (let c = 0; c < n; c++) grid.append(h("div.hdr", null, c + 1));
  const at = {};
  box.samples.forEach(s => { at[s.row + "," + s.col] = s; });
  const res = {};
  box.reservations.forEach(r => { res[r.row + "," + r.col] = r; });
  const only = state.onlyOwner ? parseInt(state.onlyOwner, 10) : null;
  for (let r = 0; r < n; r++) {
    grid.append(h("div.hdr", null, ROWS[r]));
    for (let c = 0; c < n; c++) {
      const s = at[r + "," + c];
      const rv = res[r + "," + c];
      let cls = "button.cell";
      let color = null;
      let body;
      let title;
      if (s) {
        color = state.colorBy === "owner" ? memberColor(s.owner_id) : typeColor(s.type);
        cls += ".filled" + (state.colorBy === "owner" ? ".owner-mode" : "");
        if (only && s.owner_id !== only) cls += ".dim";
        if (state.selected.has(s.id)) cls += ".selected";
        body = h("span.nm", null, s.name);
        title = [s.code + "  " + s.name, s.type, s.date, "Owner: " + (s.owner || "—"), s.notes].filter(Boolean).join("\n");
      } else if (rv) {
        color = memberColor(rv.member_id);
        cls += ".reserved";
        if (only && rv.member_id !== only) cls += ".dim";
        body = h("span.nm", null, "Reserved · " + memberName(rv.member_id));
        title = "Reserved for " + memberName(rv.member_id) + (rv.note ? " — " + rv.note : "");
      } else {
        title = pos(r, c) + " (empty)";
      }
      if (state.moving && !s) cls += ".target";
      const cell = h(cls, {
        type: "button", title,
        dataset: s ? { sample: s.id } : {},
        onclick: () => onCellClick(r, c, s, rv),
      }, h("span.pos", null, pos(r, c)), body);
      if (color) { cell.style.setProperty("--c", color); cell.style.setProperty("--c-soft", soft(color)); }
      grid.append(cell);
    }
  }
  return grid;
}

function renderLegend(box, owners) {
  if (state.colorBy === "owner") {
    const ids = new Set(owners.concat(box.reservations.map(r => r.member_id)));
    return h("div.legend", null, [...ids].map(id => h("span", null, dot(memberColor(id)), memberName(id))),
      box.reservations.length ? h("span", null, "Striped = reserved spot") : null);
  }
  const types = [...new Set(box.samples.map(s => s.type))].sort();
  return h("div.legend", null, types.map(t => h("span", null, dot(typeColor(t)), t || "No type")),
    box.reservations.length ? h("span", null, "Striped = reserved spot") : null);
}

function renderListBox(box) {
  if (!box.samples.length) return h("div.empty-state", null, "This box is empty. Use “+ Add sample”.");
  const only = state.onlyOwner ? parseInt(state.onlyOwner, 10) : null;
  return h("table.data.list-box", null,
    h("thead", null, h("tr", null, ["", "Code", "Name", "Type", "Date", "Owner", "Notes"].map(x => h("th", null, x)))),
    h("tbody", null, box.samples.map(s => h("tr.click", {
      dataset: { sample: s.id },
      style: only && s.owner_id !== only ? { opacity: .3 } : null,
      onclick: () => state.selecting ? toggleSelect(s.id) : sampleDetails(s),
    },
      h("td", null, state.selecting ? (state.selected.has(s.id) ? "☑" : "☐") : dot(typeColor(s.type))),
      h("td.muted", null, s.code), h("td", null, h("b", null, s.name)), h("td", null, s.type), h("td", null, s.date),
      h("td", null, s.owner_id ? h("span.chip", null, dot(memberColor(s.owner_id)), s.owner) : "—"),
      h("td.muted", null, s.notes)))));
}

// ---------- box interactions ----------

function toggleSelect(id) {
  if (state.selected.has(id)) state.selected.delete(id); else state.selected.add(id);
  render();
}

async function onCellClick(row, col, sample, reservation) {
  if (state.moving) {
    if (sample) { toast("That spot is taken. Pick an empty spot.", true); return; }
    return doMove({ box_id: state.box.id, row, col });
  }
  if (state.selecting) {
    if (sample) toggleSelect(sample.id);
    return;
  }
  if (sample) return sampleDetails(sample);
  return emptySpotDialog(row, col, reservation);
}

function emptySpotDialog(row, col, reservation) {
  const box = state.box;
  const where = box.name + " · " + pos(row, col);
  const add = () => { closeDialog(); sampleForm(null, { box_id: box.id, row, col }); };
  let content;
  if (reservation) {
    content = h("div", null,
      h("h3", null, "Reserved spot"),
      h("div.sub", null, where),
      h("dl.props", null,
        h("dt", null, "For"), h("dd", null, h("span.chip", null, dot(memberColor(reservation.member_id)), memberName(reservation.member_id))),
        h("dt", null, "Since"), h("dd", null, fmtTime(reservation.created_at)),
        reservation.note ? [h("dt", null, "Note"), h("dd", null, reservation.note)] : null),
      h("div.actions", null,
        h("button.danger.left", { onclick: async () => {
          await api("DELETE", "/api/reservations/" + reservation.id);
          closeDialog(); toast("Reservation cancelled"); refresh();
        } }, "Cancel reservation"),
        h("button", { onclick: () => closeDialog() }, "Close"),
        h("button.primary", { onclick: add }, "Add sample here")));
    openDialog(content);
    return;
  }
  openDialog(h("div", null,
    h("h3", null, "Empty spot"),
    h("div.sub", null, where),
    h("div.actions", null,
      h("button.left", { onclick: () => { closeDialog(); reserveDialog(row, col); } }, "Reserve this spot…"),
      h("button", { onclick: () => closeDialog() }, "Cancel"),
      h("button.primary", { onclick: add }, "Add sample here"))));
}

async function reserveDialog(row, col) {
  const m = me();
  const out = await formDialog("Reserve " + pos(row, col), state.box.name + " — hold this empty spot so others don't use it.",
    [{ name: "member_id", label: "Reserve for", type: "select", options: memberOptions(false), value: m ? m.id : "" },
     { name: "note", label: "Note (optional)", placeholder: "e.g. next week's virus preps" }],
    { okLabel: "Reserve", onSubmit: v => api("POST", "/api/reservations",
      { box_id: state.box.id, row, col, member_id: parseInt(v.member_id, 10), note: v.note }) });
  if (out) { toast("Spot reserved"); refresh(); }
}

async function sampleForm(sample, where) {
  const m = me();
  const types = h("datalist", { id: "sample-types" }, SAMPLE_TYPES.map(t => h("option", { value: t })));
  const isNew = !sample;
  const loc = isNew ? (state.box.name + (where.row !== undefined ? " · " + pos(where.row, where.col) : "")) : sample.location;
  const out = await formDialog(isNew ? "Add sample" : "Edit sample", loc, [
    { name: "name", label: "Name", value: sample ? sample.name : "", required: true },
    [{ name: "type", label: "Type", value: sample ? sample.type : "", list: "sample-types" },
     { name: "date", label: "Date", type: "date", value: sample ? sample.date : today() }],
    { name: "owner_id", label: "Owner", type: "select", options: memberOptions(true, "— no owner —"),
      value: sample ? sample.owner_id : (m ? m.id : "") },
    { name: "notes", label: "Notes", type: "textarea", value: sample ? sample.notes : "" },
  ], {
    extra: types,
    okLabel: isNew ? "Add" : "Save",
    onSubmit: async v => {
      const body = { name: v.name, type: v.type, date: v.date, notes: v.notes,
                     owner_id: v.owner_id ? parseInt(v.owner_id, 10) : null };
      const res = isNew
        ? await write("POST", "/api/samples", Object.assign(body, where))
        : await write("PUT", "/api/samples/" + sample.id, body);
      return res === null ? undefined : res;   // declined warning: keep form open
    },
  });
  if (out) { toast(isNew ? "Sample added" : "Sample saved"); state.flashId = out.id; refresh(); }
}

async function sampleDetails(s) {
  const hist = h("div.mini-history", null, h("div.muted", null, "Loading history…"));
  openDialog(h("div", null,
    h("h3", null, s.name),
    h("div.sub", null, s.code + " · " + s.location),
    h("dl.props", null,
      h("dt", null, "Type"), h("dd", null, s.type || "—"),
      h("dt", null, "Date"), h("dd", null, s.date || "—"),
      h("dt", null, "Owner"), h("dd", null, s.owner_id ? h("span.chip", null, dot(memberColor(s.owner_id)), s.owner) : "—"),
      h("dt", null, "Notes"), h("dd", null, s.notes || "—"),
      h("dt", null, "Updated"), h("dd", null, fmtTime(s.updated_at))),
    hist,
    h("div.actions", null,
      h("button.danger.left", { onclick: () => removeSample(s) }, "Remove"),
      h("button", { onclick: () => { closeDialog(); printLabels([s.id]); } }, "Print label"),
      h("button", { onclick: () => { closeDialog(); startMove(s); } }, "Move"),
      h("button.primary", { onclick: () => { closeDialog(); sampleForm(s); } }, "Edit"))));
  const rows = await api("GET", "/api/history?entity=sample&entity_id=" + s.id);
  hist.replaceChildren(h("div", null, h("b", null, "History")),
    rows.map(r => h("div", null, h("span.act." + r.action, null, r.action), " ", fmtTime(r.ts), " · ", r.user || "—", " ",
      h("span.diff", null, describeChange(r)))));
}

async function removeSample(s) {
  const ok = await confirmDialog("Remove " + s.name + "?",
    ["This takes it out of " + s.location + ". Its details stay in History."], "Remove", true);
  if (!ok) return;
  await api("DELETE", "/api/samples/" + s.id);
  closeDialog();
  toast("Sample removed");
  refresh();
}

function startMove(s) {
  state.moving = s;
  state.selecting = false;
  render();
}
function cancelMove() { state.moving = null; render(); }

async function doMove(target) {
  const s = state.moving;
  try {
    const out = await write("POST", "/api/samples/" + s.id + "/move", target);
    if (!out) return;
    state.moving = null;
    state.flashId = out.id;
    toast("Moved to " + out.location);
    refresh();
  } catch (e) { toast(e.message, true); }
}

async function claimDialog(table, thing) {
  const what = table === "racks" ? "rack" : "box";
  const m = me();
  const out = await formDialog("Claim " + what, thing.name + " — claiming tells lab mates this " + what + " is yours. They get a warning before adding samples here.",
    [{ name: "member_id", label: "Claimed by", type: "select", options: memberOptions(true, "— not claimed —"),
       value: thing.claimed_by || (m ? m.id : "") },
     { name: "note", label: "What it's for (optional)", value: thing.claim_note || "", placeholder: "e.g. MNV virus stocks" }],
    { okLabel: "Save", onSubmit: v => api("PUT", "/api/" + table + "/" + thing.id + "/claim",
      { member_id: v.member_id ? parseInt(v.member_id, 10) : null, note: v.note }) });
  if (out) { toast(out.claimed_by ? "Claimed for " + memberName(out.claimed_by) : "Claim released"); refresh(); }
}

// ---------- containers ----------

async function addFreezer() {
  const out = await formDialog("Add freezer", null, [
    { name: "name", label: "Name", required: true, placeholder: "e.g. -80 °C Freezer C" },
    [{ name: "location", label: "Location", placeholder: "Room" },
     { name: "temperature", label: "Temperature", placeholder: "-80 °C" }]],
    { okLabel: "Add", onSubmit: v => api("POST", "/api/freezers", v) });
  if (out) refresh();
}

async function addRack(f) {
  const out = await formDialog("Add rack", f.name, [{ name: "name", label: "Rack name", required: true, value: "Rack " + (f.racks.length + 1) }],
    { okLabel: "Add", onSubmit: v => api("POST", "/api/racks", { freezer_id: f.id, name: v.name }) });
  if (out) refresh();
}

async function addBox(r) {
  const out = await formDialog("Add box", r.name, [
    { name: "name", label: "Box name", required: true },
    { name: "kind", label: "Box type", type: "select", value: "grid9", options: [
      { value: "grid9", label: "9×9 grid (81 spots)" },
      { value: "grid10", label: "10×10 grid (100 spots)" },
      { value: "list", label: "No grid — a plain list (bags, large tubes, odd items)" }] }],
    { okLabel: "Add", onSubmit: v => api("POST", "/api/boxes", { rack_id: r.id, name: v.name, kind: v.kind }) });
  if (out) { await refresh(); openBox(out.id); }
}

async function editContainer(table, thing) {
  const what = { freezers: "freezer", racks: "rack", boxes: "box" }[table];
  const fields = [{ name: "name", label: "Name", value: thing.name, required: true }];
  if (table === "freezers") {
    fields.push([{ name: "location", label: "Location", value: thing.location },
                 { name: "temperature", label: "Temperature", value: thing.temperature }]);
  }
  const out = await formDialog("Edit " + what, null, fields, {
    onSubmit: v => api("PUT", "/api/" + table + "/" + thing.id, v),
    danger: { label: "Delete " + what, run: async () => {
      await api("DELETE", "/api/" + table + "/" + thing.id);
      if (table === "boxes" && state.boxId === thing.id) { state.boxId = null; state.box = null; }
      return { deleted: true };
    } },
  });
  if (out) refresh();
}

// ---------- search ----------

function renderSearch() {
  const rows = state.searchResults.rows;
  return h("div", null,
    h("div.results-head", null, h("h2", { style: { margin: 0 } }, "Search: “" + state.searchResults.q + "”"),
      h("span.muted", null, rows.length + (rows.length === 200 ? "+" : "") + " found"),
      h("button.link", { onclick: clearSearch }, "Clear")),
    rows.length ? h("table.data", null,
      h("thead", null, h("tr", null, ["Code", "Name", "Type", "Date", "Owner", "Location", "Notes"].map(x => h("th", null, x)))),
      h("tbody", null, rows.map(s => h("tr.click", { onclick: () => openBox(s.box_id, s.id) },
        h("td.muted", null, s.code), h("td", null, h("b", null, s.name)), h("td", null, h("span.chip", null, dot(typeColor(s.type)), s.type)),
        h("td", null, s.date), h("td", null, s.owner_id ? h("span.chip", null, dot(memberColor(s.owner_id)), s.owner) : "—"),
        h("td", null, s.location), h("td.muted", null, s.notes))))) : h("div.empty-state", null, "No samples match."));
}

async function runSearch(q) {
  q = q.trim();
  if (!q) return clearSearch();
  const rows = await api("GET", "/api/search?q=" + encodeURIComponent(q));
  if (rows.length === 1 && /^s-?\d+$/i.test(q)) {   // scanned/typed code: jump straight to it
    $("#search").value = "";
    return openBox(rows[0].box_id, rows[0].id);
  }
  state.searchResults = { q, rows };
  render();
}
function clearSearch() { state.searchResults = null; $("#search").value = ""; render(); }

// ---------- who's where ----------

function renderWhos() {
  const data = state.whos;
  if (!data) return h("div.empty-state", null, "Loading…");
  const members = data.members.filter(m => m.active);
  const mine = me();
  const totals = {};
  const boxesOf = {};
  data.rows.forEach(r => Object.entries(r.people).forEach(([id, n]) => {
    totals[id] = (totals[id] || 0) + n;
    boxesOf[id] = (boxesOf[id] || 0) + 1;
  }));
  const claims = id => data.rows.filter(r => r.box_claimed_by === id).length +
    new Set(data.rows.filter(r => r.rack_claimed_by === id).map(r => r.rack_id)).size;
  const unowned = data.rows.reduce((a, r) => a + (r.people["null"] || 0), 0);

  const cards = h("div.cards", null, members.map(m => h("div.card", { style: { "--c": m.color } },
    h("div", null, h("b", null, m.name)),
    h("div.big", null, totals[m.id] || 0), h("div.sub", null, "samples in " + (boxesOf[m.id] || 0) + " boxes"),
    h("div.sub", null, claims(m.id) + " racks/boxes claimed"))),
    unowned ? h("div.card", { style: { "--c": "#aaa" } }, h("b", null, "No owner"), h("div.big", null, unowned), h("div.sub", null, "samples")) : null);

  const roomInput = h("input", { type: "number", min: 1, value: state.needRoom, placeholder: "spots",
    style: { width: "90px" }, oninput: e => { state.needRoom = e.target.value; renderSidebar(); updateRoom(); } });
  const table = h("table.data", null,
    h("thead", null, h("tr", null, h("th", null, "Freezer / Rack"), h("th", null, "Box"), h("th", null, "Claimed by"),
      h("th", null, "Who has samples here"), h("th", null, "Reserved"), h("th", null, "Fill"), h("th.num", null, "Free"))),
    h("tbody", null, data.rows.map(r => {
      const claimer = r.box_claimed_by || r.rack_claimed_by;
      const tr = h("tr.click", { onclick: () => openBox(r.box_id), dataset: { free: r.free === null ? "" : r.free, claimer: claimer || "" } },
        h("td", null, r.freezer, h("div.muted", null, r.rack)),
        h("td", null, h("b", null, r.box), h("div.muted", null, KIND_LABEL[r.kind])),
        h("td", null,
          r.rack_claimed_by ? h("div", null, h("span.chip", null, dot(memberColor(r.rack_claimed_by)), memberName(r.rack_claimed_by)), h("span.muted", null, "(rack)")) : null,
          r.box_claimed_by ? h("div", null, h("span.chip", null, dot(memberColor(r.box_claimed_by)), memberName(r.box_claimed_by)), h("span.muted", null, "(box)")) : null,
          r.box_claim_note || r.rack_claim_note ? h("div.muted", null, r.box_claim_note || r.rack_claim_note) : null,
          !claimer ? h("span.muted", null, "—") : null),
        h("td", null, Object.entries(r.people).sort((a, b) => b[1] - a[1]).map(([id, n]) =>
          h("span.chip", null, dot(id === "null" ? "#aaa" : memberColor(parseInt(id, 10))), (id === "null" ? "No owner" : memberName(parseInt(id, 10))) + " " + n))),
        h("td", null, Object.entries(r.reservations).map(([id, n]) =>
          h("span.chip", null, dot(memberColor(parseInt(id, 10))), memberName(parseInt(id, 10)) + " " + n)), !r.reserved ? h("span.muted", null, "—") : null),
        h("td", null, r.capacity ? fillBar(r) : h("span.muted", null, r.used + " items")),
        h("td.num", null, r.free === null ? "—" : r.free));
      return tr;
    })));
  const view = h("div", null,
    h("h2", { style: { marginTop: 0 } }, "Who's where"),
    cards,
    h("div.toolbar", null, h("span", null, "Find room: I need"), roomInput,
      h("span.muted", null, "free spots in a box that isn't claimed by someone else" + (mine ? "" : " (set who you are, top right)") + ". Matching boxes turn green.")),
    table);
  setTimeout(updateRoom, 0);
  return view;
  function updateRoom() {
    const n = parseInt(state.needRoom, 10);
    table.querySelectorAll("tbody tr").forEach(tr => {
      const free = tr.dataset.free === "" ? null : parseInt(tr.dataset.free, 10);
      const claimer = tr.dataset.claimer ? parseInt(tr.dataset.claimer, 10) : null;
      tr.classList.toggle("room", n > 0 && free !== null && free >= n && (!claimer || (me() && claimer === me().id)));
    });
  }
}

function fillBar(r) {
  const parts = Object.entries(r.people).sort((a, b) => b[1] - a[1]).map(([id, n]) =>
    h("span", { style: { width: (100 * n / r.capacity) + "%", background: id === "null" ? "#aaa" : memberColor(parseInt(id, 10)) },
                title: (id === "null" ? "No owner" : memberName(parseInt(id, 10))) + ": " + n }));
  if (r.reserved) parts.push(h("span", { style: { width: (100 * r.reserved / r.capacity) + "%", background: "repeating-linear-gradient(45deg,#ccc,#ccc 2px,#fff 2px,#fff 4px)" }, title: "Reserved: " + r.reserved }));
  return h("div", null, h("div.bar", null, parts), h("div.muted", { style: { fontSize: "11px" } }, r.used + " / " + r.capacity));
}

// ---------- history ----------

const FIELDS = [["name", "Name"], ["type", "Type"], ["date", "Date"], ["owner", "Owner"], ["notes", "Notes"],
                ["location", "Location"], ["claimed_by", "Claimed by"], ["claim_note", "Claim note"],
                ["temperature", "Temp"], ["color", "Color"], ["active", "Active"]];

function describeChange(r) {
  const b = r.before || {}, a = r.after || {};
  const show = (k, v) => k === "claimed_by" || k === "member_id" ? (v ? memberName(v) : "nobody") : (v === "" || v === null || v === undefined ? "∅" : v);
  if (r.action === "add" && r.entity === "sample") return [a.location, a.type, a.owner].filter(Boolean).join(" · ");
  if (r.action === "remove") return "was at " + (b.location || "");
  if (r.action === "move") return h("span", null, h("del", null, b.location), " → ", h("ins", null, a.location));
  const out = [];
  for (const [k, label] of FIELDS) {
    if (!(k in b) || !(k in a) || b[k] === a[k]) continue;
    if (k === "location" && r.entity === "sample") continue;
    out.push(h("div", null, label + ": ", h("del", null, show(k, b[k])), " → ", h("ins", null, show(k, a[k]))));
  }
  if (r.entity === "reservation" && (a.note || b.note)) out.push(h("div", null, a.note || b.note));
  return out;
}

function renderHistory() {
  const hs = state.history;
  const f = hs.filters;
  const actions = ["", "add", "edit", "move", "remove", "claim", "unclaim", "reserve", "unreserve", "rename", "delete"];
  const filterBar = h("div.toolbar", null,
    h("select", { onchange: e => { f.action = e.target.value; loadHistory(true); } },
      actions.map(a => h("option", { value: a, selected: a === f.action }, a ? a : "All actions"))),
    h("select", { onchange: e => { f.user = e.target.value; loadHistory(true); } },
      h("option", { value: "" }, "Everyone"),
      state.members.map(m => h("option", { value: m.name, selected: m.name === f.user }, m.name))),
    h("input", { type: "search", placeholder: "Filter by sample, box, note…", value: f.q, style: { width: "260px" },
      onchange: e => { f.q = e.target.value; loadHistory(true); } }));
  return h("div", null,
    h("h2", { style: { marginTop: 0 } }, "History"),
    h("p.muted", { style: { marginTop: 0 } }, "Every change anyone makes is kept here, including samples that were removed."),
    filterBar,
    h("table.data", null,
      h("thead", null, h("tr", null, ["When", "Who", "Action", "What", "Change"].map(x => h("th", null, x)))),
      h("tbody", null, hs.rows.map(r => h("tr" + (r.entity === "sample" ? ".click" : ""), {
        onclick: r.entity === "sample" ? () => openSampleFromHistory(r) : null,
      },
        h("td", { style: { whiteSpace: "nowrap" } }, fmtTime(r.ts)),
        h("td", null, r.user || "—"),
        h("td", null, h("span.act." + r.action, null, r.action)),
        h("td", null, h("span.muted", null, r.entity + " "), h("b", null, r.label)),
        h("td.diff", null, describeChange(r)))))),
    !hs.rows.length ? h("div.empty-state", null, "Nothing here.") : null,
    !hs.done && hs.rows.length ? h("div", { style: { textAlign: "center", marginTop: "12px" } },
      h("button", { onclick: () => loadHistory(false) }, "Load more")) : null);
}

async function openSampleFromHistory(r) {
  try {
    const s = await api("GET", "/api/samples/" + r.entity_id);
    openBox(s.box_id, s.id);
  } catch (e) { toast("That sample has been removed. Its last details are shown in this row.", true); }
}

async function loadHistory(reset) {
  const hs = state.history;
  if (reset) { hs.rows = []; hs.done = false; }
  const p = new URLSearchParams({ limit: 100, offset: hs.rows.length });
  Object.entries(hs.filters).forEach(([k, v]) => { if (v) p.set(k, v); });
  const rows = await api("GET", "/api/history?" + p);
  hs.rows = hs.rows.concat(rows);
  hs.done = rows.length < 100;
  if (state.tab === "history" && !state.searchResults) render();
}

// ---------- labels ----------

async function printLabels(ids) {
  if (!ids.length) return;
  const samples = [];
  const known = {};
  if (state.box) state.box.samples.forEach(s => { known[s.id] = s; });
  for (const id of ids) samples.push(known[id] || await api("GET", "/api/samples/" + id));
  $("#print-area").replaceChildren(h("div.label-sheet", null, samples.map(s => h("div.label", null,
    h("div.code", null, s.code),
    h("div.lname", null, s.name),
    h("div", null, [s.type, s.date].filter(Boolean).join(" · ")),
    h("div", null, s.owner || ""),
    h("div.loc", null, s.location)))));
  state.selecting = false;
  state.selected.clear();
  render();
  setTimeout(() => window.print(), 50);
}

// ---------- CSV import / export ----------

function saveFile(blob, filename) {
  const a = h("a", { href: URL.createObjectURL(blob), download: filename });
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

// fetch (not a plain link) so the passcode header goes along.
async function downloadCsv(boxId) {
  const res = await fetch("/api/export.csv" + (boxId ? "?box_id=" + boxId : ""),
    { headers: { "X-Passcode": load("passcode", ""), "X-User": encodeURIComponent(load("user", "")) } });
  if (!res.ok) { toast("Export failed", true); return; }
  const name = (/filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "") || [])[1] || "freezer-samples.csv";
  saveFile(await res.blob(), name);
  toast("Downloaded " + name);
}

function downloadTemplate() {
  const csv = "freezer,rack,box,box_type,position,name,type,date,owner,notes\n" +
    "-80 °C Freezer A,Rack 1,Box 01,9x9,A1,MNV-1 P3 stock,Virus stock,2025-03-14,Your Name,Titer 1e7 PFU/mL\n" +
    "-80 °C Freezer A,Rack 1,Large tubes,list,,Serum pool 1,Serum,2025-04-02,Your Name,\n";
  saveFile(new Blob(["\ufeff" + csv], { type: "text/csv" }), "freezer-import-template.csv");
}

function csvDialog() {
  const out = h("div");
  const file = h("input", { type: "file", accept: ".csv,.tsv,.txt,text/csv",
    onchange: async () => {
      const f = file.files[0];
      if (!f) return;
      out.replaceChildren(h("div.muted", null, "Checking " + f.name + "…"));
      try {
        const text = await f.text();
        const report = await api("POST", "/api/import", { csv: text, commit: false });
        out.replaceChildren(importPreview(report, text, f.name));
      } catch (e) { out.replaceChildren(h("div.error", null, e.message)); }
    } });
  dialog.classList.add("wide");
  openDialog(h("div", null,
    h("h3", null, "Import / Export"),
    h("div.sub", null, "CSV files open in Excel, Numbers and Google Sheets."),
    h("div.csv-section", null,
      h("h4", null, "Export"),
      h("div.actions", { style: { justifyContent: "flex-start", marginTop: 0 } },
        h("button.primary", { onclick: () => downloadCsv() }, "Export all samples"),
        state.box && state.tab === "boxes" ? h("button", { onclick: () => downloadCsv(state.box.id) }, "Export " + state.box.name) : null)),
    h("div.csv-section", null,
      h("h4", null, "Import"),
      h("p.muted", { style: { margin: "0 0 8px" } },
        "Columns: freezer, rack, box, box_type (9x9 / 10x10 / list), position (A1…J10), name, type, date, owner, notes. ",
        "Only name is required. Missing freezers, racks, boxes and lab mates are created. You'll see a preview before anything is saved."),
      h("div.actions", { style: { justifyContent: "flex-start", marginTop: 0 } },
        file, h("button.link", { onclick: downloadTemplate }, "Download a template")),
      out),
    h("div.actions", null, h("button", { onclick: () => closeDialog() }, "Close"))));
}

function importPreview(report, text, filename) {
  const c = report.created;
  const created = [["freezers", c.freezers], ["racks", c.racks], ["boxes", c.boxes], ["lab mates", c.members]]
    .filter(([, list]) => list.length)
    .map(([what, list]) => h("li", null, list.length + " new " + what + ": " + list.slice(0, 8).join(", ") + (list.length > 8 ? "…" : "")));
  const problems = report.errors.map(e => ({ line: e.line, name: e.name, msg: e.error }))
    .concat(report.skipped.map(s => ({ line: s.line, name: s.name, msg: "skipped: " + s.reason })))
    .sort((a, b) => a.line - b.line);
  const go = h("button.primary", { disabled: !report.added, onclick: async () => {
    go.disabled = true;
    try {
      const done = await api("POST", "/api/import", { csv: text, commit: true });
      closeDialog();
      toast("Imported " + done.added + " samples from " + filename);
      refresh();
    } catch (e) { go.disabled = false; toast(e.message, true); }
  } }, "Import " + report.added + " samples");
  return h("div", { style: { marginTop: "12px" } },
    h("div", null, h("b", null, filename), ": " + report.rows + " rows. Matched columns: ",
      h("span.muted", null, Object.entries(report.columns).map(([k, v]) => v === k ? k : v + " → " + k).join(", "))),
    h("ul.summary-list", null,
      h("li", null, h("b", null, report.added + " samples"), " ready to import"),
      created,
      problems.length ? h("li", null, h("b", null, problems.length + " rows"), " will be left out (listed below)") : null),
    problems.length ? h("div.import-errors", null, h("table.data", null,
      h("thead", null, h("tr", null, h("th", null, "Row"), h("th", null, "Sample"), h("th", null, "Problem"))),
      h("tbody", null, problems.map(p => h("tr", null, h("td", null, p.line), h("td", null, p.name || "—"), h("td", null, p.msg)))))) : null,
    h("div.actions", null, go));
}

dialog.addEventListener("close", () => dialog.classList.remove("wide"));

// ---------- wiring ----------

document.querySelectorAll(".tabs button").forEach(b => b.addEventListener("click", async () => {
  state.tab = b.dataset.tab;
  state.searchResults = null;
  state.moving = null;
  $("#search").value = "";
  if (state.tab === "whos") state.whos = await api("GET", "/api/whos-where");
  if (state.tab === "history") await loadHistory(true);
  render();
}));

$("#search-form").addEventListener("submit", e => { e.preventDefault(); runSearch($("#search").value); });
$("#search").addEventListener("search", e => { if (!e.target.value) clearSearch(); });
$("#whoami").addEventListener("click", () => askWho(true));
$("#csv-btn").addEventListener("click", csvDialog);

document.addEventListener("keydown", e => {
  if (e.key === "Escape" && !dialog.open) {
    if (state.moving) cancelMove();
    else if (state.selecting) { state.selecting = false; state.selected.clear(); render(); }
  }
  if (e.key === "/" && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA" && !dialog.open) {
    e.preventDefault();
    $("#search").focus();
  }
});

// Keep everyone's screen current without clobbering an open form.
setInterval(() => {
  if (dialog.open || document.hidden) return;
  if (state.tab === "history" && !state.searchResults) return;   // don't jump the log while reading
  refresh().catch(() => {});
}, REFRESH_MS);

(async function start() {
  try {
    const cfg = await (await fetch("/api/config")).json();
    if (cfg.needs_passcode && !load("passcode", "")) await askPasscode();
    await refresh();
    const last = parseInt(load("box", ""), 10);
    const exists = last && state.tree.freezers.some(f => f.racks.some(r => r.boxes.some(b => b.id === last)));
    if (exists) await openBox(last);
    else {
      const first = state.tree.freezers.flatMap(f => f.racks.flatMap(r => r.boxes))[0];
      if (first) await openBox(first.id);
    }
    if (!me()) await askWho(false);
  } catch (e) {
    $("#main").replaceChildren(h("div.empty-state", null, "Could not load: " + e.message));
  }
})();
