// 世界地图 SVG 渲染：精美复古地图背景 + 5 城市节点 + 关系连线。

const PALETTE = {
  bg_light: '#f4e6c0',
  bg_dark: '#c9a866',
  sea: '#7b9bb3',
  river: '#5d7a92',
  mountain: '#6b5238',
  road: '#8b6f47',
  city_node: '#3a2820',
  player_glow: '#d4a537',
  link_high: '#2c7a3d',
  link_mid:  '#d4a537',
  link_low:  '#a13d2b',
};

const ARCHETYPE_COLOR = {
  agricultural_town: '#8b9d3f',
  industrial_city:   '#a87878',
  commercial_port:   '#507ba0',
  magic_academy:     '#9050a0',
  mining_border:     '#6b5238',
};

const VISIBILITY_LABELS = ['陌生人', '认识', '友好', '盟友', '完全开放'];

function visibilityFromFriendliness(f) {
  if (f >= 81) return 4;
  if (f >= 61) return 3;
  if (f >= 41) return 2;
  if (f >= 21) return 1;
  return 0;
}

export function renderWorld(state, opts = {}) {
  // M24：玩家下台时金色光晕变灰
  const playerDefeated = !!(opts.player_game_over && opts.player_game_over.is_game_over);
  const playerColor = playerDefeated ? '#5a3d44' : PALETTE.player_glow;
  const svg = document.getElementById('world-svg');
  if (!svg) return;
  svg.innerHTML = '';
  const ns = 'http://www.w3.org/2000/svg';

  // 1. SVG defs（渐变 + 滤镜）
  const defs = document.createElementNS(ns, 'defs');
  defs.innerHTML = `
    <radialGradient id="bg-grad" cx="50%" cy="50%" r="70%">
      <stop offset="0%" stop-color="${PALETTE.bg_light}"/>
      <stop offset="100%" stop-color="${PALETTE.bg_dark}"/>
    </radialGradient>
    <filter id="paper-texture">
      <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2"/>
      <feColorMatrix values="0 0 0 0 0.45 0 0 0 0 0.35 0 0 0 0 0.18 0 0 0 0.1 0"/>
      <feComposite in2="SourceGraphic" operator="in"/>
    </filter>
    <filter id="player-glow">
      <feGaussianBlur stdDeviation="5"/>
    </filter>
  `;
  svg.appendChild(defs);

  // 2. 底色
  const bg = document.createElementNS(ns, 'rect');
  bg.setAttribute('x', 0);
  bg.setAttribute('y', 0);
  bg.setAttribute('width', 1000);
  bg.setAttribute('height', 700);
  bg.setAttribute('fill', 'url(#bg-grad)');
  svg.appendChild(bg);

  // 3. 旧纸纹理覆盖
  const tex = document.createElementNS(ns, 'rect');
  tex.setAttribute('x', 0);
  tex.setAttribute('y', 0);
  tex.setAttribute('width', 1000);
  tex.setAttribute('height', 700);
  tex.setAttribute('fill', '#000');
  tex.setAttribute('filter', 'url(#paper-texture)');
  tex.setAttribute('opacity', '0.5');
  svg.appendChild(tex);

  // 4. 山脉（北侧）
  const mountains = document.createElementNS(ns, 'g');
  mountains.setAttribute('stroke', PALETTE.mountain);
  mountains.setAttribute('stroke-width', '2');
  mountains.setAttribute('fill', 'none');
  mountains.setAttribute('opacity', '0.55');
  for (let i = 0; i < 7; i++) {
    const x = 60 + i * 130;
    const path = document.createElementNS(ns, 'path');
    path.setAttribute('d', `M ${x},160 L ${x+30},100 L ${x+60},140 L ${x+95},80 L ${x+130},150 L ${x+160},110`);
    mountains.appendChild(path);
  }
  svg.appendChild(mountains);

  // 5. 河流
  const river = document.createElementNS(ns, 'path');
  river.setAttribute('d', 'M 0,420 C 200,370 400,440 600,400 S 900,440 1000,420');
  river.setAttribute('stroke', PALETTE.river);
  river.setAttribute('stroke-width', '8');
  river.setAttribute('fill', 'none');
  river.setAttribute('opacity', '0.65');
  svg.appendChild(river);

  // 6. 装饰元素：罗盘玫瑰 + 装饰花边 + 风玫瑰
  const decor = document.createElementNS(ns, 'g');
  decor.innerHTML = `
    <!-- 罗盘玫瑰 -->
    <g transform="translate(900, 620)">
      <circle r="44" fill="${PALETTE.bg_light}" stroke="${PALETTE.mountain}" stroke-width="1.5"/>
      <circle r="32" fill="none" stroke="${PALETTE.mountain}" stroke-width="0.5"/>
      <polygon points="0,-42 9,0 0,42 -9,0" fill="${PALETTE.mountain}"/>
      <polygon points="42,0 0,9 -42,0 0,-9" fill="${PALETTE.road}"/>
      <polygon points="30,-30 0,-5 -30,-30 -5,0" fill="${PALETTE.mountain}" opacity="0.6"/>
      <text y="-50" text-anchor="middle" fill="${PALETTE.mountain}" font-size="16" font-weight="bold">N</text>
    </g>
    <!-- 风玫瑰（左上） -->
    <g transform="translate(80, 100)">
      <circle r="20" fill="${PALETTE.bg_light}" stroke="${PALETTE.mountain}" stroke-width="0.5"/>
      <path d="M 0,-18 L 4,0 L -4,0 Z" fill="${PALETTE.mountain}"/>
      <path d="M 0,18 L 4,0 L -4,0 Z" fill="${PALETTE.bg_dark}"/>
      <text y="-26" text-anchor="middle" fill="${PALETTE.mountain}" font-size="10">风</text>
    </g>
    <!-- 装饰花边 -->
    <rect x="20" y="20" width="960" height="660" fill="none"
          stroke="${PALETTE.mountain}" stroke-width="3"/>
    <rect x="28" y="28" width="944" height="644" fill="none"
          stroke="${PALETTE.mountain}" stroke-width="1" stroke-dasharray="4,4"/>
  `;
  svg.appendChild(decor);

  // 7. 连线（按友好度渐变色 + 粗细）
  const linksGroup = document.createElementNS(ns, 'g');
  state.links.forEach(link => {
    const a = state.cities.find(c => c.id === link.from);
    const b = state.cities.find(c => c.id === link.to);
    if (!a || !b) return;
    const ax = a.x * 10, ay = a.y * 7;
    const bx = b.x * 10, by = b.y * 7;
    const f = link.friendliness;
    const color = f >= 70 ? PALETTE.link_high : f >= 40 ? PALETTE.link_mid : PALETTE.link_low;
    const opacity = 0.3 + f / 200;
    const width = 1 + f / 30;
    const line = document.createElementNS(ns, 'line');
    line.setAttribute('x1', ax);
    line.setAttribute('y1', ay);
    line.setAttribute('x2', bx);
    line.setAttribute('y2', by);
    line.setAttribute('stroke', color);
    line.setAttribute('stroke-width', width);
    line.setAttribute('opacity', opacity);
    linksGroup.appendChild(line);
  });
  svg.appendChild(linksGroup);

  // 8. 城市节点
  const nodesGroup = document.createElementNS(ns, 'g');
  state.cities.forEach(city => {
    const x = city.x * 10, y = city.y * 7;
    const g = document.createElementNS(ns, 'g');
    g.classList.add('city-node');
    g.setAttribute('data-city-id', city.id);
    g.style.cursor = 'pointer';
    g.onclick = () => window.onCityClick(city.id);

    // 玩家城金色光晕（M24：玩家下台 → 灰色）
    if (city.is_player) {
      const glow = document.createElementNS(ns, 'circle');
      glow.setAttribute('cx', x);
      glow.setAttribute('cy', y);
      glow.setAttribute('r', 28);
      glow.setAttribute('fill', playerColor);
      glow.setAttribute('opacity', playerDefeated ? '0.35' : '0.45');
      glow.setAttribute('filter', 'url(#player-glow)');
      g.appendChild(glow);
    }

    // 节点圆（按 archetype 配色）
    const circle = document.createElementNS(ns, 'circle');
    circle.setAttribute('cx', x);
    circle.setAttribute('cy', y);
    circle.setAttribute('r', 16);
    circle.setAttribute('fill', ARCHETYPE_COLOR[city.archetype] || PALETTE.city_node);
    circle.setAttribute('stroke', '#1a1010');
    circle.setAttribute('stroke-width', 2);
    g.appendChild(circle);

    // 内圈（区分玩家城；M24 玩家下台 → 灰描边）
    if (city.is_player) {
      const inner = document.createElementNS(ns, 'circle');
      inner.setAttribute('cx', x);
      inner.setAttribute('cy', y);
      inner.setAttribute('r', 10);
      inner.setAttribute('fill', 'none');
      inner.setAttribute('stroke', playerColor);
      inner.setAttribute('stroke-width', '2');
      g.appendChild(inner);
    }

    // 城市名字
    const text = document.createElementNS(ns, 'text');
    text.setAttribute('x', x);
    text.setAttribute('y', y + 36);
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('fill', '#1a1010');
    text.setAttribute('font-size', '14');
    text.setAttribute('font-weight', 'bold');
    text.setAttribute('paint-order', 'stroke');
    text.setAttribute('stroke', '#f4e6c0');
    text.setAttribute('stroke-width', '3');
    text.textContent = city.name_cn;
    g.appendChild(text);

    // 副标题（archetype 类型标签）
    const sub = document.createElementNS(ns, 'text');
    sub.setAttribute('x', x);
    sub.setAttribute('y', y - 22);
    sub.setAttribute('text-anchor', 'middle');
    sub.setAttribute('fill', '#5a3d44');
    sub.setAttribute('font-size', '10');
    sub.setAttribute('font-style', 'italic');
    sub.textContent = city.archetype.replace(/_/g, ' ');
    g.appendChild(sub);

    nodesGroup.appendChild(g);
  });
  svg.appendChild(nodesGroup);
}