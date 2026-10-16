// 世界地图主入口：调用 API、渲染 SVG、更新详情面板。
import { renderWorld } from './world_map.js';
import { renderDetail } from './world_detail.js';

const state = {
  cities: [],
  links: [],
  world: null,
  selectedCity: null,
};

async function fetchState() {
  const r = await fetch('/api/world/state');
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function fetchCity(cityId) {
  const r = await fetch(`/api/world/cities/${cityId}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function refresh() {
  try {
    const data = await fetchState();
    state.cities = data.cities;
    state.links = data.links;
    state.world = data.world;
    renderWorld(state);
    const year = Math.floor(state.world.month / 12) + 1;
    const monthOfYear = (state.world.month % 12) + 1;
    document.getElementById('status-line').textContent =
      `国家大陆 · 第 ${year} 年 · ${monthOfYear} 月 · 共 ${state.cities.length} 座城市`;
    document.getElementById('map-subtitle').textContent = `第 ${year} 年 · ${monthOfYear} 月`;
    if (state.selectedCity) {
      try {
        const detail = await fetchCity(state.selectedCity);
        renderDetail(detail);
      } catch (e) {
        // 玩家城市可能被 IndustryGame 管理
      }
    }
  } catch (e) {
    document.getElementById('status-line').textContent = `错误：${e.message}`;
  }
}

async function advance() {
  const btn = document.getElementById('advance-btn');
  btn.disabled = true;
  btn.textContent = '推进中…';
  try {
    const r = await fetch('/api/world/advance', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ months: 1 }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    await refresh();
  } catch (e) {
    document.getElementById('status-line').textContent = `推进失败：${e.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = '推进 1 个月 →';
  }
}

document.getElementById('advance-btn').onclick = advance;

window.worldState = state;
window.onCityClick = async (cityId) => {
  state.selectedCity = cityId;
  const detail = await fetchCity(cityId);
  renderDetail(detail);
  // 高亮节点
  document.querySelectorAll('.city-node').forEach(el => {
    el.classList.toggle('selected', el.dataset.cityId === cityId);
  });
};

refresh();