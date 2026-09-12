/* Local SVG charts: exact integer-cent labels, signed axes, keyboard/touch detail. */
window.LedgerCharts = (() => {
  const ns = 'http://www.w3.org/2000/svg';
  const node = (parent, tag, attrs = {}, text) => {
    const el = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, value));
    if (text !== undefined) el.textContent = text;
    parent.append(el); return el;
  };
  const money = value => {
    if (value == null) return '缺位';
    const n = BigInt(value), a = n < 0n ? -n : n;
    return `${n < 0n ? '-' : ''}¥${(a / 100n).toLocaleString('en-US')}.${String(a % 100n).padStart(2, '0')}`;
  };
  const button = (parent, label, action) => {
    const el = document.createElement('button'); el.type = 'button'; el.textContent = label;
    el.addEventListener('click', action); parent.append(el); return el;
  };
  function draw(container, {points, series, label, onSelect, format = money, hidden = new Set()}) {
    container.replaceChildren();
    const legend = document.createElement('div'); legend.className = 'series-controls'; container.append(legend);
    const tooltip = document.createElement('p'); tooltip.className = 'chart-detail'; tooltip.setAttribute('aria-live', 'polite');
    tooltip.textContent = '悬停或选择图形查看精确数据；点击图形查看明细。';
    const scroll = document.createElement('div'); scroll.className = 'plot-scroll'; container.append(scroll, tooltip);
    series.forEach(s => {
      const b = button(legend, `${s.line ? '━' : '▥'} ${s.label}`, () => {
        hidden.has(s.key) ? hidden.delete(s.key) : hidden.add(s.key);
        draw(container, {points, series, label, onSelect, format, hidden});
      });
      b.className = `series-${s.color}`; b.setAttribute('aria-pressed', String(!hidden.has(s.key)));
    });
    if (!points.length) { tooltip.textContent = '无匹配记录，请调整查看范围或筛选。'; return; }
    const shown = series.filter(s => !hidden.has(s.key));
    const values = points.flatMap(p => shown.map(s => p[s.key]).filter(v => v != null).map(BigInt));
    let low = values.reduce((a,b) => b < a ? b : a, 0n), high = values.reduce((a,b) => b > a ? b : a, 0n);
    if (low === high) high = low + 100n;
    const span = high - low;
    const width = Math.max(560, container.clientWidth, points.length * Math.max(24, shown.length * 10) + 100);
    const y = v => 218 - Number((BigInt(v) - low) * 19000n / span) / 100;
    const svg = node(scroll, 'svg', {viewBox: `0 0 ${width} 268`, width, height: 268, role: 'group', 'aria-label': label, class: 'analytics-svg'});
    for (let i = 0; i <= 4; i++) {
      const value = low + span * BigInt(i) / 4n, yy = y(value);
      node(svg, 'line', {x1: 84, x2: width - 12, y1: yy, y2: yy, class: 'chart-grid'});
      node(svg, 'text', {x: 78, y: yy + 4, 'text-anchor': 'end', class: 'chart-label'}, format(value));
    }
    node(svg, 'line', {x1: 84, x2: width - 12, y1: y(0), y2: y(0), class: 'zero-axis'});
    const slot = (width - 100) / points.length, x = i => 84 + slot * (i + .5);
    shown.filter(s => s.line).forEach(s => {
      let path = '', connected = false;
      points.forEach((p,i) => { if (p[s.key] == null) {connected = false; return;} path += `${connected ? 'L' : 'M'}${x(i)},${y(p[s.key])} `; connected = true; });
      node(svg, 'path', {d: path, class: `plot-line series-${s.color}${s.compare ? ' compare-line' : ''}`});
    });
    points.forEach((p,i) => {
      const text = `${p.label}${p.range ? ` · ${p.range}` : ''}；${shown.map(s => `${s.label} ${format(p[s.key])}${p[`${s.key}_count`] != null ? ` / ${p[`${s.key}_count`]}笔` : ''}`).join('；')}`;
      const group = node(svg, 'g', {tabindex: '0', role: 'button', 'aria-label': text, class: 'plot-point'});
      node(group, 'rect', {x: x(i) - slot / 2, y: 20, width: slot, height: 208, class: 'plot-hit'});
      const bars = shown.filter(s => !s.line), bw = Math.min(18, slot * .8 / Math.max(1,bars.length));
      shown.forEach(s => {
        if (p[s.key] == null) return;
        if (s.line) node(group, 'circle', {cx: x(i), cy: y(p[s.key]), r: 3, class: `plot-fill series-${s.color}`});
        else {
          const yy = y(p[s.key]), baseline = y(0);
          const bar = node(group, 'rect', {x: x(i) + (bars.indexOf(s) - bars.length / 2) * bw, y: Math.min(yy, baseline), width: Math.max(2,bw - 2), height: Math.max(1,Math.abs(baseline - yy)), rx: 2, class: `plot-fill series-${s.color}${s.compare ? ' compare-bar' : ''}`});
          bar.addEventListener('click', e => {e.stopPropagation(); tooltip.textContent = text; onSelect?.(p,s);});
        }
      });
      const show = () => {tooltip.textContent = text;};
      group.addEventListener('pointerenter', show); group.addEventListener('focus', show);
      group.addEventListener('click', () => {show(); onSelect?.(p, shown[0]);});
      group.addEventListener('keydown', e => {if (e.key === 'Enter' || e.key === ' ') {e.preventDefault(); show(); onSelect?.(p, shown[0]);}});
      if (i % Math.max(1,Math.ceil(points.length / (width / 92))) === 0)
        node(svg, 'text', {x: x(i), y: 247, 'text-anchor': 'middle', class: 'chart-label'}, p.label);
    });
    const details = document.createElement('details'); details.className = 'chart-data';
    const summary = document.createElement('summary'); summary.textContent = '查看数据与两期明细入口'; details.append(summary);
    const list = document.createElement('div'); list.className = 'chart-data-list'; details.append(list);
    points.forEach(p => series.forEach(s => {
      if (p[s.key] != null) button(list, `${p.label} · ${s.label} ${format(p[s.key])}${p.range ? ` · ${p.range}` : ''}`, () => onSelect?.(p,s));
    }));
    container.append(details);
  }
  return {draw, money};
})();
