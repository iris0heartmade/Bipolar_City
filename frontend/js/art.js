/** 全部城市素材由几何图形绘制，不依赖图片下载或外部字体。 */
import {project} from './isometric.js';

export function polygon(ctx, points, fill, stroke) {
  ctx.beginPath(); points.forEach((p,i)=>i ? ctx.lineTo(p.x,p.y) : ctx.moveTo(p.x,p.y)); ctx.closePath();
  if(fill){ctx.fillStyle=fill;ctx.fill();} if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=.7;ctx.stroke();}
}

export function diamond(ctx,x,y,fill,stroke) {
  polygon(ctx,[project(x,y),project(x+1,y),project(x+1,y+1),project(x,y+1)],fill,stroke);
}

function line(ctx,a,b,color,width=1){ctx.strokeStyle=color;ctx.lineWidth=width;ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();}
function lerp(a,b,t){return {x:a.x+(b.x-a.x)*t,y:a.y+(b.y-a.y)*t};}
function lift(p,h){return {x:p.x,y:p.y-h};}

export function tree(ctx, px, py, variant=0, small=false) {
  const size=small?.65:1;
  ctx.save();ctx.translate(px,py);ctx.scale(size,size);
  ctx.fillStyle='#455c3530';ctx.beginPath();ctx.ellipse(5,2,14,6,0,0,Math.PI*2);ctx.fill();
  ctx.fillStyle='#8e8060';ctx.fillRect(-2,-22,4,22);
  if(variant%3===0){
    polygon(ctx,[{x:0,y:-52},{x:16,y:-22},{x:-16,y:-22}],'#668757');
    polygon(ctx,[{x:0,y:-43},{x:19,y:-12},{x:-19,y:-12}],'#719061');
    polygon(ctx,[{x:0,y:-52},{x:0,y:-12},{x:-19,y:-12}],'#88a06a');
  }else{
    ctx.fillStyle=variant%3===1?'#8fa36e':'#a6b276';
    ctx.beginPath();ctx.arc(-7,-27,12,0,Math.PI*2);ctx.arc(6,-30,14,0,Math.PI*2);ctx.arc(0,-40,12,0,Math.PI*2);ctx.fill();
    ctx.fillStyle='#bdd09266';ctx.fillRect(-8,-42,8,5);ctx.fillRect(2,-36,8,5);
  }ctx.restore();
}

export function drawBuilding(ctx, def, b, options={}) {
  const swapped=b.rotation%2;
  const w=swapped?def.height:def.width, h=swapped?def.width:def.height;
  const x=b.x+.1,y=b.y+.1, bw=w-.2,bh=h-.2;
  const ground=[project(x,y),project(x+bw,y),project(x+bw,y+bh),project(x,y+bh)];
  const center=project(b.x+w/2,b.y+h/2);
  const blueprint=b.status==='blueprint';
  const constructing=b.status==='constructing'||b.status==='queued';
  ctx.save();
  if(blueprint)ctx.globalAlpha=.43;
  if(constructing)ctx.globalAlpha=.62;
  polygon(ctx,ground.map(p=>({x:p.x+8,y:p.y+6})),'#384c3026');
  if(def.id==='park') {
    polygon(ctx,ground,'#a6b481','#748e5f');
    polygon(ctx,[project(x+.15,y+.3),project(x+bw,y+.6),project(x+bw,y+.85),project(x+.15,y+.55)],'#dfd4af');
    for(const [dx,dy] of [[.45,.45],[1.45,.45],[.45,1.45]]){const p=project(b.x+dx,b.y+dy);tree(ctx,p.x,p.y,1,true);}
    ctx.fillStyle='#b9a27b';ctx.fillRect(center.x+7,center.y-2,12,3);
    ctx.fillStyle='#ecdd97';ctx.fillRect(center.x-2,center.y+5,3,3);ctx.fillStyle='#c88c7a';ctx.fillRect(center.x+4,center.y+9,3,3);
    ctx.restore();return;
  }
  const height=def.id==='apartment'?63:def.id==='town_hall'?48:def.id==='workshop'?31:32;
  const top=ground.map(p=>lift(p,height));
  const palette={row_house:['#dbc9a3','#c2af8b','#a76451'],apartment:['#dcc9a3','#baa27e','#7a8074'],
    market:['#e3cf9e','#b5aa83','#a87b56'],workshop:['#aab0a5','#879285','#727d75'],town_hall:['#e2d4b6','#c1b08e','#6a7971']};
  const [light,dark,roof]=palette[def.id]||palette.row_house;
  polygon(ctx,[ground[1],ground[2],top[2],top[1]],dark,'#64705360');
  polygon(ctx,[ground[2],ground[3],top[3],top[2]],light,'#64705360');
  line(ctx,lift(ground[2],4),lift(ground[3],4),'#c1b28e',3);
  line(ctx,lift(ground[1],4),lift(ground[2],4),'#aa9b7d',3);
  const rows=def.id==='apartment'?3:1, cols=def.id==='town_hall'?3:2;
  for(const [a,c,shade] of [[ground[1],ground[2],'#647b73'],[ground[2],ground[3],'#7f9383']]){
    for(let floor=0;floor<rows;floor++)for(let col=0;col<cols;col++){
      const p=lerp(a,c,(col+.55)/cols), winH=def.id==='town_hall'?13:9;
      const altitude=14+floor*17;
      const along={x:(c.x-a.x)/Math.hypot(c.x-a.x,c.y-a.y)*4,y:(c.y-a.y)/Math.hypot(c.x-a.x,c.y-a.y)*4};
      const pts=[{x:p.x-along.x,y:p.y-altitude-along.y},{x:p.x+along.x,y:p.y-altitude+along.y},
        {x:p.x+along.x,y:p.y-altitude+along.y+winH},{x:p.x-along.x,y:p.y-altitude-along.y+winH}];
      polygon(ctx,pts,shade,'#e7dcb0');
      line(ctx,{x:p.x,y:p.y-altitude},{x:p.x,y:p.y-altitude+winH},'#dfd2a6',.9);
      line(ctx,lift(pts[3],-1),lift(pts[2],-1),'#b8a77d',1.5);
    }
  }
  const door=lerp(ground[2],ground[3],.5);
  polygon(ctx,[{x:door.x-4,y:door.y-12},{x:door.x+4,y:door.y-16},{x:door.x+4,y:door.y-2},{x:door.x-4,y:door.y+2}],'#8e785b','#b09a75');
  if(def.id==='workshop'){
    polygon(ctx,top,'#869284','#626e61');
    for(let i=0;i<3;i++){const p=project(x+.3+i*.7,y+.3);polygon(ctx,[lift(p,height+5),lift({x:p.x+15,y:p.y+7},height+5),lift({x:p.x+15,y:p.y+15},height+5)],'#aeb7a3');}
    const chimney=project(x+bw-.3,y+.3);ctx.fillStyle='#7d7e6c';ctx.fillRect(chimney.x-5,chimney.y-height-34,10,36);ctx.fillStyle='#aea68a';ctx.fillRect(chimney.x-7,chimney.y-height-36,14,5);
    if(!blueprint&&!constructing){ctx.fillStyle='#f5f3e68a';for(let i=0;i<3;i++){ctx.beginPath();ctx.arc(chimney.x+i*5,chimney.y-height-45-i*10,6+i*2,0,Math.PI*2);ctx.fill();}}
  }else{
    const ridgeA=lift(lerp(top[0],top[1],.5),14),ridgeB=lift(lerp(top[3],top[2],.5),14);
    polygon(ctx,[top[0],top[1],ridgeA],light);
    polygon(ctx,[top[3],top[2],ridgeB],light);
    polygon(ctx,[top[1],top[2],ridgeB,ridgeA],roof,'#64705360');
    polygon(ctx,[top[0],top[3],ridgeB,ridgeA],def.id==='row_house'?'#bb8062':def.id==='apartment'?'#929686':'#8e9b86','#64705360');
    for(let i=1;i<5;i++){line(ctx,lerp(top[1],ridgeA,i/5),lerp(top[2],ridgeB,i/5),'#eee4bf28');line(ctx,lerp(top[0],ridgeA,i/5),lerp(top[3],ridgeB,i/5),'#e6d1a32e');}
    if(def.id==='row_house'||def.id==='apartment'){const p=lerp(top[0],ridgeA,.65);ctx.fillStyle='#bbac8e';ctx.fillRect(p.x-3,p.y-11,6,13);ctx.fillStyle='#c5b79b';ctx.fillRect(p.x-4,p.y-13,8,3);}
    if(def.id==='town_hall'){
      const p=lift(center,height+11);
      polygon(ctx,[{x:p.x-9,y:p.y},{x:p.x+9,y:p.y-9},{x:p.x+9,y:p.y-35},{x:p.x-9,y:p.y-26}],'#ddcaa5','#b3a281');
      polygon(ctx,[{x:p.x+9,y:p.y-9},{x:p.x+19,y:p.y-4},{x:p.x+19,y:p.y-30},{x:p.x+9,y:p.y-35}],'#b4a37f');
      polygon(ctx,[{x:p.x-13,y:p.y-26},{x:p.x+7,y:p.y-48},{x:p.x+23,y:p.y-29},{x:p.x+9,y:p.y-23}],'#657e6c');
      ctx.fillStyle='#f0e6c8';ctx.beginPath();ctx.arc(p.x+1,p.y-19,6,0,Math.PI*2);ctx.fill();line(ctx,{x:p.x+1,y:p.y-19},{x:p.x+1,y:p.y-23},'#76816c');line(ctx,{x:p.x+1,y:p.y-19},{x:p.x+4,y:p.y-18},'#76816c');
      ctx.fillStyle='#9b987d';ctx.fillRect(p.x+7,p.y-62,1,17);polygon(ctx,[{x:p.x+8,y:p.y-62},{x:p.x+24,y:p.y-58},{x:p.x+8,y:p.y-54}],'#b89d63');
    }
    if(def.id==='market'){
      const a=lift(ground[2],19),c=lift(ground[3],19);
      for(let i=0;i<8;i++) {const p=lerp(a,c,i/8),q=lerp(a,c,(i+1)/8);polygon(ctx,[p,q,{x:q.x,y:q.y+8},{x:p.x,y:p.y+8}],i%2?'#f1e0b0':'#bb7b61');}
      for(let i=0;i<3;i++){const p=lerp(ground[2],ground[3],(i+.3)/3);ctx.fillStyle='#a99869';ctx.fillRect(p.x-5,p.y-5,10,5);ctx.fillStyle=i%2?'#aab76c':'#c39569';ctx.fillRect(p.x-4,p.y-8,8,4);}
    }
  }
  ctx.restore();
  if(blueprint||constructing){
    ctx.save();ctx.strokeStyle=blueprint?'#4b8268':'#b49a64';ctx.lineWidth=1;ctx.setLineDash(blueprint?[4,3]:[]);
    polygon(ctx,ground,null,ctx.strokeStyle);ctx.setLineDash([]);
    for(const p of ground)line(ctx,p,lift(p,height+5),blueprint?'#70a189':'#b29664',1.5);
    if(constructing){line(ctx,lift(ground[2],14),lift(ground[3],14),'#b59665',2);line(ctx,lift(ground[2],27),lift(ground[3],27),'#b59665',2);}
    ctx.restore();
  }
  if(options.selected){ctx.save();polygon(ctx,ground,null,'#f8ecc0');ctx.restore();}
}

export function drawThumbnail(canvas,def) {
  const ctx=canvas.getContext('2d');canvas.width=140;canvas.height=128;
  ctx.clearRect(0,0,140,128);ctx.save();ctx.translate(70,35);ctx.scale(.85,.85);
  drawBuilding(ctx,def,{x:0,y:0,rotation:0,status:'completed'});ctx.restore();
}
