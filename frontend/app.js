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
  perf: { initial: null, filter: [], drill: [], refresh: [], refreshEpochs: [] },
};

const money = (value) => {
  const cents = BigInt(value);
  const absolute = cents < 0n ? -cents : cents;
  return `${cents < 0n ? '-' : ''}¥${absolute / 100n}.${(absolute % 100n).toString().padStart(2, '0')}`;
};

const add = (parent, tag, text) => {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = String(text);
  parent.append(element);
  return element;
};

const nowInShanghai = () => {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
};

const syncScopeControls = () => {
  const kind = $('#kind').value;
  const value = $('#value');
  const today = nowInShanghai();
  const [year, month] = today.split('-');
  const custom = kind === 'custom';
  const all = kind === 'all';
  $('#value-label').hidden = custom || all;
  $('#start').parentElement.hidden = !custom;
  $('#end').parentElement.hidden = !custom;
  if (kind === 'month') {
    value.type = 'month';
    value.placeholder = 'YYYY-MM';
  } else if (kind === 'day') {
    value.type = 'date';
    value.placeholder = 'YYYY-MM-DD';
  } else if (kind === 'year') {
    value.type = 'text';
    value.placeholder = 'YYYY';
  } else if (kind === 'quarter') {
    value.type = 'text';
    value.placeholder = 'YYYY-Q1';
  } else if (kind === 'half') {
    value.type = 'text';
    value.placeholder = 'YYYY-H1';
  }
  if (kind === 'month' && !/^\d{4}-\d{2}$/.test(value.value)) value.value = `${year}-${month}`;
  if (kind === 'day' && !/^\d{4}-\d{2}-\d{2}$/.test(value.value)) value.value = today;
  if (kind === 'quarter' && !/^\d{4}-Q[1-4]$/.test(value.value)) value.value = `${year}-Q${Math.floor((Number(month) - 1) / 3) + 1}`;
  if (kind === 'half' && !/^\d{4}-H[12]$/.test(value.value)) value.value = `${year}-H${Number(month) <= 6 ? 1 : 2}`;
  if (kind === 'year' && !/^\d{4}$/.test(value.value)) value.value = year;
  if (custom) {
    if (!$('#start').value) $('#start').value = `${year}-${month}-01`;
    if (!$('#end').value) $('#end').value = today;
  }
};

const params = (cursor = null) => {
  const kind = $('#kind').value;
  const query = new URLSearchParams({ kind, tag_mode: $('#mode').value, limit: '50' });
  if (kind === 'custom') {
    query.set('start', $('#start').value);
    query.set('end', $('#end').value);
  } else if (kind !== 'all') {
    query.set('value', $('#value').value);
  }
  for (const tag of [...state.selectedTags].sort()) query.append('tag', tag);
  for (const filter of ['direction', 'nature']) {
    if ($('#' + filter).value) query.set(filter, $('#' + filter).value);
  }
  if (state.group) query.set('group', state.group);
  if (state.category) query.set('category', state.category);
  if (cursor) query.set('cursor', cursor);
  return query;
};

const setStatus = (text) => { $('#status').textContent = text; };
const clock = () => state.lastSync ? new Date(state.lastSync).toLocaleTimeString('zh-CN', { hour12: false, fractionalSecondDigits: 3 }) : '—';

const formatPerf = (value) => value === null ? '—' : `${value.toFixed(1)}ms`;
const recordPerf = () => {
  if (!state.pendingPerf) return;
  const { kind, started } = state.pendingPerf;
  const elapsed = performance.now() - started;
  if (kind === 'initial') state.perf.initial = elapsed;
  else if (state.perf[kind]) state.perf[kind].push(elapsed);
  state.pendingPerf = null;
  const last = (values) => values.length ? values[values.length - 1] : null;
  const max = (values) => values.length ? Math.max(...values) : null;
  $('#perf').textContent = `首屏 ${formatPerf(state.perf.initial)} · 筛选 ${state.perf.filter.length}/20（最新 ${formatPerf(last(state.perf.filter))}，最大 ${formatPerf(max(state.perf.filter))}） · 钻取 ${state.perf.drill.length}/20（最新 ${formatPerf(last(state.perf.drill))}，最大 ${formatPerf(max(state.perf.drill))}） · 自动刷新 ${state.perf.refresh.length}（最新 ${formatPerf(last(state.perf.refresh))}，最大 ${formatPerf(max(state.perf.refresh))}） · 刷新 epochs [${state.perf.refreshEpochs.join(',')}] · 最近渲染 epoch ${state.renderEpoch === null ? '—' : state.renderEpoch}`;
};

const renderTags = (available) => {
  const codes = new Set(available.map((tag) => tag.code));
  state.selectedTags = new Set([...state.selectedTags].filter((tag) => codes.has(tag)));
  const container = $('#tags');
  container.replaceChildren();
  if (!available.length) {
    add(container, 'span', '暂无可用标签').className = 'muted';
    return;
  }
  for (const tag of available) {
    const label = add(container, 'label');
    label.className = 'tag-option';
    const input = add(label, 'input');
    input.type = 'checkbox';
    input.value = tag.code;
    input.checked = state.selectedTags.has(tag.code);
    input.addEventListener('change', () => {
      if (input.checked) state.selectedTags.add(tag.code);
      else state.selectedTags.delete(tag.code);
      state.group = null;
      state.category = null;
      loadDashboard({ perfKind: 'filter' });
    });
    add(label, 'span', tag.name);
  }
};

const renderTrend = (trend) => {
  const container = $('#trend');
  container.replaceChildren();
  if (!trend.length) {
    add(container, 'span', '无匹配日期').className = 'muted';
    return;
  }
  const totals = trend.map((item) => {
    const value = BigInt(item.income_cents) + BigInt(item.expense_cents);
    return value < 0n ? -value : value;
  });
  const max = totals.reduce((highest, value) => value > highest ? value : highest, 1n);
  trend.forEach((item, index) => {
    const point = add(container, 'div');
    point.className = 'trend-item';
    const income = money(item.income_cents);
    const expense = money(item.expense_cents);
    point.title = `${item.day} · 收入 ${income} · 支出 ${expense}`;
    const bar = add(point, 'progress');
    bar.className = 'trend-bar';
    bar.max = 100;
    bar.value = Math.max(3, Math.min(100, Number(totals[index] * 100n / max)));
    bar.setAttribute('aria-label', point.title);
    add(point, 'small', item.day.slice(5));
  });
};

const drillButton = (parent, label, className, callback, selected = false) => {
  const button = add(parent, 'button', label);
  button.type = 'button';
  button.className = `drill-button ${className}${selected ? ' selected' : ''}`;
  button.addEventListener('click', callback);
  return button;
};

const renderCategories = (groups, categories) => {
  const container = $('#categories');
  container.replaceChildren();
  const byGroup = new Map();
  for (const category of categories) {
    if (!byGroup.has(category.group_code)) byGroup.set(category.group_code, []);
    byGroup.get(category.group_code).push(category);
  }
  if (!groups.length) {
    add(container, 'span', '无匹配分类').className = 'muted';
    return;
  }
  for (const group of groups) {
    const section = add(container, 'div');
    section.className = 'category-group';
    drillButton(section, `${group.group_name} · ${money(group.cents)} · ${group.count} 笔`, 'group-button', () => {
      state.group = state.group === group.group_code ? null : group.group_code;
      state.category = null;
      loadDashboard({ perfKind: 'drill' });
    }, state.group === group.group_code);
    const list = add(section, 'div');
    list.className = 'category-list';
    for (const category of byGroup.get(group.group_code) || []) {
      drillButton(list, `${category.name} · ${money(category.cents)} · ${category.count} 笔`, 'category-button', () => {
        state.group = category.group_code;
        state.category = state.category === category.code ? null : category.code;
        loadDashboard({ perfKind: 'drill' });
      }, state.category === category.code);
    }
  }
};

const renderRows = (rows, append) => {
  const body = $('#rows');
  if (!append) body.replaceChildren();
  for (const row of rows) {
    const tr = add(body, 'tr');
    for (const value of [row.occurred_at.slice(0, 10), row.direction, row.category, row.tags.join(' · ') || '—', money(row.amount_cents)]) {
      add(tr, 'td', value);
    }
  }
  if (!append && !rows.length) {
    const tr = add(body, 'tr');
    const td = add(tr, 'td', '无匹配交易');
    td.colSpan = 5;
  }
};

const renderData = (data, append = false) => {
  $('#income').textContent = money(data.totals.income_cents);
  $('#expense').textContent = money(data.totals.expense_cents);
  $('#balance').textContent = money(data.totals.balance_cents);
  $('#count').textContent = String(data.filtered_count);
  $('#investment').textContent = money(data.investment.net_cents);
  $('#investment-meta').textContent = `收入 ${money(data.investment.income_cents)} · 支出 ${money(data.investment.expense_cents)} · ${data.investment.filtered_count} 笔`;
  $('#meta').textContent = `${data.scope.label} · ${data.scope.timezone} · 数据截止 ${data.data_cutoff || '—'} · 最近同步 ${clock()}`;
  $('#drill-path').textContent = `当前分类：${state.category || state.group || '全部'}`;
  renderTags(data.tags);
  renderTrend(data.trend);
  renderCategories(data.groups, data.categories);
  renderRows(data.transactions, append);
  state.nextCursor = data.next_cursor;
  $('#more').disabled = !state.nextCursor;
  $('#page-meta').textContent = `已显示 ${data.transactions.length}${data.next_cursor ? ' · 可继续加载' : ''}`;
  const pendingKind = state.pendingPerf?.kind;
  state.renderEpoch = Date.now();
  if (pendingKind === 'refresh') state.perf.refreshEpochs.push(state.renderEpoch);
  recordPerf();
};

const loadDashboard = async ({ append = false, cursor = null, perfKind = null } = {}) => {
  const mine = ++state.serial;
  if (state.controller) state.controller.abort();
  state.controller = new AbortController();
  if (perfKind) state.pendingPerf = { kind: perfKind, started: perfKind === 'initial' ? 0 : performance.now() };
  if (!append) {
    state.nextCursor = null;
    $('#more').disabled = true;
  }
  try {
    const response = await fetch(`/api/dashboard?${params(cursor)}`, { cache: 'no-store', signal: state.controller.signal });
    if (response.status === 409) {
      if (mine === state.serial) {
        state.version = null;
        setStatus('数据已变化，正在重新同步…');
        await loadDashboard({ perfKind });
      }
      return;
    }
    if (!response.ok) throw new Error('暂时无法读取数据');
    const data = await response.json();
    if (mine !== state.serial) return;
    state.version = data.version;
    state.lastSync = Date.now();
    renderData(data, append);
    setStatus(`已同步 · ${clock()}`);
  } catch (error) {
    if (error.name === 'AbortError' || mine !== state.serial) return;
    setStatus(error.message || '暂时无法读取数据');
  }
};

const pollVersion = async () => {
  if (document.hidden || state.polling) return;
  state.polling = true;
  try {
    const response = await fetch('/api/version', { cache: 'no-store' });
    if (!response.ok) throw new Error('暂时无法检查数据版本');
    const probe = await response.json();
    if (!state.version || probe.version !== state.version) await loadDashboard({ perfKind: 'refresh' });
    else setStatus(`已同步 · ${clock()}`);
  } catch (error) {
    setStatus(error.message || '暂时无法检查数据版本');
  } finally {
    state.polling = false;
  }
};

const resetAndLoad = () => {
  state.group = null;
  state.category = null;
  loadDashboard({ perfKind: 'filter' });
};

$('#value').value = nowInShanghai().slice(0, 7);
syncScopeControls();
$('#kind').addEventListener('change', () => { syncScopeControls(); resetAndLoad(); });
for (const id of ['value', 'start', 'end', 'mode', 'direction', 'nature']) $('#' + id).addEventListener('change', resetAndLoad);
$('#reload').addEventListener('click', resetAndLoad);
$('#more').addEventListener('click', () => { if (state.nextCursor) loadDashboard({ append: true, cursor: state.nextCursor }); });
document.addEventListener('visibilitychange', () => { if (!document.hidden) loadDashboard(); });
setInterval(pollVersion, 2000);
loadDashboard({ perfKind: 'initial' });
