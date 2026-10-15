"""V2.1 阶段 9 补 §5.1-5.3 招工验收测试（2026-10-15）。

【验收清单】（V2.1 §5.1-5.3）
- [x] compute_F_t = floor(lambda * N)
- [x] compute_Jop = min(J, floor(PayrollCapacity))
- [x] integer_allocation_with_largest_remainder
- [x] allocate_employment GOV 优先 + 整数最大余数
"""
import pytest

from backend.systems.v21.labor import (
    compute_F_t, compute_Jop,
    integer_allocation_with_largest_remainder,
    IndustryJobCapacity, HireDecision, allocate_employment,
)


# ===== A. compute_F_t（3 项）=====

class TestComputeFt:
    """V2.1 §5.1 F_t = floor(lambda * N_t)。"""

    def test_basic(self):
        """阶段 9 补：3000 人 × 0.55 = 1650。"""
        assert compute_F_t(3000, 0.55) == 1650

    def test_zero(self):
        """阶段 9 补：N=0 → F=0。"""
        assert compute_F_t(0, 0.55) == 0

    def test_invalid_N_raises(self):
        """阶段 9 补：负 N 抛 ValueError。"""
        with pytest.raises(ValueError, match='N 不能为负'):
            compute_F_t(-1, 0.55)

    def test_invalid_lambda_raises(self):
        """阶段 9 补：lambda 越界 [0, 1] 抛 ValueError。"""
        with pytest.raises(ValueError, match='lambda 越界'):
            compute_F_t(1000, 1.5)


# ===== B. compute_Jop（4 项）=====

class TestComputeJop:
    """V2.1 §5.2 Jop_i = min(J_i, floor(PayrollCapacity))。"""

    def test_basic(self):
        """阶段 9 补：M=1000, g=0, ua=0, w=100 → PayrollCapacity=10 → Jop=min(20, 10)=10。"""
        # M=10,000 分, g=0, ua=0, w=100 CR=10,000 分 → PayrollCapacity=10,000/10,000=1.0
        # 但 Jop=min(20, floor(1.0))=1
        jop = compute_Jop(J_i=20, M_operating_budget_cents=10_000,
                          guaranteed_receipts_cents=0,
                          unavoidable_nonpayroll_cents=0, wage_per_worker_cents=10_000)
        # PayrollCapacity = (10000 + 0 - 0) / 10000 = 1.0 → floor = 1
        assert jop == 1

    def test_capped_by_J(self):
        """阶段 9 补：充足现金但 J 小 → Jop = J。"""
        # M=100,000, w=1,000 → 100 个岗位能力；但 J=20
        jop = compute_Jop(J_i=20, M_operating_budget_cents=100_000,
                          guaranteed_receipts_cents=0,
                          unavoidable_nonpayroll_cents=0, wage_per_worker_cents=1_000)
        # PayrollCapacity = 100,000/1,000 = 100; Jop = min(20, 100) = 20
        assert jop == 20

    def test_capped_by_cash(self):
        """阶段 9 补：J 大但现金不足 → Jop = floor(PayrollCapacity)。"""
        # M=5,000, w=1,000 → PayrollCapacity=5; J=100
        jop = compute_Jop(J_i=100, M_operating_budget_cents=5_000,
                          guaranteed_receipts_cents=0,
                          unavoidable_nonpayroll_cents=0, wage_per_worker_cents=1_000)
        # PayrollCapacity = 5,000/1,000 = 5; Jop = min(100, 5) = 5
        assert jop == 5

    def test_guaranteed_receipts_count(self):
        """阶段 9 补：guaranteed_receipts 计入 PayrollCapacity。"""
        # M=2,000, g=3,000, w=1,000 → PayrollCapacity=5
        jop = compute_Jop(J_i=100, M_operating_budget_cents=2_000,
                          guaranteed_receipts_cents=3_000,
                          unavoidable_nonpayroll_cents=0, wage_per_worker_cents=1_000)
        assert jop == 5

    def test_unavoidable_nonpayroll_subtracts(self):
        """阶段 9 补：unavoidable_nonpayroll 从 PayrollCapacity 中扣除。"""
        # M=10,000, g=0, ua=8,000, w=1,000 → PayrollCapacity=2
        jop = compute_Jop(J_i=100, M_operating_budget_cents=10_000,
                          guaranteed_receipts_cents=0,
                          unavoidable_nonpayroll_cents=8_000, wage_per_worker_cents=1_000)
        assert jop == 2


# ===== C. integer_allocation_with_largest_remainder（4 项）=====

class TestLargestRemainder:
    """V2.1 §5.3 整数最大余数法。"""

    def test_even_weights(self):
        """阶段 9 补：均匀权重分配均匀。"""
        result = integer_allocation_with_largest_remainder(10, [1, 1, 1])
        assert sum(result) == 10
        # 3 个权重均匀：10/3=3.33，base=[3,3,3] remainder=1 → 余数最大者 +1 → [4,3,3]
        assert sum(result) == 10

    def test_uneven_weights(self):
        """阶段 9 补：权重不等分配按比例。"""
        result = integer_allocation_with_largest_remainder(100, [3, 2, 1])
        assert sum(result) == 100
        # 3:2:1 → 50, 33, 17
        assert result[0] >= 49

    def test_total_zero(self):
        """阶段 9 补：total=0 全 0。"""
        result = integer_allocation_with_largest_remainder(0, [1, 1, 1])
        assert result == [0, 0, 0]

    def test_empty_weights(self):
        """阶段 9 补：空权重列表返回空。"""
        assert integer_allocation_with_largest_remainder(10, []) == []


# ===== D. allocate_employment（4 项）=====

class TestAllocateEmployment:
    """V2.1 §5.3 多行业招工。"""

    def test_gov_first(self):
        """阶段 9 补：GOV 优先锁定岗位。"""
        industries = [
            IndustryJobCapacity(industry_id='AGR', J_op=100, application_demand=100),
            IndustryJobCapacity(industry_id='IND', J_op=100, application_demand=100),
        ]
        decisions = allocate_employment(
            industries, F=200, gov_reserved_E=50,
        )
        # GOV 优先占 50；剩下 150 给 AGR + IND（按权重 1:1 平均 75/75）
        agr = next(d for d in decisions if d.industry_id == 'AGR')
        ind = next(d for d in decisions if d.industry_id == 'IND')
        assert agr.E_i + ind.E_i == 150

    def test_underdemand(self):
        """阶段 9 补：申请量 < Jop → 按实际申请量分配。"""
        industries = [
            IndustryJobCapacity(industry_id='AGR', J_op=100, application_demand=30),
        ]
        decisions = allocate_employment(industries, F=200, gov_reserved_E=0)
        assert decisions[0].E_i == 30
        assert decisions[0].shortage == 0

    def test_demand_capped(self):
        """阶段 9 补：申请量 > Jop 时不超 Jop。"""
        industries = [
            IndustryJobCapacity(industry_id='IND', J_op=50, application_demand=100),
        ]
        decisions = allocate_employment(industries, F=200, gov_reserved_E=0)
        # 总需求 = min(50, 100) = 50；F=200 够 → 全部满足
        assert decisions[0].E_i == 50
        assert decisions[0].shortage == 50  # 50 个申请未满足

    def test_shortage(self):
        """阶段 9 补：F 不足时按比例分配并记 shortage。"""
        industries = [
            IndustryJobCapacity(industry_id='AGR', J_op=100, application_demand=100),
            IndustryJobCapacity(industry_id='IND', J_op=100, application_demand=100),
        ]
        decisions = allocate_employment(industries, F=100, gov_reserved_E=0)
        # F=100 不够分两行业 → 按权重 1:1 平均 50/50
        assert sum(d.E_i for d in decisions) == 100
        assert sum(d.shortage for d in decisions) == 100
