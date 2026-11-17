(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const nf = new Intl.NumberFormat("zh-CN");

  const state = {
    q: "",
    source: "",
    doc_type: "",
    days: "90",
    limit: 50,
    offset: 0,
    total: 0,
    items: [],
    sources: [],
    refreshing: false,
    requestId: 0,
  };

  let toastTimer = null;
  let searchTimer = null;
  let pollTimer = null;

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function toast(message, isError = false) {
    const element = $("#research-toast");
    if (!element) return;
    element.textContent = message;
    element.classList.toggle("error", Boolean(isError));
    element.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { element.hidden = true; }, 5000);
  }

  async function api(path, options = {}) {
    const headers = Object.assign({}, options.headers || {});
    if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    const token = sessionStorage.getItem("grt-admin-token");
    if (token) headers["X-Admin-Token"] = token;
    const response = await fetch(path, { ...options, headers });
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = {}; }
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  function truncate(value, limit) {
    const text = String(value || "").trim();
    return text.length > limit ? `${text.slice(0, limit)}…` : text;
  }

  function docLabel(type) {
    return type === "report" ? "报告" : "论文";
  }

  function formatDate(value) {
    return String(value || "").slice(0, 10);
  }

  function renderStats(stats = {}) {
    const cards = [
      ["条目", stats.items ?? 0],
      ["论文", stats.papers ?? 0],
      ["报告", stats.reports ?? 0],
      ["来源", stats.sources ?? state.sources.length],
    ];
    $("#research-stats").innerHTML = cards.map(([label, value]) =>
      `<div class="research-stat"><span>${esc(label)}</span><strong>${nf.format(Number(value) || 0)}</strong></div>`
    ).join("");
  }

  function renderSources() {
    const wrap = $("#research-sources");
    if (!state.sources.length) {
      wrap.innerHTML = "";
      return;
    }
    wrap.innerHTML = state.sources.map((source) => {
      const status = source.last_status || "";
      const dot = status === "error" ? "error" : (status ? "ok" : "");
      const count = source.last_count ? ` · ${source.last_count}` : "";
      return `<button type="button" class="research-source-chip ${state.source === source.id ? "active" : ""}" data-source-id="${esc(source.id)}" title="${esc(source.last_error || source.name)}">
        <span class="state ${dot}"></span>${esc(source.name)}${esc(count)}
      </button>`;
    }).join("");
    $$("#research-sources [data-source-id]").forEach((button) => {
      button.addEventListener("click", () => {
        state.source = state.source === button.dataset.sourceId ? "" : button.dataset.sourceId;
        $("#research-source").value = state.source;
        load(true);
      });
    });
  }

  function renderItems() {
    const wrap = $("#research-list");
    wrap.classList.remove("research-loading");
    if (!state.items.length) {
      wrap.innerHTML = `<div class="research-empty"><strong>暂无研究条目</strong>可以先点右上角“刷新元数据”，或扩大时间范围/更换来源。</div>`;
      $("#research-more-wrap").hidden = true;
      return;
    }
    wrap.innerHTML = state.items.map((item) => {
      const authors = (item.authors || []).join("、");
      const institutions = (item.institutions || []).join("、");
      const topics = (item.topics || []).slice(0, 5).join(" · ");
      const doi = item.doi ? `<a href="https://doi.org/${encodeURI(item.doi)}" target="_blank" rel="noopener">DOI ${esc(item.doi)}</a>` : "";
      const reportNo = item.report_no ? `<span>编号 ${esc(item.report_no)}</span>` : "";
      const pdf = item.pdf_url ? `<a href="${esc(item.pdf_url)}" target="_blank" rel="noopener">PDF ↗</a>` : "";
      const source = item.source_name || item.publisher || "";
      return `<article class="research-card">
        <div class="research-card-top">
          <span class="doc-badge ${esc(item.doc_type || "paper")}">${esc(docLabel(item.doc_type))}</span>
          <span class="research-card-source">${esc(source)}</span>
          <time class="research-card-date">${esc(formatDate(item.published_at))}</time>
        </div>
        <h2><a href="${esc(item.url)}" target="_blank" rel="noopener">${esc(item.title)}</a></h2>
        ${(authors || institutions) ? `<p class="research-people">${authors ? `作者：${esc(truncate(authors, 220))}` : ""}${authors && institutions ? " · " : ""}${institutions ? `机构：${esc(truncate(institutions, 220))}` : ""}</p>` : ""}
        <p class="research-abstract">${esc(truncate(item.abstract || "暂无摘要", 520))}</p>
        <div class="research-meta">
          ${doi}${reportNo}${topics ? `<span>${esc(truncate(topics, 160))}</span>` : ""}
          <a href="${esc(item.url)}" target="_blank" rel="noopener">原文 ↗</a>${pdf}
        </div>
      </article>`;
    }).join("");
    $("#research-more-wrap").hidden = state.items.length >= state.total;
  }

  function queryParams() {
    const params = new URLSearchParams();
    if (state.q) params.set("q", state.q);
    if (state.source) params.set("source", state.source);
    if (state.doc_type) params.set("doc_type", state.doc_type);
    params.set("days", state.days);
    params.set("limit", String(state.limit));
    params.set("offset", String(state.offset));
    return params;
  }

  async function load(reset = false) {
    if (reset) {
      state.offset = 0;
      state.items = [];
      $("#research-list").classList.add("research-loading");
    }
    const requestId = ++state.requestId;
    try {
      const data = await api(`/api/research?${queryParams().toString()}`);
      if (requestId !== state.requestId) return;
      state.total = data.total || 0;
      state.items = reset ? (data.items || []) : state.items.concat(data.items || []);
      state.refreshing = Boolean(data.refreshing);
      renderStats(data.stats || {});
      renderItems();
      if (!state.items.length && state.refreshing && !pollTimer) {
        pollTimer = setTimeout(() => { pollTimer = null; load(true); }, 3000);
      }
    } catch (error) {
      $("#research-list").classList.remove("research-loading");
      if (requestId === state.requestId) {
        $("#research-list").innerHTML = `<div class="research-empty"><strong>加载失败</strong>${esc(error.message)}</div>`;
      }
    }
  }

  async function loadSources() {
    try {
      const data = await api("/api/research/sources");
      state.sources = data.sources || [];
      state.refreshing = Boolean(data.refreshing);
      renderStats(data.stats || {});
      renderSources();
      const select = $("#research-source");
      const current = state.source;
      select.innerHTML = `<option value="">全部来源</option>` + state.sources.map((source) =>
        `<option value="${esc(source.id)}">${esc(source.name)}</option>`
      ).join("");
      select.value = current;
    } catch (error) {
      toast(`来源状态加载失败：${error.message}`, true);
    }
  }

  async function refreshMetadata() {
    const button = $("#research-refresh");
    button.disabled = true;
    button.textContent = "刷新中…";
    toast("正在抓取公开元数据…");
    try {
      const data = await api("/api/research/refresh", {
        method: "POST",
        body: JSON.stringify({ force: true }),
      });
      if (data.running) toast("抓取已启动，正在等待公开 API 返回…");
      const started = Date.now();
      while (Date.now() - started < 120000) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        const sources = await api("/api/research/sources");
        state.refreshing = Boolean(sources.refreshing);
        if (!state.refreshing) break;
      }
      await loadSources();
      await load(true);
      toast(state.refreshing ? "抓取仍在后台运行，可以稍后刷新" : "研究元数据已更新");
    } catch (error) {
      toast(`刷新失败：${error.message}`, true);
    } finally {
      button.disabled = false;
      button.textContent = "刷新元数据";
    }
  }

  function bind() {
    $("#research-q").addEventListener("input", (event) => {
      state.q = event.target.value.trim();
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => load(true), 300);
    });
    $("#research-source").addEventListener("change", (event) => {
      state.source = event.target.value;
      load(true);
    });
    $("#research-days").addEventListener("change", (event) => {
      state.days = event.target.value;
      load(true);
    });
    $$("#research-types [data-doc-type]").forEach((button) => {
      button.addEventListener("click", () => {
        state.doc_type = button.dataset.docType || "";
        $$("#research-types [data-doc-type]").forEach((item) => item.classList.toggle("active", item === button));
        load(true);
      });
    });
    $("#research-refresh").addEventListener("click", refreshMetadata);
    $("#research-more").addEventListener("click", () => {
      state.offset += state.limit;
      load(false);
    });
  }

  async function init() {
    bind();
    await loadSources();
    await load(true);
  }

  init().catch((error) => toast(error.message, true));
})();
