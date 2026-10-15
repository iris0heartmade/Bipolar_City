const $=id=>document.getElementById(id);
const number=v=>new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}).format(v);
let snapshot, busy=false;
const records=[];
function notice(text,error=false){$('status').textContent=text;$('status').classList.toggle('error',error);}
function render(data){
  snapshot=data;
  const r=data.latest;
  $('month').textContent=data.month;
  $('employment').textContent=`${number(Object.values(data.employment).reduce((a,b)=>a+b,0))} / 1,650`;
  $('government').textContent=number(data.cash.GOV/100);
  $('households').textContent=number(Object.entries(data.cash).filter(([k])=>k.startsWith('H')).reduce((n,[,v])=>n+v,0)/100);
  $('consumption').textContent=r?number(r.consumer_spending_cents/100):'尚未结算';
  $('balance').textContent=r?`全账户现金对账差额：${r.cash_residual_cents} 分`:'尚未结算';
  const tbody=$('sectors');tbody.replaceChildren();
  for(const [i,sector] of Object.entries(data.scenario.sectors)){
    const s=r?.sectors[i];const tr=document.createElement('tr');
    const values=[sector.name,data.employment[i],s?.capacity??'—',s?number(s.attractiveness):'—',s?number(s.wage_cents/100):'—',s?number(s.sales_cents/100):'—',number(data.cash[i]/100)];
    for(const value of values){const td=document.createElement('td');td.textContent=value;tr.append(td);}tbody.append(tr);
  }
  if(!$('industry').options.length){for(const [i,s] of Object.entries(data.scenario.sectors)){const option=document.createElement('option');option.value=i;option.textContent=s.name;$('industry').append(option);}$('industry').value='IND';}
  $('wage').value=data.scenario.sectors[$('industry').value].wage_multiplier;
  $('tax').value=data.scenario.city.profit_tax_multiplier;
  $('load').disabled=!data.save_exists;
  if(r){
    const changes=r.switches.map(x=>`${data.scenario.sectors[x.from].name} → ${data.scenario.sectors[x.to].name} ${x.people}人`);
    const hires=Object.values(r.hires).reduce((a,b)=>a+b,0),exits=Object.values(r.layoffs).reduce((a,b)=>a+b,0);
    $('flows').textContent=`从待业池录用 ${hires} 人；缩编 ${exits} 人。${changes.length?changes.join('；'):'本月没有在职转行。'}`;
    const loss=Object.values(r.sectors).filter(s=>s.profit_cents<0).map(s=>s.name);
    $('observations').textContent=`本月实际工资 ${number(r.wages_cents/100)} CR，营业额 ${number(r.sales_cents/100)} CR。${loss.length?`税前亏损：${loss.join('、')}。`:'非公共行业经营结果见上表。'}基准场景仍需进一步平衡校准，经营收缩会如实保留。`;
  }
}
async function read(response){const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'参数未通过校验');return data;}
async function command(type,fields={}){
  if(busy)return;busy=true;document.querySelectorAll('button').forEach(x=>x.disabled=true);
  try{const data=await read(await fetch('/api/industry/commands',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type,request_id:crypto.randomUUID(),...fields})}));
    render(data);if(type==='advance'){records.unshift(`第${data.month}月：就业 ${Object.values(data.employment).reduce((a,b)=>a+b,0)}人，政府现金 ${number(data.cash.GOV/100)} CR，真实消费 ${number(data.latest.consumer_spending_cents/100)} CR。`);}
    if(type==='new_game'||type==='load')records.length=0;
    $('history').replaceChildren(...records.slice(0,12).map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));
    notice(type==='set_parameter'?'计划已应用，将影响下一次真实结算。':type==='save'?'城市已保存。':type==='load'?'已恢复保存时的完整经营状态。':`城市状态已更新：第${data.month}月。`);
  }catch(error){notice(error.message,true);}finally{busy=false;document.querySelectorAll('button').forEach(x=>x.disabled=false);$('load').disabled=!snapshot?.save_exists;}
}
$('advance').onclick=()=>command('advance');$('save').onclick=()=>command('save');$('load').onclick=()=>command('load');
$('new').onclick=()=>{if(confirm('新建会替换未保存的经营进度，已有存档保留。继续吗？'))command('new_game');};
$('industry').onchange=()=>{$('wage').value=snapshot.scenario.sectors[$('industry').value].wage_multiplier;};
$('policy').onsubmit=e=>{e.preventDefault();command('set_parameter',{section:'sector',key:'wage_multiplier',industry:$('industry').value,value:Number($('wage').value)});};
$('tax-policy').onsubmit=e=>{e.preventDefault();command('set_parameter',{section:'city',key:'profit_tax_multiplier',value:Number($('tax').value)});};
try{render(await read(await fetch('/api/industry/state',{cache:'no-store'})));notice('城市已就绪。调整计划或推进一个月，查看真实经营结果。');}catch(error){notice(`读取城市失败：${error.message}`,true);}
