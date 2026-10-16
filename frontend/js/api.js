async function readResponse(response) {
  let data;
  try { data = await response.json(); } catch { throw new Error('服务返回异常，请检查后端运行状态。'); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '操作参数无效，请重试。');
  return data;
}

// 阶段衔接（2026-10-16）：地图游戏接入新行业经济内核，
// 所有 REST 走 /api/game 前缀（与 /api/industry 行业经营 webui 区分）。
export async function fetchState() {
  return readResponse(await fetch('/api/game/state', {cache:'no-store'}));
}

export async function sendCommand(type, fields = {}) {
  return readResponse(await fetch('/api/game/commands', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({type, request_id:crypto.randomUUID(), ...fields}),
  }));
}

export async function previewPlacement(definitionId, x, y, rotation) {
  const params = new URLSearchParams({definition_id:definitionId, x, y, rotation});
  return readResponse(await fetch(`/api/game/placement?${params}`, {cache:'no-store'}));
}
