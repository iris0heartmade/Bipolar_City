import {drawThumbnail} from './art.js';

export const $=id=>document.getElementById(id);
export const money=value=>new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}).format(value/100);
const count=value=>new Intl.NumberFormat('zh-CN').format(value);

const statuses={constructing:'施工中，下次月结完工',completed:'已建成'};

export class GameUI {
  constructor(onTool){
    this.onTool=onTool;
    this.category='全部';
    this.tool=null;
    this.selectedId=null;
    this.catalog=[];
    this.toastTimer=null;
  }
  notify(message,error=false){
    const toast=$('toast');
    toast.textContent=message;
    toast.classList.toggle('error',error);
    toast.hidden=false;
    clearTimeout(this.toastTimer);
    this.toastTimer=setTimeout(()=>toast.hidden=true,error?6000:3000);
  }
  showCatalog(){
    const container=$('building-catalog');
    container.replaceChildren();
    const list=this.catalog.filter(d=>d.buildable!==false);
    for(const def of list){
      const button=document.createElement('button');
      button.className='building-card';
      button.dataset.building=def.id;
      const industry=def.industry;
      const industryLabel=industry?` → ${industry} +${def.jobs_added||0} 岗位与产能`:def.housing_added?` → +${def.housing_added} 人住房`:'（公共设施）';
      button.setAttribute('aria-label',`建造${def.name}`);
      button.setAttribute('aria-pressed',String(this.tool===def.id));
      button.classList.toggle('selected',this.tool===def.id);
      const canvas=document.createElement('canvas');
      canvas.setAttribute('aria-hidden','true');
      const info=document.createElement('div');
      const name=document.createElement('strong');
      const meta=document.createElement('div');
      const cost=document.createElement('div');
      const duration=document.createElement('small');
      name.textContent=def.name;
      meta.className='card-info';
      meta.textContent=`${def.width}×${def.height} 格${industryLabel}`;
      cost.className='card-cost';
      cost.textContent=`◈ ${money(def.cost)}`;
      duration.textContent=`下一月完工 · 维护 ${money(def.maintenance)}/月`;
      cost.append(duration);
      info.append(name,meta,cost);
      button.append(canvas,info);
      button.addEventListener('click',()=>this.onTool(def.id));
      container.append(button);
      drawThumbnail(canvas,def);
    }
  }
  setTool(id){this.tool=id;this.showCatalog();$('inspect-btn').classList.toggle('active',!id);}
  update(state){
    const first=!this.catalog.length;
    this.state=state;
    this.catalog=state.catalog||[];
    if(first)this.showCatalog();
    const industries=state.industries||[];
    const totalEmployment=industries.reduce((s,d)=>s+(d.employment||0),0);
    const totalCapacity=industries.reduce((s,d)=>s+(d.capacity||0),0);
    const ind=industries.find(d=>d.id==='IND')||{};
    // 顶 4 个指标（重新定义为新内核派生值）：
    // - 政府现金（来自 industry_economy.cash['GOV']）
    // - 当前月份（来自 industry_economy.month）
    // - 总就业人数（7 行业 employment 求和）
    // - IND 月营业额（来自 last_report['IND'].revenue_cents）
    $('cash').textContent=money(state.government_cash_cents||0);
    $('population').textContent=`已结算 ${state.month||0} 月`;
    $('capacity').textContent=count(totalEmployment);
    $('jobs').textContent=money(state.last_report?.sectors?.IND?.sales_cents||0);
    $('cash-change').textContent=`建设从此付款 · 维护欠款 ${money(state.maintenance_due_cents||0)}`;
    $('vacancy').textContent=`行业岗位上限 ${count(totalCapacity)}`;
    $('merchant-metric').textContent=`IND 就业 ${count(ind.employment||0)}/${count(ind.capacity||0)}`;
    const yearLabel=`第 ${Math.floor((state.month||0)/12)+1} 年 · ${((state.month||0)%12)+1} 月`;
    $('date').textContent=yearLabel;
    $('day').textContent=`按月经营 · 建设次月完工`;
    $('map-subtitle').textContent=`北方河岸 / ${yearLabel}`;
    $('plan-bar').hidden=!this.tool;
    $('plan-count').textContent=this.tool?'点击合法空地立即投资建设':'先在左侧选择建筑类型';
    $('plan-cost').textContent='实际付款给工业 · 下一月完工';
    $('housing-ratio').textContent=`居民 ${count(state.housing?.residents||0)} / 住房 ${count(state.housing?.capacity||0)}`;
    $('housing-percent').textContent=`施工 ${state.summary?.construction||0} 座`;
    $('housing-progress').style.width=`${Math.min(100,(state.housing?.residents||0)/(state.housing?.capacity||1)*100)}%`;
    $('city-advice').textContent='就业受工资资金、原料与需求共同限制。新增岗位不保证立即招满；住宅只增加住房。';
    const queue=$('queue-list');queue.replaceChildren();
    for(const b of state.world?.buildings||[]){if(b.status!=='constructing')continue;const row=document.createElement('p');row.textContent=`${this.catalog.find(d=>d.id===b.definition_id)?.name||b.definition_id}：下次月结完工`;queue.append(row);}
    $('load-btn').disabled=!state.save_exists;
    // 新经济面板用旧面板的元素复用（不再展示 v1 ledger / queue / events）
    this.showIndustries();
    this.showActivePolicies();
    this.showMayorActions();
    const ledger=$('ledger-list2');ledger.replaceChildren();
    const names={building_investment:'建设投资',building_maintenance:'建筑维护',consumer_purchase:'居民消费',wage:'工资',tax:'税款'};
    for(const tx of (state.last_report?.transactions||[]).slice(-30)){
      const row=document.createElement('p');row.textContent=`${names[tx.kind]||tx.kind}：${tx.from} → ${tx.to} · ${money(tx.amount_cents||0)} CR`;ledger.append(row);
    }
    if(this.selectedId)this.showSelection();
  }
  showIndustries(){
    // 在右侧「城市概况」面板里嵌入行业表（替换原 v1 housing 字段）。
    const container=$('industry-list');
    if(!container)return;
    container.replaceChildren();
    const industries=this.state.industries||[];
    for(const d of industries){
      const row=document.createElement('div');
      row.className='industry-row';
      const id=document.createElement('strong');
      id.textContent=d.id;
      const emp=document.createElement('span');
      const ratio=d.capacity?(d.employment/d.capacity*100).toFixed(0):'0';
      emp.textContent=`${d.employment||0}/${d.capacity||0} (${ratio}%)`;
      const cash=document.createElement('small');
      cash.textContent=money(d.cash_cents||0);
      row.append(id,emp,cash);
      container.append(row);
    }
  }
  showActivePolicies(){
    const container=$('policy-list');
    if(!container)return;
    container.replaceChildren();
    const policies=this.state.active_policies||{};
    const keys=Object.keys(policies);
    if(!keys.length){
      const p=document.createElement('p');
      p.className='empty-note';
      p.textContent='暂无生效政策。';
      container.append(p);
      return;
    }
    for(const key of keys){
      const [target,kind]=key.split('|');
      const row=document.createElement('div');
      row.className='policy-row';
      row.textContent=`${target} · ${kind} → 过期月份 ${policies[key]}`;
      container.append(row);
    }
  }
  showMayorActions(){
    const container=$('mayor-actions');
    if(!container)return;
    container.replaceChildren();
    const actions=(this.state.recent_mayor_actions||[]).slice(-5);
    if(!actions.length){
      const p=document.createElement('p');
      p.className='empty-note';
      p.textContent='本月尚无市政指令。在「行业经营」页面下达指令后会在此显示。';
      container.append(p);
      return;
    }
    for(const a of actions){
      const row=document.createElement('div');
      row.className='mayor-row';
      row.textContent=`${a.kind}${a.industry?' / '+a.industry:''}: ${money(a.cash_cents||0)}`;
      container.append(row);
    }
  }
  showSelection(){
    if(!this.selectedId){
      $('selection-section').hidden=true;
      return;
    }
    const buildings=this.state.world?.buildings||[];
    const b=buildings.find(x=>x.id===this.selectedId);
    if(!b){
      $('selection-section').hidden=true;
      return;
    }
    $('selection-section').hidden=false;
    const def=this.catalog.find(d=>d.id===b.definition_id);
    $('selected-name').textContent=def?.name||b.definition_id;
    $('selected-description').textContent=def?.description||'';
    $('selected-stats').replaceChildren();
    const rows=[
      ['定义',b.definition_id],
      ['位置',`(${b.x}, ${b.y})`],
      ['状态',statuses[b.status]||b.status],
      ['建筑行业',def?.industry||'—'],
      ['单座岗位扩展',b.player_built?(def?.jobs_added||0):'已包含于开局行业底座'],
      ['新增住房',b.player_built?(def?.housing_added||0):'已包含于全城住房底座'],
      ['每月维护',b.player_built?`${money(def?.maintenance||0)} CR`:'开局底座已计入行业运营'],
      ['维护欠款',`${money(b.maintenance_due_cents||0)} CR`],
    ];
    for(const [name,value] of rows){
      const dt=document.createElement('dt');
      const dd=document.createElement('dd');
      dt.textContent=name;
      dd.textContent=String(value);
      $('selected-stats').append(dt,dd);
    }
  }
  connected(ok){
    $('connection-status').textContent=ok?'本地模拟已连接':'连接中断 · 请检查后端';
    const dot=document.querySelector('.connection-dot');
    if(dot)dot.style.background=ok?'#7b9471':'#b77865';
  }
}
