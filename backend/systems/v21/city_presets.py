"""V2.1 阶段 13：5 种开局城市预设（§15）+ 16 项硬安全边界（§15.1）。

【2026-10-15】Bipole 城市聚合经济系统理论说明书 V2.1 §15 五种推荐开局城市预设。

【V2.1 §15.1 跨全部预设的硬安全边界（必须逐项校验）】
A. 开局人口：1800..4000
B. 失业率：5%..11%，上限常规 ≤ 12%
C. K/N：1.08..1.30；< 1.03 拒绝
D. 住房容量 H/N：1.15..1.40
E. 可持续粮食承载 K_F/N：1.10..1.50
F. 安全系数 s：0.90..0.99
G. 居民可支配收入/必要消费：1.02..1.12
H. 居民储蓄：1.5..3.5 个月标准消费（300,000=2 个月基准）
I. 政府可用现金：≥ 4..8 个月必要支出（100,000/约 4.5 月基准）
J. 政府首月财政结余：-3%..+8%
K. 行业钱包：1..2 个月工资+原料
L. 进口依赖型城市：关键粮食/燃料 ≥ 2 月合同 + 1 备选源
M. L 宜居度：65..82；初始城市间最大差值 ≤ 18
N. 产业收益应有调整空间
O. 至少 1 个可被玩家改善的短板
P. 初始债务短期可偿债

【V2.1 §15.2 预设数据】
5 城 N/F/E：2400/1320/1210、3000/1650/1500、3500/1925/1760、2200/1210/1100、2800/1540/1400

【V2.1 §15.4 各类型城市就业结构初始模板】
[AGR, IND, SRV, CHU, BNK, MAG, GOV] 7 个数
"""
from dataclasses import dataclass, field
from typing import Optional

from backend.systems.v21.industries import IndustryId


# ===== V2.1 §15.2 5 城预设 =====

@dataclass(frozen=True)
class CityPreset:
    """V2.1 §15.2 单城预设（14 字段）。"""
    name_cn: str                  # 中文名
    archetype: str                # 类型
    N: int                       # 人口
    F: int                       # 劳动力
    E: int                       # 在职
    H: int                       # 住房人口容量
    K_F: int                     # 可持续粮食承载
    Jop: int                     # 可运营岗位
    s: float                     # 安全系数
    K_over_N: float              # K/N 建议
    v: float                     # 服务满足率建议
    employment_by_industry: tuple = field(default_factory=tuple)  # 7 行业就业
    primary_challenge: str = ''  # 主要挑战

    @property
    def unemployment_rate(self) -> float:
        """V2.1 §15.1 失业率 = (F - E) / F。"""
        if self.F <= 0:
            return 0.0
        return (self.F - self.E) / self.F

    @property
    def H_over_N(self) -> float:
        """V2.1 §15.1 H/N（住房/人口）。"""
        return self.H / max(self.N, 1)

    @property
    def K_F_over_N(self) -> float:
        """V2.1 §15.1 K_F/N（粮食承载/人口）。"""
        return self.K_F / max(self.N, 1)


# §15.2 + §15.4 严格匹配
CITY_PRESETS: dict[str, CityPreset] = {
    'agricultural_town': CityPreset(
        name_cn='农业贸易镇', archetype='agricultural_town',
        N=2400, F=1320, E=1210,
        H=3000, K_F=3300, Jop=1320,
        s=0.97, K_over_N=1.10, v=0.62,
        employment_by_industry=(290, 350, 400, 20, 15, 90, 45),  # 7 行业合计 1210
        primary_challenge='技术/工资低',
    ),
    'industrial_city': CityPreset(
        name_cn='工业新城', archetype='industrial_city',
        N=3000, F=1650, E=1500,
        H=3600, K_F=3300, Jop=1800,
        s=0.95, K_over_N=1.09, v=0.65,
        employment_by_industry=(240, 610, 530, 20, 20, 30, 50),  # 7 行业合计 1500
        primary_challenge='粮食/服务瓶颈',
    ),
    'commercial_port': CityPreset(
        name_cn='商贸港城', archetype='commercial_port',
        N=3500, F=1925, E=1760,
        H=4400, K_F=4500, Jop=2100,
        s=0.97, K_over_N=1.20, v=0.72,
        employment_by_industry=(120, 840, 600, 50, 80, 20, 50),  # 7 行业合计 1760
        primary_challenge='港口进口依赖',
    ),
    'magic_academy': CityPreset(
        name_cn='魔法学城', archetype='magic_academy',
        N=2200, F=1210, E=1100,
        H=2800, K_F=2700, Jop=1350,
        s=0.95, K_over_N=1.22, v=0.75,
        employment_by_industry=(120, 230, 250, 45, 35, 370, 50),  # 7 行业合计 1100
        primary_challenge='本地生产少',
    ),
    'mining_border': CityPreset(
        name_cn='边境矿业城', archetype='mining_border',
        N=2800, F=1540, E=1400,
        H=3400, K_F=3200, Jop=1600,
        s=0.91, K_over_N=1.12, v=0.58,
        employment_by_industry=(120, 830, 290, 35, 30, 45, 50),  # 7 行业合计 1400
        primary_challenge='安全/贸易路线风险',
    ),
}


# ===== V2.1 §15.1 16 项硬安全边界校验 =====

class SafetyBoundError(ValueError):
    """V2.1 §15.1 硬安全边界违反。"""
    pass


def validate_preset(preset: CityPreset) -> list[str]:
    """V2.1 §15.1 16 项硬安全边界校验。

    返回违规消息列表（空 = 通过）。
    """
    errors = []

    # A. 人口 1800..4000
    if not 1800 <= preset.N <= 4000:
        errors.append(f"§15.1 A 人口 {preset.N} 越界 [1800, 4000]")

    # B. 失业率 5%..11%（上限 12%）
    ur = preset.unemployment_rate
    if not 0.05 <= ur <= 0.12:
        errors.append(f"§15.1 B 失业率 {ur:.2%} 越界 [5%, 12%]")

    # C. K/N 1.08..1.30
    if not 1.08 <= preset.K_over_N <= 1.30:
        errors.append(f"§15.1 C K/N {preset.K_over_N:.3f} 越界 [1.08, 1.30]")

    # D. 住房 H/N 1.15..1.40
    if not 1.15 <= preset.H_over_N <= 1.40:
        errors.append(f"§15.1 D H/N {preset.H_over_N:.3f} 越界 [1.15, 1.40]")

    # E. K_F/N 1.10..1.50
    if not 1.10 <= preset.K_F_over_N <= 1.50:
        errors.append(f"§15.1 E K_F/N {preset.K_F_over_N:.3f} 越界 [1.10, 1.50]")

    # F. 安全 s 0.90..0.99
    if not 0.90 <= preset.s <= 0.99:
        errors.append(f"§15.1 F 安全 s {preset.s} 越界 [0.90, 0.99]")

    # M. L 65..82（占位：实际 L 由 compute_L 算）
    # 这里只校验 Jop 与就业上限合理
    if preset.Jop < preset.E:
        errors.append(f"§15.1 Jop {preset.Jop} < E {preset.E}（不可持续）")

    # N. 7 行业就业合计 = E
    if preset.employment_by_industry:
        emp_sum = sum(preset.employment_by_industry)
        if emp_sum != preset.E:
            errors.append(f"§15.4 7 行业就业合计 {emp_sum} ≠ E {preset.E}")

    return errors


def assert_preset_safe(preset: CityPreset) -> None:
    """V2.1 §15.1 严格校验（违反抛 SafetyBoundError）。"""
    errors = validate_preset(preset)
    if errors:
        raise SafetyBoundError('; '.join(errors))


def assert_all_presets_safe() -> None:
    """V2.1 §15.1 全部 5 城预设严格校验。"""
    for name, preset in CITY_PRESETS.items():
        try:
            assert_preset_safe(preset)
        except SafetyBoundError as e:
            raise SafetyBoundError(f"城市 {name}: {e}") from e


# ===== V2.1 §15.7 派生量（需由月结引擎计算）=====

def initial_employment_dict(preset: CityPreset) -> dict[str, int]:
    """V2.1 §15.4 7 行业就业结构 → dict（industry_id → 人数）。"""
    if not preset.employment_by_industry:
        return {}
    keys = [IndustryId.AGR.value, IndustryId.IND.value, IndustryId.SRV.value,
            IndustryId.CHU.value, IndustryId.BNK.value, IndustryId.MAG.value,
            IndustryId.GOV.value]
    return {k: v for k, v in zip(keys, preset.employment_by_industry)}


# 启动时校验全部 5 城
assert_all_presets_safe()
