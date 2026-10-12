import {drawThumbnail} from './art.js';

export const $=id=>document.getElementById(id);
export const money=value=>new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}).format(value/100);
const count=value=>new Intl.NumberFormat('zh-CN').format(value);
const statuses={blueprint:'规划蓝图',queued:'待施工',constructing:'施工中',completed:'已竣工'};

export class GameUI {
  constructor(onTool){this.onTool=onTool;this.category='全部';this.tool=null;this.selectedId=null;this.catalog=[];this.toastTimer=null;}
  notify(message,error=false){const toast=$('toast');toast.textContent=message;toast.classList.toggle('error',error);toast.hidden=false;clearTimeout(this.toastTimer);this.toastTimer=setTimeout(()=>toast.hidden=true,error?6000:3000);}
  showCatalog(){
    const container=$('building-catalog');container.replaceChildren();
    const list=this.catalog.filter(d=>d.buildable&&(this.category==='全部'||d.category===this.category||(this.category==='产业'&&d.category==='商业')));
    for(const def of list){
      const button=document.createElement('button');button.className='building-card';button.dataset.building=def.id;
      button.setAttribute('aria-label',`建造${def.name}`);button.setAttribute('aria-pressed',String(this.tool===def.id));button.classList.toggle('selected',this.tool===def.id);
      const canvas=document.createElement('canvas');canvas.setAttribute('aria-hidden','true');
      const info=document.createElement('div'),name=document.createElement('strong'),meta=document.createElement('div'),cost=document.createElement('div'),duration=document.createElement('small');
      name.textContent=def.name;meta.className='card-info';meta.textContent=`${def.width}×${def.height} 格 · ${def.capacity?`${def.capacity} 人容量`:def.jobs?`${def.jobs} 个岗位`:'公共绿地'}`;
      cost.className='card-cost';cost.textContent=`◈ ${money(def.cost)}`;duration.textContent=`${def.duration} 日`;
      cost.append(duration);info.append(name,meta,cost);button.append(canvas,info);button.addEventListener('click',()=>this.onTool(def.id));container.append(button);drawThumbnail(canvas,def);
    }
  }
  setTool(id){this.tool=id;this.showCatalog();$('inspect-btn').classList.toggle('active',!id);}
  dateLabel(day){const {days_per_month:days,months_per_year:months}=this.state.calendar;return `第 ${Math.floor(day/days/months)+1} 年 · ${Math.floor(day/days)%months+1} 月`;}
  update(state){
    const first=!this.catalog.length;this.state=state;this.catalog=state.catalog;const {world,summary}=state;
    if(first)this.showCatalog();
    $('cash').textContent=money(world.cash);$('population').textContent=count(world.population);$('capacity').textContent=count(summary.capacity);$('jobs').textContent=count(summary.jobs);
    $('cash-change').textContent=`预计月结余 ${summary.net>=0?'+':''}${money(summary.net)}`;
    $('vacancy').textContent=`剩余 ${Math.max(0,summary.capacity-world.population)} 人容量`;
    const last=world.history.at(-1);$('migration').textContent=last?`上月${last.migration>=0?'迁入':'迁出'} ${Math.abs(last.migration)} 人`:'已有居民 · 等待首份月报';
    const percentage=summary.capacity?Math.round(world.population/summary.capacity*100):0;
    $('housing-percent').textContent=`${percentage}%`;$('housing-progress').style.width=`${Math.min(100,percentage)}%`;$('housing-ratio').textContent=`${world.population} / ${summary.capacity} 人`;
    $('city-advice').textContent=world.population>=summary.capacity?'住房已满。规划新住宅，为下一批居民留出空间。':world.population>=summary.jobs?'岗位不足。新增集市或工坊可提供更多岗位。':'住房与岗位尚有余量。推进月份后，新居民会逐渐迁入。';
    $('date').textContent=this.dateLabel(world.day);const day=world.day%state.calendar.days_per_month+1,month=Math.floor(world.day/state.calendar.days_per_month)%state.calendar.months_per_year;
    $('day').textContent=`第 ${day} 日 · ${['早春','暖春','暮春','初夏','盛夏','晚夏','初秋','金秋','暮秋','初冬','深冬','岁末'][month]}`;
    $('map-subtitle').textContent=`北方河岸 / ${this.dateLabel(world.day)}`;
    $('pause-btn').textContent=world.paused?'▶ 继续':'Ⅱ 暂停';$('pause-btn').setAttribute('aria-label',world.paused?'继续模拟':'暂停模拟');
    $('advance-btn').disabled=!world.paused;
    document.querySelectorAll('[data-speed]').forEach(button=>button.classList.toggle('active',Number(button.dataset.speed)===world.speed));
    $('plan-bar').hidden=!summary.blueprints&&!this.tool;$('plan-count').textContent=summary.blueprints?`规划 ${summary.blueprints} 座建筑`:'点击地图放置蓝图';
    $('plan-cost').textContent=`总预算 ◈ ${money(summary.blueprint_cost)}`;$('commit-btn').disabled=!summary.blueprints;$('cancel-btn').disabled=!summary.blueprints;
    $('rotate-btn').disabled=!this.tool;
    $('queue-count').textContent=summary.construction?`${summary.construction} 项施工中`:'没有施工项目';
    $('load-btn').disabled=!state.save_exists;
    this.showEvents();this.showChart();this.showLedger();this.showQueue();this.showSelection();
  }
  showEvents(){
    const container=$('events');container.replaceChildren();
    for(const event of [...this.state.world.events].reverse().slice(0,4)){
      const item=document.createElement('div');item.className='event-item';const dot=document.createElement('i'),body=document.createElement('div'),title=document.createElement('strong'),date=document.createElement('small'),text=document.createElement('p');
      title.textContent=event.title;date.textContent=this.dateLabel(event.day);text.textContent=event.text;body.append(title,date,text);item.append(dot,body);container.append(item);
    }
  }
  showChart(){
    const history=this.state.world.history,values=history.length?history.map(item=>item.population):[this.state.world.population,this.state.world.population];
    if(history.length===1){
      $('growth-chart').innerHTML='<path d="M5,56 H235" stroke="#e4e7d8" fill="none"/><circle cx="120" cy="32" r="3" fill="#8aa171"/>';
      $('chart-start').textContent=this.dateLabel(history[0].day);$('chart-end').textContent=`${history[0].population} 人 · 单月记录`;
      return;
    }
    const min=Math.min(...values)-10,max=Math.max(...values)+10;
    const points=values.map((v,i)=>`${5+i/(values.length-1||1)*230},${56-(v-min)/(max-min)*48}`);
    // Values are numbers from the authoritative state, never placeholder trends.
    $('growth-chart').innerHTML=`<defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#96ad80" stop-opacity=".3"/><stop offset="1" stop-color="#96ad80" stop-opacity="0"/></linearGradient></defs><path d="M5,56 H235" stroke="#e4e7d8" fill="none"/><path d="M${points.join(' L')} L235,62 L5,62 Z" fill="url(#chart-fill)"/><path d="M${points.join(' L')}" stroke="#8aa171" stroke-width="2" fill="none" stroke-dasharray="${history.length?'':'4 4'}"/>`;
    $('chart-start').textContent=history.length?this.dateLabel(history[0].day):'开局人口';$('chart-end').textContent=history.length?`${history.at(-1).population} 人`:'推进月份后记录';
  }
  showLedger(){
    const {summary,world}=this.state;$('finance-summary').replaceChildren();
    for(const [label,value] of [['预计月税收',summary.revenue],['预计月维护',-summary.upkeep],['预计月结余',summary.net]]){
      const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=`${value>=0?'+':''}${money(value)}`;$('finance-summary').append(dt,dd);
    }
    const list=$('ledger-list');list.replaceChildren();
    if(!world.ledger.length){const p=document.createElement('p');p.className='empty-note';p.textContent='尚无交易。确认建造或推进一个月后，每笔实际收支会记录在这里。';list.append(p);}
    for(const entry of [...world.ledger].reverse().slice(0,30)){
      const item=document.createElement('div');item.className='ledger-item';const label=document.createElement('div'),date=document.createElement('small'),value=document.createElement('b');
      label.textContent=entry.label;date.textContent=this.dateLabel(entry.day);value.textContent=`${entry.amount>=0?'+':''}${money(entry.amount)}`;value.classList.toggle('negative',entry.amount<0);label.append(date);item.append(label,value);list.append(item);
    }
  }
  showQueue(){
    const list=$('queue-list');list.replaceChildren();const buildings=this.state.world.buildings.filter(b=>['queued','constructing'].includes(b.status));
    if(!buildings.length){const p=document.createElement('p');p.className='empty-note';p.textContent='还没有施工项目。先在地图规划建筑，再确认预算。';list.append(p);}
    for(const b of buildings){
      const def=this.catalog.find(d=>d.id===b.definition_id),item=document.createElement('div');item.className='queue-item';item.textContent=def.name;
      const status=document.createElement('small');status.textContent=`${statuses[b.status]} · ${b.progress}/${def.duration} 日`;
      const track=document.createElement('div');track.className='progress-track';const bar=document.createElement('div');bar.style.width=`${Math.round(b.progress/def.duration*100)}%`;track.append(bar);item.append(status,track);list.append(item);
    }
  }
  showSelection(){
    const b=this.state.world.buildings.find(b=>b.id===this.selectedId);$('selection-section').hidden=!b;if(!b)return;
    const def=this.catalog.find(d=>d.id===b.definition_id);$('selected-name').textContent=def.name;$('selected-description').textContent=def.description;$('selected-stats').replaceChildren();
    const rows=[['状态',statuses[b.status]],['位置',`(${b.x}, ${b.y})`],['朝向',`${b.rotation*90}°`],['产权','市政持有'],['维护 / 月',`${money(def.maintenance)} 银镑`]];
    if(def.capacity)rows.push(['住房容量',`${b.status==='completed'?def.capacity:0} 人`]);if(def.jobs)rows.push(['就业岗位',`${b.status==='completed'?def.jobs:0} 个`]);
    if(['queued','constructing'].includes(b.status))rows.push(['施工进度',`${b.progress} / ${def.duration} 日`]);
    for(const [name,value] of rows){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=name;dd.textContent=value;$('selected-stats').append(dt,dd);}
  }
  connected(ok){$('connection-status').textContent=ok?'本地模拟已连接':'连接中断 · 请检查后端';document.querySelector('.connection-dot').style.background=ok?'#7b9471':'#b77865';}
}
