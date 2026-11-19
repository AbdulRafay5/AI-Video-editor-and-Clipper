// Minimal DOM/transport harness for the real Creator Studio browser script.
// No browser, external network, npm packages or video models required.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
  constructor(tag = "div") {
    this.tagName = tag;
    this.children = [];
    this.style = {};
    this.attributes = {};
    this.textContent = "";
    this.value = "";
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; this.textContent = ""; }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name] ?? null; }
}
const elements = new Map();
const get = id => {
  if (!elements.has(id)) elements.set(id, new Element());
  return elements.get(id);
};
const main = new Element("main");
const document = {
  querySelector(selector) { return selector === "main" ? main : null; },
  createElement(tag) { return new Element(tag); },
  getElementById: get,
};

let timers = [];
const setTimeoutFake = (fn, delay) => {
  const timer = {fn, delay, cancelled: false};
  timers.push(timer);
  return timer;
};
const clearTimeoutFake = timer => { if (timer) timer.cancelled = true; };
const runTimers = async () => {
  const pending = timers;
  timers = [];
  for (const timer of pending) if (!timer.cancelled) await timer.fn();
};
const tick = () => new Promise(resolve => setImmediate(resolve));
let batch = {
  id: 1,
  status: "paused",
  items: [{id: 7, project_id: 3, topic: "Coffee roasting", status: "running"}],
};
const reply = (json, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  async json() { return json; },
});
let batchDetailOverride = null;
const fetchFake = async url => {
  if (url === "/api/creator/projects") return reply({projects: []});
  if (url === "/api/creator/batches")
    return reply({batches: [{id: 1, status: batch.status}]});
  if (url === "/api/creator/batches/1") {
    if (batchDetailOverride) return reply(await batchDetailOverride());
    return reply({batch});
  }
  throw Error("Unexpected request in frontend test: " + url);
};

const script = fs.readFileSync(
  path.join(__dirname, "../static/ext_creator.js"), "utf8");
vm.runInNewContext(script, {
  document, fetch: fetchFake, setTimeout: setTimeoutFake,
  clearTimeout: clearTimeoutFake, console,
  confirm: () => true,
});
(async () => {
  for (let n = 0; n < 5; n++) await tick();
  assert.equal(get("creator-batches").children.length, 1);
  assert.ok(timers.some(t => !t.cancelled && t.delay === 5000),
    "Paused batches with a running worker must continue to poll");

  // Simulate out-of-order network responses. A delayed old response must
  // not replace the contents rendered by a subsequent faster refresh.
  let releaseOld;
  const held = new Promise(resolve => { releaseOld = resolve; });
  let requestNumber = 0;
  batchDetailOverride = async () => {
    requestNumber++;
    if (requestNumber === 1) return held;
    return {batch: {...batch, status: "done", items: [
      {...batch.items[0], status: "done"}]}};
  };
  const clickRefresh = get("creator-batch-refresh").onclick;
  const oldCall = clickRefresh();
  for (let n = 0; n < 3; n++) await tick();
  const newCall = clickRefresh();
  await newCall;
  releaseOld({batch: {...batch, status: "paused", items: batch.items}});
  await oldCall;
  const heading = get("creator-batches").children[0].children[0];
  assert.equal(heading.textContent, "Batch 1 · done",
    "Stale results must never overwrite a later refresh");
  console.log("Creator Studio frontend polling tests passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
