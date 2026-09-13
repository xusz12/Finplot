const $ = selector => document.querySelector(selector);
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


const state = {
  data: null, detail: null, history: [], selectedTags: new Set(), group: null,
  serial: 0, controller: null, version: null, cursor: null, polling: false,
  hidden: new Map(), lastSync: null, refreshCount: 0, renderedRows: 0,
};
const percent = value => value == null ? '无基数' : `${(Number(value) * 100).toFixed(1)}%`;
const decimalMoney = value => {
  if (value == null) return '—';
  // Decimal average cents may contain fractions. Round only for display.
  const [whole, fraction = ''] = String(value).split('.');
  const negative = whole.startsWith('-');
  const cents = BigInt(whole) + ((fraction[0] || '0') >= '5' ? (negative ? -1n : 1n) : 0n);
  return money(cents);
};
const ratioMoney = (numerator, denominator, fallback) => {
  if (numerator == null || denominator == null) return decimalMoney(fallback);
  const n=BigInt(numerator), d=BigInt(denominator); if(d<=0n)return '—';
  const a=n<0n?-n:n, rounded=(a*2n+d)/(d*2n);
  return money(n<0n?-rounded:rounded);
};
const previousDate = end => {
  if (!end) return null;
  const d = new Date(`${end}T00:00:00Z`); d.setUTCDate(d.getUTCDate() - 1); return d.toISOString().slice(0,10);
};
const rangeText = range => range?.start && range?.end_exclusive ? `${range.start} 至 ${previousDate(range.end_exclusive)}` : '无有效日期范围';
const days = range => range?.start && range?.end_exclusive ? Math.round((Date.parse(range.end_exclusive) - Date.parse(range.start)) / 86400000) : 0;
const action = (parent, label, fn, className = '') => {
  const b = add(parent, 'button', label); b.type = 'button'; b.className = className; b.addEventListener('click', fn); return b;
};
const query = () => {
  const q = new URLSearchParams({kind: $('#kind').value, tag_mode: $('#mode').value, compare: $('#compare').value, period_mode: $('#period-mode').value, grain: $('#grain').value, top_n: '0'});
  if ($('#kind').value === 'custom') {q.set('start', $('#start').value); q.set('end', $('#end').value);}
  else if ($('#kind').value !== 'all') q.set('value', $('#value').value);
  for (const key of ['direction', 'nature']) if ($('#'+key).value) q.set(key,$('#'+key).value);
  for (const tag of [...state.selectedTags].sort()) q.append('tag',tag);
  if ($('#compare').value === 'custom') {q.set('compare_start',$('#compare-start').value);q.set('compare_end',$('#compare-end').value);}
  return q;
};
const detailQuery = (data, detail, cursor) => {
  const q = query();
  for (const key of ['compare','compare_start','compare_end','period_mode','grain','top_n','value']) q.delete(key);
  const range = detail?.range || data.scope;
  q.set('kind','custom'); q.set('start', range.start); q.set('end',previousDate(range.end_exclusive)); q.set('version',data.version); q.set('limit','50');
  for (const key of ['direction','nature','group','category']) if (detail?.[key]) q.set(key,detail[key]);
  if (cursor) q.set('cursor',cursor);
  return q;
};
async function get(url, signal) {
  const response = await fetch(url, {cache:'no-store',signal});
  if (!response.ok) {
    const err = new Error(response.status === 422 ? '日期或筛选组合无效，请检查输入。' : response.status === 409 ? '账本已变化，正在重新同步…' : '暂时无法读取账本，请重试。');
    err.status = response.status; throw err;
  }
  return response.json();
}
function tags(data) {
  const parent = $('#tags'); parent.replaceChildren();
  if (!data.length) add(parent,'span','暂无可用标签').className='muted';
  data.forEach(tag => {
    const label = add(parent,'label');label.className='tag-option';const input=add(label,'input'); input.type='checkbox';input.checked=state.selectedTags.has(tag.code);
    add(label,'span',tag.name);input.addEventListener('change',()=>{input.checked?state.selectedTags.add(tag.code):state.selectedTags.delete(tag.code);resetSelection();load();});
  });
}
function comparisonText(current, prior, signed = false) {
  if (prior == null) return '未启用对比';
  const a=BigInt(current), b=BigInt(prior), diff=a-b;
  const change = `${diff>0n?'+':''}${money(diff)}`;
  if (signed && (a<0n || b<0n)) return `${change} · ${a>=0n&&b<0n?'转盈':a<0n&&b>=0n?'转亏':'金额变化'}`;
  return `${change} · ${b===0n?'无基数':`${diff>0n?'+':''}${Number(diff*1000n/b)/10}%`}`;
}
function draw(id, points, series, label, onSelect, format) {
  if (!state.hidden.has(id)) state.hidden.set(id,new Set());
  LedgerCharts.draw($('#'+id),{points,series,label,onSelect,format,hidden:state.hidden.get(id)});
}
const incomeSeries={key:'income',label:'本期收入',color:'green',direction:'收入'};
const expenseSeries={key:'expense',label:'本期支出',color:'sand',direction:'支出'};
const compareSeries=[{key:'oldIncome',label:'对比期收入',color:'green',compare:true,direction:'收入'},{key:'oldExpense',label:'对比期支出',color:'sand',compare:true,direction:'支出'}];
function alignedPoints(data) {
  let cumulativeInvestment=0n, oldCumulativeInvestment=0n;
  return data.trend.aligned.map((pair,i)=>{
    const a=pair.current,b=pair.compare;
    if(a?.investment)cumulativeInvestment+=BigInt(a.investment.net_cents);
    if(b?.investment)oldCumulativeInvestment+=BigInt(b.investment.net_cents);
    return {label:a?.label||b?.label||String(i+1),range:`本期 ${rangeText(a)}；对比期 ${rangeText(b)}`,current:a,compare:b,
      income:a?.income_cents??null,expense:a?.expense_cents??null,oldIncome:b?.income_cents??null,oldExpense:b?.expense_cents??null,
      income_count:a?.income_count,expense_count:a?.expense_count,oldIncome_count:b?.income_count,oldExpense_count:b?.expense_count,
      balance:a?.balance_cents??null,oldBalance:b?.balance_cents??null,cumulative:a?.cumulative_balance_cents??null,oldCumulative:b?.cumulative_balance_cents??null,
      gain:a?.investment?.gain_cents??null,loss:a?.investment?.loss_cents??null,net:a?.investment?.net_cents??null,
      cumulativeInvestment:a?.investment?String(cumulativeInvestment):null,
      oldNet:b?.investment?.net_cents??null,oldCumulativeInvestment:b?.investment?String(oldCumulativeInvestment):null};
  });
}
function selectPeriod(point, series, nature) {
  const range=series?.compare?point.compare:point.current;
  if(!range || range.is_future)return;
  if(nature && $('#nature').value && $('#nature').value!==nature)return;
  if(series?.direction && $('#direction').value && $('#direction').value!==series.direction)return;
  selectDetail({range,period:series?.compare?'对比期':'本期',label:`${series?.label||'交易'} · ${rangeText(range)}`,direction:series?.direction,nature});
}
function renderTrends(data) {
  const points=alignedPoints(data), comparing=data.comparison.available;
  draw('trend',points,[incomeSeries,expenseSeries,...(comparing?compareSeries:[])],'分期收支',selectPeriod);
  const cumulative=$('#balance-view').value==='cumulative';
  draw('balance-trend',points,[{key:cumulative?'cumulative':'balance',label:cumulative?'本期累计盈余':'本期盈余',color:'blue',line:true},...(comparing?[{key:cumulative?'oldCumulative':'oldBalance',label:'对比期盈余',color:'purple',line:true,compare:true}]:[])],'盈余走势',selectPeriod);
  draw('investment-trend',points,[{key:'gain',label:'已实现收益',color:'green',direction:'收入'},{key:'loss',label:'已实现亏损',color:'sand',direction:'支出'}],'已实现投资收益与亏损',(p,s)=>selectPeriod(p,s,'投资'));
  const investmentCumulative=$('#investment-view').value==='cumulative';
  draw('investment-net',points,[{key:investmentCumulative?'cumulativeInvestment':'net',label:investmentCumulative?'累计净损益':'分期净损益',line:true,color:'blue'},...(comparing?[{key:investmentCumulative?'oldCumulativeInvestment':'oldNet',label:'对比期净损益',line:true,color:'purple',compare:true}]:[])],'已实现投资净损益',(p,s)=>selectPeriod(p,s,'投资'));
}
function renderRanks(data) {
  const parent=$('#categories');parent.replaceChildren();
  const limit=$('#top-n').value==='all'?Infinity:Number($('#top-n').value);
  for(const key of ['income','expense']){
    const view=data.categories[key];add(parent,'h3',`${view.direction} · ${money(view.total_cents)}`);
    let items=view.items;
    if(state.group)items=items.filter(c=>c.group_code===state.group);
    if(!items.length){add(parent,'p','无匹配分类').className='section-note';continue;}
    const sorted=[...items].sort((a,b)=>BigInt(a.amount_cents)===BigInt(b.amount_cents)?a.category_code.localeCompare(b.category_code):BigInt(a.amount_cents)>BigInt(b.amount_cents)?-1:1);
    const max=BigInt(sorted[0].amount_cents)||1n;
    const showRow=(item)=>{
      const button=action(parent,'',()=>{
        if(item.is_other){$('#top-n').value='all';renderRanks(data);return;}
        if(!state.group){state.group=item.group_code;selectDetail({range:data.scope,period:'本期',group:item.group_code,direction:view.direction,label:`分类组 ${item.group_name}`});renderRanks(data);}
        else selectDetail({range:data.scope,period:'本期',group:item.group_code,category:item.category_code,direction:view.direction,label:`${item.group_name} / ${item.category_name}`});
      },'rank-row');
      const head=add(button,'strong');add(head,'span',item.category_name);add(head,'span',money(item.amount_cents));
      const share=BigInt(view.total_cents)?`${Number(BigInt(item.amount_cents)*1000n/BigInt(view.total_cents))/10}%`:'—';
      add(button,'small',`${share} · ${item.transaction_count} 笔 · 均笔 ${item.transaction_count?ratioMoney(item.amount_cents,item.transaction_count,item.average_cents):'—'}${!state.group&&!item.is_other?' · 查看分类组':''}`);
      const meter=add(button,'progress');meter.className='rank-meter';meter.max=10000;meter.value=Number(BigInt(item.amount_cents)*10000n/max);meter.setAttribute('aria-label',`${item.category_name} ${share}`);
    };
    sorted.slice(0,limit).forEach(showRow);
    const rest=sorted.slice(limit);
    if(rest.length)showRow({category_name:`其他 ${rest.length} 个分类 · 展开`,amount_cents:String(rest.reduce((n,c)=>n+BigInt(c.amount_cents),0n)),transaction_count:rest.reduce((n,c)=>n+c.transaction_count,0),is_other:true});
  }
}
function renderChanges(data) {
  const parent=$('#changes');parent.replaceChildren();
  if(!data.comparison.available){add(parent,'p','未启用可用对比，请选择对比范围。');return;}
  const items=data.category_changes.filter(c=>c.direction===$('#change-direction').value);
  if(!items.length){add(parent,'p','两期均无匹配分类。');return;}
  const points=items.map(c=>({label:c.category_name,range:`本期 ${money(c.current_cents)} / 对比期 ${money(c.compare_cents)}`,delta:c.delta_cents,item:c}));
  const graph=add(parent,'div');graph.id='change-chart';
  LedgerCharts.horizontal(graph,items,c=>showChange(c,'current'));
  for(const c of items){
    const row=add(parent,'div');row.className='change-row';add(row,'strong',`${c.category_name} · ${comparisonText(c.current_cents,c.compare_cents)}`);
    add(row,'p',`交易频次 ${c.current_transaction_count} / ${c.compare_transaction_count} 笔；均笔 ${ratioMoney(c.current_cents,c.current_transaction_count,c.current_average_cents)} / ${ratioMoney(c.compare_cents,c.compare_transaction_count,c.compare_average_cents)}`);
    action(row,`本期 ${money(c.current_cents)}`,()=>showChange(c,'current'));action(row,`对比期 ${money(c.compare_cents)}`,()=>showChange(c,'compare'));
  }
}
function showChange(c, period){
  const range=period==='compare'?state.data.comparison.compare:state.data.scope;
  selectDetail({range,period:period==='compare'?'对比期':'本期',category:c.category_code,group:c.group_code,direction:c.direction,label:`分类变化 / ${c.category_name}`});
}
function stat(parent,label,value,note){const card=add(parent,'div');card.className='daily-stat';add(card,'span',label);add(card,'strong',value);add(card,'small',note);}
function renderDaily(data){
  const nature=$('#nature-cards');nature.replaceChildren();
  for(const item of data.nature_breakdown){const b=action(nature,item.nature,()=>{$('#nature').value=item.nature;resetSelection();load();});add(b,'strong',`盈余 ${money(item.balance_cents)}`);add(b,'small',`收入 ${money(item.income_cents)} / 支出 ${money(item.expense_cents)}`);add(b,'small',`${item.transaction_count} 笔`);}
  const parent=$('#daily-stats');parent.replaceChildren();const daily=data.nature_breakdown.find(n=>n.nature==='日常'), ref=data.three_month_reference;
  stat(parent,'日常结余率',percent(daily?.balance_rate),'日常盈余 ÷ 日常收入');
  stat(parent,'本期日均支出',ratioMoney(data.daily_expense.expense_cents,data.daily_expense.effective_days,data.daily_expense.daily_expense_cents),`${data.daily_expense.effective_days} 个自然日 · 不随无记录日缩短`);
  if(ref){stat(parent,'近3个月参照 / 日',ratioMoney(ref.expense_cents,ref.natural_days,ref.daily_expense_cents),`${rangeText(ref)} · ${ref.natural_days} 天`);stat(parent,'近3个月月均',ratioMoney(ref.expense_cents,3,ref.monthly_average_expense_cents),`${ref.coverage?.message||'按已记录账单计算'} · 边界覆盖 ${ref.coverage?.covered_days??'—'} 天`);}
  const inv=$('#investment-stats');inv.replaceChildren();const investment=data.investment;
  stat(inv,'已实现收益',money(investment.gain_cents),`${investment.transaction_count} 笔投资交易`);stat(inv,'已实现亏损',money(investment.loss_cents),'亏损以支出记录');stat(inv,'净损益',money(investment.net_cents),'已实现收益 − 已实现亏损');
  // Percentage charts use scaled thousandths of a percentage point, with custom labels below.
  const dailyPoints=(data.overview_12_months?.months||[]).map(m=>{const n=m.nature?.find(x=>x.nature==='日常');return {label:m.month,range:n?.balance_rate==null?'日常结余率：无基数':`日常结余率 ${percent(n.balance_rate)}`,rate:n?.balance_rate==null?null:String(Math.round(Number(n.balance_rate)*10000)),current:m};});
  renderRateChart(dailyPoints);
}
function renderRateChart(points){
  draw('daily-trend',points,[{key:'rate',label:'日常结余率',line:true,color:'green'}],'近12个月日常结余率',p=>switchMonth(p.label),v=>v==null?'无基数':`${(Number(v)/100).toFixed(1)}%`);
}
function renderCalendar(data){
  const calendar=data.expense_calendar,parent=$('#calendar');parent.replaceChildren();$('#calendar-month').value=calendar.month;
  $('#calendar-note').textContent=`${calendar.month} · 所选范围内支出 ${money(calendar.total_expense_cents)} · 点击日期查看当日分类与明细`;
  for(const day of ['一','二','三','四','五','六','日'])add(parent,'span',day);
  for(let i=0;i<(calendar.days[0]?.weekday||0);i++)add(parent,'span');
  const max=calendar.days.reduce((m,d)=>d.expense_cents!=null&&BigInt(d.expense_cents)>m?BigInt(d.expense_cents):m,1n);
  for(const day of calendar.days){const caption=day.is_future?'未来':!day.in_scope?'范围外':day.empty_label||money(day.expense_cents);
    const b=action(parent,'',()=>{if(day.is_future||!day.in_scope)return;selectDetail({range:{start:day.date,end_exclusive:new Date(Date.parse(day.date)+86400000).toISOString().slice(0,10)},period:'本期',direction:'支出',label:`支出日历 / ${day.date}`,calendar:true});});
    b.disabled=day.is_future||!day.in_scope;b.className=day.is_future?'future':`heat-${day.expense_cents&&BigInt(day.expense_cents)>0n?Math.max(1,Number(BigInt(day.expense_cents)*4n/max)):0}`;
    b.setAttribute('aria-label',`${day.date} ${caption} ${day.transaction_count??0} 笔`);add(b,'span',String(Number(day.date.slice(-2))));add(b,'small',caption);
  }
}
function switchMonth(month){$('#kind').value='month';$('#value').value=month;$('#grain').value='day';syncScopeControls();resetSelection();load();}
function renderLong(data){const view=data.overview_12_months;$('#long-note').textContent=view?`${rangeText(view)} · 独立于本期总额；点击月份切换主范围`:'无可用月份';
  const points=(view?.months||[]).map(m=>({label:`${m.month}${m.is_current_month?'（未完）':''}`,range:rangeText({...m,end_exclusive:m.display_end_exclusive}),income:m.income_cents,expense:m.expense_cents,balance:m.balance_cents,month:m.month}));
  draw('long-trend',points,[{...incomeSeries,label:'收入'},{...expenseSeries,label:'支出'},{key:'balance',label:'盈余',line:true,color:'blue'}],'近12个月收支',p=>switchMonth(p.month));
}
function render(data,detailData){
  $('#income').textContent=money(data.totals.income_cents);$('#expense').textContent=money(data.totals.expense_cents);$('#balance').textContent=money(data.totals.balance_cents);$('#count').textContent=data.filtered_count;
  for(const [id,key] of [['income','income_cents'],['expense','expense_cents'],['balance','balance_cents']])$('#'+id).parentElement.querySelector('small').textContent=comparisonText(data.totals[key],data.summary.compare?.[key],id==='balance');
  $('#investment').textContent=money(data.investment.net_cents);$('#investment-meta').textContent=`已实现收益 ${money(data.investment.gain_cents)} · 已实现亏损 ${money(data.investment.loss_cents)}`;
  $('#meta').textContent=`本期 ${rangeText(data.scope)}（${days(data.scope)}天） · ${data.comparison.available?`对比期 ${rangeText(data.comparison.compare)}（${days(data.comparison.compare)}天）${data.comparison.days_difference?' · 两期天数不同':''}`:data.comparison.reason==='all_scope_requires_custom_dates'?'全部范围请自选对比日期':'对比已关闭'} · 上海时区 · 数据截止 ${data.data_cutoff||'—'}`;
  $('#drill-path').textContent=state.detail?`图表选择：${state.detail.label} · 仅联动分类上下文与明细`:'图表选择：全部';$('#clear-drill').hidden=!state.detail;
  tags(data.tags);renderTrends(data);renderRanks(data);renderChanges(data);renderDaily(data);renderCalendar(data);renderLong(data);renderDetail(detailData,false);
}
function renderDetail(data,append){
  renderRows(data?.transactions||[],append);state.cursor=data?.next_cursor||null;state.renderedRows=append?state.renderedRows+(data?.transactions.length||0):(data?.transactions.length||0);
  $('#more').disabled=!state.cursor;$('#page-meta').textContent=`已显示 ${state.renderedRows} / ${data?.filtered_count||0} 笔`;
  const d=state.detail;$('#detail-context').textContent=`${d?.period||'本期'} · ${d?.label||'全部交易'} · ${rangeText(d?.range||state.data.scope)}${d?.nature?` · 性质 ${d.nature}`:''}`;
  $('#detail-back').hidden=!state.history.length;$('#detail-clear').hidden=!d;
  if(d?.calendar&&data){const parent=$('#categories');parent.replaceChildren();add(parent,'h3',`${d.range.start} · 当日分类`);for(const c of data.categories){action(parent,`${c.name} · ${money(c.cents)} · ${c.count} 笔`,()=>selectDetail({...d,category:c.code,group:c.group_code,label:`${d.range.start} / ${c.name}`,calendar:false}),'rank-row');}}
}
function resetSelection(){state.detail=null;state.history=[];state.group=null;state.cursor=null;}
function selectDetail(detail){state.history.push(state.detail);state.detail=detail;load({detailOnly:true});$('#transactions').scrollIntoView({behavior:'smooth',block:'start'});}
async function load({append=false,detailOnly=false,refresh=false,retry=0}={}){
  const mine=++state.serial;state.controller?.abort();state.controller=new AbortController();const signal=state.controller.signal;const started=performance.now();
  $('#status').textContent=append?'正在加载更多…':'正在读取…';$('#more').disabled=true;document.querySelector('main').setAttribute('aria-busy','true');
  try{
    const data=detailOnly&&state.data?state.data:await get(`/api/analytics?${query()}`,signal);
    const detail=state.detail;
    const conflict=['nature','direction'].some(key=>detail?.[key]&&$('#'+key).value&&detail[key]!==$('#'+key).value);
    const detailData=conflict?{version:data.version,transactions:[],filtered_count:0,categories:[],next_cursor:null}:data.scope.start&&data.scope.end_exclusive?await get(`/api/dashboard?${detailQuery(data,detail,append?state.cursor:null)}`,signal):null;
    if(mine!==state.serial)return;
    if(detailData&&detailData.version!==data.version){const error=new Error('数据版本变化');error.status=409;throw error;}
    document.querySelector('main').classList.remove('data-stale');state.data=data;state.version=data.version;state.lastSync=Date.now();
    if(detailOnly) {renderRanks(data);renderDetail(detailData,append);$('#drill-path').textContent=detail?`图表选择：${detail.label} · 仅联动分类上下文与明细`:'图表选择：全部';$('#clear-drill').hidden=!detail;}
    else render(data,detailData);
    if(refresh)state.refreshCount++;
    $('#perf').textContent=`最近读取并渲染 ${(performance.now()-started).toFixed(1)}ms · 自动刷新 ${state.refreshCount} 次 · 版本 ${data.version}`;
    $('#status').textContent=`已同步 · ${new Date(state.lastSync).toLocaleTimeString('zh-CN',{hour12:false})}`;
  }catch(error){if(error.name==='AbortError'||mine!==state.serial)return;if(error.status===409&&retry<2){await load({refresh,retry:retry+1});return;}$('#status').textContent=error.message;$('#more').disabled=true;document.querySelector('main').classList.add('data-stale');$('#meta').textContent='读取失败：以下为上次成功结果，请检查筛选后点击刷新账本。';}
  finally{if(mine===state.serial)document.querySelector('main').setAttribute('aria-busy','false');}
}
$('#value').value=nowInShanghai().slice(0,7);syncScopeControls();
$('#kind').addEventListener('change',()=>{syncScopeControls();$('#grain').value=['month','day'].includes($('#kind').value)?'day':'month';resetSelection();load();});
for(const id of ['value','start','end','mode','direction','nature','period-mode','grain','compare-start','compare-end'])$('#'+id).addEventListener('change',()=>{resetSelection();load();});
$('#compare').addEventListener('change',()=>{const custom=$('#compare').value==='custom';for(const id of ['compare-start','compare-end'])$('#'+id).parentElement.hidden=!custom;if(custom){$('#compare-start').value||=state.data?.comparison.compare?.start||nowInShanghai();$('#compare-end').value||=previousDate(state.data?.comparison.compare?.end_exclusive)||nowInShanghai();}resetSelection();load();});
$('#top-n').addEventListener('change',()=>state.data&&renderRanks(state.data));$('#change-direction').addEventListener('change',()=>state.data&&renderChanges(state.data));
for(const id of ['balance-view','investment-view'])$('#'+id).addEventListener('change',()=>state.data&&renderTrends(state.data));
$('#calendar-month').addEventListener('change',()=>switchMonth($('#calendar-month').value));
for(const id of ['clear-drill','detail-clear'])$('#'+id).addEventListener('click',()=>{resetSelection();load({detailOnly:true});});
$('#detail-back').addEventListener('click',()=>{state.detail=state.history.pop()||null;state.group=state.detail?.group||null;load({detailOnly:true});});
$('#more').addEventListener('click',()=>{if(state.cursor)load({append:true,detailOnly:true});});
$('#reload').addEventListener('click',()=>load());
$('#reset').addEventListener('click',()=>{$('#kind').value='month';$('#value').value=nowInShanghai().slice(0,7);$('#direction').value='';$('#nature').value='';$('#mode').value='any';$('#compare').value='previous';$('#period-mode').value='elapsed';$('#grain').value='day';for(const id of ['compare-start','compare-end'])$('#'+id).parentElement.hidden=true;state.selectedTags.clear();resetSelection();syncScopeControls();load();});
async function poll(){if(document.hidden||state.polling||document.querySelector('main').getAttribute('aria-busy')==='true')return;state.polling=true;try{const probe=await get('/api/version');if(probe.version!==state.version)await load({refresh:true});}catch(e){$('#status').textContent=e.message;}finally{state.polling=false;}}
setInterval(poll,2000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});
load();
