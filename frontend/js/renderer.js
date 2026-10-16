import {diamond, drawBuilding, lantern, polygon, tree} from './art.js';
import {entryCell, footprint, project, unproject} from './isometric.js';
import {MAP_THEME} from './theme.js';

export class CityRenderer {
  constructor(canvas,minimap){
    this.canvas=canvas;this.ctx=canvas.getContext('2d');this.minimap=minimap;
    this.zoom=1;this.pan={x:0,y:0};this.grid=false;this.housing=false;this.hover=null;this.preview=null;this.tool=null;this.selection=null;
    this.size={w:0,h:0};this.fitZoom=1;this.peopleFrame=0;
    this.observer=new ResizeObserver(()=>this.resize());this.observer.observe(canvas);
  }
  resize(){
    const bounds=this.canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;
    this.size={w:bounds.width,h:bounds.height};this.canvas.width=Math.round(bounds.width*dpr);this.canvas.height=Math.round(bounds.height*dpr);
    this.dpr=dpr;
    if(this.state)this.fitZoom=Math.min(bounds.width/1450,bounds.height/800);
    this.render();
  }
  setState(state){this.state=state;this.catalog=Object.fromEntries(state.catalog.map(d=>[d.id,d]));this.resize();this.drawMinimap();}
  origin(){return {x:this.size.w/2+this.pan.x,y:this.size.h*.17+this.pan.y};}
  actualZoom(){return this.zoom*this.fitZoom;}
  cellAt(clientX,clientY){const rect=this.canvas.getBoundingClientRect(),o=this.origin(),z=this.actualZoom();return unproject((clientX-rect.left-o.x)/z,(clientY-rect.top-o.y)/z);}
  screenAt(x,y){const p=project(x+.5,y+.5),o=this.origin(),r=this.canvas.getBoundingClientRect(),z=this.actualZoom();return {x:r.left+o.x+p.x*z,y:r.top+o.y+p.y*z};}
  buildingAt(cell){return this.state.world.buildings.find(b=>footprint(this.catalog[b.definition_id],b).some(([x,y])=>x===cell.x&&y===cell.y));}
  setZoom(value){this.zoom=Math.max(.55,Math.min(2.8,value));this.render();document.getElementById('zoom-label').textContent=`${Math.round(this.zoom*100)}%`;}
  reset(){this.pan={x:0,y:0};this.setZoom(1);}
  render(){
    if(!this.state||!this.size.w)return;
    const ctx=this.ctx,{w,h}=this.size,o=this.origin(),z=this.actualZoom();
    ctx.setTransform(this.dpr,0,0,this.dpr,0,0);ctx.clearRect(0,0,w,h);
    const bg=ctx.createLinearGradient(0,0,w,h);bg.addColorStop(0,MAP_THEME.sky[0]);bg.addColorStop(1,MAP_THEME.sky[1]);ctx.fillStyle=bg;ctx.fillRect(0,0,w,h);
    this.drawArcaneFrame(ctx,w,h);
    ctx.save();ctx.translate(o.x,o.y);ctx.scale(z,z);ctx.imageSmoothingEnabled=false;
    const terrain=this.state.world.terrain,height=terrain.length,width=terrain[0].length;
    const mapCorners=[project(0,0),project(width,0),project(width,height),project(0,height)];
    polygon(ctx,mapCorners.map(p=>({x:p.x+9,y:p.y+16})),'#00000066');
    polygon(ctx,[mapCorners[1],mapCorners[2],{x:mapCorners[2].x,y:mapCorners[2].y+9},{x:mapCorners[1].x,y:mapCorners[1].y+9}],MAP_THEME.rimDark);
    polygon(ctx,[mapCorners[2],mapCorners[3],{x:mapCorners[3].x,y:mapCorners[3].y+9},{x:mapCorners[2].x,y:mapCorners[2].y+9}],MAP_THEME.rimLight);
    const occupied=new Set(this.state.world.buildings.flatMap(b=>footprint(this.catalog[b.definition_id],b).map(c=>c.join(','))));
    for(let y=0;y<height;y++)for(let x=0;x<width;x++){
      const terrainType=terrain[y][x],hash=((x*73+y*157+this.state.world.seed)%97);
      let color=terrainType==='grass'?MAP_THEME.grass[hash%4]:terrainType==='water'?MAP_THEME.water[hash%3]:terrainType==='bridge'?MAP_THEME.bridge:MAP_THEME.road;
      diamond(ctx,x,y,color,this.grid?MAP_THEME.grid:null);
      const p=project(x+.5,y+.5);
      if(terrainType==='water'&&hash%4===0){ctx.fillStyle=MAP_THEME.waves;ctx.fillRect(p.x-7,p.y,13,1);ctx.fillRect(p.x+3,p.y+4,9,1);}
      if(terrainType==='road'||terrainType==='bridge'){
        ctx.strokeStyle=terrainType==='bridge'?'#8f8664':'#bbae8c';ctx.lineWidth=.6;ctx.beginPath();ctx.moveTo(p.x-15,p.y+1);ctx.lineTo(p.x+12,p.y+1);ctx.stroke();
        if(hash%5===0){ctx.fillStyle='#b7ab88';ctx.fillRect(p.x-2,p.y-3,3,2);}
      }
      if(terrainType==='grass'&&!occupied.has(`${x},${y}`)&&hash%5===0){ctx.fillStyle='#97af7445';ctx.fillRect(p.x,p.y,3,2);ctx.fillRect(p.x+5,p.y-2,2,2);}
    }
    const objects=[];
    for(let y=0;y<height;y++)for(let x=0;x<width;x++){
      const hash=(x*73+y*157+this.state.world.seed)%97;
      if(terrain[y][x]==='road'&&(x+y)%6===0)
        objects.push({depth:x+y+1,draw:()=>lantern(ctx,x+.9,y+.2)});
      if(terrain[y][x]==='grass'&&!occupied.has(`${x},${y}`)&&!this.nearRoad(x,y)&&hash%11===0)
        objects.push({depth:x+y,draw:()=>{const p=project(x+.5,y+.5);tree(ctx,p.x,p.y,hash,hash%2===0);}});
    }
    for(const b of this.state.world.buildings){
      const def=this.catalog[b.definition_id],size=b.rotation%2?[def.height,def.width]:[def.width,def.height];
      objects.push({depth:b.x+b.y+size[0]+size[1]-1,draw:()=>{
        drawBuilding(ctx,def,b,{selected:b.id===this.selection});
        const p=project(b.x+size[0]/2,b.y+size[1]/2);
        if(this.housing&&def.capacity){const capacity=b.housing_mode==='for_sale'?b.sold_units*def.unit_capacity:def.capacity;this.label(ctx,p.x,p.y-70,b.player_built?`新增住房 ${b.status==='completed'?capacity:0} 人`:'开局住房底座', '#bddecc');}
        if(b.status==='completed'&&b.housing_mode==='for_sale'&&b.sold_units<def.units)this.label(ctx,p.x,p.y-70,`待售 ${def.units-b.sold_units} 户`,'#dfc48e');
        if(b.status==='blueprint')this.label(ctx,p.x,p.y-65,'规划','#bde0ce');
        if(b.status==='queued'||b.status==='constructing')this.label(ctx,p.x,p.y-74,'下月完工','#dbbe81');
      }});
    }
    objects.sort((a,b)=>a.depth-b.depth).forEach(item=>item.draw());
    this.drawPeople(ctx);
    if(this.selection){const b=this.state.world.buildings.find(b=>b.id===this.selection);if(b)for(const [x,y] of footprint(this.catalog[b.definition_id],b))diamond(ctx,x,y,'#f5eec31a','#f7eac0');}
    if(this.hover&&this.tool){
      const def=this.catalog[this.tool.id],b={x:this.hover.x,y:this.hover.y,rotation:this.tool.rotation,status:'blueprint'};
      const valid=this.preview?.valid;
      for(const [x,y] of footprint(def,b))diamond(ctx,x,y,valid===true?'#4b8a6755':valid===false?'#be74616b':'#c4a35d66',valid===false?'#b15d4b':'#f5f3c1');
      drawBuilding(ctx,def,b);
      const [ex,ey]=entryCell(def,b);diamond(ctx,ex,ey,'#edd89488','#fff1bf');
    }
    ctx.restore();
  }
  nearRoad(x,y){return [[x-1,y],[x+1,y],[x,y-1],[x,y+1]].some(([a,b])=>['road','bridge'].includes(this.state.world.terrain[b]?.[a]));}
  label(ctx,x,y,text,color){ctx.save();ctx.font='10px "Microsoft YaHei",sans-serif';const width=ctx.measureText(text).width+14;ctx.fillStyle='#162737ed';ctx.fillRect(x-width/2,y-9,width,17);ctx.strokeStyle='#b598635a';ctx.strokeRect(x-width/2,y-9,width,17);ctx.fillStyle=color;ctx.textAlign='center';ctx.fillText(text,x,y+3);ctx.restore();}
  drawArcaneFrame(ctx,w,h){
    ctx.save();ctx.strokeStyle='#b89a6326';ctx.lineWidth=.7;
    const cx=w/2,cy=h*.53,r=Math.min(w,h)*.44;
    for(const factor of [1,1.07]){ctx.beginPath();ctx.ellipse(cx,cy,r*factor,r*factor,0,0,Math.PI*2);ctx.stroke();}
    for(let i=0;i<32;i++){const a=i*Math.PI/16;ctx.beginPath();ctx.moveTo(cx+Math.cos(a)*r,cy+Math.sin(a)*r);ctx.lineTo(cx+Math.cos(a)*r*(i%4?1.015:1.045),cy+Math.sin(a)*r*(i%4?1.015:1.045));ctx.stroke();}
    ctx.fillStyle='#a9c5d139';for(let i=0;i<45;i++){const x=(i*157+59)%w,y=(i*97+19)%h;ctx.fillRect(x,y,i%5===0?2:1,1);}
    ctx.font='11px Georgia,serif';ctx.fillStyle='#bfa67670';ctx.fillText('✦  B I P O L E  ✦',Math.max(20,cx-56),h-28);ctx.restore();
  }
  drawPeople(ctx){
    // Cosmetic pedestrians; coordinates never enter population or tax calculation.
    const palette=['#8c6e55','#79907e','#ac8564','#6c7d81'];
    for(let i=0;i<14;i++){
      const x=2+(i*1.61)%19,y=i%2?10.45:17.5,p=project(x,y);
      ctx.fillStyle='#60734630';ctx.beginPath();ctx.ellipse(p.x+2,p.y+2,4,2,0,0,Math.PI*2);ctx.fill();
      ctx.fillStyle=palette[i%4];ctx.fillRect(p.x-1,p.y-7,3,5);ctx.fillStyle='#d1bb93';ctx.fillRect(p.x-1,p.y-10,3,3);ctx.fillStyle='#718066';ctx.fillRect(p.x-1,p.y-2,1,3);ctx.fillRect(p.x+1,p.y-2,1,3);
    }
  }
  drawMinimap(){
    const ctx=this.minimap.getContext('2d'),terrain=this.state.world.terrain;
    ctx.clearRect(0,0,144,100);ctx.save();ctx.translate(72,8);ctx.scale(.1,.1);
    for(let y=0;y<terrain.length;y++)for(let x=0;x<terrain[0].length;x++)diamond(ctx,x,y,terrain[y][x]==='water'?MAP_THEME.water[1]:['road','bridge'].includes(terrain[y][x])?MAP_THEME.road:MAP_THEME.grass[0]);
    for(const b of this.state.world.buildings)for(const [x,y] of footprint(this.catalog[b.definition_id],b))diamond(ctx,x,y,b.status==='blueprint'?'#d6c891':this.catalog[b.definition_id].color);
    ctx.restore();
  }
}
