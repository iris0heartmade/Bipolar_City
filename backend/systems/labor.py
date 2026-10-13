"""P03 劳动力市场：岗位来源于真实雇佣，工资由付款账户支付。

岗位不是凭空生成的数字：每个岗位绑定一个雇主与一个工资价，
招聘后该群体可用劳动力相应减少，同一个人不会被两家企业重复录用。
"""
from dataclasses import dataclass, field

from backend.config import LABOR_CONSUMPTION
from backend.core.errors import GameError
from backend.model.economy import ReferenceCity, cr_to_cents
from backend.model.households import HouseholdRegistry


@dataclass
class JobPost:
    """一个雇佣岗位：属于某行业或公共部门，带工资价。"""

    employer_kind: str      # private / public
    employer_id: str        # 行业 ID 或公共部门 ID
    posts: int
    wage_cents: int


@dataclass
class LabourAssignment:
    """用工结果：已就业人数、新招聘人数与剩余求职者。

    occupied_by_band 是本期之前已经坐在岗位上的人数，newly_hired_by_band 是本期新招的人。
    基准情形下岗位已被坐满（无空缺），新招聘为 0，150 名失业者保持待业；
    只有出现空缺时，才会从失业者中招聘——招聘不是每月例行发生的事。
    """

    occupied_by_band: dict[str, int] = field(default_factory=dict)
    newly_hired_by_band: dict[str, int] = field(default_factory=dict)
    unemployment_by_band: dict[str, int] = field(default_factory=dict)
    wage_bill_cents: int = 0
    vacant_posts: int = 0

    @property
    def total_employed(self) -> int:
        return sum(self.occupied_by_band.values()) + sum(self.newly_hired_by_band.values())

    @property
    def total_newly_hired(self) -> int:
        return sum(self.newly_hired_by_band.values())

    @property
    def total_unemployed(self) -> int:
        return sum(self.unemployment_by_band.values())

    @property
    def total_labour_force(self) -> int:
        return self.total_employed + self.total_unemployed


def build_posts(city: ReferenceCity) -> list[JobPost]:
    """岗位表：私企来自六行业聚合，公共岗位来自 P00 公共部门配置。"""
    posts = [JobPost(employer_kind="private", employer_id=s.sector_id,
                     posts=s.employees, wage_cents=s.wage_per_employee)
             for s in city.sectors]
    for row in city.config["public_sector"]["posts"]:
        posts.append(JobPost(employer_kind="public", employer_id=row["id"],
                             posts=row["staff"], wage_cents=cr_to_cents(row["wage"])))
    return posts


def fill_vacancies(posts: list[JobPost], registry: HouseholdRegistry,
                   vacant_by_employer: dict[str, int] | None = None) -> LabourAssignment:
    """填补空缺岗位：只有企业扩张或离职产生的空缺才会触发招聘。

    现有就业者已占据其余岗位，因此基准情形（无空缺）不会消耗任何失业者。
    录用后该群体的剩余求职者相应减少，同一个人不会被两家企业重复录用。

    岗位与劳动者按工资降序匹配：高工资岗位优先由人均收入最高的收入层填补，
    避免低技能劳动力被塞进高工资岗位而高技能劳动力待业。
    注意收入层按人均收入排序而不是按收入总额排序——高收入层人少，
    总额本来就低，用总额排会把高技能群体排到最后。
    """
    vacant_by_employer = vacant_by_employer or {}
    remaining = {c.band_id: c.unemployed for c in registry}
    assignment = LabourAssignment(occupied_by_band={c.band_id: c.employed for c in registry})

    ordered_posts = sorted(posts, key=lambda p: (-p.wage_cents, p.employer_id))
    band_order = [c.band_id for c in sorted(registry.cohorts,
                                            key=lambda c: (-c.per_capita_income_cents, c.band_id))]

    for post in ordered_posts:
        vacant = vacant_by_employer.get(post.employer_id, 0)
        if vacant < 0:
            raise GameError(f"岗位 {post.employer_id} 的空缺数不能为负")
        if vacant > post.posts:
            raise GameError(f"岗位 {post.employer_id} 的空缺 {vacant} 超过岗位总数 {post.posts}")

        still_open = vacant
        for band_id in band_order:
            if still_open <= 0:
                break
            take = min(still_open, remaining[band_id])
            if take <= 0:
                continue
            remaining[band_id] -= take
            assignment.newly_hired_by_band[band_id] = \
                assignment.newly_hired_by_band.get(band_id, 0) + take
            assignment.wage_bill_cents += take * post.wage_cents
            still_open -= take
        if still_open > 0:
            raise GameError(
                f"空缺岗位 {still_open} 个但无可用劳动力：招聘需求 {post.employer_id} 超过失业人数")

    assignment.unemployment_by_band = dict(remaining)
    assignment.vacant_posts = sum(vacant_by_employer.values())
    # 已有岗位继续产生工资，空缺岗位不发工资
    for post in posts:
        occupied = post.posts - vacant_by_employer.get(post.employer_id, 0)
        assignment.wage_bill_cents += occupied * post.wage_cents
    return assignment


def validate_posts(posts: list[JobPost], registry: HouseholdRegistry) -> None:
    """校验岗位总量、人口恒等式与最低工资约束。

    先查「岗位数超过就业人数」：凭空创造工作是比配置对不上更严重的错误，
    报错要说清楚是哪一种，不能让配置校验把真正的问题挡在后面。
    """
    labour = LABOR_CONSUMPTION["labour_market"]
    total_posts = sum(p.posts for p in posts)
    if total_posts > registry.total_employed:
        raise GameError(f"岗位数 {total_posts} 超过就业人数 {registry.total_employed}，不得凭空创造岗位")
    if total_posts != labour["private_post_total"] + labour["public_post_total"]:
        raise GameError(f"岗位合计 {total_posts} 与配置的岗位总量不符")
    for post in posts:
        if post.posts <= 0 or post.wage_cents <= 0:
            raise GameError(f"岗位 {post.employer_id} 的数量或工资非正")
    if not registry.population_identity_holds():
        raise GameError("人口三项恒等式不成立")


def unemployment_rate(registry: HouseholdRegistry, assignment: LabourAssignment) -> float:
    """失业率＝失业人数 ÷ 劳动力人数（非劳动力不参与分母）。"""
    labour_force = assignment.total_labour_force or (registry.total_employed + registry.total_unemployed)
    return assignment.total_unemployed / labour_force if labour_force else 0.0