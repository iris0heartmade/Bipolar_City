// 城市详情渲染：可见字段 + 锁定字段 + 关系 + AI 最近动作。

const FIELD_LABELS = {
  population_g: '人口量级',
  labor_force: '劳动力',
  employment: '7 行业就业',
  unemployment_rate: '失业率',
  sales_cents: '七行业月营业额',
  government_cash_cents: '政府现金',
  wages_cents: '七行业工资义务',
  paid_wages_cents: '七行业实付工资',
  wage_arrears_cents: '累计未偿工资',
  food_production_units: '粮食月产',
  material_production_units: '原料月产',
  stockpile_food_units: '粮食库存',
  stockpile_material_units: '原料库存',
  monthly_trade_volume_cents: '月贸易额',
  cash_months: 'GOV 现金覆盖月数',
};

function formatValue(key, value) {
  if (value === null || value === undefined) return '？？？';
  if (key === 'population_g') return `${value} 千人`;
  if (key === 'unemployment_rate') return `${(value * 100).toFixed(1)}%`;
  if (key === 'cash_months') return `${value.toFixed(1)} 月`;
  if (key === 'employment') {
    const parts = Object.entries(value).map(([k, v]) => `${k}:${v}`);
    return parts.join(' / ');
  }
  if (key.endsWith('_cents')) {
    return `${(value / 100).toLocaleString('zh-CN', { maximumFractionDigits: 0 })} CR`;
  }
  return value.toLocaleString('zh-CN');
}

export function renderDetail(detail) {
  if (!detail || detail.error) {
    document.getElementById('detail-content').innerHTML =
      `<p class="hint">${detail?.error || '暂无数据'}</p>`;
    return;
  }

  const lines = [];
  lines.push(`<h3>${detail.name_cn}（${detail.archetype}）</h3>`);

  if (detail.relation) {
    const f = detail.relation.friendliness;
    lines.push(`<div class="row"><span>友好度</span><span class="value">${f} / 100</span></div>`);
    const v = detail.visibility || 0;
    lines.push(`<div class="row"><span>可见等级</span><span class="value">${v}（${detail.visibility_label || '陌生人'}）</span></div>`);
    lines.push(`<p class="event">最近事件：${detail.relation.last_event}</p>`);
  }

  if (detail.is_player) {
    lines.push(`<p class="hint">玩家城市详情请访问 <a href="/industry" style="color:#d4a537">行业经营</a> 页面。</p>`);
  } else {
    // 字段顺序按 FIELD_LABELS
    for (const [key, label] of Object.entries(FIELD_LABELS)) {
      const hasValue = detail[key] !== undefined && detail[key] !== null;
      const lockedClass = hasValue ? '' : ' locked';
      const display = hasValue ? formatValue(key, detail[key]) : '';
      lines.push(`<div class="row${lockedClass}"><span>${label}</span><span class="value">${display}</span></div>`);
    }
  }

  // AI 最近动作
  if (detail.ai_recent_actions && detail.ai_recent_actions.length > 0) {
    lines.push(`<h3>AI 市长最近动作</h3>`);
    const recent = detail.ai_recent_actions.slice(-6).reverse();
    for (const action of recent) {
      const kindLabel = ({
        'tax_adjust': `调税率 → ${action.new_rate}`,
        'subsidy_industry': `补贴 ${action.industry} ${(action.cash_cents || 0) / 100} CR`,
        'pardon_welfare': `发救济 ${(action.cash_cents || 0) / 100} CR`,
        'hire_civil': `雇公务员 ${action.people} 人`,
        'limit_hire': `限招 ${action.industry}`,
        'limit_layoff': `限裁 ${action.industry}`,
        'purchase_external': `采购原料 ${(action.cash_cents || 0) / 100} CR`,
      })[action.kind] || action.kind;
      const okMark = action.ok === false ? '❌' : '✓';
      lines.push(`<div class="row"><span>${okMark} ${kindLabel}</span><span class="value" style="font-size:0.75rem">${action.reason || ''}</span></div>`);
    }
  }

  document.getElementById('detail-content').innerHTML = lines.join('');
}