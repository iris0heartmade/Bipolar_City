"""AI 市长决策逻辑：每座 NPC 城市根据偏好 + 当前状态自动生成月度市政动作。

决策模型（综合指数驱动的主动决策）：
  综合指数 = 0.40·就业得分 + 0.30·财政得分 + 0.20·欠薪得分 + 0.10·人口得分
  各维度得分根据目标差距与偏好动态计算。

AI 月度流程：
  1. 评估当前指标（财政、就业、欠薪、原料、人口、营业额）
  2. 计算综合指数与各维度得分
  3. 候选动作生成（财政差调税率、失业高发补贴、原料低采购、利润异常限裁等）
  4. 按偏好加权打分排序
  5. 预算约束（GOV 保留 min_cash_months 月必要支出）
  6. 选 1-2 个最可行动作执行
  7. 写入 self.city.ai_commands 历史（玩家可读）

AI 边界：
  允许：调税率（含 cycle 调整）、补贴、救济、雇/裁公务员、限招、限裁、采购原料
  禁止：调基础参数（scenario 结构参数）、跨城资金流、建设（M4 后续）、借贷（M10 后续）
"""
import math
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional

from backend.systems.v21.mayor_preferences import MayorPreferences
from backend.systems.v21.industry_economy import IndustryEconomy
from backend.systems.v21.industry_scenario import change_control, validate_scenario


# 调税率的步长（每动作）
_TAX_STEP = 0.02
# 综合指数权重
_W_EMPLOYMENT = 0.40
_W_FINANCE = 0.30
_W_ARREARS = 0.20
_W_POPULATION = 0.10


@dataclass
class MayorAction:
    """单个候选动作（决策前评估用）。"""
    kind: str            # 'subsidy_industry' / 'pardon_welfare' / 'hire_civil' /
                        # 'limit_layoff' / 'limit_hire' / 'purchase_external' /
                        # 'tax_adjust' / 'skip'
    industry: Optional[str] = None
    cash_cr: float = 0.0
    people: int = 0
    months: int = 1
    target_value: float = 0.0  # 用于 tax_adjust
    priority: float = 0.0
    reason: str = ''

    def to_execution(self) -> tuple[str, dict]:
        """转换为 city.mayor_command / purchase_external 调用参数。"""
        if self.kind == 'tax_adjust':
            return ('tax_adjust', {'new_rate': self.target_value})
        params = {'cash_cr': self.cash_cr}
        if self.industry is not None:
            params['industry'] = self.industry
        if self.people:
            params['people'] = self.people
        if self.months:
            params['months'] = self.months
        params['note'] = f'[AI] {self.reason}'
        return (self.kind, params)


class AiMayor:
    """单 NPC 城市的 AI 市长；持有偏好 + 引用 IndustryEconomy。"""
    
    def __init__(self, city: IndustryEconomy, prefs: MayorPreferences):
        if not isinstance(city, IndustryEconomy):
            raise ValueError('city 必须是 IndustryEconomy 实例')
        if not isinstance(prefs, MayorPreferences):
            raise ValueError('prefs 必须是 MayorPreferences 实例')
        self.city = city
        self.prefs = prefs
    
    # ============ 评估 ============
    def _evaluate(self) -> dict:
        """读取最近月报，计算各维度得分（0-1）。"""
        report = self.city.last_report or {}
        employment = self.city.employment()
        total_emp = sum(employment.values())
        labor_force = max(1, self.city.labor_force)
        unemployment_rate = 1.0 - total_emp / labor_force
        wages_due = report.get('wages_cents', 0)
        wages_paid = report.get('paid_wages_cents', 0)
        arrears = max(0, wages_due - wages_paid)
        arrears_ratio = arrears / max(1, wages_due)
        gov_cash = self.city.cash.get('GOV', 0)
        # 必要支出约等于当月工资 + 政府月采购
        monthly_cost = self._estimate_monthly_cost()
        cash_months = gov_cash / max(1, monthly_cost)
        population = sum(self.city.population.values())
        labor_force_count = self.city.labor_force
        return {
            'unemployment_rate': unemployment_rate,
            'arrears_ratio': arrears_ratio,
            'gov_cash': gov_cash,
            'cash_months': cash_months,
            'monthly_cost': monthly_cost,
            'population': population,
            'labor_force': labor_force_count,
            'report': report,
            'employment': employment,
            'current_tax_rate': self.city.scenario['city'].get('profit_tax_multiplier', 1.0),
            'external_raw': self.city.external_raw,
        }
    
    def _estimate_monthly_cost(self) -> float:
        """估算月必要支出（工资 + 政府月采购）。"""
        wages = sum(employment * self.city.nominal_wages.get(i, 0)
                    for i, employment in self.city.employment().items())
        gov_purchase = self.city.cr(self.city.scenario['city'].get('government_purchases_cr', 0))
        return wages + gov_purchase
    
    def _composite_index(self, profile: dict) -> float:
        """综合指数 0-1。"""
        # 就业得分：越接近目标失业率越高
        target_unemp = self.prefs.target_unemployment_rate
        if profile['unemployment_rate'] <= target_unemp:
            emp_score = 1.0
        else:
            gap = (profile['unemployment_rate'] - target_unemp) / max(1e-9, 1 - target_unemp)
            emp_score = max(0.0, 1.0 - gap * 1.5)
        # 财政得分：cash_months >= min_cash_months 给满分
        fin_score = min(1.0, profile['cash_months'] / self.prefs.min_cash_months)
        # 欠薪得分：arrears_ratio <= 偏好阈值给满分
        arrears_threshold = self.prefs.target_wage_arrears_ratio
        if profile['arrears_ratio'] <= arrears_threshold:
            arr_score = 1.0
        else:
            arr_score = max(0.0, 1.0 - (profile['arrears_ratio'] - arrears_threshold) * 2)
        # 人口得分：劳动力 ≥ 开局规模 90% 给满分
        pop_score = min(1.0, profile['labor_force'] / max(1, profile['population'] * 0.55))
        composite = (_W_EMPLOYMENT * emp_score + _W_FINANCE * fin_score
                     + _W_ARREARS * arr_score + _W_POPULATION * pop_score)
        return composite
    
    # ============ 候选动作生成 ============
    def _generate_candidates(self, profile: dict) -> list[MayorAction]:
        cands: list[MayorAction] = []
        # 1. 财政差：调税率（向偏好靠拢，步长 0.02/次）
        if profile['cash_months'] < self.prefs.min_cash_months * 0.8:
            target = self.prefs.tax_rate_preference
            current = profile['current_tax_rate']
            if abs(target - current) > _TAX_STEP / 2:
                step = _TAX_STEP if target > current else -_TAX_STEP
                new_rate = current + step
                new_rate = max(0.0, min(1.0, new_rate))
                cands.append(MayorAction(
                    kind='tax_adjust', target_value=round(new_rate, 4),
                    priority=0.8, reason='GOV 现金偏低，向偏好税率靠拢',
                ))
        # 2. 失业率高：行业补贴（按 industry_focus 加权）
        if profile['unemployment_rate'] > self.prefs.target_unemployment_rate + 0.05:
            subsidy_budget = max(500, profile['monthly_cost'] * 0.1)
            focus_industries = sorted(
                self.prefs.industry_focus.items(), key=lambda x: -x[1])
            for industry, weight in focus_industries[:3]:
                if weight >= 1.0 and industry != 'GOV':
                    cands.append(MayorAction(
                        kind='subsidy_industry', industry=industry,
                        cash_cr=round(subsidy_budget / 3 / 100, 2) * 100,
                        priority=0.6 * weight, reason=f'失业高，按行业偏好补贴 {industry}',
                    ))
                    break
        # 3. 失业率高且福利倾向 lavish：发救济
        if (profile['unemployment_rate'] > self.prefs.target_unemployment_rate + 0.1
                and self.prefs.welfare_stance == 'lavish'):
            cands.append(MayorAction(
                kind='pardon_welfare',
                cash_cr=round(profile['monthly_cost'] * 0.05 / 100, 2) * 100,
                priority=0.5, reason='失业过高，追加救济',
            ))
        # 4. 原料低：采购
        if profile['external_raw'] < 30 * profile['monthly_cost'] / max(1, 10):
            purchase_budget = max(1000, profile['monthly_cost'] * self.prefs.purchase_urgency * 0.2)
            cands.append(MayorAction(
                kind='purchase_external',
                cash_cr=round(purchase_budget / 100, 2) * 100,
                priority=0.7, reason='原料储备偏低，自动补库',
            ))
        # 5. 欠薪过高：发补贴
        if profile['arrears_ratio'] > self.prefs.target_wage_arrears_ratio + 0.3:
            focus_industries = sorted(
                self.prefs.industry_focus.items(), key=lambda x: -x[1])
            for industry, weight in focus_industries[:2]:
                if industry != 'GOV':
                    cands.append(MayorAction(
                        kind='subsidy_industry', industry=industry,
                        cash_cr=round(profile['monthly_cost'] * 0.1 / 100, 2) * 100,
                        priority=0.7, reason=f'欠薪严重，补贴 {industry}',
                    ))
                    break
        # 6. 繁荣期预防过热：限招
        if (profile['cash_months'] > self.prefs.min_cash_months * 2.5
                and profile['unemployment_rate'] < 0.05
                and self.prefs.risk_tolerance < 0.5):
            cands.append(MayorAction(
                kind='limit_hire', industry='IND',
                months=2, priority=0.3,
                reason='繁荣且失业极低，温和限招防过热',
            ))
        return cands
    
    # ============ 预算约束 ============
    def _filter_by_budget(self, cands: list[MayorAction], profile: dict) -> list[MayorAction]:
        """按 GOV 现金余量过滤：必须保留至少 min_cash_months 月储备。"""
        if not cands:
            return cands
        # 按 priority 排序（从高到低）
        cands = sorted(cands, key=lambda a: -a.priority)
        # 累加现金支出直到触发 min_cash_months 下限
        monthly_cost = profile['monthly_cost']
        floor = monthly_cost * self.prefs.min_cash_months
        available = max(0, profile['gov_cash'] - floor)
        accepted: list[MayorAction] = []
        used = 0
        for c in cands:
            cost = c.cash_cr if c.kind != 'tax_adjust' else 0
            if used + cost <= available:
                accepted.append(c)
                used += cost
            if len(accepted) >= 2:
                break
        return accepted
    
    # ============ 执行 ============
    def _execute(self, action: MayorAction) -> Optional[dict]:
        """执行动作并返回记录。"""
        kind, params = action.to_execution()
        if kind == 'tax_adjust':
            try:
                new_scenario = change_control(
                    self.city.scenario, 'city', 'profit_tax_multiplier',
                    action.target_value)
                self.city.scenario = new_scenario
                return {'kind': kind, 'new_rate': action.target_value,
                        'reason': action.reason, 'ok': True}
            except ValueError as exc:
                return {'kind': kind, 'reason': str(exc), 'ok': False}
        elif kind == 'purchase_external':
            cash_cents = int(action.cash_cr * 100)
            if cash_cents <= 0:
                return None
            paid_cash, paid_raw, reason = self.city.purchase_external(
                cash_cents=cash_cents, raw_units=cash_cents / 100.0,
                note=action.reason)
            return {'kind': kind, 'cash_cents': paid_cash, 'raw_units': paid_raw,
                    'reason': action.reason, 'ok': bool(paid_cash or paid_raw)}
        else:
            ok, reason, _ = self.city.mayor_command(kind=kind, **params)
            return {'kind': kind, 'ok': ok, 'reason': reason or action.reason}
    
    def step(self) -> list[dict]:
        """月度决策：评估 → 候选 → 筛选 → 执行。"""
        profile = self._evaluate()
        composite = self._composite_index(profile)
        cands = self._generate_candidates(profile)
        cands = self._filter_by_budget(cands, profile)
        results: list[dict] = []
        for c in cands:
            res = self._execute(c)
            if res is not None:
                results.append(res)
        return results