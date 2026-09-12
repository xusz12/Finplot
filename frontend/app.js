const $ = (selector) => document.querySelector(selector);
const state = {
  serial: 0,
  controller: null,
  version: null,
  nextCursor: null,
  selectedTags: new Set(),
  group: null,
  category: null,
  lastSync: null,
  renderEpoch: null,
  polling: false,
  pendingPerf: null,
  perf: {
    initial: null,
    filter: [],
    drill: [],
    refresh: [],
    refreshEpochs: [],
  },
};

const money = (value) => {
  const cents = BigInt(value);
  const absolute = cents < 0n ? -cents : cents;
  return `${cents < 0n ? "-" : ""}¥${(absolute / 100n).toLocaleString("en-US")}.${(absolute % 100n).toString().padStart(2, "0")}`;
};

const add = (parent, tag, text) => {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = String(text);
  parent.append(element);
  return element;
};

const nowInShanghai = () => {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(
    parts.map((part) => [part.type, part.value]),
  );
  return `${values.year}-${values.month}-${values.day}`;
};

const syncScopeControls = () => {
  const kind = $("#kind").value;
  const value = $("#value");
  const today = nowInShanghai();
  const [year, month] = today.split("-");
  const custom = kind === "custom";
  const all = kind === "all";
  $("#value-label").hidden = custom || all;
  $("#start").parentElement.hidden = !custom;
  $("#end").parentElement.hidden = !custom;
  if (kind === "month") {
    value.type = "month";
    value.placeholder = "YYYY-MM";
  } else if (kind === "day") {
    value.type = "date";
    value.placeholder = "YYYY-MM-DD";
  } else if (kind === "year") {
    value.type = "text";
    value.placeholder = "YYYY";
  } else if (kind === "quarter") {
    value.type = "text";
    value.placeholder = "YYYY-Q1";
  } else if (kind === "half") {
    value.type = "text";
    value.placeholder = "YYYY-H1";
  }
  if (kind === "month" && !/^\d{4}-\d{2}$/.test(value.value))
    value.value = `${year}-${month}`;
  if (kind === "day" && !/^\d{4}-\d{2}-\d{2}$/.test(value.value))
    value.value = today;
  if (kind === "quarter" && !/^\d{4}-Q[1-4]$/.test(value.value))
    value.value = `${year}-Q${Math.floor((Number(month) - 1) / 3) + 1}`;
  if (kind === "half" && !/^\d{4}-H[12]$/.test(value.value))
    value.value = `${year}-H${Number(month) <= 6 ? 1 : 2}`;
  if (kind === "year" && !/^\d{4}$/.test(value.value)) value.value = year;
  if (custom) {
    if (!$("#start").value) $("#start").value = `${year}-${month}-01`;
    if (!$("#end").value) $("#end").value = today;
  }
};

const params = (cursor = null) => {
  const kind = $("#kind").value;
  const query = new URLSearchParams({
    kind,
    tag_mode: $("#mode").value,
    limit: "50",
  });
  if (kind === "custom") {
    query.set("start", $("#start").value);
    query.set("end", $("#end").value);
  } else if (kind !== "all") {
    query.set("value", $("#value").value);
  }
  for (const tag of [...state.selectedTags].sort()) query.append("tag", tag);
  for (const filter of ["direction", "nature"]) {
    if ($("#" + filter).value) query.set(filter, $("#" + filter).value);
  }
  if (state.group) query.set("group", state.group);
  if (state.category) query.set("category", state.category);
  if (cursor) query.set("cursor", cursor);
  return query;
};

const setStatus = (text) => {
  $("#status").textContent = text;
};
const clock = () =>
  state.lastSync
    ? new Date(state.lastSync).toLocaleTimeString("zh-CN", {
        hour12: false,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "—";

const formatPerf = (value) => (value === null ? "—" : `${value.toFixed(1)}ms`);
const recordPerf = () => {
  if (!state.pendingPerf) return;
  const { kind, started } = state.pendingPerf;
  const elapsed = performance.now() - started;
  if (kind === "initial") state.perf.initial = elapsed;
  else if (state.perf[kind]) state.perf[kind].push(elapsed);
  state.pendingPerf = null;
  const last = (values) => (values.length ? values[values.length - 1] : null);
  const max = (values) => (values.length ? Math.max(...values) : null);
  $("#perf").textContent =
    `首屏 ${formatPerf(state.perf.initial)} · 筛选 ${state.perf.filter.length}/20（最新 ${formatPerf(last(state.perf.filter))}，最大 ${formatPerf(max(state.perf.filter))}） · 钻取 ${state.perf.drill.length}/20（最新 ${formatPerf(last(state.perf.drill))}，最大 ${formatPerf(max(state.perf.drill))}） · 自动刷新 ${state.perf.refresh.length}（最新 ${formatPerf(last(state.perf.refresh))}，最大 ${formatPerf(max(state.perf.refresh))}） · 刷新 epochs [${state.perf.refreshEpochs.join(",")}] · 最近渲染 epoch ${state.renderEpoch === null ? "—" : state.renderEpoch}`;
};

const renderTags = (available) => {
  const codes = new Set(available.map((tag) => tag.code));
  state.selectedTags = new Set(
    [...state.selectedTags].filter((tag) => codes.has(tag)),
  );
  const container = $("#tags");
  container.replaceChildren();
  if (!available.length) {
    add(container, "span", "暂无可用标签").className = "muted";
    return;
  }
  for (const tag of available) {
    const label = add(container, "label");
    label.className = "tag-option";
    const input = add(label, "input");
    input.type = "checkbox";
    input.value = tag.code;
    input.checked = state.selectedTags.has(tag.code);
    input.addEventListener("change", () => {
      if (input.checked) state.selectedTags.add(tag.code);
      else state.selectedTags.delete(tag.code);
      state.group = null;
      state.category = null;
      loadDashboard({ perfKind: "filter" });
    });
    add(label, "span", tag.name);
  }
};

const renderTrend = (trend) => {
  const container = $("#trend");
  container.replaceChildren();
  if (!trend.length) {
    add(container, "div", "这个范围还没有交易 · 试试调整筛选条件").className =
      "empty-state";
    return;
  }
  const svgNode = (parent, tag, attrs, text) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs))
      node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    parent.append(node);
    return node;
  };
  const width = Math.max(360, container.clientWidth, trend.length * 24 + 64);
  const svg = svgNode(container, "svg", {
    viewBox: `0 0 ${width} 250`,
    class: "trend-svg",
    role: "img",
    "aria-label": "每日收入与支出，绿色为收入，沙色为支出",
  });
  // Preserve daily granularity even for long ranges; use a scrollable plot.
  if (trend.length > 28) svg.setAttribute("width", width);
  svg.classList.toggle("wide-chart", trend.length > 28);
  const absolute = (value) => {
    const n = BigInt(value);
    return n < 0n ? -n : n;
  };
  const maximum = trend.reduce(
    (m, item) =>
      [absolute(item.income_cents), absolute(item.expense_cents)].reduce(
        (a, v) => (v > a ? v : a),
        m,
      ),
    1n,
  );
  for (let i = 0; i <= 4; i++) {
    const y = 20 + i * 48;
    svgNode(svg, "line", {
      x1: 62,
      x2: width - 10,
      y1: y,
      y2: y,
      class: "chart-grid",
    });
    const amount = (maximum * BigInt(4 - i)) / 4n;
    const yuan = amount / 100n;
    const label =
      yuan >= 10000n ? `${Number(yuan / 100n) / 100}万` : String(yuan);
    svgNode(
      svg,
      "text",
      { x: 53, y: y + 4, "text-anchor": "end", class: "chart-label" },
      label,
    );
  }
  const slot = (width - 76) / trend.length;
  const barWidth = Math.min(14, slot * 0.3);
  trend.forEach((item, index) => {
    const x = 62 + slot * (index + 0.5);
    const point = svgNode(svg, "g", {
      class: "chart-point",
      tabindex: "0",
      "aria-label": `${item.day}，收入 ${money(item.income_cents)}，支出 ${money(item.expense_cents)}`,
    });
    svgNode(
      point,
      "title",
      {},
      `${item.day} · 收入 ${money(item.income_cents)} · 支出 ${money(item.expense_cents)}`,
    );
    for (const [key, offset, className] of [
      ["income_cents", -barWidth - 1, "chart-income"],
      ["expense_cents", 1, "chart-expense"],
    ]) {
      const height = Number((absolute(item[key]) * 19200n) / maximum) / 100;
      svgNode(point, "rect", {
        x: x + offset,
        y: 212 - height,
        width: barWidth,
        height,
        rx: 2,
        class: className,
      });
    }
    if (
      index % Math.max(1, Math.ceil(trend.length / (width / 65))) === 0 ||
      index === trend.length - 1
    ) {
      svgNode(
        svg,
        "text",
        { x, y: 237, "text-anchor": "middle", class: "chart-label" },
        item.day.slice(5),
      );
    }
  });
};

const drillButton = (parent, label, className, callback, selected = false) => {
  const button = add(parent, "button");
  const pieces = label.split(" · ");
  add(button, "span", pieces.slice(0, -2).join(" · ")).className = "drill-name";
  add(button, "span", pieces[pieces.length - 2]).className = "drill-amount";
  add(button, "span", pieces[pieces.length - 1]).className = "drill-count";
  button.setAttribute("aria-pressed", String(selected));
  button.type = "button";
  button.className = `drill-button ${className}${selected ? " selected" : ""}`;
  button.addEventListener("click", callback);
  return button;
};

const renderCategories = (groups, categories) => {
  const container = $("#categories");
  container.replaceChildren();
  const byGroup = new Map();
  for (const category of categories) {
    if (!byGroup.has(category.group_code)) byGroup.set(category.group_code, []);
    byGroup.get(category.group_code).push(category);
  }
  if (!groups.length) {
    add(container, "span", "无匹配分类").className = "muted";
    return;
  }
  for (const group of groups) {
    const section = add(container, "div");
    section.className = "category-group";
    drillButton(
      section,
      `${group.group_name} · ${money(group.cents)} · ${group.count} 笔`,
      "group-button",
      () => {
        state.group =
          state.group === group.group_code ? null : group.group_code;
        state.category = null;
        loadDashboard({ perfKind: "drill" });
      },
      state.group === group.group_code,
    );
    const list = add(section, "div");
    list.className = "category-list";
    for (const category of byGroup.get(group.group_code) || []) {
      drillButton(
        list,
        `${category.name} · ${money(category.cents)} · ${category.count} 笔`,
        "category-button",
        () => {
          state.group = category.group_code;
          state.category =
            state.category === category.code ? null : category.code;
          loadDashboard({ perfKind: "drill" });
        },
        state.category === category.code,
      );
    }
  }
};

const renderRows = (rows, append) => {
  const body = $("#rows");
  if (!append) body.replaceChildren();
  for (const row of rows) {
    const tr = add(body, "tr");
    for (const value of [
      row.occurred_at.slice(0, 10),
      row.direction,
      row.category,
      row.tags.join(" · ") || "—",
      money(row.amount_cents),
    ]) {
      const cell = add(tr, "td");
      if (value === row.direction)
        add(cell, "span", value).className =
          `direction-pill${value === "支出" ? " expense" : ""}`;
      else cell.textContent = value;
    }
  }
  if (!append && !rows.length) {
    const tr = add(body, "tr");
    const td = add(tr, "td", "无匹配交易");
    td.colSpan = 5;
  }
};

const renderData = (data, append = false) => {
  $("#income").textContent = money(data.totals.income_cents);
  $("#expense").textContent = money(data.totals.expense_cents);
  $("#balance").textContent = money(data.totals.balance_cents);
  $("#count").textContent = String(data.filtered_count);
  $("#investment").textContent = money(data.investment.net_cents);
  $("#investment-meta").textContent =
    `收入 ${money(data.investment.income_cents)} · 支出 ${money(data.investment.expense_cents)} · ${data.investment.filtered_count} 笔`;
  $("#meta").textContent =
    `${data.scope.label} · ${data.scope.timezone} · 数据截止 ${data.data_cutoff || "—"} · 最近同步 ${clock()}`;
  const groupName = data.groups.find(
    (g) => g.group_code === state.group,
  )?.group_name;
  const categoryName = data.categories.find(
    (c) => c.code === state.category,
  )?.name;
  $("#drill-path").textContent =
    `当前分类：${[groupName, categoryName].filter(Boolean).join(" / ") || "全部"}`;
  $("#clear-drill").hidden = !state.group && !state.category;
  renderTags(data.tags);
  renderTrend(data.trend);
  renderCategories(data.groups, data.categories);
  renderRows(data.transactions, append);
  state.nextCursor = data.next_cursor;
  $("#more").disabled = !state.nextCursor;
  $("#page-meta").textContent =
    `已显示 ${data.filtered_count ? $("#rows").querySelectorAll("tr").length : 0}${data.next_cursor ? " · 可继续加载" : ""}`;
  const pendingKind = state.pendingPerf?.kind;
  state.renderEpoch = Date.now();
  if (pendingKind === "refresh")
    state.perf.refreshEpochs.push(state.renderEpoch);
  recordPerf();
};

const loadDashboard = async ({
  append = false,
  cursor = null,
  perfKind = null,
} = {}) => {
  const mine = ++state.serial;
  if (state.controller) state.controller.abort();
  state.controller = new AbortController();
  if (perfKind)
    state.pendingPerf = {
      kind: perfKind,
      started: perfKind === "initial" ? 0 : performance.now(),
    };
  if (!append) {
    state.nextCursor = null;
    $("#more").disabled = true;
  }
  try {
    const response = await fetch(`/api/dashboard?${params(cursor)}`, {
      cache: "no-store",
      signal: state.controller.signal,
    });
    if (response.status === 409) {
      if (mine === state.serial) {
        state.version = null;
        setStatus("数据已变化，正在重新同步…");
        await loadDashboard({ perfKind });
      }
      return;
    }
    if (!response.ok) throw new Error("暂时无法读取数据");
    const data = await response.json();
    if (mine !== state.serial) return;
    state.version = data.version;
    state.lastSync = Date.now();
    renderData(data, append);
    setStatus(`已同步 · ${clock()}`);
  } catch (error) {
    if (error.name === "AbortError" || mine !== state.serial) return;
    setStatus(error.message || "暂时无法读取数据");
  }
};

const pollVersion = async () => {
  if (document.hidden || state.polling) return;
  state.polling = true;
  try {
    const response = await fetch("/api/version", { cache: "no-store" });
    if (!response.ok) throw new Error("暂时无法检查数据版本");
    const probe = await response.json();
    if (!state.version || probe.version !== state.version)
      await loadDashboard({ perfKind: "refresh" });
    else setStatus(`已同步 · ${clock()}`);
  } catch (error) {
    setStatus(error.message || "暂时无法检查数据版本");
  } finally {
    state.polling = false;
  }
};

const resetAndLoad = () => {
  state.group = null;
  state.category = null;
  loadDashboard({ perfKind: "filter" });
};

$("#reset").addEventListener("click", () => {
  $("#kind").value = "month";
  $("#value").value = nowInShanghai().slice(0, 7);
  $("#direction").value = "";
  $("#nature").value = "";
  $("#mode").value = "any";
  state.selectedTags.clear();
  syncScopeControls();
  resetAndLoad();
});
$("#clear-drill").addEventListener("click", resetAndLoad);
for (const link of document.querySelectorAll("nav a"))
  link.addEventListener("click", () => {
    document
      .querySelectorAll("nav a")
      .forEach((item) => item.classList.toggle("active", item === link));
  });
$("#value").value = nowInShanghai().slice(0, 7);
syncScopeControls();
$("#kind").addEventListener("change", () => {
  syncScopeControls();
  resetAndLoad();
});
for (const id of ["value", "start", "end", "mode", "direction", "nature"])
  $("#" + id).addEventListener("change", resetAndLoad);
$("#reload").addEventListener("click", resetAndLoad);
$("#more").addEventListener("click", () => {
  if (state.nextCursor)
    loadDashboard({ append: true, cursor: state.nextCursor });
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) loadDashboard();
});
setInterval(pollVersion, 2000);
loadDashboard({ perfKind: "initial" });
