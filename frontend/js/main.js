import {fetchState,sendCommand} from './api.js';
import {CityRenderer} from './renderer.js';
import {MapControls} from './controls.js';
import {$,GameUI} from './ui.js';

const renderer=new CityRenderer($('city-canvas'),$('minimap'));
let controls,busy=false,pollBusy=false,commandEpoch=0;
const ui=new GameUI(id=>controls.setTool(id));
function update(state){ui.update(state);renderer.setState(state);ui.connected(true);}

async function command(type,fields={},successMessage=''){
  if(busy){ui.notify('上一项操作正在完成，请稍候。');return false;}
  busy=true;commandEpoch++;
  try{
    update(await sendCommand(type,fields));
    if(['build','load','new_game'].includes(type))controls.setTool(null);
    if(['load','new_game'].includes(type)){renderer.selection=null;ui.selectedId=null;ui.showSelection();renderer.reset();}
    if(successMessage)ui.notify(successMessage);
    return true;
  }catch(error){ui.notify(error.message,true);return false;}
  finally{busy=false;}
}
controls=new MapControls(renderer,ui,command);

$('commit-btn').addEventListener('click',()=>controls.setTool(null));
$('cancel-btn').addEventListener('click',()=>controls.setTool(null));
$('advance-btn').addEventListener('click',()=>command('advance',{months:1},'已推进一个月，月报与行业指标已更新。'));
$('save-btn').addEventListener('click',()=>command('save',{},'城市已保存到本地存档。'));
$('load-btn').addEventListener('click',()=>{
  if(window.confirm('读取会替换当前未保存的进度，恢复存档中的城市。继续吗？'))command('load',{},'城市已恢复。');
});
document.querySelectorAll('[data-category]').forEach(button=>button.addEventListener('click',()=>{ui.category=button.dataset.category;document.querySelectorAll('[data-category]').forEach(b=>b.classList.toggle('active',b===button));ui.showCatalog();}));
document.querySelectorAll('[data-pane]').forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll('[data-pane]').forEach(b=>b.classList.toggle('active',b===button));for(const pane of ['overview','economy','ledger'])$(`${pane}-pane`).hidden=button.dataset.pane!==pane;}));
$('clear-selection').addEventListener('click',()=>{ui.selectedId=null;renderer.selection=null;ui.showSelection();renderer.render();});
// 阶段衔接（2026-10-16）扩展：拆除 / 迁址
$('remove-btn').addEventListener('click',()=>{
  if(!ui.selectedId){ui.notify('请先选中一栋建筑',true);return;}
  const buildings=ui.state.world?.buildings||[];
  const target=buildings.find(b=>b.id===ui.selectedId);
  const defn=ui.catalog.find(d=>d.id===target?.definition_id);
  const label=defn?.name||target?.definition_id||ui.selectedId;
  if(!window.confirm(`确认拆除「${label}」？投资不退还；已完工建筑将回收新增岗位、产能或住房。住房及在岗人员容量不足时拒绝拆除。`))return;
  command('remove',{building_id:ui.selectedId},'建筑已拆除，新增能力已回收。');
  ui.selectedId=null;renderer.selection=null;
});
$('relocate-btn').addEventListener('click',()=>{
  if(!ui.selectedId){ui.notify('请先选中一栋建筑',true);return;}
  const buildings=ui.state.world?.buildings||[];
  const target=buildings.find(b=>b.id===ui.selectedId);
  if(!target){ui.notify('找不到选中的建筑',true);return;}
  const defn=ui.catalog.find(d=>d.id===target.definition_id);
  $('relocate-title').textContent=`把「${defn?.name||target.definition_id}」迁到新地块`;
  $('relocate-x').value=String(target.x);
  $('relocate-y').value=String(target.y);
  $('relocate-dialog').showModal();
});
$('confirm-relocate').addEventListener('click',async()=>{
  const x=Number($('relocate-x').value);
  const y=Number($('relocate-y').value);
  if(!Number.isInteger(x)||!Number.isInteger(y)||x<0||x>27||y<0||y>27){
    ui.notify('坐标必须在 0—27 之间',true);return;
  }
  $('relocate-dialog').close();
  await command('relocate',{building_id:ui.selectedId,new_x:x,new_y:y},'建筑已迁址，行业岗位不变。');
});
$('queue-toggle').addEventListener('click',()=>{$('queue-drawer').hidden=!$('queue-drawer').hidden;});$('queue-close').addEventListener('click',()=>{$('queue-drawer').hidden=true;});
$('help-btn').addEventListener('click',()=>$('help-dialog').showModal());$('new-btn').addEventListener('click',()=>$('new-dialog').showModal());
document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
$('confirm-new').addEventListener('click',async()=>{const input=$('seed-input');if(!input.reportValidity()||input.value==='')return;const success=await command('new_game',{seed:Number(input.value)},'新的河湾镇已准备好。');if(success)$('new-dialog').close();});

async function poll(){
  if(busy||pollBusy)return;pollBusy=true;const epoch=commandEpoch;
  try{const state=await fetchState();if(!busy&&epoch===commandEpoch)update(state);}catch{ui.connected(false);}finally{pollBusy=false;}
}
try{update(await fetchState());}catch(error){ui.connected(false);ui.notify(`连接失败：${error.message}`,true);}
setInterval(poll,1200);

// A narrow rendering handle allows map-coordinate browser tests without modifying simulation state.
window.bipoleView={screenAt:(x,y)=>renderer.screenAt(x,y)};
