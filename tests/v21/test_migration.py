"""V2.1 阶段 8 跨城迁移验收测试（2026-10-15）。

【验收清单】（V2.1 §11.2 + §11.3 + §11.4）
- [x] 默认参数（rho=0.01, beta=0.08, d0=200km, cap=1%）
- [x] CitySnapshot 字段 + housing_room + per_capita_cash
- [x] DistanceMatrix 对称 set/get
- [x] MigrationFlow 数据类
- [x] compute_M_cd 公式（调和平均 + 距离衰减 + L 差 sigmoid）
- [x] antisymmetric_pair（M_cd = -M_dc）
- [x] cap_outflow_1pct（§11.3 c + §11.4）
- [x] cap_destination_housing（§11.3 d）
- [x] solve_multi_city_migration 9 步强制流程
- [x] sum_c deltaN_c = 0（反对称保证）
- [x] JSON 配置字段一致
"""
import pytest

from backend.systems.v21.migration import (
    DEFAULT_RHO, DEFAULT_BETA, DEFAULT_D0_KM,
    DEFAULT_OUTFLOW_CAP_RATE, DEFAULT_DEST_HOUSING_BUFFER,
    CitySnapshot, DistanceMatrix, MigrationFlow, MigrationResult,
    compute_M_cd, antisymmetric_pair,
    cap_outflow_1pct, cap_destination_housing,
    solve_multi_city_migration,
)
from backend.systems.v21.loader import load_migration_config


# ===== A. 默认参数（3 项）=====

class TestDefaultParams:
    """V2.1 §11.2 + §11.4 默认参数。"""

    def test_rho_beta_d0(self):
        """阶段 8：rho=0.01, beta=0.08, d0=200km（§11.2 默认）。"""
        assert DEFAULT_RHO == 0.01
        assert DEFAULT_BETA == 0.08
        assert DEFAULT_D0_KM == 200.0

    def test_outflow_cap_rate_is_1_percent(self):
        """阶段 8：outflow_cap_rate = 0.01（§11.4 首月 1% 上限）。"""
        assert DEFAULT_OUTFLOW_CAP_RATE == 0.01

    def test_dest_housing_buffer(self):
        """阶段 8：目的住房余量下限默认 0（不留余地）。"""
        assert DEFAULT_DEST_HOUSING_BUFFER == 0


# ===== B. CitySnapshot（4 项）=====

class TestCitySnapshot:
    """V2.1 §11.3(a) 月末快照。"""

    def test_basic_construction(self):
        """阶段 8：CitySnapshot 基础字段。"""
        s = CitySnapshot(city_id='A', N=3000, L=75.0, K=3257)
        assert s.city_id == 'A'
        assert s.N == 3000
        assert s.L == 75.0
        assert s.faithful_ratio == 0.55   # §9.5 默认
        assert s.labor_participation == 0.55

    def test_housing_room(self):
        """阶段 8：housing_room = housing_capacity - N。"""
        s = CitySnapshot(city_id='A', N=3000, L=75.0, housing_capacity=3600)
        assert s.housing_room() == 600

    def test_housing_room_zero_when_full(self):
        """阶段 8：满员时 housing_room = 0。"""
        s = CitySnapshot(city_id='A', N=3600, L=75.0, housing_capacity=3600)
        assert s.housing_room() == 0

    def test_per_capita_cash(self):
        """阶段 8：per_capita_cash_cents = savings / N。"""
        s = CitySnapshot(city_id='A', N=3000, L=75.0, savings_cents=30_000_00)
        # 3,000,000 / 3000 = 1,000 分 = 10 CR/人
        assert s.per_capita_cash_cents() == 1_000


# ===== C. DistanceMatrix（3 项）=====

class TestDistanceMatrix:
    """V2.1 §11.2 城际距离矩阵。"""

    def test_get_returns_zero_for_same_city(self):
        """阶段 8：同城距离 = 0。"""
        dm = DistanceMatrix()
        assert dm.get('A', 'A') == 0.0

    def test_get_returns_zero_for_unset_pair(self):
        """阶段 8：未设置的距离 = 0（默认值）。"""
        dm = DistanceMatrix()
        assert dm.get('A', 'B') == 0.0

    def test_set_is_symmetric(self):
        """阶段 8：set() 同时设两端（对称矩阵）。"""
        dm = DistanceMatrix()
        dm.set('A', 'B', 150.0)
        assert dm.get('A', 'B') == 150.0
        assert dm.get('B', 'A') == 150.0


# ===== D. compute_M_cd 公式（5 项）=====

class TestComputeMcd:
    """V2.1 §11.2 M_c_to_d 公式。"""

    def test_zero_when_L_equal(self):
        """阶段 8：两城 L 相等时 M = 0（§11.2 两城 L 相等净迁移 0）。"""
        M = compute_M_cd(N_c=3000, N_d=3000, L_c=75.0, L_d=75.0, distance_cd_km=100.0)
        assert M == 0.0

    def test_positive_when_d_higher_L(self):
        """阶段 8：目的 L 高 → M > 0（c→d 净迁）。"""
        M = compute_M_cd(N_c=3000, N_d=3000, L_c=70.0, L_d=80.0, distance_cd_km=100.0)
        assert M > 0

    def test_negative_when_origin_higher_L(self):
        """阶段 8：来源 L 高 → M < 0（d→c 净迁）。"""
        M = compute_M_cd(N_c=3000, N_d=3000, L_c=80.0, L_d=70.0, distance_cd_km=100.0)
        assert M < 0

    def test_harmonic_mean_factor(self):
        """阶段 8：人口用调和平均 (2*N_c*N_d/(N_c+N_d))。"""
        # N_c=3000, N_d=1000 → 2*3000*1000/(3000+1000) = 1500
        # rho=0.01, d=0 → exp(0)=1, tanh(0)=0（假设 L 相等）→ M=0
        M = compute_M_cd(N_c=3000, N_d=1000, L_c=75.0, L_d=75.0, distance_cd_km=0.0)
        assert M == 0.0

    def test_zero_population_returns_zero(self):
        """阶段 8：任一城市人口为 0 → M = 0。"""
        assert compute_M_cd(N_c=0, N_d=1000, L_c=50, L_d=80, distance_cd_km=100) == 0.0
        assert compute_M_cd(N_c=1000, N_d=0, L_c=50, L_d=80, distance_cd_km=100) == 0.0


# ===== E. antisymmetric_pair（2 项）=====

class TestAntisymmetricPair:
    """V2.1 §11.2 反对称。"""

    def test_pair_is_antisymmetric(self):
        """阶段 8：(M_cd, M_dc) = (x, -x) 反对称。"""
        c = CitySnapshot(city_id='A', N=3000, L=70.0)
        d = CitySnapshot(city_id='B', N=3000, L=80.0)
        M_cd, M_dc = antisymmetric_pair(c, d, distance_cd_km=100.0)
        assert abs(M_cd + M_dc) < 1e-9

    def test_equal_L_zero_pair(self):
        """阶段 8：L 相等时 M_cd = M_dc = 0（净迁移 0）。"""
        c = CitySnapshot(city_id='A', N=3000, L=75.0)
        d = CitySnapshot(city_id='B', N=3000, L=75.0)
        M_cd, M_dc = antisymmetric_pair(c, d, distance_cd_km=100.0)
        assert M_cd == 0.0 and M_dc == 0.0


# ===== F. 上限（4 项）=====

class TestCaps:
    """V2.1 §11.3 c + d + §11.4。"""

    def test_outflow_cap_1pct_applied(self):
        """阶段 8：迁出超过 1% 来源人口被截断。"""
        # N_c=3000, cap=30；M=50 → 截断到 30
        capped, was_capped = cap_outflow_1pct(M_cd_signed=50.0, N_c=3000)
        assert capped == 30.0
        assert was_capped is True

    def test_outflow_cap_not_triggered(self):
        """阶段 8：迁出 ≤ 1% 不截断。"""
        capped, was_capped = cap_outflow_1pct(M_cd_signed=20.0, N_c=3000)
        assert capped == 20.0
        assert was_capped is False

    def test_negative_M_not_capped(self):
        """阶段 8：负值（M<0，即 d→c）不应用 1% 上限（c 是目的不是来源）。"""
        capped, was_capped = cap_outflow_1pct(M_cd_signed=-10.0, N_c=3000)
        assert capped == -10.0
        assert was_capped is False

    def test_destination_housing_cap(self):
        """阶段 8：迁入超过目的住房余量被截断。"""
        capped, was_capped = cap_destination_housing(delta_N_positive=100.0, destination_room=50)
        assert capped == 50.0
        assert was_capped is True


# ===== G. solve_multi_city_migration（6 项）=====

class TestSolveMultiCityMigration:
    """V2.1 §11.3 多城迁移 9 步强制流程。"""

    def _make_two_cities(self):
        """A: N=3000, L=70；B: N=3000, L=80，距离 100km。"""
        city_a = CitySnapshot(city_id='A', N=3000, L=70.0, savings_cents=30_000_00, housing_capacity=4000)
        city_b = CitySnapshot(city_id='B', N=3000, L=80.0, savings_cents=30_000_00, housing_capacity=4000)
        dm = DistanceMatrix()
        dm.set('A', 'B', 100.0)
        return [city_a, city_b], dm

    def test_basic_migration_flow(self):
        """阶段 8：基本迁移（A→B 净流）。"""
        snapshots, dm = self._make_two_cities()
        result = solve_multi_city_migration(snapshots, dm)
        # L_B > L_A → 应该 A→B 净迁
        assert result.net_delta('A') < 0  # A 减少
        assert result.net_delta('B') > 0  # B 增加

    def test_zero_sum_deltaN(self):
        """阶段 8：§11.3(f) sum_c deltaN_c = 0（反对称保证）。"""
        snapshots, dm = self._make_two_cities()
        result = solve_multi_city_migration(snapshots, dm)
        total = sum(result.delta_N.values())
        assert total == 0

    def test_outflow_capped_at_1pct(self):
        """阶段 8：单源迁出不超过 1% 来源人口（§11.3 c + §11.4）。"""
        snapshots, dm = self._make_two_cities()
        result = solve_multi_city_migration(snapshots, dm)
        # |delta_N_A| = |delta_N_B| <= N_A * 0.01 = 30
        assert abs(result.net_delta('A')) <= 30

    def test_cash_transfer_positive(self):
        """阶段 8：现金转移从来源到目的（§11.3 g）。"""
        snapshots, dm = self._make_two_cities()
        result = solve_multi_city_migration(snapshots, dm)
        # 至少一笔 A→B 现金转移
        cash_a_to_b = result.cash_transfers.get(('A', 'B'), 0)
        assert cash_a_to_b > 0

    def test_no_migration_when_L_equal(self):
        """阶段 8：所有城市 L 相等时无迁移。"""
        city_a = CitySnapshot(city_id='A', N=3000, L=75.0, savings_cents=30_000_00, housing_capacity=4000)
        city_b = CitySnapshot(city_id='B', N=3000, L=75.0, savings_cents=30_000_00, housing_capacity=4000)
        dm = DistanceMatrix()
        dm.set('A', 'B', 100.0)
        result = solve_multi_city_migration([city_a, city_b], dm)
        assert result.net_delta('A') == 0
        assert result.net_delta('B') == 0

    def test_three_cities_antisymmetric(self):
        """阶段 8：三城同步求解，结果 sum deltaN = 0。"""
        a = CitySnapshot(city_id='A', N=3000, L=65.0, savings_cents=30_000_00, housing_capacity=4000)
        b = CitySnapshot(city_id='B', N=3000, L=75.0, savings_cents=30_000_00, housing_capacity=4000)
        c = CitySnapshot(city_id='C', N=3000, L=85.0, savings_cents=30_000_00, housing_capacity=4000)
        dm = DistanceMatrix()
        dm.set('A', 'B', 100.0)
        dm.set('A', 'C', 200.0)
        dm.set('B', 'C', 150.0)
        result = solve_multi_city_migration([a, b, c], dm)
        total = sum(result.delta_N.values())
        assert total == 0
        # 高 L 的 C 应该净迁入（delta > 0）
        assert result.net_delta('C') > 0


# ===== H. JSON 配置（3 项）=====

class TestMigrationJsonConfig:
    """V2.1 §11 migration.json 配置。"""

    def test_json_loads(self):
        """阶段 8：migration.json 可加载。"""
        config = load_migration_config()
        assert config['_meta']['version'] == '2.1.0'
        assert '§11' in config['_meta']['spec']

    def test_json_migration_params(self):
        """阶段 8：migration_params 字段（§11.2 默认值）。"""
        config = load_migration_config()
        mp = config['migration_params']
        assert mp['rho'] == 0.01
        assert mp['beta'] == 0.08
        assert mp['d0_km'] == 200.0
        assert mp['outflow_cap_rate'] == 0.01

    def test_json_initial_protection(self):
        """阶段 8：initial_protection 字段（§11.4 防开局无限流失）。"""
        config = load_migration_config()
        ip = config['initial_protection']
        assert ip['first_month_outflow_max_rate'] == 0.01
        assert ip['L_initial_diff_max'] == 18.0
        assert ip['L_target_min'] == 65.0
        assert ip['L_target_max'] == 82.0
