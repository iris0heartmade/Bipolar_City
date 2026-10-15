"""V2.1 阶段 6 通胀与世局指标验收测试（2026-10-15）。

【验收清单】（V2.1 §6.1 + §6.2）
- [x] BasketCategory 枚举（5 类）
- [x] DEFAULT_BASKET_WEIGHTS 总和 = 1.0
- [x] BasketWeights 校验
- [x] CityPriceCoefficients r_c,k,t 默认 = 1
- [x] WorldState 初始 I_0=1, C_0=50
- [x] update_world_index 月度更新 I/C
- [x] verify_consistency（C_t == C_0 * I_t）
- [x] compute_Cneed_c_t 加权求和
- [x] JSON 配置（inflation.json）字段一致
"""
import pytest

from backend.systems.v21.inflation import (
    BasketCategory, DEFAULT_BASKET_WEIGHTS,
    DEFAULT_INFLATION_PI, DEFAULT_I_0, DEFAULT_C_0_CR_PER_PERSON,
    BasketWeights, CityPriceCoefficients, WorldState,
    compute_Cneed_c_t,
)
from backend.systems.v21.loader import load_inflation_config


# ===== A. BasketCategory 与默认权重（4 项）=====

class TestBasketWeights:
    """V2.1 §6.2 篮子权重。"""

    def test_basket_category_has_5_values(self):
        """阶段 6：BasketCategory 5 类。"""
        assert len(BasketCategory) == 5
        assert BasketCategory.FOOD.value == 'food'
        assert BasketCategory.INDUSTRIAL.value == 'industrial'
        assert BasketCategory.HOUSING.value == 'housing'
        assert BasketCategory.SERVICES.value == 'services'
        assert BasketCategory.FINANCIAL_EDUCATION.value == 'fin_edu'

    def test_default_basket_weights_sum_to_one(self):
        """阶段 6：V2.1 §6.2 默认权重总和 = 1.0（食品 45/工业 20/住房 20/服务 13/金融教育 2）。"""
        total = sum(DEFAULT_BASKET_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9
        assert DEFAULT_BASKET_WEIGHTS['food'] == 0.45
        assert DEFAULT_BASKET_WEIGHTS['industrial'] == 0.20
        assert DEFAULT_BASKET_WEIGHTS['housing'] == 0.20
        assert DEFAULT_BASKET_WEIGHTS['services'] == 0.13
        assert DEFAULT_BASKET_WEIGHTS['fin_edu'] == 0.02

    def test_basket_weights_custom_validation(self):
        """阶段 6：自定义权重若总和 ≠ 1.0 抛 ValueError。"""
        with pytest.raises(ValueError, match='篮子权重总和'):
            BasketWeights(weights={'food': 0.5, 'industrial': 0.3})  # 总和 0.8

    def test_basket_weights_weight_lookup(self):
        """阶段 6：weight(BasketCategory) 查表。"""
        bw = BasketWeights()
        assert bw.weight(BasketCategory.FOOD) == 0.45
        assert bw.weight(BasketCategory.HOUSING) == 0.20


# ===== B. CityPriceCoefficients（3 项）=====

class TestCityPriceCoefficients:
    """V2.1 §6.2 城市本地相对世界价格系数 r_c,k,t。"""

    def test_default_coefficients_all_one(self):
        """阶段 6：默认 r_c,k = 1（与全球价格一致）。"""
        r = CityPriceCoefficients(city_id='city_a')
        for cat in BasketCategory:
            assert r.r(cat) == 1.0

    def test_custom_coefficients(self):
        """阶段 6：自定义 r_c,k（如某城食品贵 1.2 倍）。"""
        r = CityPriceCoefficients(city_id='city_a', r_food=1.2, r_housing=0.8)
        assert r.r(BasketCategory.FOOD) == 1.2
        assert r.r(BasketCategory.HOUSING) == 0.8
        assert r.r(BasketCategory.INDUSTRIAL) == 1.0

    def test_r_function_maps_correctly(self):
        """阶段 6：r(BasketCategory) 字段映射正确。"""
        r = CityPriceCoefficients(
            city_id='x',
            r_food=1.1, r_industrial=1.2, r_housing=1.3,
            r_services=1.4, r_fin_edu=1.5,
        )
        assert r.r(BasketCategory.FOOD) == 1.1
        assert r.r(BasketCategory.INDUSTRIAL) == 1.2
        assert r.r(BasketCategory.HOUSING) == 1.3
        assert r.r(BasketCategory.SERVICES) == 1.4
        assert r.r(BasketCategory.FINANCIAL_EDUCATION) == 1.5


# ===== C. WorldState（5 项）=====

class TestWorldState:
    """V2.1 §6.1 全球世局状态。"""

    def test_initial_values(self):
        """阶段 6：WorldState 初始 I_0=1, C_0=50, pi=0.003。"""
        ws = WorldState()
        assert ws.global_I == DEFAULT_I_0
        assert ws.global_C == DEFAULT_C_0_CR_PER_PERSON
        assert ws.inflation_pi == DEFAULT_INFLATION_PI
        assert ws.month == 0

    def test_initial_consistency(self):
        """阶段 6：初始 C == C_0 * I（= 50.0 * 1.0 = 50.0）。"""
        ws = WorldState()
        assert ws.verify_consistency()

    def test_update_index_multiplies(self):
        """阶段 6：update_world_index 后 I_t+1 = I_t * (1 + pi_t)。"""
        ws = WorldState()
        ws.inflation_pi = 0.10  # 10%
        ws.update_world_index()
        assert abs(ws.global_I - 1.10) < 1e-9
        assert abs(ws.global_C - 55.0) < 1e-9
        assert ws.month == 1

    def test_update_preserves_consistency(self):
        """阶段 6：update 后 C_t 仍等于 C_0 * I_t（§6.1「存 C 为状态」规则）。"""
        ws = WorldState()
        ws.inflation_pi = 0.05
        ws.update_world_index()
        ws.inflation_pi = 0.03
        ws.update_world_index()
        assert ws.verify_consistency()
        # I_t = 1 * 1.05 * 1.03 = 1.0815
        assert abs(ws.global_I - 1.0815) < 1e-6

    def test_update_extreme_negative_raises(self):
        """阶段 6：通胀率 < -100% 抛 ValueError（§6.1 防极端值）。"""
        ws = WorldState()
        ws.inflation_pi = -2.0  # -200%
        with pytest.raises(ValueError, match='通胀率越界'):
            ws.update_world_index()


# ===== D. compute_Cneed_c_t（4 项）=====

class TestComputeCneed:
    """V2.1 §6.2 Cneed_c,t = C_t * sum_k basket_weight_k * r_c,k,t。"""

    def test_default_city_gets_Ct(self):
        """阶段 6：默认 r_c,k 全 1 时 Cneed = C_t。"""
        r = CityPriceCoefficients(city_id='x')
        assert compute_Cneed_c_t(world_C=50.0, weights=BasketWeights(), r=r) == 50.0

    def test_higher_prices_raise_Cneed(self):
        """阶段 6：r 全 1.2 时 Cneed = 1.2 * C_t。"""
        r = CityPriceCoefficients(
            city_id='x',
            r_food=1.2, r_industrial=1.2, r_housing=1.2,
            r_services=1.2, r_fin_edu=1.2,
        )
        assert compute_Cneed_c_t(world_C=50.0, weights=BasketWeights(), r=r) == 60.0

    def test_weighted_sum_correct(self):
        """阶段 6：加权求和按 5 类权重精确计算。

        world_C=100, r_food=2.0（权重 0.45），r_housing=1.0（权重 0.2），
        其它 r=1.0 → 加权和 = 0.45*2 + 0.2*1 + 0.13*1 + 0.2*1 + 0.02*1 = 0.45*2 + 0.55 = 1.45
        Cneed = 100 * 1.45 = 145
        """
        r = CityPriceCoefficients(city_id='x', r_food=2.0)
        assert compute_Cneed_c_t(world_C=100.0, weights=BasketWeights(), r=r) == 145.0

    def test_zero_r_categories_excluded(self):
        """阶段 6：r=0 时该项不出现在加权和中（边界值）。"""
        r = CityPriceCoefficients(city_id='x', r_food=0.0)
        # 食品权重 0.45 失效 → sum = 0.45*0 + 0.2 + 0.2 + 0.13 + 0.02 = 0.55
        # Cneed = 100 * 0.55 = 55
        assert abs(compute_Cneed_c_t(world_C=100.0, weights=BasketWeights(), r=r) - 55.0) < 1e-9


# ===== E. JSON 配置（3 项）=====

class TestInflationJsonConfig:
    """V2.1 §6 inflation.json 配置。"""

    def test_json_loads(self):
        """阶段 6：inflation.json 可加载。"""
        config = load_inflation_config()
        assert config['_meta']['version'] == '2.1.0'
        assert '§6' in config['_meta']['spec']

    def test_json_world_initial(self):
        """阶段 6：world_initial 字段严格匹配 §6.1。"""
        config = load_inflation_config()
        wi = config['world_initial']
        assert wi['inflation_pi'] == 0.003
        assert wi['global_I_0'] == 1.0
        assert wi['global_C_0_cr_per_person'] == 50.0

    def test_json_wage_params(self):
        """阶段 6：wage_params 严格匹配 §6.3（alpha=0.40, gamma=0.02, cap_up=+0.03, cap_down=-0.02）。"""
        config = load_inflation_config()
        wp = config['wage_params']
        assert wp['alpha'] == 0.40
        assert wp['gamma'] == 0.02
        assert wp['cap_up'] == 0.03
        assert wp['cap_down'] == -0.02

    def test_json_household(self):
        """阶段 9 补：household 字段（pit_rate=0.08, savings_draw_limit=0.10）。"""
        config = load_inflation_config()
        h = config['household']
        assert h['pit_rate_tau_P'] == 0.08
        assert h['savings_draw_limit'] == 0.10
        assert h['agricultural_wage_pit_exempt'] is True

    def test_json_basket_weights_sum_one(self):
        """阶段 6：JSON basket_weights 总和 = 1.0。"""
        config = load_inflation_config()
        bw = config['basket_weights']
        total = sum(bw.values())
        assert abs(total - 1.0) < 1e-9
