import {previewPlacement} from './api.js';
import {$} from './ui.js';

export class MapControls {
  constructor(renderer,ui,command){
    this.renderer=renderer;this.ui=ui;this.command=command;this.rotation=0;this.previewSequence=0;
    const canvas=renderer.canvas;
    canvas.addEventListener('pointerdown',event=>{if(event.button!==0)return;this.drag={x:event.clientX,y:event.clientY,pan:{...renderer.pan},moved:false};canvas.setPointerCapture(event.pointerId);});
    canvas.addEventListener('pointermove',event=>{
      if(this.drag){const dx=event.clientX-this.drag.x,dy=event.clientY-this.drag.y;if(Math.abs(dx)+Math.abs(dy)>5)this.drag.moved=true;if(this.drag.moved){renderer.pan={x:this.drag.pan.x+dx,y:this.drag.pan.y+dy};renderer.render();return;}}
      this.hover(event);
    });
    canvas.addEventListener('pointerup',event=>{const drag=this.drag;this.drag=null;if(drag&&!drag.moved)this.click(event);});
    canvas.addEventListener('pointercancel',()=>{this.drag=null;});
    canvas.addEventListener('pointerleave',()=>{if(!this.drag){renderer.hover=null;renderer.render();$('placement-hint').hidden=true;}});
    canvas.addEventListener('wheel',event=>{event.preventDefault();renderer.setZoom(renderer.zoom*(event.deltaY>0?.9:1.1));},{passive:false});
    document.addEventListener('keydown',event=>{if(event.target.matches('input,textarea')||document.querySelector('dialog[open]'))return;if(event.key.toLowerCase()==='r')this.rotate();if(event.key==='Escape')this.setTool(null);});
    $('zoom-in').addEventListener('click',()=>renderer.setZoom(renderer.zoom*1.15));$('zoom-out').addEventListener('click',()=>renderer.setZoom(renderer.zoom/1.15));$('reset-view').addEventListener('click',()=>renderer.reset());
    $('grid-btn').addEventListener('click',()=>{renderer.grid=!renderer.grid;$('grid-btn').setAttribute('aria-pressed',String(renderer.grid));renderer.render();});
    $('layer-btn').addEventListener('click',()=>{renderer.housing=!renderer.housing;$('layer-btn').setAttribute('aria-pressed',String(renderer.housing));renderer.render();});
    $('rotate-btn').addEventListener('click',()=>this.rotate());$('inspect-btn').addEventListener('click',()=>this.setTool(null));
  }
  setTool(id){
    this.previewSequence++;this.tool=id;this.rotation=0;this.renderer.tool=id?{id,rotation:0}:null;this.renderer.preview=null;this.ui.setTool(id);
    this.renderer.canvas.classList.toggle('build-mode',Boolean(id));$('placement-hint').hidden=!id;
    if(id&&window.matchMedia('(max-width: 650px)').matches)this.renderer.canvas.scrollIntoView({block:'center',behavior:'instant'});
    $('placement-hint').textContent=id?'沿道路选择空地 · R 旋转 · 黄色格为入口':' ';
    this.renderer.render();if(this.ui.state)this.ui.update(this.ui.state);
  }
  rotate(){if(!this.tool)return;this.rotation=(this.rotation+1)%4;this.renderer.tool.rotation=this.rotation;this.validateHover();}
  setHousingMode(mode){this.ui.housingMode=mode;this.ui.showHousingTool();if(this.tool&&window.matchMedia('(max-width: 650px)').matches)this.renderer.canvas.scrollIntoView({block:'center',behavior:'instant'});}
  hover(event){const cell=this.renderer.cellAt(event.clientX,event.clientY);if(this.renderer.hover?.x===cell.x&&this.renderer.hover?.y===cell.y)return;this.renderer.hover=cell;if(this.tool)this.validateHover();else this.renderer.render();}
  validateHover(){
    clearTimeout(this.previewTimer);const sequence=++this.previewSequence;this.renderer.preview=null;this.renderer.render();
    if(!this.tool||!this.renderer.hover)return;
    const {x,y}=this.renderer.hover,tool=this.tool,rotation=this.rotation;
    $('placement-hint').hidden=false;$('placement-hint').textContent='检查地块……';$('placement-hint').classList.remove('invalid');
    this.previewTimer=setTimeout(async()=>{
      try{const result=await previewPlacement(tool,x,y,rotation);if(sequence!==this.previewSequence)return;this.renderer.preview=result;$('placement-hint').textContent=`${result.valid?'✓':'×'} ${result.reason} · 朝向 ${rotation*90}°`;$('placement-hint').classList.toggle('invalid',!result.valid);this.renderer.render();}
      catch(error){if(sequence===this.previewSequence){$('placement-hint').textContent=error.message;$('placement-hint').classList.add('invalid');}}
    },90);
  }
  async click(event){
    const cell=this.renderer.cellAt(event.clientX,event.clientY);
    if(this.tool){
      // 阶段衔接（2026-10-16）：地图游戏接入新经济内核后，
      // 点击空地直接 build（不再保留「place_blueprint + commit」两步）。
      // 后端把建筑类型翻译为对应行业 capacity 扩展：
      //   workshop → IND +40 jobs；market → SRV +30 jobs；
      //   row_house/apartment → AGR +12/+24 jobs；park 仅装饰。
      const ok=await this.command('build',{definition_id:this.tool,x:cell.x,y:cell.y,rotation:this.rotation});
      if(ok)this.validateHover();
    }
    else {
      const b=this.renderer.buildingAt(cell);
      this.renderer.selection=b?.id??null;this.ui.selectedId=b?.id??null;
      document.querySelector('[data-pane=overview]').click();this.ui.showSelection();this.renderer.render();
    }
  }
}
