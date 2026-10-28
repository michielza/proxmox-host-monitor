"use strict";
const $ = id => document.getElementById(id);
const state = {hosts: [], selected: null, csrf: "", timer: null, controller: null,
  samples: [], last: null, settings: false, busy: false, generation: 0, failures: 0};
let certificateTicket = "";
let certificateGeneration = 0;
let caPem = "";
const num = value => typeof value === "number" && Number.isFinite(value);
const fmt = value => num(value) ? new Intl.NumberFormat(undefined, {maximumFractionDigits: 1}).format(value) : "—";
const hostNameOrder = new Intl.Collator(undefined, {numeric: true, sensitivity: "base"});
function bytes(value) {
  if (!num(value) || value < 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  const power = value ? Math.min(5, Math.floor(Math.log(value) / Math.log(1024))) : 0;
  return `${fmt(value / 1024 ** power)} ${units[power]}`;
}
function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function labels(target, host, key) {
  target.replaceChildren();
  const value = host.labels?.[key];
  if (value) target.append(node("span", value, "label"));
}
async function api(path, options = {}) {
  const headers = {"Content-Type": "application/json", "X-CSRF-Token": state.csrf};
  const response = await fetch(path, {...options, headers, cache: "no-store", credentials: "same-origin"});
  const data = await response.json();
  if (response.status === 401 && path !== "/api/login") lock();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}
function stop() {
  clearTimeout(state.timer);
  state.timer = null;
  if (state.controller) state.controller.abort();
  state.controller = null;
  state.generation++;
  state.busy = false;
}
function lock() {
  stop();
  state.csrf = "";
  state.hosts = [];
  state.samples = [];
  state.last = null;
  $("tree").replaceChildren();
  $("settings-hosts").replaceChildren();
  $("host-form").reset();
  resetCertificate();
  $("shell").hidden = true;
  $("login").hidden = false;
  $("access-key").value = "";
  $("access-key").focus();
}
function renderTree() {
  const query = $("search").value.trim().toLowerCase();
  const groups = new Map();
  for (const host of state.hosts) {
    if (![host.name, host.provider, host.location, ...Object.values(host.labels || {})].join(" ").toLowerCase().includes(query)) continue;
    if (!groups.has(host.provider)) groups.set(host.provider, new Map());
    const locations = groups.get(host.provider);
    if (!locations.has(host.location)) locations.set(host.location, []);
    locations.get(host.location).push(host);
  }
  $("tree").replaceChildren();
  for (const [provider, locations] of groups) {
    const group = node("details"); group.open = true;
    group.append(node("summary", provider));
    for (const [location, hosts] of locations) {
      const branch = node("details"); branch.open = true;
      branch.append(node("summary", location));
      for (const host of hosts.sort((a, b) => hostNameOrder.compare(a.name, b.name))) {
        const button = node("button", undefined, `server-button${state.selected?.id === host.id ? " active" : ""}`);
        button.append(node("span", "", "dot"), node("span", host.name));
        if (state.selected?.id === host.id) button.setAttribute("aria-current", "page");
        button.onclick = () => selectHost(host);
        branch.append(button);
      }
      group.append(branch);
    }
    $("tree").append(group);
  }
  if (!groups.size) $("tree").append(node("p", "No matching servers.", "fine"));
  $("host-count").textContent = state.hosts.length;
}
async function loadHosts() {
  const data = await api("/api/hosts");
  state.hosts = data.hosts;
  renderTree();
  renderSettingsList();
}
function selectHost(host) {
  stop();
  state.selected = host; state.settings = false; state.samples = []; state.last = null; state.failures = 0;
  $("settings").hidden = true; $("empty").hidden = true; $("overview").hidden = false;
  $("breadcrumb").textContent = `${host.provider} / ${host.location} / ${host.name}`;
  $("server-name").textContent = host.name;
  labels($("server-labels"), host, "server"); labels($("cpu-labels"), host, "cpu"); labels($("ram-labels"), host, "ram");
  $("stats-error").hidden = true;
  for (const id of ["cpu-value", "ram-value", "cpu-model", "cpu-spec", "ram-used", "ping-value", "guest-counts", "network-ports", "storage-count", "version"]) $(id).textContent = "—";
  $("version").textContent = "Proxmox VE · waiting for data";
  $("version").title = "";
  $("ram-spec").textContent = "Waiting for memory data";
  $("ram-spec").title = "";
  $("storage-body").replaceChildren();
  $("cpu-bar").value = 0; $("ram-bar").value = 0;
  $("updated").textContent = "Waiting for first sample";
  $("connection").className = ""; $("connection").textContent = "Connecting…";
  $("ping-note").textContent = "Waiting for an ICMP sample from the monitor.";
  drawGraphs(); renderTree(); sample();
}
function percent(used, total) { return num(used) && num(total) && total > 0 ? Math.max(0, Math.min(100, used / total * 100)) : null; }
function guestText(kind, counts) {
  if (!counts) return `${kind}: unavailable`;
  return `${kind}: ${counts.running} running / ${counts.stopped} stopped${counts.other ? ` / ${counts.other} other` : ""} · ${counts.total} total`;
}
function renderStats(data) {
  const cpu = num(data.cpu.usage) ? data.cpu.usage * 100 : null;
  const ram = percent(data.memory.used, data.memory.total);
  $("cpu-value").textContent = `${fmt(cpu)}%`;
  $("cpu-model").textContent = data.cpu.model || "CPU model unavailable";
  $("cpu-spec").textContent = `${fmt(data.cpu.sockets)} sockets · ${fmt(data.cpu.cores)} cores · ${fmt(data.cpu.threads)} threads`;
  $("cpu-bar").value = cpu || 0;
  const available = num(data.memory.available) && data.memory.available >= 0;
  const free = num(data.memory.free) && data.memory.free >= 0;
  $("ram-value").textContent = bytes(available ? data.memory.available : free ? data.memory.free : null);
  $("ram-spec").textContent = available ? "Available memory" : free ? "Free memory · available not reported" : "Memory availability not reported";
  $("ram-spec").title = available ? "Includes memory the OS estimates it can reclaim from caches." : free ? "Unused RAM only; reclaimable cache is not included. This API does not report available memory." : "The API returned neither a valid available-memory value nor a free-memory value.";
  $("ram-used").textContent = `${bytes(data.memory.used)} used / ${bytes(data.memory.total)} total`;
  $("ram-bar").value = ram || 0;
  $("storage-count").textContent = data.storage ? `${data.storage.length} entries` : "Unavailable";
  $("storage-body").replaceChildren();
  for (const storage of data.storage || []) {
    const row = node("tr");
    const name = node("td"); name.append(node("strong", storage.storage));
    const tags = node("div", undefined, "labels"); labels(tags, state.selected, `storage:${storage.storage}`); name.append(tags);
    if (!storage.active) name.append(node("span", " · inactive", "fine"));
    const usage = percent(storage.used, storage.total);
    const barCell = node("td");
    const bar = node("progress"); bar.max = 100; bar.value = usage || 0; bar.setAttribute("aria-label", `${storage.storage} usage`);
    barCell.append(bar, node("span", `${fmt(usage)}%`, "fine"));
    row.append(name, node("td", storage.type || "—"), node("td", `${bytes(storage.used)} / ${bytes(storage.total)}`), node("td", bytes(storage.avail), "available"), barCell);
    $("storage-body").append(row);
  }
  if (!data.storage?.length) {
    const row = node("tr"); const cell = node("td", data.storage ? "No visible storage entries." : "Storage unavailable. Check API permissions."); cell.colSpan = 5; row.append(cell); $("storage-body").append(row);
  }
  $("guest-counts").replaceChildren(node("p", guestText("VMs", data.guests.qemu), "guest-line"), node("p", guestText("Containers", data.guests.lxc), "guest-line"));
  $("network-ports").replaceChildren();
  const ports = data.network.filter(port => ["eth", "bond", "OVSBond"].includes(port.type));
  for (const port of ports) {
    const line = node("div", undefined, `port${port.active ? "" : " off"}`);
    line.append(node("span", "", "dot"), node("span", `${port.iface} · ${port.active ? "active" : "inactive"}`));
    const value = state.selected.labels?.[`network:${port.iface}`]; if (value) line.append(node("span", value, "label"));
    $("network-ports").append(line);
  }
  if (!ports.length) $("network-ports").textContent = data.errors.network ? "Interface data unavailable" : "No physical ports reported";
  $("ping-value").textContent = num(data.ping_ms) ? `${fmt(data.ping_ms)} ms` : data.ping_error ? "Unavailable" : "No reply";
  $("ping-note").textContent = data.ping_error || (num(data.ping_ms) ? "ICMP round-trip time from the machine running this monitor." : "No ICMP echo reply. The host may be unreachable or ICMP may be filtered.");
  const version = typeof data.version === "string" ? data.version.trim() : "";
  const release = version.match(/^pve-manager\/([^/]+)/)?.[1];
  $("version").textContent = version ? `Proxmox VE · ${release || version}` : "Proxmox VE · version unavailable";
  $("version").title = version;
  $("updated").textContent = `Updated ${new Date(data.sampled_at * 1000).toLocaleTimeString()}`;
  $("connection").textContent = data.demo ? "● Demo · fictional data" : "● Connected";
  $("connection").className = "good";
  const errors = Object.entries(data.errors).map(([key, value]) => `${key}: ${value}`);
  $("stats-error").hidden = !errors.length;
  $("stats-error").textContent = errors.join(" ");
  state.samples.push({t: data.sampled_at * 1000, cpu, ram, ping: data.ping_ms});
  state.samples = state.samples.filter(s => s.t >= Date.now() - 300000).slice(-31);
  drawGraphs();
}
function drawGraph(id, field, max) {
  const svg = $(id); svg.replaceChildren();
  const ns = "http://www.w3.org/2000/svg";
  const baseline = document.createElementNS(ns, "line");
  for (const [key, value] of Object.entries({x1: 0, y1: 60, x2: 300, y2: 60})) baseline.setAttribute(key, value);
  svg.append(baseline);
  const now = Date.now();
  const samples = state.samples.filter(s => s.t >= now - 300000);
  if (!samples.some(s => num(s[field]))) {
    const text = document.createElementNS(ns, "text"); text.setAttribute("x", "0"); text.setAttribute("y", "35"); text.textContent = "Waiting for samples"; svg.append(text); return;
  }
  const scale = max || Math.max(1, ...samples.map(s => num(s[field]) ? s[field] * 1.2 : 0));
  let segment = [];
  function flush() {
    if (segment.length === 1) {const dot = document.createElementNS(ns, "circle"); dot.setAttribute("cx", segment[0][0]); dot.setAttribute("cy", segment[0][1]); dot.setAttribute("r", "2"); svg.append(dot);}
    else if (segment.length) {const line = document.createElementNS(ns, "polyline"); line.setAttribute("points", segment.map(p => p.join(",")).join(" ")); svg.append(line);}
    segment = [];
  }
  for (let i = 0; i < samples.length; i++) {
    const s = samples[i];
    if (!num(s[field]) || (i && s.t - samples[i - 1].t > 20000)) flush();
    if (num(s[field])) segment.push([Math.max(0, 300 - (now - s.t) / 1000), 60 - Math.max(0, Math.min(1, s[field] / scale)) * 56]);
  }
  flush();
}
function drawGraphs() { drawGraph("cpu-graph", "cpu", 100); drawGraph("ram-graph", "ram", 100); drawGraph("ping-graph", "ping"); }
async function sample() {
  if (state.busy || !state.selected || state.settings || document.hidden || !state.csrf) return;
  clearTimeout(state.timer);
  state.busy = true; $("refresh").disabled = true;
  const generation = state.generation;
  const hostId = state.selected.id;
  const controller = new AbortController(); state.controller = controller;
  try {
    const data = await api(`/api/hosts/${encodeURIComponent(hostId)}/stats`, {signal: controller.signal});
    if (generation !== state.generation) return;
    state.failures = 0; state.last = data; renderStats(data);
  } catch (error) {
    if (error.name === "AbortError" || generation !== state.generation) return;
    state.failures++;
    $("connection").textContent = state.last ? "● Stale · last successful readings retained" : "● Unavailable";
    $("connection").className = "bad";
    $("stats-error").hidden = false; $("stats-error").textContent = error.message;
    state.samples.push({t: Date.now(), cpu: null, ram: null, ping: null});
    state.samples = state.samples.filter(s => s.t >= Date.now() - 300000).slice(-31);
    drawGraphs();
  } finally {
    if (generation === state.generation) {
      state.busy = false; state.controller = null; $("refresh").disabled = false;
      const delay = state.failures >= 3 ? 30000 : 10000;
      $("sample-note").textContent = state.failures >= 3 ? "Retrying every 30s · Refresh to try now" : "Updates every 10s while visible";
      if (!document.hidden && !state.settings && state.csrf) state.timer = setTimeout(sample, delay);
    }
  }
}
function renderSettingsList() {
  $("settings-hosts").replaceChildren();
  for (const host of state.hosts.filter(h => !h.demo)) {
    const button = node("button", host.name); button.type = "button"; button.onclick = () => editHost(host); $("settings-hosts").append(button);
  }
  if (!state.hosts.some(h => !h.demo)) $("settings-hosts").append(node("p", "No real servers configured.", "fine"));
}
function addLabelRow(key = "server", value = "") {
  const row = node("div", undefined, "label-row");
  const kind = node("select"); kind.setAttribute("aria-label", "Component type");
  for (const [id, title] of [["server","Server"],["cpu","CPU"],["ram","RAM"],["storage","Storage"],["network","Network port"]]) {
    const option = node("option", title); option.value=id; kind.append(option);
  }
  const parts=key.split(":"); kind.value=parts[0];
  const target=node("input"); target.value=parts.slice(1).join(":"); target.maxLength=100;
  target.setAttribute("aria-label", "Storage or interface name");
  const label=node("input"); label.value=value; label.placeholder="Your label"; label.maxLength=250; label.setAttribute("aria-label", "Label text");
  const remove=node("button", "×"); remove.type="button"; remove.setAttribute("aria-label", "Remove label"); remove.onclick=()=>row.remove();
  function updateTarget() {target.hidden=!["storage","network"].includes(kind.value); target.required=!target.hidden; target.placeholder=kind.value==="storage" ? "Storage name, e.g. local-lvm" : "Port name, e.g. eno1";}
  kind.onchange=updateTarget; updateTarget(); row.append(kind,target,label,remove); $("label-rows").append(row);
}
function readLabels() {
  const result={};
  for (const row of $("label-rows").children) {
    const [kind,target,label]=row.children;
    if (!label.value.trim()) continue;
    const key=kind.value+(["storage","network"].includes(kind.value) ? `:${target.value.trim()}` : "");
    if (Object.hasOwn(result,key)) throw new Error("Use one label per component; combine descriptions in its label text.");
    result[key]=label.value.trim();
  }
  return result;
}
function editHost(host = null) {
  $("host-form").reset();
  $("config-message").textContent = "";
  $("form-heading").textContent = host ? `Edit ${host.name}` : "Add server";
  $("host-id").value = host?.id || crypto.randomUUID();
  const fields = {name: "name", node: "node", provider: "provider", location: "location", url: "url", "token-id": "token_id"};
  for (const [input, key] of Object.entries(fields)) $(`host-${input}`).value = host?.[key] || "";
  $("host-token-secret").required = !host;
  $("label-rows").replaceChildren();
  for (const [key,value] of Object.entries(host?.labels || {})) addLabelRow(key,value);
  $("delete-host").hidden = !host;
  resetCertificate();
  $("trust-mode").querySelector('[value="existing"]').disabled = !host;
  $("trust-mode").value = host ? "existing" : "system";
  updateTrust();
}
function resetCertificate() {
  certificateGeneration++;
  certificateTicket = ""; caPem = "";
  $("ca-file").value = "";
  $("trust-confirmed").checked = false;
  $("certificate-details").hidden = true;
  $("certificate-summary").textContent = "";
  $("connection-message").textContent = "";
}
function updateTrust() {
  const mode = $("trust-mode").value;
  $("ca-upload").hidden = mode !== "ca";
  $("certificate-details").hidden = mode !== "certificate" || !certificateTicket;
  $("trust-description").textContent = {existing: "Uses the certificate trust already saved for this server.", system: "Uses the monitor's standard trusted certificate authorities.", ca: "Upload the CA that issued this server's certificate. It will be stored and reused automatically.", certificate: "Inspect the certificate, verify its fingerprint, then explicitly approve it. Hostname and expiry checks remain enabled."}[mode];
  $("connection-message").textContent = "";
}
function connectionPayload() {
  if ($("trust-mode").value === "ca" && !caPem) throw new Error("Choose a CA certificate file first.");
  return {id: $("host-id").value, name: $("host-name").value,
    node: $("host-node").value, provider: $("host-provider").value, location: $("host-location").value,
    url: $("host-url").value, token_id: $("host-token-id").value, token_secret: $("host-token-secret").value,
    labels: readLabels(), trust_mode: $("trust-mode").value, ca_pem: caPem,
    certificate_ticket: certificateTicket, trust_confirmed: $("trust-confirmed").checked};
}
$("trust-mode").onchange = () => {$("trust-confirmed").checked = false; updateTrust();};
$("host-url").oninput = () => {resetCertificate(); updateTrust();};
$("ca-file").onchange = async () => {
  caPem = "";
  const file = $("ca-file").files[0], generation = certificateGeneration;
  try {
    if (!file) return;
    if (file.size > 32768) throw new Error("Certificate files must be at most 32 KiB.");
    const pem = await file.text();
    if (generation === certificateGeneration && $("ca-file").files[0] === file) caPem = pem;
  } catch (error) {$("connection-message").textContent = error.message;}
};
$("inspect-certificate").onclick = async () => {
  const button = $("inspect-certificate"), generation = certificateGeneration;
  button.disabled = true;
  certificateTicket = ""; $("trust-confirmed").checked = false; $("certificate-details").hidden = true;
  try {
    const result = await api("/api/connection/certificate", {method: "POST", body: JSON.stringify({url: $("host-url").value})});
    if (generation !== certificateGeneration || !state.csrf) return;
    certificateTicket = result.ticket;
    const cert = result.certificate;
    $("certificate-summary").textContent = `Subject: ${cert.subject}\nIssuer: ${cert.issuer}\nNames: ${cert.names.join(", ")}\nValid from: ${cert.not_before}\nValid until: ${cert.not_after}\nSHA-256: ${cert.fingerprint}`;
    $("trust-mode").value = "certificate"; updateTrust();
    $("connection-message").textContent = cert.valid_now ? "Verify the fingerprint, tick approval, then test or save." : "This certificate is outside its validity period and cannot be trusted.";
  } catch (error) {if (generation === certificateGeneration) $("connection-message").textContent = error.message;}
  finally {button.disabled = false;}
};
$("test-connection").onclick = async () => {
  if (!$("host-form").reportValidity()) return;
  const button = $("test-connection"), generation = certificateGeneration;
  button.disabled = true; $("connection-message").textContent = "Checking TLS and read-only API access…";
  try {
    const result = await api("/api/connection/test", {method: "POST", body: JSON.stringify(connectionPayload())});
    if (generation === certificateGeneration && state.csrf) $("connection-message").textContent = result.ok ? result.message : result.error;
  } catch (error) {if (generation === certificateGeneration) $("connection-message").textContent = error.message;}
  finally {button.disabled = false;}
};
function showSettings() {
  stop(); state.settings = true;
  $("overview").hidden = true; $("empty").hidden = true; $("settings").hidden = false;
  $("breadcrumb").textContent = "Configuration"; renderSettingsList(); editHost();
}
async function unlock() {
  $("login").hidden = true; $("shell").hidden = false;
  await loadHosts();
  if (state.hosts.length) selectHost(state.hosts[0]);
  else { $("empty").hidden = false; $("overview").hidden = true; $("settings").hidden = true; }
}
$("login-form").onsubmit = async event => {
  event.preventDefault();
  const button = event.submitter; button.disabled = true;
  $("login-error").textContent = "";
  try {
    const data = await api("/api/login", {method: "POST", body: JSON.stringify({key: $("access-key").value})});
    $("access-key").value = ""; state.csrf = data.csrf; await unlock();
  } catch (error) { $("login-error").textContent = error.message; }
  finally { button.disabled = false; }
};
$("logout").onclick = async () => {try {await api("/api/logout", {method: "POST", body: "{}"}); lock();} catch (e) {$("stats-error").hidden=false; $("stats-error").textContent=e.message;}};
$("search").oninput = renderTree;
$("refresh").onclick = sample;
$("settings-button").onclick = showSettings;
$("empty-add").onclick = showSettings;
$("new-host").onclick = () => editHost();
$("add-label").onclick = () => addLabelRow();
$("back-button").onclick = () => {
  if (state.selected && state.hosts.some(h => h.id === state.selected.id)) selectHost(state.hosts.find(h => h.id === state.selected.id));
  else if (state.hosts.length) selectHost(state.hosts[0]);
  else {state.settings=false; $("settings").hidden=true; $("empty").hidden=false; $("breadcrumb").textContent="Infrastructure";}
};
$("host-form").onsubmit = async event => {
  event.preventDefault();
  const button=event.submitter; button.disabled=true;
  try {
    const host = connectionPayload();
    const saved = await api("/api/hosts", {method: "POST", body: JSON.stringify(host)});
    $("host-token-secret").value = "";
    await loadHosts(); editHost(saved.host);
    $("config-message").className = "muted"; $("config-message").textContent = "Saved. Select the server in the sidebar to fetch its stats.";
  } catch (error) {$("config-message").className="error"; $("config-message").textContent=error.message;}
  finally {button.disabled=false;}
};
$("delete-host").onclick = async () => {
  if (!confirm("Remove this server from the monitor? Proxmox will not be changed.")) return;
  try {await api(`/api/hosts/${encodeURIComponent($("host-id").value)}`, {method:"DELETE"}); await loadHosts(); editHost();}
  catch(error) {$("config-message").textContent=error.message;}
};
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {stop(); $("sample-note").textContent="Paused while hidden";}
  else {$("sample-note").textContent="Updates every 10s while visible"; sample();}
});
(async () => {
  try {
    const response = await fetch("/api/session", {cache: "no-store"});
    const data = await response.json();
    if (response.ok && data.authenticated) {state.csrf = data.csrf; await unlock();}
    else if (response.status === 401) lock();
    else throw new Error(data.error || "Cannot load monitor.");
  } catch (error) {$("startup-error").hidden=false; $("startup-error").textContent=error.message;}
})();
