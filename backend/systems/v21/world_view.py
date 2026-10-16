"""世界地图视图层：从 NPC 城市的 IndustryEconomy.last_report 派生脱敏字段。

参数分层：脱敏字段基于 NPC 城市级数据派生，与玩家城市的同源。
visibility_level 由 DiplomacySystem 决定：陌生人/认识/友好/盟友/完全开放。
"""
from typing import Optional


# 字段标签（前端显示用）
FIELD_LABELS = {
    'name_cn': '城市名',
    'archetype': '类型',
    'month': '当前月份',
    'population': '总人口',
    'labor_force': '劳动力',
    'employment': '7 行业就业',
    'unemployment_rate': '失业率',
    'K_over_N': 'K/N（承载/人口）',
    'L': 'L 宜居度',
    'food_production_units': '粮食月产（吨）',
    'material_production_units': '原料月产（吨）',
    'stockpile_food_units': '粮食库存（吨）',
    'stockpile_material_units': '原料库存（吨）',
    'monthly_trade_volume_cents': '月贸易额（分）',
    'sales_cents': '七行业月营业额',
    'wages_cents': '七行业工资义务',
    'paid_wages_cents': '七行业实付工资',
    'wage_arrears_cents': '累计未偿工资',
    'government_cash_cents': '政府现金',
    'cash_months': 'GOV 现金覆盖月数',
}


def get_city_view(city, visibility: int, relation=None) -> dict:
    """从 IndustryEconomy 的 last_report + scenario 派生脱敏字段。"""
    if city is None:
        return {'error': '城市不存在'}
    report = city.last_report or {}
    sectors = report.get('sectors', {})
    base = {
        'id': next((c.id for c in city.scenario.get('_npc_id_ref', []) if False), ''),  # placeholder
        'name_cn': '',  # 由调用方填充（来自 CityNode）
        'archetype': '',  # 由调用方填充
        'month': city.month,
    }
    if visibility >= 0:
        base['population_g'] = sum(city.population.values()) // 1000
    if visibility >= 1:
        base['population_g'] = sum(city.population.values()) // 100
    if visibility >= 2:
        base.update({
            'labor_force': report.get('labor_force', city.labor_force),
            'unemployment_rate': round(report.get('unemployment', 0) / max(1, city.labor_force), 3),
            'employment': {i: report.get('employment', {}).get(i, city.employment().get(i, 0))
                          for i in ('AGR', 'IND', 'SRV', 'CHU', 'BNK', 'MAG', 'GOV')},
            'sales_cents': report.get('sales_cents', 0),
            'government_cash_cents': city.cash.get('GOV', 0),
        })
    if visibility >= 3:
        base.update({
            'wages_cents': report.get('wages_cents', 0),
            'paid_wages_cents': report.get('paid_wages_cents', 0),
            'wage_arrears_cents': report.get('wage_arrears_cents', 0),
            'food_production_units': report.get('food_production_units', 0),
            'material_production_units': report.get('material_production_units', 0),
        })
    if visibility >= 4:
        base.update({
            'stockpile_food_units': report.get('stockpile_food_units', 0),
            'stockpile_material_units': report.get('stockpile_material_units', 0),
            'monthly_trade_volume_cents': report.get('monthly_trade_volume_cents', 0),
            'cash_months': report.get('cash_months', 0),
        })
    if relation is not None:
        base['relation'] = {
            'friendliness': relation.friendliness,
            'last_event': relation.last_event,
        }
        base['visibility'] = visibility
    return base


def full_city_payload(city_node, city, visibility: int, relation) -> dict:
    """完整城市详情（含 city_node 元信息 + 脱敏视图）。"""
    if city is None:
        return {'error': '城市不存在'}
    view = get_city_view(city, visibility, relation)
    view['id'] = city_node.id
    view['name_cn'] = city_node.name_cn
    view['archetype'] = city_node.archetype
    view['x'] = city_node.x
    view['y'] = city_node.y
    view['is_player'] = city_node.is_player
    view['visibility_label'] = VISIBILITY_LABELS[visibility]
    return view


VISIBILITY_LABELS = {
    0: '陌生人',
    1: '认识',
    2: '友好',
    3: '盟友',
    4: '完全开放',
}


def ai_log_view(city, limit: int = 12) -> list[dict]:
    """从 NpcCityEngine.ai_log 取最近 N 条 AI 动作。"""
    return []  # 实际由 NpcCityEngine 注入