export const TILE_W = 48;
export const TILE_H = 24;

export function project(x, y) { return {x:(x-y)*TILE_W/2, y:(x+y)*TILE_H/2}; }
export function unproject(x, y) { return {x:Math.floor(x/TILE_W+y/TILE_H), y:Math.floor(y/TILE_H-x/TILE_W)}; }
export function rotatePoint(x, y, w, h, r) {
  for (let i=0;i<r;i++) { [x,y] = [h-1-y,x]; [w,h] = [h,w]; }
  return [x,y];
}
export function footprint(definition, b) {
  const cells=[];
  for(let x=0;x<definition.width;x++) for(let y=0;y<definition.height;y++) {
    const [dx,dy]=rotatePoint(x,y,definition.width,definition.height,b.rotation);
    cells.push([b.x+dx,b.y+dy]);
  }
  return cells;
}
export function entryCell(definition, b) {
  const [x,y]=rotatePoint(...definition.entry,definition.width,definition.height,b.rotation);
  return [b.x+x,b.y+y];
}
