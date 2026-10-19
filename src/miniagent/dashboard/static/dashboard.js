"use strict";
Object.assign(htmx.config, {
  allowEval: false, allowScriptTags: false, includeIndicatorStyles: false,
  selfRequestsOnly: true, historyCacheSize: 0, historyEnabled: false,
  allowNestedOobSwaps: false, timeout: 10000
});

(() => {
  let generation = 0, timer = null, delay = 5000, active = null;
  const operations = new WeakMap();
  const region = () => document.querySelector("#run-database, #workspace-facts, #run-list");
  const status = text => { document.querySelector("#refresh-status").textContent = text; };
  const auto = () => document.querySelector("#auto-refresh");
  const terminal = () => region()?.dataset.terminal === "true";
  const clearTimer = () => { clearTimeout(timer); timer = null; };

  function enhance(root = document) {
    root.querySelectorAll("time[datetime]").forEach(element => {
      const value = element.getAttribute("datetime"), date = new Date(value);
      if (!Number.isNaN(date.valueOf())) {
        element.title = value + " UTC";
        element.textContent = new Intl.DateTimeFormat(undefined, {dateStyle: "medium", timeStyle: "long"}).format(date);
      }
    });
    root.querySelectorAll("details[id]").forEach(element => { element.dataset.expandId = element.id; });
  }

  function savePosition() {
    const entries = [...document.querySelectorAll("[data-event-sequence]")];
    const anchor = entries.find(element => {
      const rect = element.getBoundingClientRect();
      return rect.bottom > 95 && rect.top < innerHeight;
    });
    return {
      id: anchor?.id, offset: anchor?.getBoundingClientRect().top,
      scroll: scrollY,
      expanded: [...document.querySelectorAll("details[open][data-expand-id]")].map(element => element.dataset.expandId)
    };
  }

  function restorePosition(saved) {
    if (!saved) return;
    for (const id of saved.expanded) {
      const element = document.getElementById(id);
      if (element?.tagName === "DETAILS") element.open = true;
    }
    const anchor = saved.id && document.getElementById(saved.id);
    if (anchor) scrollBy(0, anchor.getBoundingClientRect().top - saved.offset);
    else scrollTo(0, saved.scroll);
  }

  function schedule() {
    clearTimer();
    auto().dataset.nextDelay = String(delay);
    if (terminal()) { auto().checked = false; return; }
    if (auto().checked && !document.hidden && !active?.pending) {
      timer = setTimeout(() => refresh(), delay);
    }
  }

  function finish(operation, successful) {
    if (!operation || operation.done) return;
    operation.done = true;
    operation.pending = false;
    if (operation.generation !== generation) return;
    if (successful) {
      delay = 5000;
      status(terminal() ? "已更新 · 终态，自动刷新已停止" : "已更新");
    } else {
      delay = Math.min(delay * 2, 30000);
      status("读取失败，保留上次内容；可重试");
    }
    schedule();
  }

  function send(url, target, {kind = "refresh", source = null, select = null, saved = null} = {}) {
    clearTimer();
    if (generation === 2147483647) { location.reload(); return; }
    generation += 1;
    document.body.dataset.generation = String(generation);
    const operation = {generation, kind, saved, pending: true, done: false, url};
    active = operation;
    source ||= document.querySelector("#refresh");
    source.setAttribute("hx-sync", "body:replace");
    status("正在读取…");
    htmx.ajax("GET", url, {
      source, target, swap: target === "#artifact-previews" ? "innerHTML" : "outerHTML",
      select, headers: {"X-Dashboard-Generation": String(generation)}
    }).catch(() => finish(operation, false));
  }

  function refresh() {
    const target = region();
    if (!target) return;
    const saved = savePosition();
    const url = new URL(location.href);
    url.hash = "";
    if (target.id === "run-database" && saved.id?.startsWith("event-")) {
      url.searchParams.set("event_id", saved.id.slice(6));
    }
    // A bare list URL still has a query delimiter so requests have one consistent shape.
    send(url.pathname + "?" + url.searchParams.toString(), "#" + target.id, {saved});
  }

  document.addEventListener("htmx:beforeRequest", event => {
    operations.set(event.detail.xhr, active);
  });
  document.addEventListener("htmx:beforeSwap", event => {
    const operation = operations.get(event.detail.xhr);
    const returned = event.detail.xhr.getResponseHeader("X-Dashboard-Generation");
    if (!operation || operation.generation !== generation || returned !== String(generation)) {
      event.detail.shouldSwap = false;
    }
  });
  document.addEventListener("htmx:afterSwap", event => {
    const operation = operations.get(event.detail.xhr);
    enhance();
    if (operation?.generation === generation) {
      const target = region();
      if (target) target.dataset.generation = String(generation);
      restorePosition(operation.saved);
    }
  });
  document.addEventListener("htmx:afterRequest", event => {
    const operation = operations.get(event.detail.xhr);
    if (operation?.generation === generation && event.detail.xhr.status === 422 && region()?.id === "run-list") {
      const url = new URL(operation.url, location.origin);
      if (url.searchParams.has("cursor")) {
        url.searchParams.delete("cursor");
        history.replaceState(null, "", url.pathname + "?" + url.searchParams);
        send(url.pathname + "?" + url.searchParams, "#run-list");
        status("原分页位置失效，正在更新首页");
        return;
      }
    }
    finish(operation, event.detail.successful);
  });
  document.addEventListener("htmx:sendError", event => finish(operations.get(event.detail.xhr), false));
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) clearTimer(); else schedule();
  });

  document.addEventListener("DOMContentLoaded", () => {
    enhance();
    document.body.dataset.generation = "0";
    if (!region()) return;
    document.querySelector("#refresh-controls").hidden = false;
    document.querySelector("#refresh").addEventListener("click", refresh);
    auto().addEventListener("change", schedule);
    schedule();
  });
  document.addEventListener("submit", event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.matches("#run-filters, #event-filters")) return;
    event.preventDefault();
    const query = new URLSearchParams();
    for (const [key, value] of new FormData(form)) if (value !== "") query.append(key, value);
    const url = form.getAttribute("action") + "?" + query;
    history.replaceState(null, "", url);
    send(url, "#" + region().id, {kind: "filter", source: form});
  });
  document.addEventListener("click", event => {
    const copy = event.target.closest("[data-copy]");
    if (copy) {
      navigator.clipboard.writeText(copy.dataset.copy).then(() => status("ID 已复制"), () => status("复制不可用，请选择完整 ID"));
      return;
    }
    const link = event.target.closest("a[href]");
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const url = new URL(link.href, location.origin);
    if (url.origin !== location.origin) return;
    let target = null, select = null;
    if (link.hasAttribute("data-events-next")) target = "#timeline";
    else if (link.hasAttribute("data-metadata-next")) target = "#artifact-metadata";
    else if (document.querySelector("#artifact-previews") && /\/artifacts\/[0-9a-f-]+$/.test(url.pathname)) {
      target = "#artifact-previews";
      select = "#artifact-preview-content";
    }
    if (target) {
      event.preventDefault();
      send(url.pathname + url.search, target, {kind: "partial", source: link, select});
    }
  });
})();
