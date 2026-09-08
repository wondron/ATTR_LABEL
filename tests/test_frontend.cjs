"use strict";

// Run with: node --test tests/test_frontend.cjs
// Execute the unmodified browser application with a small DOM and controllable
// HTTP/event/timer boundary. These tests never access or delete image files.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

class Element {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parentElement = null;
    this.attributes = new Map();
    this.listeners = new Map();
    this.dataset = {};
    this.style = {};
    this.className = "";
    this.value = "";
    this._text = "";
    this.hidden = this.disabled = this.open = this.checked = false;
    this.clientWidth = 800;
    this.clientHeight = 600;
    this.naturalWidth = 640;
    this.naturalHeight = 480;
    this.classList = {
      contains: (name) => this.className.split(/\s+/).includes(name),
      add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...names])].join(" "); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter((name) => !names.includes(name)).join(" "); },
      toggle: (name, force) => {
        const enabled = force === undefined ? !this.classList.contains(name) : force;
        this.classList[enabled ? "add" : "remove"](name);
        return enabled;
      }
    };
  }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
  set textContent(value) { this._text = String(value); this.replaceChildren(); }
  get isContentEditable() {
    const own = this.getAttribute("contenteditable");
    if (own === "false") return false;
    if (own === "" || own === "true" || own === "plaintext-only") return true;
    return Boolean(this.parentElement && this.parentElement.isContentEditable);
  }
  setAttribute(name, value) {
    const text = String(value);
    this.attributes.set(name, text);
    if (name === "class") this.className = text;
    if (["id", "type", "name", "value", "src", "href"].includes(name)) this[name] = text;
    if (["hidden", "disabled", "open", "checked"].includes(name)) this[name] = true;
    if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = text;
  }
  getAttribute(name) {
    if (name === "class") return this.className;
    if (["id", "type", "name", "value", "src", "href"].includes(name) && this[name] != null) return String(this[name]);
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }
  hasAttribute(name) { return this.getAttribute(name) != null; }
  removeAttribute(name) {
    this.attributes.delete(name);
    if (["src", "href"].includes(name)) delete this[name];
    if (["hidden", "disabled", "open", "checked"].includes(name)) this[name] = false;
  }
  append(...children) {
    for (const child of children) {
      if (child.tagName === "#FRAGMENT") { this.append(...Array.from(child.children)); continue; }
      child.remove();
      child.parentElement = this;
      this.children.push(child);
    }
  }
  appendChild(child) { this.append(child); return child; }
  replaceChildren(...children) {
    for (const child of this.children) child.parentElement = null;
    this.children = [];
    this.append(...children);
  }
  remove() {
    if (this.parentElement) this.parentElement.children = this.parentElement.children.filter((child) => child !== this);
    this.parentElement = null;
  }
  matches(selector) {
    return selector.split(",").some((part) => {
      const exclusions = [...part.matchAll(/:not\(([^)]+)\)/g)].map((match) => match[1]);
      if (exclusions.some((excluded) => this.matches(excluded))) return false;
      const value = part.replace(/:not\([^)]+\)/g, "").trim();
      const tag = value.match(/^[a-z][\w-]*/i);
      if (tag && this.tagName !== tag[0].toUpperCase()) return false;
      for (const match of value.matchAll(/\.([\w-]+)/g)) if (!this.classList.contains(match[1])) return false;
      for (const match of value.matchAll(/#([\w-]+)/g)) if (this.id !== match[1]) return false;
      for (const match of value.matchAll(/\[([^=\]]+)(?:=['"]?([^'"\]]*)['"]?)?\]/g)) {
        if (match[2] === undefined ? !this.hasAttribute(match[1]) : this.getAttribute(match[1]) !== match[2]) return false;
      }
      if (value.includes(":checked") && !this.checked) return false;
      return true;
    });
  }
  closest(selector) {
    for (let node = this; node; node = node.parentElement) if (node.matches(selector)) return node;
    return null;
  }
  querySelectorAll(selector) {
    return this.children.flatMap((child) => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(callback);
  }
  dispatchEvent(event) {
    event.target ||= this;
    event.preventDefault ||= function () { this.defaultPrevented = true; };
    for (const callback of this.listeners.get(event.type) || []) callback(event);
    if (event.bubbles !== false && this.parentElement) this.parentElement.dispatchEvent(event);
    return !event.defaultPrevented;
  }
  click() {
    if (this.disabled) return;
    if (this.tagName === "A" && this.download) this.onDownload?.(this);
    this.dispatchEvent({ type: "click" });
  }
  focus() { this.closest("#document").activeElement = this; }
  scrollIntoView() {}
  getBoundingClientRect() { return { left: 0, top: 0, right: 800, bottom: 600 }; }
  showModal() { this.open = true; }
  close() { this.open = false; this.dispatchEvent({ type: "close", bubbles: false }); }
}

function makeDocument(html, downloads) {
  const document = new Element("#document");
  document.id = "document";
  document.hidden = false;
  document.createElement = (tag) => {
    const node = new Element(tag);
    node.onDownload = (link) => downloads.push({ name: link.download, href: link.href });
    return node;
  };
  document.createDocumentFragment = () => new Element("#fragment");
  document.getElementById = (id) => document.querySelector(`#${id}`);
  const stack = [document];
  for (const match of html.matchAll(/<!--[\s\S]*?-->|<![^>]*>|<\/?([a-z][\w-]*)\b([^>]*)>|([^<]+)/gi)) {
    if (match[0].startsWith("<!")) continue;
    if (match[3]) { stack.at(-1)._text += match[3]; continue; }
    const tag = match[1].toLowerCase();
    if (match[0].startsWith("</")) { if (stack.at(-1).tagName === tag.toUpperCase()) stack.pop(); continue; }
    const node = document.createElement(tag);
    for (const attr of match[2].matchAll(/([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s]+)))?/g)) {
      node.setAttribute(attr[1], attr[2] ?? attr[3] ?? attr[4] ?? "");
    }
    stack.at(-1).append(node);
    if (!["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"].includes(tag)) stack.push(node);
  }
  document.body = document.querySelector("body");
  document.activeElement = document.body;
  for (const select of document.querySelectorAll("select")) select.value = select.querySelector("option")?.value || "";
  return document;
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

function response(payload, status = 200, headers = {}) {
  const values = new Map(Object.entries({ "content-type": "application/json", ...headers }));
  return { ok: status >= 200 && status < 300, status, headers: { get: (name) => values.get(name.toLowerCase()) || null },
    json: async () => payload, text: async () => JSON.stringify(payload), blob: async () => new Blob(["test zip"]) };
}

function image(name, annotated = false) {
  return { id: name, image_id: name, name, annotated, annotation_exists: annotated, annotation_valid: annotated ? true : null, size: 128, modified_at: "2026-09-05T08:00:00" };
}

function listPayload(items) {
  const labeled = items.filter((item) => item.annotated).length;
  return { items: items.map((item) => ({ ...item })), directory_generation: 0,
    summary: { total: items.length, labeled, unlabeled: items.length - labeled, invalid: 0 } };
}

async function flush() {
  for (let index = 0; index < 20; index += 1) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

async function createApp(initial = [image("第一张.jpg"), image("第二张.jpg")], config = {}) {
  const root = path.resolve(__dirname, "..");
  const downloads = [];
  const document = makeDocument(fs.readFileSync(path.join(root, "annotation_app/static/index.html"), "utf8"), downloads);
  const requests = [];
  const confirms = [];
  const sources = [];
  const timers = new Map();
  let timerId = 0;
  let now = 0;
  const server = { images: initial.map((item) => ({ ...item })), confirm: true, handler: null };
  const fetch = async (url, options = {}) => {
    const parsed = new URL(url, "http://annotation.test");
    const request = { path: parsed.pathname, params: parsed.searchParams, method: options.method || "GET", options };
    requests.push(request);
    if (server.handler) {
      const handled = server.handler(request);
      if (handled !== undefined) return handled;
    }
    if (request.path.endsWith("/config")) return response({ directory_generation: 0, data_dir: "/data/当前文件夹", ...config });
    if (request.path.endsWith("/health")) return response({ directory_generation: 0 });
    if (request.path.endsWith("/images")) return response(listPayload(server.images));
    if (request.path.endsWith("/annotation")) {
      const item = server.images.find((item) => item.id === request.params.get("image_id"));
      if (request.method === "PUT") {
        const { annotations } = JSON.parse(request.options.body);
        Object.assign(item, { annotated: true, annotation_exists: true, annotation_valid: true, annotations });
        return response({ exists: true, revision: "revision-2", document: { annotations } });
      }
      return response({ exists: Boolean(item?.annotated), revision: item?.annotated ? "revision-1" : "__missing__", document: { annotations: item?.annotations ?? { food_name: "已保存的包子" } } });
    }
    if (request.path.endsWith("/image") && request.method === "DELETE") {
      server.images = server.images.filter((item) => item.id !== request.params.get("image_id"));
      return response({ image_exists: false, annotation_exists: false, directory_generation: 0 });
    }
    if (request.path.endsWith("/statistics")) return response({ directory_generation: 0, summary: { ...listPayload(server.images).summary, valid: 0 }, fields: [] });
    throw new Error(`Unexpected request: ${request.method} ${request.path}`);
  };
  const window = new Element("window");
  window.confirm = (message) => { confirms.push(message); return server.confirm; };
  window.setTimeout = (callback, delay = 0) => { const id = ++timerId; timers.set(id, { callback, due: now + delay }); return id; };
  window.clearTimeout = (id) => timers.delete(id);
  window.EventSource = class extends Element {
    constructor(url) { super("eventsource"); this.url = url; this.closed = false; sources.push(this); }
    close() { this.closed = true; }
  };
  const sandboxURL = class extends URL {};
  sandboxURL.createObjectURL = () => "blob:test-download";
  sandboxURL.revokeObjectURL = () => {};
  vm.runInNewContext(fs.readFileSync(path.join(root, "annotation_app/static/app.js"), "utf8"), {
    window, document, HTMLElement: Element, Element, fetch, URL: sandboxURL, Blob, console,
    setTimeout: window.setTimeout, clearTimeout: window.clearTimeout,
  }, { filename: "app.js" });
  await flush();
  const app = {
    document, window, server, requests, confirms, sources, downloads,
    get: (id) => document.getElementById(id),
    visible: () => document.getElementById("imageList").querySelectorAll(".image-item").map((item) => item.dataset.imageId),
    deletes: () => requests.filter((request) => request.method === "DELETE"),
    selected: () => document.getElementById("previewImage").dataset.imageId || null,
    key(key, target = document.body, extra = {}) {
      const event = { type: "keydown", key, target, ctrlKey: false, metaKey: false, altKey: false, repeat: false, ...extra };
      target.dispatchEvent(event);
      return event;
    },
    edit(value) { const input = app.get("foodNameInput"); input.value = value; input.dispatchEvent({ type: "input" }); },
    async tick(milliseconds) {
      now += milliseconds;
      const due = [...timers].filter(([, timer]) => timer.due <= now).sort((left, right) => left[1].due - right[1].due);
      for (const [id, timer] of due) { if (timers.delete(id)) { timer.callback(); await flush(); } }
      await flush();
    },
    async sync() {
      const source = sources.findLast((source) => !source.closed);
      source.dispatchEvent({ type: "image-created", data: JSON.stringify({ type: "image-created", directory_generation: 0 }), bubbles: false });
      await app.tick(100);
    }
  };
  return app;
}

test("D does not delete while typing, composing, repeating, or using modifiers", async () => {
  const app = await createApp();
  for (const tag of ["input", "select", "textarea"]) {
    const field = app.document.createElement(tag);
    app.document.body.append(field);
    app.key("d", field);
  }
  for (const value of ["true", "", "plaintext-only"]) {
    const editable = app.document.createElement("div");
    editable.setAttribute("contenteditable", value);
    const child = app.document.createElement("span");
    editable.append(child);
    app.document.body.append(editable);
    app.key("D", child);
  }
  for (const flag of ["repeat", "ctrlKey", "metaKey", "altKey", "isComposing"]) app.key("d", app.document.body, { [flag]: true });
  await flush();
  assert.equal(app.confirms.length, 0);
  assert.equal(app.deletes().length, 0);
  assert.equal(app.selected(), "第一张.jpg");
});

test("button and D both confirm the filename and permanent deletion; cancel preserves draft", async () => {
  const app = await createApp();
  app.edit("未保存的饺子");
  app.server.confirm = false;
  app.get("deleteImageButton").click();
  app.key("d");
  await flush();
  assert.equal(app.confirms.length, 2);
  for (const message of app.confirms) {
    assert.match(message, /第一张\.jpg/);
    assert.match(message, /将从磁盘永久删除原图及对应标注文件，无法恢复/);
  }
  assert.equal(app.deletes().length, 0);
  assert.equal(app.selected(), "第一张.jpg");
  assert.equal(app.get("foodNameInput").value, "未保存的饺子");
  assert.equal(app.get("saveButton").disabled, false);
});

test("successful deletion selects next image and removing the last image shows empty state", async () => {
  const app = await createApp([image("第一张.jpg", true), image("第二张.jpg")]);
  app.key("D");
  await flush();
  assert.equal(app.deletes().length, 1);
  assert.equal(app.deletes()[0].params.get("directory_generation"), "0");
  assert.equal(app.selected(), "第二张.jpg");
  assert.deepEqual(app.visible(), ["第二张.jpg"]);
  assert.equal(app.get("totalCount").textContent, "1");
  assert.equal(app.get("labeledCount").textContent, "0");
  assert.equal(app.get("unlabeledCount").textContent, "1");
  app.get("deleteImageButton").click();
  await flush();
  assert.equal(app.selected(), null);
  assert.deepEqual(app.visible(), []);
  assert.equal(app.get("totalCount").textContent, "0");
  assert.equal(app.get("emptyView").classList.contains("hidden"), false);
  assert.equal(app.get("previewImage").hidden, true);
  assert.equal(app.get("deleteImageButton").disabled, true);
  assert.equal(app.get("annotationFields").disabled, true);
});

test("deletion in flight cannot be submitted twice or switch the selected image", async () => {
  const app = await createApp();
  const pending = deferred();
  app.server.handler = (request) => request.method === "DELETE" ? pending.promise : undefined;
  app.key("d");
  app.key("d");
  app.get("nextButton").click();
  await flush();
  assert.equal(app.confirms.length, 1);
  assert.equal(app.deletes().length, 1);
  assert.equal(app.selected(), "第一张.jpg");
  app.server.images.shift();
  pending.resolve(response({ image_exists: false, annotation_exists: false, directory_generation: 0 }));
  await flush();
  assert.equal(app.selected(), "第二张.jpg");
});

test("partial failure keeps the remaining original and updates removed annotation status", async () => {
  const app = await createApp([image("第一张.jpg", true), image("第二张.jpg")]);
  app.edit("保留未保存内容");
  app.server.handler = (request) => {
    if (request.method !== "DELETE") return undefined;
    Object.assign(app.server.images[0], { annotated: false, annotation_exists: false, annotation_valid: null });
    return response({ detail: { code: "image_delete_failed", message: "原图被占用", image_exists: true, annotation_exists: false } }, 500);
  };
  app.key("d");
  await flush();
  assert.equal(app.selected(), "第一张.jpg");
  assert.equal(app.get("foodNameInput").value, "保留未保存内容");
  assert.equal(app.get("totalCount").textContent, "2");
  assert.equal(app.get("labeledCount").textContent, "0");
  assert.match(app.get("toastRegion").textContent, /原图被占用/);
  assert.equal(app.get("saveButton").disabled, false);
});

test("partial deletion of a saved annotation marks the preserved form as an unsaved draft", async () => {
  const app = await createApp([image("第一张.jpg", true)]);
  app.server.handler = (request) => {
    if (request.method !== "DELETE") return undefined;
    Object.assign(app.server.images[0], { annotated: false, annotation_exists: false, annotation_valid: null });
    return response({ detail: { code: "image_delete_failed", message: "原图被占用", image_exists: true, annotation_exists: false } }, 500);
  };
  app.key("d");
  await flush();
  assert.equal(app.get("foodNameInput").value, "已保存的包子");
  assert.equal(app.get("labeledCount").textContent, "0");
  assert.equal(app.get("saveButton").disabled, false);
  assert.notEqual(app.get("annotationState").textContent, "已标注");
  assert.doesNotMatch(app.get("saveState").textContent, /已加载同名 JSON 标注/);
  const leave = { type: "beforeunload", bubbles: false };
  app.window.dispatchEvent(leave);
  assert.equal(leave.defaultPrevented, true, "the only remaining annotation copy is in the form");
});

test("a failed delete response reconciles with the actual image list", async () => {
  const app = await createApp();
  app.server.handler = (request) => {
    if (request.method !== "DELETE") return undefined;
    app.server.images.shift();
    return Promise.reject(new Error("connection lost after deletion"));
  };
  app.key("d");
  await flush();
  assert.equal(app.selected(), "第二张.jpg");
  assert.deepEqual(app.visible(), ["第二张.jpg"]);
  assert.equal(app.get("totalCount").textContent, "1");
  assert.match(app.get("toastRegion").textContent, /connection lost after deletion/);
});

test("known deletion result remains reflected when list reconciliation fails", async () => {
  const app = await createApp();
  app.server.handler = (request) => {
    if (request.method === "DELETE") {
      app.server.images.shift();
      return response({ image_exists: false, annotation_exists: false });
    }
    if (request.path.endsWith("/images")) return Promise.reject(new Error("offline"));
  };
  app.key("d");
  await flush();
  assert.deepEqual(app.visible(), ["第二张.jpg"]);
  assert.equal(app.selected(), "第二张.jpg");
  assert.equal(app.get("totalCount").textContent, "1");
  assert.match(app.get("toastRegion").textContent, /offline/);
});

test("unknown disk state disables saving until reconnect confirms deletion", async () => {
  const app = await createApp();
  app.edit("断线前未保存的包子");
  app.server.handler = (request) => {
    if (request.method === "DELETE") {
      app.server.images.shift();
      return Promise.reject(new Error("delete response lost"));
    }
    if (request.path.endsWith("/images")) return Promise.reject(new Error("list connection lost"));
  };
  app.key("d");
  await flush();
  assert.equal(app.get("saveButton").disabled, true);
  assert.equal(app.get("deleteImageButton").disabled, true);
  assert.equal(app.get("foodNameInput").value, "断线前未保存的包子");
  app.server.handler = null;
  await app.sync();
  assert.deepEqual(app.visible(), ["第二张.jpg"]);
  assert.equal(app.selected(), "第二张.jpg");
  assert.equal(app.get("totalCount").textContent, "1");
  assert.equal(app.get("deleteImageButton").disabled, false);
});

test("reconnect after uncertain partial deletion preserves draft and permits a fresh annotation save", async () => {
  const app = await createApp([image("第一张.jpg", true)]);
  app.edit("保留断线期间的修改");
  app.server.handler = (request) => {
    if (request.method === "DELETE") {
      Object.assign(app.server.images[0], { annotated: false, annotation_exists: false, annotation_valid: null });
      return Promise.reject(new Error("delete response lost"));
    }
    if (request.path.endsWith("/images")) return Promise.reject(new Error("list connection lost"));
  };
  app.key("d");
  await flush();
  assert.equal(app.get("saveButton").disabled, true);
  app.server.handler = null;
  await app.sync();
  assert.equal(app.selected(), "第一张.jpg");
  assert.equal(app.get("foodNameInput").value, "保留断线期间的修改");
  assert.equal(app.get("labeledCount").textContent, "0");
  assert.equal(app.get("saveButton").disabled, false);
  app.get("saveButton").click();
  await flush();
  const saved = app.requests.find((request) => request.method === "PUT");
  assert.ok(saved, "saving should be allowed after disk reconciliation");
  assert.equal(JSON.parse(saved.options.body).revision, "__missing__");
  assert.equal(JSON.parse(saved.options.body).annotations.food_name, "保留断线期间的修改");
});

test("live refresh preserves selected image, form draft, and annotation request count", async () => {
  const app = await createApp();
  app.edit("新鲜蒸饺，尚未保存");
  const before = app.requests.filter((request) => request.path.endsWith("/annotation")).length;
  app.server.images.unshift(image("刚拍摄.jpg"));
  await app.sync();
  assert.deepEqual(app.visible(), ["刚拍摄.jpg", "第一张.jpg", "第二张.jpg"]);
  assert.equal(app.get("totalCount").textContent, "3");
  assert.equal(app.selected(), "第一张.jpg");
  assert.equal(app.get("foodNameInput").value, "新鲜蒸饺，尚未保存");
  assert.equal(app.get("saveButton").disabled, false);
  assert.equal(app.requests.filter((request) => request.path.endsWith("/annotation")).length, before);
  const leave = { type: "beforeunload", bubbles: false };
  app.window.dispatchEvent(leave);
  assert.equal(leave.defaultPrevented, true);
});

test("late list responses cannot resurrect an image after a completed deletion", async () => {
  const app = await createApp();
  const oldList = listPayload(app.server.images);
  const pending = deferred();
  let interceptNextList = true;
  app.server.handler = (request) => {
    if (request.path.endsWith("/images") && interceptNextList) { interceptNextList = false; return pending.promise; }
  };
  await app.sync();
  app.key("d");
  await flush();
  assert.deepEqual(app.visible(), ["第二张.jpg"]);
  pending.resolve(response(oldList));
  await flush();
  assert.deepEqual(app.visible(), ["第二张.jpg"]);
  assert.equal(app.selected(), "第二张.jpg");
  assert.equal(app.get("totalCount").textContent, "1");
});

test("first newly written image is automatically selected from empty state", async () => {
  const app = await createApp([]);
  assert.equal(app.selected(), null);
  app.server.images.push(image("新拍摄.jpg"));
  await app.sync();
  assert.equal(app.selected(), "新拍摄.jpg");
  assert.equal(app.get("totalCount").textContent, "1");
});

test("continuous camera events cannot keep postponing the first list refresh", async () => {
  const app = await createApp();
  const source = app.sources.at(-1);
  const emit = () => source.dispatchEvent({ type: "image-created", data: JSON.stringify({ type: "image-created", directory_generation: 0 }), bubbles: false });
  const before = app.requests.filter((request) => request.path.endsWith("/images")).length;
  app.server.images.push(image("连拍新增.jpg"));
  emit();
  await app.tick(40);
  emit();
  await app.tick(40);
  assert.equal(app.requests.filter((request) => request.path.endsWith("/images")).length, before + 1);
  assert.ok(app.visible().includes("连拍新增.jpg"));
});

test("event-stream reconnect reconciles photographs written during the subscription gap", async () => {
  const app = await createApp();
  app.server.images.push(image("订阅空隙新增.jpg"));
  app.sources.at(-1).onopen();
  await app.tick(0);
  assert.ok(app.visible().includes("订阅空隙新增.jpg"));
  assert.equal(app.selected(), "第一张.jpg");
});

test("event-stream failure falls back to polling without a manual page refresh", async () => {
  const app = await createApp();
  app.server.images.push(image("轮询新增.jpg"));
  app.sources.at(-1).onerror();
  await app.tick(2000);
  await app.tick(0);
  assert.ok(app.visible().includes("轮询新增.jpg"));
  assert.equal(app.get("totalCount").textContent, "3");
});

test("transient preview load errors retry without replacing an unsaved annotation", async () => {
  const app = await createApp();
  app.edit("保持草稿");
  const preview = app.get("previewImage");
  const originalSource = preview.src;
  preview.dispatchEvent({ type: "error", bubbles: false });
  assert.equal(app.get("imageError").classList.contains("hidden"), true);
  await app.tick(300);
  assert.notEqual(preview.src, originalSource);
  assert.match(preview.src, /retry=1/);
  preview.dispatchEvent({ type: "load", bubbles: false });
  assert.equal(preview.hidden, false);
  assert.equal(app.get("imageError").classList.contains("hidden"), true);
  assert.equal(app.get("foodNameInput").value, "保持草稿");
  assert.equal(app.get("saveButton").disabled, false);
});

test("changing images cancels a pending retry for the previous preview", async () => {
  const app = await createApp();
  const preview = app.get("previewImage");
  preview.dispatchEvent({ type: "error", bubbles: false });
  app.get("nextButton").click();
  await flush();
  const currentSource = preview.src;
  await app.tick(300);
  assert.equal(app.selected(), "第二张.jpg");
  assert.equal(preview.src, currentSource);
});

test("D cannot delete through either modal dialog", async () => {
  const app = await createApp();
  for (const id of ["statisticsDialog", "dataDirectoryDialog"]) {
    app.get(id).showModal();
    app.key("d");
    app.get(id).close();
  }
  assert.equal(app.confirms.length, 0);
  assert.equal(app.deletes().length, 0);
});

test("a directory-generation conflict preserves draft and prevents subsequent deletion", async () => {
  const app = await createApp();
  app.edit("原目录未保存内容");
  app.server.handler = (request) => request.method === "DELETE"
    ? response({ detail: { code: "data_directory_changed", message: "目录已在其他页面切换", directory_generation: 1, data_dir: "/data/other" } }, 409)
    : undefined;
  app.key("d");
  await flush();
  app.key("d");
  assert.equal(app.confirms.length, 1);
  assert.equal(app.deletes().length, 1);
  assert.equal(app.selected(), "第一张.jpg");
  assert.equal(app.get("foodNameInput").value, "原目录未保存内容");
  assert.equal(app.get("saveButton").disabled, true);
  assert.equal(app.get("deleteImageButton").disabled, true);
});

test("download reports no annotated data and uses the current folder ZIP filename", async () => {
  const app = await createApp();
  app.server.handler = (request) => request.path.endsWith("/export")
    ? response({ detail: { code: "no_annotated_data", message: "暂无已标注数据可下载" } }, 404)
    : undefined;
  app.get("downloadAnnotatedButton").click();
  await flush();
  assert.match(app.get("toastRegion").textContent, /暂无已标注数据可下载/);
  assert.equal(app.downloads.length, 0);
  app.server.handler = (request) => request.path.endsWith("/export")
    ? response(null, 200, { "content-type": "application/zip", "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent("当前文件夹.zip")}` })
    : undefined;
  app.get("downloadAnnotatedButton").click();
  await flush();
  assert.deepEqual(app.downloads.map((item) => item.name), ["当前文件夹.zip"]);
  assert.equal(app.requests.findLast((request) => request.path.endsWith("/export")).params.get("directory_generation"), "0");
});

function annotatedImage(name, annotations) {
  return { ...image(name, true), annotations };
}

function setAnnotationFilter(app, field, mode, value) {
  const modeInput = app.get(`annotationFilterMode_${field}`);
  assert.ok(modeInput, `the dialog must offer a filter for ${field}`);
  modeInput.value = mode;
  modeInput.dispatchEvent({ type: "change" });
  if (value === undefined) return;
  if (Array.isArray(value)) {
    const choices = app.get(`annotationFilterChoices_${field}`).querySelectorAll('input[type="checkbox"]');
    for (const expected of value) assert.ok(choices.some((choice) => choice.value === expected), `missing choice ${expected}`);
    for (const choice of choices) {
      choice.checked = value.includes(choice.value);
      choice.dispatchEvent({ type: "change" });
    }
  } else {
    const input = app.get(`annotationFilterValue_${field}`);
    input.value = String(value);
    input.dispatchEvent({ type: "input" });
    input.dispatchEvent({ type: "change" });
  }
}

async function applyAnnotationFilter(app) {
  app.get("annotationFilterForm").dispatchEvent({ type: "submit" });
  await flush();
}

test("attribute defaults include unannotated and missing fields, distinguish zero, and exclude invalid JSON", async () => {
  const defaults = {
    food_name: "无", food_count: null, quality: null, device_model: null,
    container_type: ["无"], accessory_type: ["无"], rack_level: ["无"], food_size: null
  };
  const app = await createApp([
    image("unannotated.jpg"),
    annotatedImage("partial.jpg", { food_name: "无" }),
    annotatedImage("explicit-defaults.jpg", defaults),
    annotatedImage("zero-count.jpg", { ...defaults, food_count: 0 }),
    { ...annotatedImage("invalid.jpg", defaults), annotation_valid: false }
  ]);
  app.get("openFilterButton").click();
  assert.equal(app.get("annotationFilterDialog").open, true);
  for (const field of Object.keys(defaults)) setAnnotationFilter(app, field, "default");
  await applyAnnotationFilter(app);
  assert.equal(app.get("annotationFilterDialog").open, false);
  assert.deepEqual(app.visible(), ["unannotated.jpg", "partial.jpg", "explicit-defaults.jpg"]);
  assert.equal(app.get("visibleCount").textContent, "3 张");
  assert.equal(app.get("totalCount").textContent, "5", "folder totals must not become filtered totals");

  app.get("openFilterButton").click();
  setAnnotationFilter(app, "food_count", "value", 0);
  await applyAnnotationFilter(app);
  assert.deepEqual(app.visible(), ["zero-count.jpg"]);
  assert.equal(app.selected(), "zero-count.jpg");
});

test("configured defaults apply equally to missing fields and unannotated images", async () => {
  const app = await createApp([
    image("new.jpg"),
    annotatedImage("missing-count.jpg", { food_name: "包子" }),
    annotatedImage("two.jpg", { food_count: 2 }),
    annotatedImage("explicit-null.jpg", { food_count: null })
  ], { fields: [{ name: "food_count", default: 2 }] });
  app.get("openFilterButton").click();
  setAnnotationFilter(app, "food_count", "default");
  await applyAnnotationFilter(app);
  assert.deepEqual(app.visible(), ["new.jpg", "missing-count.jpg", "two.jpg"]);
});

test("all eight attribute filters combine with AND, checkbox choices use OR, and filename/status filters remain active", async () => {
  const matching = {
    food_name: "蒸鸡蛋", food_count: 2, quality: 125.5, device_model: "C9277A", food_size: 4,
    container_type: ["金属容器"], accessory_type: ["烤盘"], rack_level: ["1", "2"]
  };
  const app = await createApp([
    annotatedImage("alpha-1.jpg", matching),
    annotatedImage("alpha-2.jpg", { ...matching, container_type: ["陶瓷容器"] }),
    annotatedImage("beta-1.jpg", matching),
    annotatedImage("alpha-wrong.jpg", { ...matching, device_model: "DB677" }),
    image("alpha-new.jpg")
  ]);
  app.get("imageSearch").value = "ALPHA";
  app.get("imageSearch").dispatchEvent({ type: "input" });
  app.get("statusFilter").value = "labeled";
  app.get("statusFilter").dispatchEvent({ type: "change" });
  app.get("openFilterButton").click();
  setAnnotationFilter(app, "food_name", "contains", "鸡蛋");
  setAnnotationFilter(app, "food_count", "value", "2");
  setAnnotationFilter(app, "quality", "value", "125.50");
  setAnnotationFilter(app, "food_size", "value", "4");
  setAnnotationFilter(app, "device_model", "value", "C9277A");
  setAnnotationFilter(app, "container_type", "value", ["金属容器", "陶瓷容器"]);
  setAnnotationFilter(app, "accessory_type", "value", ["烤盘", "无孔蒸盘"]);
  setAnnotationFilter(app, "rack_level", "value", ["1", "3"]);
  await applyAnnotationFilter(app);
  assert.deepEqual(app.visible(), ["alpha-1.jpg", "alpha-2.jpg"]);
  app.get("nextButton").click();
  await flush();
  assert.equal(app.selected(), "alpha-2.jpg");
  assert.equal(app.get("nextButton").disabled, true);
  app.get("statusFilter").value = "unlabeled";
  app.get("statusFilter").dispatchEvent({ type: "change" });
  await flush();
  assert.deepEqual(app.visible(), []);
});

test("cancel discards dialog edits, reset only changes draft conditions, and clear restores all images", async () => {
  const app = await createApp([
    annotatedImage("a.jpg", { food_name: "包子" }),
    annotatedImage("b.jpg", { food_name: "鸡蛋" })
  ]);
  app.get("openFilterButton").click();
  setAnnotationFilter(app, "food_name", "value", "包子");
  await applyAnnotationFilter(app);
  assert.deepEqual(app.visible(), ["a.jpg"]);
  for (const closeId of ["cancelAnnotationFilterButton", "closeAnnotationFilterButton"]) {
    app.get("openFilterButton").click();
    setAnnotationFilter(app, "food_name", "value", "鸡蛋");
    app.get(closeId).click();
    await flush();
    assert.equal(app.get("annotationFilterDialog").open, false);
    assert.deepEqual(app.visible(), ["a.jpg"]);
    app.get("openFilterButton").click();
    assert.equal(app.get("annotationFilterValue_food_name").value, "包子");
    app.get("resetAnnotationFilterButton").click();
    assert.equal(app.get("annotationFilterMode_food_name").value, "all");
    assert.deepEqual(app.visible(), ["a.jpg"], "reset must not apply before submission");
    app.get("cancelAnnotationFilterButton").click();
  }
  app.get("openFilterButton").click();
  app.get("resetAnnotationFilterButton").click();
  await applyAnnotationFilter(app);
  assert.deepEqual(app.visible(), ["a.jpg", "b.jpg"]);
  app.get("openFilterButton").click();
  setAnnotationFilter(app, "food_name", "value", "鸡蛋");
  await applyAnnotationFilter(app);
  assert.deepEqual(app.visible(), ["b.jpg"]);
  app.get("clearAnnotationFilterButton").click();
  await flush();
  assert.deepEqual(app.visible(), ["a.jpg", "b.jpg"]);
  assert.equal(app.selected(), "b.jpg", "clearing conditions should preserve a matching selection");
});

test("no matching attributes clear the preview and disable editing until conditions are cleared", async () => {
  const app = await createApp([annotatedImage("a.jpg", { food_name: "包子" })]);
  app.get("openFilterButton").click();
  setAnnotationFilter(app, "food_name", "value", "不存在的名称");
  await applyAnnotationFilter(app);
  assert.deepEqual(app.visible(), []);
  assert.equal(app.selected(), null);
  assert.equal(app.get("previewImage").hidden, true);
  assert.equal(app.get("emptyView").classList.contains("hidden"), false);
  for (const id of ["annotationFields", "deleteImageButton", "saveButton", "previousButton", "nextButton"]) {
    assert.equal(app.get(id).disabled, true, `${id} must be disabled without a matching image`);
  }
  assert.match(app.get("imageList").textContent, /没有匹配/);
  app.get("clearAnnotationFilterButton").click();
  await flush();
  assert.equal(app.selected(), "a.jpg");
  assert.equal(app.get("annotationFields").disabled, false);
});

test("applying a filter that removes a dirty image requires discard confirmation and preserves a cancelled draft", async () => {
  const app = await createApp([
    annotatedImage("a.jpg", { food_name: "包子" }),
    annotatedImage("b.jpg", { food_name: "鸡蛋" })
  ]);
  app.edit("未保存的包子");
  app.server.confirm = false;
  app.get("openFilterButton").click();
  setAnnotationFilter(app, "food_name", "value", "鸡蛋");
  await applyAnnotationFilter(app);
  assert.equal(app.confirms.length, 1);
  assert.equal(app.selected(), "a.jpg");
  assert.equal(app.get("foodNameInput").value, "未保存的包子");
  assert.equal(app.get("saveButton").disabled, false);
  assert.deepEqual(app.visible(), ["a.jpg", "b.jpg"]);
  assert.equal(app.get("annotationFilterDialog").open, true);
  app.server.confirm = true;
  await applyAnnotationFilter(app);
  assert.equal(app.confirms.length, 2);
  assert.deepEqual(app.visible(), ["b.jpg"]);
  assert.equal(app.selected(), "b.jpg");
  assert.equal(app.get("foodNameInput").value, "鸡蛋");
});

test("a filter retaining the current image uses saved values and preserves its unsaved form", async () => {
  const app = await createApp([
    annotatedImage("a.jpg", { food_name: "包子" }),
    annotatedImage("b.jpg", { food_name: "鸡蛋" })
  ]);
  app.edit("未保存的名称");
  app.get("openFilterButton").click();
  setAnnotationFilter(app, "food_name", "value", "包子");
  await applyAnnotationFilter(app);
  assert.equal(app.confirms.length, 0);
  assert.deepEqual(app.visible(), ["a.jpg"]);
  assert.equal(app.selected(), "a.jpg");
  assert.equal(app.get("foodNameInput").value, "未保存的名称");
  assert.equal(app.get("saveButton").disabled, false);
});

test("the filter dialog blocks deletion, saving, and navigation shortcuts even from its non-input controls", async () => {
  const app = await createApp();
  app.edit("保留筛查期间的草稿");
  app.get("openFilterButton").click();
  for (const key of ["d", "D", "e", "E", "q", "w", "ArrowLeft", "ArrowRight"]) {
    app.key(key, app.get("resetAnnotationFilterButton"));
  }
  for (const key of ["ArrowLeft", "ArrowRight"]) app.key(key, app.document.body, { altKey: true });
  app.key("s", app.document.body, { ctrlKey: true });
  app.key("Enter", app.document.body, { metaKey: true });
  await flush();
  assert.equal(app.confirms.length, 0);
  assert.equal(app.deletes().length, 0);
  assert.equal(app.requests.filter((request) => request.method === "PUT").length, 0);
  assert.equal(app.selected(), "第一张.jpg");
  assert.equal(app.get("foodNameInput").value, "保留筛查期间的草稿");
  assert.equal(app.get("annotationFilterDialog").open, true);
});

test("saving annotations refreshes attribute matches and advances when the saved image no longer matches", async () => {
  const app = await createApp([
    annotatedImage("a.jpg", { food_name: "包子" }),
    annotatedImage("b.jpg", { food_name: "包子" }),
    annotatedImage("c.jpg", { food_name: "鸡蛋" })
  ]);
  app.get("openFilterButton").click();
  setAnnotationFilter(app, "food_name", "value", "包子");
  await applyAnnotationFilter(app);
  app.edit("鸡蛋");
  app.get("saveButton").click();
  await flush();
  assert.equal(app.requests.filter((request) => request.method === "PUT").length, 1);
  assert.equal(app.server.images[0].annotations.food_name, "鸡蛋");
  assert.deepEqual(app.visible(), ["b.jpg"]);
  assert.equal(app.selected(), "b.jpg");
  assert.equal(app.confirms.length, 0);
  app.edit("鸡蛋");
  app.get("saveNextButton").click();
  await flush();
  assert.deepEqual(app.visible(), []);
  assert.equal(app.selected(), null);
  assert.equal(app.get("annotationFields").disabled, true);
});

test("live image updates obey applied filters and select a new matching image after the old match changes", async () => {
  const app = await createApp([
    annotatedImage("a.jpg", { food_name: "包子" }),
    annotatedImage("b.jpg", { food_name: "鸡蛋" })
  ]);
  app.get("openFilterButton").click();
  setAnnotationFilter(app, "food_name", "value", "包子");
  await applyAnnotationFilter(app);
  app.server.images.push(annotatedImage("new-match.jpg", { food_name: "包子" }));
  app.server.images.push(annotatedImage("new-other.jpg", { food_name: "鸡蛋" }));
  await app.sync();
  assert.deepEqual(app.visible(), ["a.jpg", "new-match.jpg"]);
  assert.equal(app.selected(), "a.jpg");
  app.server.images[0].annotations = { food_name: "鸡蛋" };
  await app.sync();
  assert.deepEqual(app.visible(), ["new-match.jpg"]);
  assert.equal(app.selected(), "new-match.jpg");
  app.server.images = app.server.images.filter((item) => item.id !== "new-match.jpg");
  await app.sync();
  assert.deepEqual(app.visible(), []);
  assert.equal(app.selected(), null);
});
