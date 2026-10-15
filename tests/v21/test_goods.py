"""V2.1 阶段 5 商品目录验收测试（2026-10-15）。

【验收清单】（V2.1 §4.1 + §4.2）
- [x] 17 类商品完整定义
- [x] GoodCategory 枚举 17 值
- [x] ShipmentMode 枚举 3 值（instant/deferrable/transit）
- [x] SettlementTrigger 枚举 3 值（on_payment/on_delivery/on_use）
- [x] GoodDefinition frozen dataclass 全部字段
- [x] GOOD_DEFINITIONS 字典完整性
- [x] REQUIRED_17_CATEGORIES 完整覆盖
- [x] 万物可交易技术含义（is_real_good/is_point_like/is_service/is_property）
- [x] JSON 配置字段一致
- [x] 实物 vs 点数 vs 服务 vs 产权分类正确
"""
import pytest

from backend.systems.v21.goods import (
    GoodCategory, ShipmentMode, SettlementTrigger,
    GoodDefinition, GOOD_DEFINITIONS, REQUIRED_17_CATEGORIES,
    assert_catalog_complete, good_ids, get_good,
)
from backend.systems.v21.loader import load_goods_config


# ===== A. 枚举完整性（4 项）=====

class TestGoodsEnums:
    """V2.1 §4.1 枚举完整性。"""

    def test_good_category_has_17_values(self):
        """阶段 5：GoodCategory 必须有 17 类商品（V2.1 §4.1）。"""
        assert len(GoodCategory) == 17
        # 验证 §4.1 必含分类
        required_ids = {'food', 'fuel', 'ore', 'steel', 'goods', 'logistics',
                        'services', 'financial_service', 'education', 'research',
                        'magic', 'faith', 'civic', 'magic_material',
                        'property_title', 'construction', 'security_service'}
        defined_ids = {c.value for c in GoodCategory}
        assert defined_ids == required_ids

    def test_shipment_mode_has_3_values(self):
        """阶段 5：ShipmentMode 3 值（instant/deferrable/transit）。"""
        assert ShipmentMode.INSTANT.value == 'instant'
        assert ShipmentMode.DEFERRABLE.value == 'deferrable'
        assert ShipmentMode.TRANSIT.value == 'transit'

    def test_settlement_trigger_has_3_values(self):
        """阶段 5：SettlementTrigger 3 值（on_payment/on_delivery/on_use）。"""
        assert SettlementTrigger.ON_PAYMENT.value == 'on_payment'
        assert SettlementTrigger.ON_DELIVERY.value == 'on_delivery'
        assert SettlementTrigger.ON_USE.value == 'on_use'

    def test_required_17_categories_complete(self):
        """阶段 5：REQUIRED_17_CATEGORIES 必须覆盖全部 17 类。"""
        assert len(REQUIRED_17_CATEGORIES) == 17
        # 每类必须有定义
        for cat in REQUIRED_17_CATEGORIES:
            matching = [g for g in GOOD_DEFINITIONS.values() if g.category == cat]
            assert len(matching) >= 1, f"商品目录缺 {cat.value}"


# ===== B. GoodDefinition 数据类（5 项）=====

class TestGoodDefinitionClass:
    """V2.1 §4.1 GoodDefinition 数据类。"""

    def test_construction_with_all_fields(self):
        """阶段 5：GoodDefinition 可构造全部 12 个字段。"""
        g = GoodDefinition(
            good_id='TEST', category=GoodCategory.GOODS, unit='吨',
            base_price_cents=100_00, stockable=True,
            shipment_mode=ShipmentMode.TRANSIT, deliverable_location='either',
            recipe_inputs={'STEEL': 0.5}, perish_rate=0.0,
            contract_transfer_rule='standard',
            settlement_trigger=SettlementTrigger.ON_DELIVERY,
            technological_constraints=('iron_required',),
            notes='test',
        )
        assert g.good_id == 'TEST'
        assert g.category == GoodCategory.GOODS
        assert g.recipe_inputs == {'STEEL': 0.5}
        assert g.technological_constraints == ('iron_required',)

    def test_definition_is_frozen(self):
        """阶段 5：GoodDefinition 是 frozen dataclass。"""
        g = get_good('FOOD')
        with pytest.raises((AttributeError, Exception)):
            g.base_price_cents = 99999  # frozen 拒改

    def test_is_real_good_for_physicals(self):
        """阶段 5：实物商品 is_real_good=True（V2.1 §4.2a）。"""
        assert get_good('FOOD').is_real_good is True
        assert get_good('FUEL').is_real_good is True
        assert get_good('ORE').is_real_good is True
        assert get_good('STEEL').is_real_good is True
        assert get_good('GOODS').is_real_good is True
        assert get_good('MAGIC_MATERIAL').is_real_good is True

    def test_is_point_like_for_instant_points(self):
        """阶段 5：可储点数类 is_point_like=True（V2.1 §4.2b）。"""
        # RESEARCH/MAGIC/FAITH/CIVIC 都是不可储 + instant
        assert get_good('RESEARCH').is_point_like is True
        assert get_good('MAGIC').is_point_like is True
        assert get_good('FAITH').is_point_like is True
        assert get_good('CIVIC').is_point_like is True

    def test_is_service_for_service_categories(self):
        """阶段 5：服务类 is_service=True（V2.1 §4.2c）。"""
        assert get_good('SERVICES').is_service is True
        assert get_good('FINANCIAL_SERVICE').is_service is True
        assert get_good('EDUCATION').is_service is True
        assert get_good('SECURITY_SERVICE').is_service is True
        assert get_good('CONSTRUCTION').is_service is True
        # 非服务类
        assert get_good('FOOD').is_service is False
        assert get_good('STEEL').is_service is False


# ===== C. GOOD_DEFINITIONS 字典（5 项）=====

class TestGoodsCatalog:
    """V2.1 §4.1 GOOD_DEFINITIONS 商品目录。"""

    def test_catalog_has_17_entries(self):
        """阶段 5：目录必须有 17 条商品。"""
        assert len(GOOD_DEFINITIONS) == 17

    def test_catalog_complete_validation_passes(self):
        """阶段 5：assert_catalog_complete() 校验通过。"""
        assert_catalog_complete()  # 不抛异常

    def test_all_real_goods_have_transit_or_deferrable(self):
        """阶段 5：实物必须 transit/deferrable（V2.1 §4.2a 必须按运输交付）。"""
        real_good_ids = ['FOOD', 'FUEL', 'ORE', 'STEEL', 'GOODS', 'MAGIC_MATERIAL']
        for gid in real_good_ids:
            mode = get_good(gid).shipment_mode
            assert mode in (ShipmentMode.TRANSIT, ShipmentMode.DEFERRABLE), \
                f'{gid} 是实物但 shipment_mode={mode}'

    def test_property_title_has_property_cross_city_rule(self):
        """阶段 5：产权类 contract_transfer_rule=property_cross_city（V2.1 §4.2d）。"""
        prop = get_good('PROPERTY_TITLE')
        assert prop.contract_transfer_rule == 'property_cross_city'
        assert prop.is_property is True

    def test_security_service_must_be_local(self):
        """阶段 5：安全服务只能本地提供（V2.1 §4.2c 服务必须按地点兑现）。"""
        sec = get_good('SECURITY_SERVICE')
        assert sec.deliverable_location == 'local'

    def test_construction_is_local_only(self):
        """阶段 5：建筑施工只能本地履约（V2.1 §4.2f）。"""
        const = get_good('CONSTRUCTION')
        assert const.deliverable_location == 'local'
        assert const.contract_transfer_rule == 'local_only'


# ===== D. 工具函数（3 项）=====

class TestGoodsHelpers:
    """V2.1 §4.1 工具函数。"""

    def test_get_good_returns_definition(self):
        """阶段 5：get_good() 返回 GoodDefinition。"""
        g = get_good('FOOD')
        assert g.good_id == 'FOOD'
        assert g.category == GoodCategory.FOOD

    def test_get_good_missing_raises(self):
        """阶段 5：get_good() 不存在抛 KeyError。"""
        with pytest.raises(KeyError, match='商品不存在'):
            get_good('NOPE')

    def test_good_ids_returns_all_keys(self):
        """阶段 5：good_ids() 返回 17 个 good_id。"""
        ids = good_ids()
        assert len(ids) == 17
        assert 'FOOD' in ids
        assert 'STEEL' in ids
        assert 'PROPERTY_TITLE' in ids


# ===== E. JSON 配置（3 项）=====

class TestGoodsJsonConfig:
    """V2.1 §4.1 goods.json 配置。"""

    def test_json_loads(self):
        """阶段 5：goods.json 可加载。"""
        config = load_goods_config()
        assert config['_meta']['version'] == '2.1.0'
        assert '§4.1' in config['_meta']['spec']

    def test_json_has_17_categories(self):
        """阶段 5：goods.json 必须有 17 个 categories。"""
        config = load_goods_config()
        assert len(config['categories']) == 17

    def test_json_field_consistency(self):
        """阶段 5：JSON 字段与 GOOD_DEFINITIONS 字段一致（双源一致）。"""
        config = load_goods_config()
        json_ids = {c['id'] for c in config['categories']}
        py_ids = set(GOOD_DEFINITIONS.keys())
        assert json_ids == py_ids

        # 抽查 FOOD 的 base_price_cents 一致
        food_json = next(c for c in config['categories'] if c['id'] == 'FOOD')
        food_py = GOOD_DEFINITIONS['FOOD']
        assert food_json['base_price_cents'] == food_py.base_price_cents
        assert food_json['unit'] == food_py.unit
        assert food_json['stockable'] == food_py.stockable
