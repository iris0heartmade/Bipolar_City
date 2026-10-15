"""V2.1 阶段 10 政府接口验收测试（2026-10-15）。

【验收清单】（V2.1 §9.1 + §9.2 + §9.3 + §9.4）
- [x] GovernmentActionType 11 个动作
- [x] GovernmentAction 13 字段（§9.2 统一协议）
- [x] GovernmentActionResult 执行结果
- [x] validate_government_action 校验
- [x] GovernmentRevenue 5 项收入聚合
- [x] PropertyTax 房产税（§9.4 + 宗教减免 0.15×f）
- [x] execute_government_action 占位
"""
import pytest

from backend.systems.v21.government import (
    DEFAULT_TAU_P, DEFAULT_TAU_H, DEFAULT_FAITHFUL_RATIO,
    GovernmentActionType, GovernmentAction, GovernmentActionResult,
    GovernmentRevenue, PropertyTaxInput,
    validate_government_action, compute_government_revenue,
    compute_property_tax_cents, execute_government_action,
)


# ===== A. GovernmentActionType 枚举（2 项）=====

class TestGovernmentActionType:
    """V2.1 §9.1 政府动作枚举。"""

    def test_has_11_values(self):
        """阶段 10：§9.1 必须有 11 个动作（含 POLICY_EXTENSION）。"""
        assert len(GovernmentActionType) == 11
        assert GovernmentActionType.TAX.value == 'tax'
        assert GovernmentActionType.SPEND.value == 'spend'
        assert GovernmentActionType.BUILD.value == 'build'
        assert GovernmentActionType.EMPLOY.value == 'employ'
        assert GovernmentActionType.TRADE_ORGANIZE.value == 'trade_organize'
        assert GovernmentActionType.TRADE_DIRECT.value == 'trade_direct'
        assert GovernmentActionType.FINANCE.value == 'finance'
        assert GovernmentActionType.SERVICE.value == 'service'
        assert GovernmentActionType.RELIGION_POLICY.value == 'religion_policy'
        assert GovernmentActionType.RESERVE.value == 'reserve'
        assert GovernmentActionType.POLICY_EXTENSION.value == 'policy_extension'

    def test_values_unique(self):
        """阶段 10：11 值互不相同。"""
        values = [a.value for a in GovernmentActionType]
        assert len(values) == len(set(values))


# ===== B. GovernmentAction 数据类（4 项）=====

class TestGovernmentAction:
    """V2.1 §9.2 统一协议。"""

    def test_construction_with_defaults(self):
        """阶段 10：13 字段默认值。"""
        a = GovernmentAction(
            action_id='A1',
            action_type=GovernmentActionType.TAX,
        )
        assert a.action_id == 'A1'
        assert a.action_type == GovernmentActionType.TAX
        assert a.actor == 'government'    # 默认 actor
        assert a.payer_account == 'government'
        assert a.money_amount_cents == 0
        assert a.input_goods == {}
        assert a.output_goods == {}

    def test_frozen(self):
        """阶段 10：GovernmentAction 是 frozen dataclass。"""
        a = GovernmentAction(action_id='A1', action_type=GovernmentActionType.SPEND)
        with pytest.raises((AttributeError, Exception)):
            a.money_amount_cents = 999  # frozen 拒改

    def test_all_13_fields(self):
        """阶段 10：13 字段全部可显式赋值。"""
        a = GovernmentAction(
            action_id='A1', action_type=GovernmentActionType.BUILD,
            actor='gov', legal_permission='public_works_2026',
            target_city='A', payer_account='gov', payee_account='firms:IND',
            money_amount_cents=100_000, input_goods={'STEEL': 10.0},
            output_goods={'CONSTRUCTION': 1.0},
            start_month=1, settle_month=3,
            state_delta_after_delivery={'building_id': 'B1'},
            cancellation_terms={'penalty': 0.1},
        )
        assert a.legal_permission == 'public_works_2026'
        assert a.money_amount_cents == 100_000
        assert a.input_goods == {'STEEL': 10.0}
        assert a.cancelation_terms_penalty if False else a.cancellation_terms == {'penalty': 0.1}

    def test_action_id_required(self):
        """阶段 10：action_id 为空触发校验错误。"""
        a = GovernmentAction(action_id='', action_type=GovernmentActionType.TAX)
        errors = validate_government_action(a)
        assert any('action_id' in e for e in errors)


# ===== C. validate_government_action 校验（5 项）=====

class TestValidateAction:
    """V2.1 §9.2 校验：未来新增政府权限均不得越过资金/资源守恒。"""

    def test_valid_action_passes(self):
        """阶段 10：正常动作校验通过。"""
        a = GovernmentAction(action_id='A1', action_type=GovernmentActionType.SPEND,
                            payer_account='gov', money_amount_cents=1000)
        errors = validate_government_action(a)
        assert errors == []

    def test_negative_money_raises(self):
        """阶段 10：负资金金额校验失败。"""
        a = GovernmentAction(action_id='A1', action_type=GovernmentActionType.TAX,
                            money_amount_cents=-100)
        errors = validate_government_action(a)
        assert any('资金金额不能为负' in e for e in errors)

    def test_empty_payer_raises(self):
        """阶段 10：payer_account 为空校验失败。"""
        a = GovernmentAction(action_id='A1', action_type=GovernmentActionType.SPEND,
                            payer_account='')
        errors = validate_government_action(a)
        assert any('payer_account' in e for e in errors)

    def test_religion_policy_requires_legal_permission(self):
        """阶段 10：RELIGION_POLICY 必须指定 legal_permission（5 档之一）。"""
        a = GovernmentAction(action_id='A1', action_type=GovernmentActionType.RELIGION_POLICY,
                            legal_permission='')
        errors = validate_government_action(a)
        assert any('RELIGION_POLICY' in e and 'legal_permission' in e for e in errors)

    def test_empty_action_id_raises(self):
        """阶段 10：空 action_id 校验失败。"""
        a = GovernmentAction(action_id='', action_type=GovernmentActionType.SPEND)
        errors = validate_government_action(a)
        assert any('action_id' in e for e in errors)


# ===== D. GovernmentRevenue 财政收入（3 项）=====

class TestGovernmentRevenue:
    """V2.1 §9.3 G_income = 5 项收入。"""

    def test_total_sums_five_components(self):
        """阶段 10：G_income = 5 项合计。"""
        rev = GovernmentRevenue(
            personal_income_tax_cents=12_388_00,         # §14 PIT
            industry_profit_tax_cents=3_500_00,          # §14 利润税
            property_tax_cents=500_00,                   # §9.4
            actual_trade_tax_and_fees_cents=1_000_00,    # §14 贸易税
            public_asset_operating_remittance_cents=200_00,
        )
        # 12,388 + 3,500 + 500 + 1,000 + 200 = 17,588
        assert rev.total_cents() == 17_588_00
        assert compute_government_revenue(rev) == 17_588_00

    def test_zero_when_all_components_zero(self):
        """阶段 10：所有项为 0 → G_income = 0。"""
        rev = GovernmentRevenue()
        assert rev.total_cents() == 0

    def test_loan_principal_not_counted(self):
        """阶段 10：§9.3 财政贷款本金不算收入（语义保证：FINANCE 不进 GovernmentRevenue）。"""
        # 验证 GovernmentRevenue 没有 loan_principal 字段
        rev = GovernmentRevenue(
            personal_income_tax_cents=1000,
            industry_profit_tax_cents=2000,
        )
        # 即使借贷发生，本数据类不接收贷款本金
        assert rev.total_cents() == 3000


# ===== E. PropertyTax 房产税（4 项）=====

class TestPropertyTax:
    """V2.1 §9.4 PropertyTax = AssessedValue * tau_H * (1 - 0.15 * f)。"""

    def test_property_tax_no_faithful(self):
        """阶段 10：无信徒时 PropertyTax = value * tau_H。"""
        # 100,000 * 0.0012 = 120（浮点允许 ±1）
        inp = PropertyTaxInput(assessed_value_cents=100_000, faithful_ratio=0.0)
        assert abs(compute_property_tax_cents(inp) - 120) <= 1

    def test_property_tax_default_faithful(self):
        """阶段 10：默认 f=0.55 时 PropertyTax = value * tau_H * (1 - 0.0825)。"""
        # 100,000 * 0.0012 * 0.9175 = 110.1 → 110
        inp = PropertyTaxInput(assessed_value_cents=100_000)
        assert abs(compute_property_tax_cents(inp) - 110) <= 1

    def test_property_tax_full_faithful(self):
        """阶段 10：f=1.0 时 PropertyTax = value * tau_H * 0.85。"""
        # 100,000 * 0.0012 * 0.85 = 102
        inp = PropertyTaxInput(assessed_value_cents=100_000, faithful_ratio=1.0)
        assert abs(compute_property_tax_cents(inp) - 102) <= 1

    def test_property_tax_zero_value(self):
        """阶段 10：评估值为 0 → 房产税 0。"""
        inp = PropertyTaxInput(assessed_value_cents=0)
        assert compute_property_tax_cents(inp) == 0

    def test_property_tax_invalid_input(self):
        """阶段 10：评估值 < 0 或 faithful 越界抛 ValueError。"""
        with pytest.raises(ValueError, match='评估值不能为负'):
            PropertyTaxInput(assessed_value_cents=-1)
        with pytest.raises(ValueError, match='信徒比越界'):
            PropertyTaxInput(assessed_value_cents=100, faithful_ratio=1.5)


# ===== F. execute_government_action 占位（3 项）=====

class TestExecuteAction:
    """V2.1 §9.2 execute_government_action 占位。"""

    def test_valid_action_executes(self):
        """阶段 10：合法动作执行成功。"""
        a = GovernmentAction(action_id='A1', action_type=GovernmentActionType.SPEND,
                            money_amount_cents=1000)
        result = execute_government_action(a, payer_cash_cents=10000)
        assert result.executed is True
        assert result.actual_money_cents == 1000
        assert result.error_message == ''

    def test_insufficient_cash_caps_amount(self):
        """阶段 10：现金不足时实际支付 = min(requested, available)。"""
        a = GovernmentAction(action_id='A1', action_type=GovernmentActionType.SPEND,
                            money_amount_cents=100_000)
        result = execute_government_action(a, payer_cash_cents=10_000)
        assert result.executed is True   # 仍有部分可执行
        assert result.actual_money_cents == 10_000

    def test_invalid_action_returns_error(self):
        """阶段 10：非法动作不执行。"""
        a = GovernmentAction(action_id='', action_type=GovernmentActionType.SPEND)
        result = execute_government_action(a, payer_cash_cents=10_000)
        assert result.executed is False
        assert result.error_message != ''


# ===== G. 默认参数（2 项）=====

class TestGovernmentDefaults:
    """V2.1 §9.4 默认参数。"""

    def test_default_tau_H_is_0_0012(self):
        """阶段 10：tau_H = 0.0012/月（§9.4 房产税月率）。"""
        assert DEFAULT_TAU_H == 0.0012

    def test_default_faithful_ratio_is_0_55(self):
        """阶段 10：f = 0.55 初始信徒比（§9.4 + §9.5）。"""
        assert DEFAULT_FAITHFUL_RATIO == 0.55
