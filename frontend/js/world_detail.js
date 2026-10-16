// 城市详情渲染：可见字段 + 锁定字段 + 关系 + AI 最近动作 + M26 外交行动 + M28 情报按钮。

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

const DIPLOMACY_ACTIONS = [
  { key: 'trade_agreement', label: '贸易协议', delta: '+1/月' },
  { key: 'aid',             label: '援助',     delta: '▲金额/100' },
  { key: 'immigration_open', label: '开放移民', delta: '+3' },
  { key: 'statement',       label: '公开声明', delta: '+2/月' },
  { key: 'tariff',          label: '征收关税', delta: '-10' },
];

const ESPIONAGE_COST_CR = 2000;

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
    // M26：外交行动按钮（仅 NPC 城市）
    lines.push('<h3>外交行动</h3>');
    lines.push('<div class="actions">');
    for (const action of DIPLOMACY_ACTIONS) {
      lines.push(`<button class="dip-btn" data-action="${action.key}" data-city="${detail.id}">
        <strong>${action.label}</strong><small>${action.delta}</small>
      </button>`);
    }
    lines.push('</div>');
    // 援助金额输入
    lines.push(`<div id="aid-input-wrap" class="aid-input" style="display:none">
      <label>援助金额（CR）<input id="aid-amount" type="number" min="100" step="100" value="500"></label>
      <button id="aid-confirm" class="primary">执行援助</button>
    </div>`);
    lines.push(`<div id="dip-result" class="dip-result"></div>`);
    // 历史
    lines.push(`<div id="dip-history" class="dip-history"></div>`);
    // M28：行业情报按钮（仅 visibility ≥ 3 盟友级）
    const visibility = (detail.relation && detail.visibility !== undefined) ? detail.visibility : 0;
    if (visibility >= 3) {
      lines.push('<h3>行业情报</h3>');
      lines.push('<p class="hint">派间谍获取完整月报（不脱敏）。被发现可能损失友好度。</p>');
      lines.push(`<button class="dip-espionage" id="espionage-btn" data-city="${detail.id}">
        派间谍（成本 ${ESPIONAGE_COST_CR} CR + 风险）
      </button>`);
      lines.push('<div id="espionage-result" class="dip-result"></div>');
    }
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
  // M26：绑定外交按钮
  bindDiplomacyHandlers(detail.id);
  // 加载玩家对该城市的历史
  loadDiplomacyHistory(detail.id);
  // M28：绑定情报按钮
  bindEspionageHandler(detail.id);
}

function bindDiplomacyHandlers(cityId) {
  const buttons = document.querySelectorAll('.dip-btn');
  buttons.forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      const action = btn.dataset.action;
      if (action === 'aid') {
        // 援助需要金额：弹出输入框
        document.getElementById('aid-input-wrap').style.display = 'block';
        document.getElementById('aid-confirm').onclick = () => {
          const amount = Number(document.getElementById('aid-amount').value || 0);
          submitDiplomacyAction(cityId, 'aid', amount);
        };
      } else {
        submitDiplomacyAction(cityId, action, 0);
      }
    });
  });
}

async function submitDiplomacyAction(cityId, actionKey, amountCr) {
  const resultEl = document.getElementById('dip-result');
  resultEl.textContent = '执行中…';
  try {
    const r = await fetch('/api/world/diplomacy_action', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        action_key: actionKey, target_city_id: cityId,
        amount_cr: amountCr, note: '',
      }),
    });
    const data = await r.json();
    if (data.success) {
      const f = data.relation.friendliness;
      const v = data.relation.visibility;
      resultEl.innerHTML = `<span class="ok">✓ 友好度 → ${f}/100（可见等级 ${v}）</span>`;
      // 隐藏 aid 输入框
      const aiWrap = document.getElementById('aid-input-wrap');
      if (aiWrap) aiWrap.style.display = 'none';
      // 重新加载历史
      loadDiplomacyHistory(cityId);
      // 触发外层 refresh（更新地图连线颜色 + 详情面板友好度）
      window.dispatchEvent(new Event('diplomacy-changed'));
    } else {
      resultEl.innerHTML = `<span class="err">✗ 拒绝：${data.reason}</span>`;
    }
  } catch (error) {
    resultEl.innerHTML = `<span class="err">✗ 网络错误：${error.message}</span>`;
  }
}

async function loadDiplomacyHistory(cityId) {
  const histEl = document.getElementById('dip-history');
  if (!histEl) return;
  try {
    const r = await fetch(`/api/world/diplomacy_actions/${cityId}`);
    const data = await r.json();
    if (!data || data.length === 0) {
      histEl.innerHTML = '<p class="hint">暂无外交行动历史</p>';
      return;
    }
    const lines = ['<h3>外交行动历史</h3>'];
    for (const a of data.slice(0, 8)) {
      const sign = a.friendliness_delta >= 0 ? '+' : '';
      const okMark = a.success ? '✓' : '✗';
      lines.push(`<div class="row"><span>${okMark} ${a.action_key}（月 ${a.month}）</span>` +
                 `<span class="value">${sign}${a.friendliness_delta} ${a.success ? '' : '— ' + a.reject_reason}</span></div>`);
    }
    histEl.innerHTML = lines.join('');
  } catch (error) {
    histEl.innerHTML = '';
  }
}

// M28：派间谍按钮绑定
async function bindEspionageHandler(cityId) {
  const btn = document.getElementById('espionage-btn');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const resultEl = document.getElementById('espionage-result');
    if (resultEl) resultEl.textContent = '执行中…';
    try {
      const r = await fetch('/api/world/espionage', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({target_city_id: cityId}),
      });
      const data = await r.json();
      if (data.success) {
        if (resultEl) {
          resultEl.innerHTML = `<span class="ok">✓ 情报获取成功</span>
            <div class="intell-detail">
              <strong>${data.full_report.month ? `月 ${data.full_report.month}` : ''}</strong>
              <p>劳动力 ${data.full_report.labor_force ?? '?'} · 失业率 ${((data.full_report.unemployed ?? 0) / Math.max(1, data.full_report.labor_force ?? 1) * 100).toFixed(1)}%</p>
              <p>GOV 现金 ${(data.full_report.government_cash_cents ?? 0)/100} CR · 累计欠薪 ${(data.full_report.wage_arrears_cents ?? 0)/100} CR</p>
              <p>行业就业：${Object.entries(data.full_report.sectors || {}).map(([k,v]) => `${k}:${v.employed ?? 0}`).join(' / ')}</p>
            </div>`;
        }
      } else {
        if (resultEl) {
          resultEl.innerHTML = `<span class="err">✗ ${data.reason}</span>`;
        }
      }
      // 触发全局刷新（友好度可能变化）
      window.dispatchEvent(new Event('diplomacy-changed'));
    } catch (error) {
      if (resultEl) resultEl.innerHTML = `<span class="err">✗ 网络错误：${error.message}</span>`;
    }
  });
}