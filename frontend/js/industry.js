/* 城市经营交互：
   左侧：调整计划 + 市政指令；
   右侧：实时指标 + 行业表 + 人员流动 + 折线图。
   折线图：每次"月进"在当前指标上追加一个点，与前一个点连成折线。
*/
const $=id=>document.getElementById(id);
const number=v=>new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}).format(v||0);

let snapshot=null;
let busy=false;
let chartSeries=[];      // [{month, value}]
let chartMetric='employment';
const records=[];
let chartStartMonth=0;

const METRICS=[
  {key:'employment',      label:'就业总人数',       getter:d=>sumEmployment(d), unit:'人'},
  {key:'unemployment',    label:'失业人数',         getter:d=>d.latest?.unemployed ?? 0, unit:'人'},
  {key:'workforce',       label:'劳动力',           getter:d=>d.latest?.labor_force ?? d.labor_force ?? 0, unit:'人'},
  {key:'household',       label:'消费等价人数',     getter:d=>d.latest?.consumption_equivalent_population ?? d.latest?.household_population ?? 0, unit:'等价人'},
  {key:'gov',             label:'政府现金',         getter:d=>d.cash.GOV/100, unit:'CR'},
  {key:'hh',              label:'居民总储蓄',       getter:d=>sumHouseholds(d)/100, unit:'CR'},
  {key:'ind',             label:'行业总经营现金',   getter:d=>d.latest?.industry_cash_cents/100 ?? 0, unit:'CR'},
  {key:'ext',             label:'外部现金',         getter:d=>d.cash.EXT/100, unit:'CR'},
  {key:'ext_raw',         label:'外部原料',         getter:d=>d.cash.EXT/100>=0 ? (d.latest?.external_raw_units ?? d.latest?.raw_reserve_units ?? 0) : 0, unit:'单位'},
  {key:'sales',           label:'当月营业额',       getter:d=>d.latest?.sales_cents/100 ?? 0, unit:'CR'},
  {key:'consumption',     label:'当月实际消费',     getter:d=>d.latest?.consumer_spending_cents/100 ?? 0, unit:'CR'},
  {key:'wages',           label:'当月实付工资',     getter:d=>(d.latest?.paid_wages_cents ?? d.latest?.wages_cents ?? 0)/100, unit:'CR'},
  {key:'arrears',         label:'累计未偿工资',     getter:d=>(d.latest?.wage_arrears_cents ?? 0)/100, unit:'CR'},
  {key:'fiscal',          label:'财政当月变动',     getter:d=>d.latest?.fiscal_balance_cents/100 ?? 0, unit:'CR'},
  {key:'cpi',             label:'物价指数',         getter:d=>d.latest?.cpi ?? 1, unit:'倍'},
  {key:'cbr',             label:'年出生率',         getter:d=>d.latest?.demography?.cbr_per_thousand ?? 0, unit:'‰/年'},
  {key:'cdr',             label:'年死亡率',         getter:d=>d.latest?.demography?.cdr_per_thousand ?? 0, unit:'‰/年'},
  {key:'net',             label:'人口月净增率',     getter:d=>(d.latest?.demography?.cbr_monthly_per_thousand ?? 0)-(d.latest?.demography?.cdr_monthly_per_thousand ?? 0), unit:'‰/月'},
  {key:'IND_emp',         label:'工业就业',         getter:d=>d.employment.IND ?? 0, unit:'人'},
  {key:'IND_cash',        label:'工业经营现金',     getter:d=>d.cash.IND/100, unit:'CR'},
  {key:'IND_sales',       label:'工业营业额',       getter:d=>d.latest?.sectors?.IND?.sales_cents/100 ?? 0, unit:'CR'},
  {key:'IND_profit',      label:'工业税前利润',     getter:d=>d.latest?.sectors?.IND?.profit_cents/100 ?? 0, unit:'CR'},
];

function sumEmployment(d){return Object.values(d.employment||{}).reduce((a,b)=>a+b,0)}
function sumHouseholds(d){return Object.entries(d.cash||{}).filter(([k])=>k.startsWith('H')).reduce((n,[,v])=>n+v,0)}

function notice(text,error=false){$('status').textContent=text;$('status').classList.toggle('error',error)}

function render(data){
  snapshot=data;
  const r=data.latest;
  $('month').textContent=data.month;
  $('employment').textContent=`${number(sumEmployment(data))} / ${number(r?.labor_force ?? data.labor_force)} / ${number(r?.population ?? data.population)}`;
  $('household-population').textContent=r?number(r.household_population ?? 0):'—';
  $('government').textContent=number(data.cash.GOV/100);
  $('households').textContent=number(sumHouseholds(data)/100);
  $('consumption').textContent=r?number(r.consumer_spending_cents/100):'—';
  $('sales').textContent=r?number(r.sales_cents/100):'—';
  $('external-cash').textContent=number(data.cash.EXT/100);
  $('external-raw').textContent=r?number(r.external_raw_units ?? r.raw_reserve_units ?? 0):'—';
  $('net-rate').textContent=r&&r.demography?number((r.demography.cbr_monthly_per_thousand||0)-(r.demography.cdr_monthly_per_thousand||0)):'—';
  $('status-line').textContent=`行业经营版 · 玩家是市长，所有改变按月结算 · ${data.month} 月份`;

  const tbody=$('sectors');
  if(tbody){
    tbody.replaceChildren();
    for(const [i,sector] of Object.entries(data.scenario.sectors)){
      const s=r?.sectors?.[i];
      const tr=document.createElement('tr');
      const values=[
        sector.name,
        s?number(s.employed):'—',
        s?number(s.capacity):'—',
        s?number(s.target):'—',
        s?number(s.attractiveness):'—',
        s?number(s.wage_cents/100):'—',
        s?number(s.sales_cents/100):'—',
        s?.profit_cents===undefined?'—':number(s.profit_cents/100),
        number(data.cash[i]/100),
        s?number(s.stock_units):'—',
        s?number(s.raw_units):'—',
        s?number(s.price_cents/100):'—',
        s?number(s.loss_streak_months||0):'—',
      ];
      for(const v of values){const td=document.createElement('td');td.textContent=v;tr.appendChild(td)}
      tbody.appendChild(tr)
    }
  }
  $('balance').textContent=r?`现金+OCEAN 守恒差：${r.cash_residual_cents} 分`:'尚未结算';
  if(r){
    const changes=r.switches.map(x=>`${data.scenario.sectors[x.from]?.name} → ${data.scenario.sectors[x.to]?.name} ${x.people}人`);
    const hires=Object.values(r.hires||{}).reduce((a,b)=>a+b,0),exits=Object.values(r.layoffs||{}).reduce((a,b)=>a+b,0);
    $('flows').textContent=`从待业池录用 ${hires} 人；缩编 ${exits} 人。${changes.length?changes.join('；'):'本月没有在职转行。'}`;
    const loss=(r.sectors?Object.values(r.sectors).filter(s=>(s.profit_cents||0)<0).map(s=>s.name):[]);
    const paid=r.paid_wages_cents ?? r.transactions.filter(t=>t.kind==='wage').reduce((n,t)=>n+t.amount_cents,0);
    $('observations').textContent=`本月应付工资 ${number(r.wages_cents/100)} CR，实付 ${number(paid/100)} CR，累计未偿 ${number(r.wage_arrears_cents/100)} CR；营业额 ${number(r.sales_cents/100)} CR。${loss.length?`税前亏损：${loss.join('、')}。`:'非公共行业经营结果见上表。'}`;
    const crisis=(r.sectors?Object.entries(r.sectors).filter(([,s])=>s.crisis).map(([i])=>data.scenario.sectors[i]?.name):[]);
    $('active-policies').textContent=(()=>{
      const pol=Object.entries(data.active_policies||{});
      const ap=pol.length?`市政限时政策：${pol.map(([k,v])=>`${k}(剩${v-data.month}月)`).join('；')}。`:'';
      const rec=data.recent_mayor_actions?.length?data.recent_mayor_actions:(r.recent_mayor_actions||[]);
      const recText=rec.length?`本月市政动作：${rec.map(a=>a.kind+(a.industry?`/${a.industry}`:'')+(a.cash_cents?` ${a.cash_cents/100}CR`:a.people?` ${a.people}人`:a.months?` ${a.months}月`:'')).join('；')}。`:'';
      return [crisis.length?`⚠ 危机标记：${crisis.join('、')}`:'',ap,recText].filter(Boolean).join(' / ')||'无'
    })()
  }
  fillSelects(data);
  syncControlValues(data);
  rebuildHistory(data);
  drawChart();
}

function fillSelects(data){
  const ind=$('industry');
  if(!ind.options.length){
    for(const [i,s] of Object.entries(data.scenario.sectors)){
      ind.append(new Option(s.name,i));
      $('subsidy-industry').append(new Option(s.name,i));
      $('limit-hire-industry').append(new Option(s.name,i));
      $('limit-layoff-industry').append(new Option(s.name,i));
      $('tax-amnesty-industry').append(new Option(s.name,i));
    }
    ind.value='IND'
  }
  const met=$('chart-metric');
  if(!met.options.length){
    for(const m of METRICS){met.append(new Option(`${m.label} (${m.unit})`,m.key))}
    met.value=chartMetric;
    met.onchange=()=>{chartMetric=met.value;if(snapshot)rebuildHistory(snapshot);drawChart()}
  }
}

function syncControlValues(data){
  const i=($('industry')?.value)||'IND';
  const sc=data.scenario.sectors[i];
  $('wage').value=sc.wage_multiplier;
  $('tax').value=data.scenario.city.profit_tax_multiplier;
  $('living').value=data.scenario.city.living_cost_cr;
  $('household').value=data.scenario.city.household_consumption_size;
  $('external-split').value=data.scenario.city.external_local_split;
  $('load').disabled=!data.save_exists;
  $('advance-months').value=1;
}

async function read(response){const data=await response.json();if(!response.ok){const msg=typeof data.detail==='string'?data.detail:JSON.stringify(data.detail);throw new Error(msg)}return data}
async function command(type,fields={}){
  if(busy)return;busy=true;document.querySelectorAll('button,input,select').forEach(x=>x.disabled=true);
  try{
    const data=await read(await fetch('/api/industry/commands',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type,request_id:crypto.randomUUID(),...fields})}));
    if(type==='advance'){onAdvance(data)}
    else if(type==='new_game'||type==='load'){chartStartMonth=0;chartSeries=[];records.length=0}
    render(data);
    if(type==='save')notice('城市已保存');
    else if(type==='load')notice('已恢复保存状态');
    else if(type==='set_parameter')notice('计划已应用，将影响下一次真实结算');
    else if(type==='purchase_external'||type==='mayor_command')notice('市政指令已生效');
    else if(type==='new_game')notice('已新建基准城市');
    else if(type==='advance')notice(`已推进到第 ${data.month} 月`);
    else notice(`第${data.month}月状态已更新`);
    $('history').replaceChildren(...records.slice(0,12).map(text=>{const li=document.createElement('li');li.textContent=text;return li}));
  }catch(error){notice(error.message,true)}
  finally{busy=false;restoreFormState()}
}

function restoreFormState(){
  document.querySelectorAll('button,input,select').forEach(x=>{
    if(x.id==='load'){x.disabled=!snapshot?.save_exists}
    else{x.disabled=false}
  })
}

function onAdvance(data){
  rebuildHistory(data);
}

function rebuildHistory(data){
  const history=data.history||[];
  const metric=METRICS.find(x=>x.key===chartMetric);
  chartSeries=history.filter(r=>r.month>chartStartMonth).map(r=>{
    const cash={GOV:r.government_cash_cents,EXT:r.external_cash_cents,H_TOTAL:r.household_cash_cents};
    for(const [i,s] of Object.entries(r.sectors||{}))cash[i]=s.cash_cents;
    return {month:r.month,value:metric?.getter({latest:r,cash,employment:r.employment})||0};
  });
  records.splice(0,records.length,...history.slice(-12).reverse().map(r=>`第${r.month}月：就业 ${Object.values(r.employment).reduce((a,b)=>a+b,0)}人；政府现金 ${number(r.government_cash_cents/100)} CR；营业额 ${number(r.sales_cents/100)} CR。`));
  if(records.length)$('history').replaceChildren(...records.map(text=>{const li=document.createElement('li');li.textContent=text;return li}));
}

function drawChart(){
  const c=$('chart');
  if(!c)return;
  const ctx=c.getContext('2d');
  const W=c.width,H=c.height;
  ctx.clearRect(0,0,W,H);
  ctx.fillStyle='#fafdfd';ctx.fillRect(0,0,W,H);
  // 网格
  ctx.strokeStyle='#e6edef';ctx.lineWidth=1;
  for(let i=0;i<=4;i++){const y=20+i*(H-50)/4;ctx.beginPath();ctx.moveTo(60,y);ctx.lineTo(W-20,y);ctx.stroke()}
  if(!chartSeries.length){
    ctx.fillStyle='#94a3a8';ctx.font='14px Microsoft YaHei';
    ctx.textAlign='center';ctx.fillText('尚无数据 — 点"月进"开始',W/2,H/2);
    return
  }
  const xs=chartSeries.map(p=>p.month);
  const ys=chartSeries.map(p=>p.value);
  const ymin=Math.min(0,...ys),ymax=Math.max(1,...ys);
  const yr=(ymax-ymin)||1;
  const xmin=Math.min(...xs),xmax=Math.max(...xs);
  const xr=(xmax-xmin)||1;
  const xAt=m=>60+(m-xmin)/xr*(W-80);
  const yAt=v=>20+(H-30)-((v-ymin)/yr)*(H-50);
  // 轴标签
  ctx.fillStyle='#657e88';ctx.font='11px Microsoft YaHei';ctx.textAlign='right';
  for(let i=0;i<=4;i++){const y=20+i*(H-50)/4;const v=ymax-i*yr/4;ctx.fillText(formatNumber(v),55,y+3)}
  ctx.textAlign='center';
  for(let i=0;i<=Math.min(6,chartSeries.length-1);i++){const idx=Math.round(i/6*(chartSeries.length-1));const p=chartSeries[idx];ctx.fillText(`${p.month}`,xAt(p.month),H-10)}
  // 折线
  ctx.strokeStyle='#167d70';ctx.lineWidth=2;ctx.beginPath();
  chartSeries.forEach((p,i)=>{const x=xAt(p.month),y=yAt(p.value);i?ctx.lineTo(x,y):ctx.moveTo(x,y)});
  ctx.stroke();
  // 数据点
  ctx.fillStyle='#167d70';
  for(const p of chartSeries){const x=xAt(p.month),y=yAt(p.value);ctx.beginPath();ctx.arc(x,y,3,0,Math.PI*2);ctx.fill()}
  // 当前指标说明
  const m=METRICS.find(x=>x.key===chartMetric);
  $('chart-summary').textContent=`当前指标：${m?.label}（${m?.unit}）。共 ${chartSeries.length} 个点，最后值 ${formatNumber(chartSeries.at(-1).value)}。`
}

function formatNumber(v){if(Math.abs(v)>=10000)return v.toFixed(0);if(Math.abs(v)>=10)return v.toFixed(1);return v.toFixed(3)}

// 事件绑定
$('advance-form').onsubmit=e=>{e.preventDefault();const m=Math.max(1,Math.min(120,parseInt($('advance-months').value||'1',10)));command('advance',{months:m})};
$('policy-form').onsubmit=e=>{e.preventDefault();command('set_parameter',{section:'sector',key:'wage_multiplier',industry:$('industry').value,value:Number($('wage').value)})};
$('tax-form').onsubmit=e=>{e.preventDefault();command('set_parameter',{section:'city',key:'profit_tax_multiplier',value:Number($('tax').value)})};
$('living-form').onsubmit=e=>{e.preventDefault();command('set_parameter',{section:'city',key:'living_cost_cr',value:Number($('living').value)})};
$('household-form').onsubmit=e=>{e.preventDefault();command('set_parameter',{section:'city',key:'household_consumption_size',value:Number($('household').value)})};
$('external-split-form').onsubmit=e=>{e.preventDefault();command('set_parameter',{section:'city',key:'external_local_split',value:Number($('external-split').value)})};
$('purchase-form').onsubmit=e=>{e.preventDefault();const c=Number($('purchase-cash').value)||0;const r=Number($('purchase-raw').value)||0;if(c<=0&&r<=0){notice('请输入采购金额或原料数量',true);return}command('purchase_external',{cash_cr:c,raw_units:r,note:'GUI 市政采购'})};
$('subsidy-form').onsubmit=e=>{e.preventDefault();const ind=$('subsidy-industry').value;const amt=Number($('subsidy-amount').value)||0;if(amt<=0){notice('请输入补贴金额',true);return}command('mayor_command',{mayor_kind:'subsidy_industry',industry:ind,cash_cr:amt,note:'GUI 行业补贴'})};
$('hire-civil-form').onsubmit=e=>{e.preventDefault();const n=Number($('hire-civil').value)||0;if(n<=0){notice('请输入雇佣人数',true);return}command('mayor_command',{mayor_kind:'hire_civil',people:n,note:'GUI 雇佣'})};
$('public-works-form').onsubmit=e=>{e.preventDefault();const amt=Number($('public-works').value)||0;if(amt<=0){notice('请输入救济金额',true);return}command('mayor_command',{mayor_kind:'pardon_welfare',cash_cr:amt,note:'GUI 救济'})};
$('limit-hire-form').onsubmit=e=>{e.preventDefault();command('mayor_command',{mayor_kind:'limit_hire',industry:$('limit-hire-industry').value,policy_months:Number($('limit-hire-months').value)||3,note:'GUI 限招'})};
$('limit-layoff-form').onsubmit=e=>{e.preventDefault();command('mayor_command',{mayor_kind:'limit_layoff',industry:$('limit-layoff-industry').value,policy_months:Number($('limit-layoff-months').value)||3,note:'GUI 限裁'})};
$('tax-amnesty-form').onsubmit=e=>{e.preventDefault();command('mayor_command',{mayor_kind:'tax_amnesty',industry:$('tax-amnesty-industry').value,note:'GUI 税豁免'})};
$('save').onclick=()=>command('save');
$('load').onclick=()=>command('load');
$('new').onclick=()=>{if(confirm('新建会替换当前经营状态，确认吗？'))command('new_game')};
$('chart-reset').onclick=()=>{chartStartMonth=snapshot?.month||0;chartSeries=[];drawChart()};
$('industry').onchange=()=>{if(snapshot){$('wage').value=snapshot.scenario.sectors[$('industry').value].wage_multiplier}};

try{
  render(await read(await fetch('/api/industry/state',{cache:'no-store'})));
  notice('城市已就绪。先调整计划或下达指令，再点"月进"看当月结果。')
}catch(error){notice(`读取城市失败：${error.message}`,true)}
