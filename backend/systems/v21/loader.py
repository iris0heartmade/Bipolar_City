"""V2.1 阶段 1：JSON 加载器。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 配置 JSON 加载。

仅承载阶段 1 的 `industries.json` 加载。其它配置（sectors_v2.json、
cohorts_v2.json、reference_city_v2.json 等）由后续阶段提供对应的 loader。
"""
import json
from pathlib import Path
from typing import Any

# V2.1 配置根目录
ECONOMY_V2_DIRNAME = 'economy_v2'


def find_project_root(start: Path | None = None) -> Path:
    """从 start 向上查找项目根（含 data/ 目录）。"""
    start = (start or Path(__file__).resolve()).parent
    for candidate in (start, *start.parents):
        if (candidate / 'data').is_dir():
            return candidate
    # 找不到就 fallback 到本文件上 4 级（backend/systems/v21/loader.py）
    return Path(__file__).resolve().parents[3]


PROJECT_ROOT = find_project_root()
ECONOMY_V2_DIR = PROJECT_ROOT / 'data' / ECONOMY_V2_DIRNAME


def read_json(path: Path) -> Any:
    """读取 JSON，兼容 UTF-8 BOM（Windows 编辑器可能写入 BOM）。"""
    return json.loads(path.read_text(encoding='utf-8-sig'))


def load_industries_config(path: Path | None = None) -> dict[str, Any]:
    """加载 `data/economy_v2/industries.json`。

    返回的 dict 结构：
    {
        '_meta': {...},
        'industries': {
            'AGR': {'name_cn': '农业', 'job_capacity_max': 300, 'wage_tier': 'D', ...},
            ...
        }
    }
    """
    p = path or (ECONOMY_V2_DIR / 'industries.json')
    if not p.exists():
        raise FileNotFoundError(
            f'V2.1 行业配置文件不存在：{p}\n'
            f'请先创建 data/economy_v2/industries.json'
        )
    return read_json(p)


def load_cohorts_config(path: Path | None = None) -> dict[str, Any]:
    """加载 `data/economy_v2/cohorts.json`（V2.1 §1.4 + §14.2 + §6.4）。

    返回的 dict 结构：
    {
        '_meta': {...},
        'demographics': {...},
        'pit_rules': {...},
        'initial_savings': {...},
        'cohorts': {
            'NONE': {'name_cn': ..., 'industry_id': None, 'population_P_h': 70, 'employees_E_h': 0},
            'AGR': {'name_cn': ..., 'industry_id': 'AGR', 'population_P_h': 480, 'employees_E_h': 240},
            ...
        }
    }
    """
    p = path or (ECONOMY_V2_DIR / 'cohorts.json')
    if not p.exists():
        raise FileNotFoundError(
            f'V2.1 群体配置文件不存在：{p}\n'
            f'请先创建 data/economy_v2/cohorts.json'
        )
    return read_json(p)


def load_wallets_config(path: Path | None = None) -> dict[str, Any]:
    """加载 `data/economy_v2/wallets.json`（V2.1 §1.5 + §14.12）。

    返回的 dict 结构：
    {
        '_meta': {...},
        'city_id': 'industrial_city',
        'openings': {
            'government': 10000000,           # 分（100,000 CR）
            'church': 2000000,                # 分（20,000 CR，与 CHU 行业共用）
            'ocean': 0,
            'property_pass': 0,
            'firms': {'AGR': 7000000, 'IND': 25000000, ...},
            'cohorts': {'H0': 7000, 'H1': 48000, ...}
        }
    }
    """
    p = path or (ECONOMY_V2_DIR / 'wallets.json')
    if not p.exists():
        raise FileNotFoundError(
            f'V2.1 钱包配置文件不存在：{p}\n'
            f'请先创建 data/economy_v2/wallets.json'
        )
    return read_json(p)


def load_banking_config(path: Path | None = None) -> dict[str, Any]:
    """加载 `data/economy_v2/banking.json`（V2.1 §8 银行与融资）。

    返回：
    {
        '_meta': {...},
        'default_params': {
            'max_loan_to_bank_cash_ratio': 1.0,
            'max_loan_to_borrower_cash_ratio': 5.0,
            'default_interest_rate_monthly': 0.01,
            'max_interest_rate_monthly': 0.10,
            'default_grace_period_months': 12
        },
        'prohibited_purposes': ['emergency_relief'],
        'allowed_purposes': [...],
        'initial_state': {
            'bank_account': 'firms:BNK',
            'bank_opening_cash_cr': 60000,
            'initial_loans': []
        }
    }
    """
    p = path or (ECONOMY_V2_DIR / 'banking.json')
    if not p.exists():
        raise FileNotFoundError(
            f'V2.1 银行配置文件不存在：{p}\n'
            f'请先创建 data/economy_v2/banking.json'
        )
    return read_json(p)


def load_goods_config(path: Path | None = None) -> dict[str, Any]:
    """加载 `data/economy_v2/goods.json`（V2.1 §4.1 商品目录）。

    返回：
    {
        '_meta': {...},
        'categories': [
            {'id': 'FOOD', 'category': 'food', 'unit': '吨',
             'base_price_cents': 10000, 'stockable': True, ...},
            ...
        ]
    }
    """
    p = path or (ECONOMY_V2_DIR / 'goods.json')
    if not p.exists():
        raise FileNotFoundError(
            f'V2.1 商品配置文件不存在：{p}\n'
            f'请先创建 data/economy_v2/goods.json'
        )
    return read_json(p)


def load_inflation_config(path: Path | None = None) -> dict[str, Any]:
    """加载 `data/economy_v2/inflation.json`（V2.1 §6 通胀 + 工资 + 消费）。

    返回：
    {
        '_meta': {...},
        'world_initial': {
            'inflation_pi': 0.003, 'global_I_0': 1.0, 'global_C_0_cr_per_person': 50.0
        },
        'basket_weights': {'food': 0.45, 'industrial': 0.20, 'housing': 0.20, 'services': 0.13, 'fin_edu': 0.02},
        'wage_params': {
            'alpha': 0.40, 'gamma': 0.02, 'cap_up': 0.03, 'cap_down': -0.02,
            'default_policy_modifier': 1.0
        },
        'household': {
            'pit_rate_tau_P': 0.08, 'savings_draw_limit': 0.30,
            'agricultural_wage_pit_exempt': true
        }
    }
    """
    p = path or (ECONOMY_V2_DIR / 'inflation.json')
    if not p.exists():
        raise FileNotFoundError(
            f'V2.1 通胀配置文件不存在：{p}\n'
            f'请先创建 data/economy_v2/inflation.json'
        )
    return read_json(p)


def load_capacity_config(path: Path | None = None) -> dict[str, Any]:
    """加载 `data/economy_v2/capacity.json`（V2.1 §10 K + §11.1 L）。

    返回：
    {
        '_meta': {...},
        'K_params': {
            'lambda': 0.55, 'e_target': 0.90, 'job_use_exp': 0.20,
            'food_per_person': 1.0, 's_base': 0.50, 'k_s': 0.50
        },
        'L_params': {
            'weights': {'h': 0.35, 'a': 0.35, 'v': 0.20, 's': 0.10},
            'h_floor': 0.90, 'h_divisor': 0.45, 'cneed_multiplier': 1.20,
            'target_range': {'L_target_min': 65.0, 'L_target_max': 82.0, ...}
        }
    }
    """
    p = path or (ECONOMY_V2_DIR / 'capacity.json')
    if not p.exists():
        raise FileNotFoundError(
            f'V2.1 承载力配置文件不存在：{p}\n'
            f'请先创建 data/economy_v2/capacity.json'
        )
    return read_json(p)


def load_migration_config(path: Path | None = None) -> dict[str, Any]:
    """加载 `data/economy_v2/migration.json`（V2.1 §11.2 + §11.3 + §11.4 跨城迁移）。

    返回：
    {
        '_meta': {...},
        'migration_params': {
            'rho': 0.01, 'beta': 0.08, 'd0_km': 200.0,
            'outflow_cap_rate': 0.01,
            'default_faithful_ratio': 0.55,
            'default_labor_participation': 0.55
        },
        'initial_protection': {
            'first_month_outflow_max_rate': 0.01,
            'L_initial_diff_max': 18.0,
            'L_target_min': 65.0, 'L_target_max': 82.0
        }
    }
    """
    p = path or (ECONOMY_V2_DIR / 'migration.json')
    if not p.exists():
        raise FileNotFoundError(
            f'V2.1 迁移配置文件不存在：{p}\n'
            f'请先创建 data/economy_v2/migration.json'
        )
    return read_json(p)
