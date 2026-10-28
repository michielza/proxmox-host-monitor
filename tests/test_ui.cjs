"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

// Exercise the shipped renderer/handlers with a small DOM double. Geometry and
// browser behavior still require browser checks; no application functions are copied.
class Element {
  constructor(tag = "div") {
    this.tag = tag; this.children = []; this.attributes = {};
    this.value = ""; this.hidden = false; this.title = ""; this.text = "";
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(""); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ""; this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
}

function app() {
  const elements = new Map();
  const context = vm.createContext({
    Intl, Date, clearTimeout, AbortController,
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, new Element());
        return elements.get(id);
      },
      createElement: tag => new Element(tag),
      addEventListener() {},
    },
  });
  const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
  const startup = source.lastIndexOf("(async () => {");
  assert.ok(startup > 0, "startup boundary must exist");
  vm.runInContext(source.slice(0, startup), context);
  // Disable only chart geometry and network sampling, outside this test's scope.
  vm.runInContext("drawGraphs = () => {}; sample = () => {}; state.selected = {labels: {}};", context);
  return {
    context, elements,
    run(code, input) { context.input = input; return vm.runInContext(code, context); },
    get(id) { return context.document.getElementById(id); },
  };
}

function stats(storage, memory = {}, version = "pve-manager/8.4.21/example-build") {
  return {cpu: {}, memory, storage, guests: {}, network: [], errors: {}, version, sampled_at: Date.now() / 1000};
}

test("storage refresh order is natural and stable; labels/metrics follow identity", () => {
  const a = app();
  a.run("state.selected.labels = input", {"storage:LOCAL-lvm2": "SSD", "storage:local-lvm10": "HDD"});
  const entries = [
    Object.freeze({storage: "local-lvm10", type: "lvmthin", used: 10, total: 100, avail: 90, active: true}),
    Object.freeze({storage: "LOCAL-lvm2", type: "lvmthin", used: 20, total: 100, avail: 80, active: true}),
    Object.freeze({storage: "local", type: "dir", used: 30, total: 100, avail: 70, active: true}),
  ];
  for (const order of [[0,1,2], [0,2,1], [1,0,2], [1,2,0], [2,0,1], [2,1,0]]) {
    const storage = Object.freeze(order.map(i => entries[i]));
    a.run("renderStats(input)", stats(storage));
    const rows = a.get("storage-body").children;
    assert.deepEqual(rows.map(row => row.children[0].children[0].textContent), ["local", "LOCAL-lvm2", "local-lvm10"]);
    assert.equal(rows[1].children[0].children[1].textContent, "SSD");
    assert.equal(rows[2].children[0].children[1].textContent, "HDD");
    assert.equal(rows[1].children[2].textContent, "20 B / 100 B");
    assert.equal(rows[2].children[3].textContent, "90 B");
    assert.deepEqual(storage.map(x => x.storage), order.map(i => entries[i].storage));
  }
});

test("missing and empty storage remain distinct", () => {
  const a = app();
  a.run("renderStats(input)", stats(null));
  assert.match(a.get("storage-body").textContent, /Storage unavailable/);
  a.run("renderStats(input)", stats([]));
  assert.equal(a.get("storage-body").textContent, "No visible storage entries.");
});

const hosts = () => [
  {id: "ten", name: "host10", provider: "Provider Z", location: "Location Z"},
  {id: "other-provider", name: "aaa", provider: "Provider A", location: "Location A"},
  {id: "other-location", name: "aaa", provider: "Provider Z", location: "Location A"},
  {id: "two", name: "HOST2", provider: "Provider Z", location: "Location Z"},
];

test("initial selection matches nested tree order, not global name order", async () => {
  const a = app();
  const inventory = Object.freeze(hosts().map(Object.freeze));
  a.run("state.hosts = input; renderTree();", inventory);
  const firstButton = a.get("tree").children[0].children[1].children[1];
  assert.equal(firstButton.children[1].textContent, "HOST2");
  assert.equal(a.run("firstHostInTree().id"), "two");
  // Run the actual async unlock flow with a fixture-only host-list response.
  a.run("api = async () => ({hosts: state.hosts}); $('search').value = 'stale search';");
  await a.run("unlock()");
  assert.equal(a.run("state.selected.id"), "two");
  assert.equal(a.get("search").value, "");
  assert.deepEqual(inventory.map(h => h.id), ["ten", "other-provider", "other-location", "two"]);
});

test("settings return retains selection or falls back to first displayed host", () => {
  const a = app();
  a.run("state.hosts = input; state.selected = state.hosts[0];", hosts());
  a.get("back-button").onclick();
  assert.equal(a.run("state.selected.id"), "ten");
  a.run("state.selected = {id: 'removed'}; $('search').value = 'stale';");
  a.get("back-button").onclick();
  assert.equal(a.run("state.selected.id"), "two");
  assert.equal(a.get("search").value, "");
});

test("empty inventory opens the empty view", async () => {
  const a = app();
  assert.equal(a.run("firstHostInTree()"), null);
  a.run("api = async () => ({hosts: []});");
  await a.run("unlock()");
  assert.equal(a.get("empty").hidden, false);
  assert.equal(a.get("overview").hidden, true);
});

test("RAM uses available then free, accepts zero and rejects invalid values", () => {
  const a = app(), GiB = 1024 ** 3;
  a.run("renderStats(input)", stats([], {available: 10*GiB, free: 2*GiB}));
  assert.equal(a.get("ram-value").textContent, "10 GiB");
  assert.equal(a.get("ram-spec").textContent, "Available memory");
  a.run("renderStats(input)", stats([], {free: 2*GiB}));
  assert.equal(a.get("ram-value").textContent, "2 GiB");
  assert.match(a.get("ram-spec").textContent, /^Free memory/);
  a.run("renderStats(input)", stats([], {available: 0, free: GiB}));
  assert.equal(a.get("ram-value").textContent, "0 B");
  assert.equal(a.get("ram-spec").textContent, "Available memory");
  for (const value of [undefined, null, -1, NaN, Infinity, "12"]) {
    a.run("renderStats(input)", stats([], {available: value, free: 0}));
    assert.equal(a.get("ram-value").textContent, "0 B");
    assert.match(a.get("ram-spec").textContent, /^Free memory/);
    a.run("renderStats(input)", stats([], {free: value, total: GiB, used: 1}));
    assert.equal(a.get("ram-value").textContent, "—");
  }
});

test("version formatting and stale details reset when changing host", () => {
  const a = app();
  a.run("renderStats(input)", stats([], {available: 1}));
  assert.equal(a.get("version").textContent, "Proxmox VE · 8.4.21");
  assert.equal(a.get("version").title, "pve-manager/8.4.21/example-build");
  a.run("renderStats(input)", stats([], {}, "<img src=x>"));
  assert.equal(a.get("version").textContent, "Proxmox VE · <img src=x>");
  assert.equal(a.get("version").children.length, 0);
  a.run("renderStats(input)", stats([], {}, null));
  assert.equal(a.get("version").textContent, "Proxmox VE · version unavailable");
  a.run("selectHost(input)", {...hosts()[0], labels: {}});
  assert.equal(a.get("version").textContent, "Proxmox VE · waiting for data");
  assert.equal(a.get("version").title, "");
  assert.equal(a.get("ram-spec").textContent, "Waiting for memory data");
  assert.equal(a.get("ram-spec").title, "");
});
