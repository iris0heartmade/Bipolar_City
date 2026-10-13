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
    if(['commit','load','new_game'].includes(type))controls.setTool(null);
    if(['load','new_game'].includes(type)){renderer.selection=null;ui.selectedId=null;ui.showSelection();renderer.reset();}
    if(successMessage)ui.notify(successMessage);
    return true;
  }catch(error){ui.notify(error.message,true);return false;}
  finally{busy=false;}
}
controls=new MapControls(renderer,ui,command);

$('commit-btn').addEventListener('click',()=>command('commit',{},'工程已批准，推进时间开始施工。'));
$('cancel-btn').addEventListener('click',()=>command('cancel',{},'蓝图已取消，未收取费用。'));
$('pause-btn').addEventListener('click',()=>command('pause',{paused:!ui.state.world.paused}));
$('advance-btn').addEventListener('click',()=>command('advance_month',{},'已推进一个月，月报与财政账本已更新。'));
$('save-btn').addEventListener('click',()=>command('save',{},'城市已保存到本地存档。'));
$('load-btn').addEventListener('click',()=>{
  if(window.confirm('读取会替换当前未保存的进度，恢复存档中的城市。继续吗？'))command('load',{},'城市已恢复。');
});
document.querySelectorAll('[data-speed]').forEach(button=>button.addEventListener('click',()=>command('speed',{speed:Number(button.dataset.speed)})));
document.querySelectorAll('[data-category]').forEach(button=>button.addEventListener('click',()=>{ui.category=button.dataset.category;document.querySelectorAll('[data-category]').forEach(b=>b.classList.toggle('active',b===button));ui.showCatalog();}));
document.querySelectorAll('[data-pane]').forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll('[data-pane]').forEach(b=>b.classList.toggle('active',b===button));for(const pane of ['overview','economy','ledger'])$(`${pane}-pane`).hidden=button.dataset.pane!==pane;}));
document.querySelectorAll('[data-housing-mode]').forEach(button=>button.addEventListener('click',()=>controls.setHousingMode(button.dataset.housingMode)));
$('property-public').addEventListener('click',()=>command('set_housing_mode',{building_id:ui.selectedId,housing_mode:'public'},'未售住宅已改为公共出租。'));
$('property-sale').addEventListener('click',()=>command('set_housing_mode',{building_id:ui.selectedId,housing_mode:'for_sale'},'未售住宅已进入待售方案，月度按资金与需求成交。'));
$('clear-selection').addEventListener('click',()=>{ui.selectedId=null;renderer.selection=null;ui.showSelection();renderer.render();});
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
