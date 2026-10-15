"""企业生老病死：设计稿 §9.1 的入驻决策与 §4.7 的进退条件。

为什么需要这一层
----------------
产能公式解决「产能多大」，但还需要回答「产能变了之后企业怎么办」。
没有这一层，产能波动只会让营业额曲线抖一抖，城市规模永远不会变。

设计稿 §9.1 把入驻门槛写得很死：

- 预期营业额用**过去 3 个月**平滑销量估计，**不允许预知未来**
- 利润预期 > 6%，且流动资金覆盖 **1.5 个月**固定支出
- 规划准入 + 门前道路可达 + 技能工人可得

§4.7 又给了退出门槛，刻意保守：

- **连续 3 个月**无法支付必要现金费用，或**连续 6 个月**税前亏损才进入退出程序
- 「不在一个偶然亏损月立即清盘」

这条保守门槛是经济稳定性的关键。没有它，一次意外就会让全市企业集体倒闭，
继而消灭税基、消灭就业、逼走居民——城市就彻底死了。
"""
from dataclasses import dataclass, field
from fractions import Fraction

from backend.core.errors import GameError

# §11 business.entry_expected_margin_min = 0.06，允许区间 [0.03, 0.15]
ENTRY_MARGIN_MIN = Fraction(6, 100)
# §11 business.expansion_utilization_min = 0.85，允许区间 [0.75, 0.95]
EXPANSION_UTILISATION_MIN = Fraction(85, 100)
# §4.7 退出：连续 3 月付不出现金，或连续 6 月税前亏损
EXIT_CASH_STREAK = 3
EXIT_LOSS_STREAK = 6
# §9.1 流动资金须覆盖 1.5 个月固定支出
LIQUIDITY_MONTHS = Fraction(3, 2)
# §9.1 预期营业额用过去 3 个月平滑销量，不预知未来
FORECAST_WINDOW = 3
# 历史不足时允许缩短窗口，但至少要 1 个月，否则等于凭空预测
MIN_FORECAST_WINDOW = 1


@dataclass
class FirmHistory:
    """单个行业最近若干个月的经营轨迹。

    只保留判断进退所必需的量，不做完整账本——完整账本在企业账户簿里，
    这里存的是**决策输入**：过去几个月的利用率、税前利润与现金缺口。
    """

    sector_id: str
    utilisation: list[Fraction] = field(default_factory=list)
    pre_tax_profit: list[int] = field(default_factory=list)
    cash_shortfall: list[bool] = field(default_factory=list)

    def record(self, utilisation: Fraction, pre_tax_profit: int,
               cash_shortfall: bool = False) -> None:
        """记一个月。窗口之外自动截断，历史再长也不会把内存撑爆。"""
        self.utilisation.append(utilisation)
        self.pre_tax_profit.append(pre_tax_profit)
        self.cash_shortfall.append(cash_shortfall)
        keep = max(FORECAST_WINDOW, EXIT_LOSS_STREAK, EXIT_CASH_STREAK)
        del self.utilisation[:-keep]
        del self.pre_tax_profit[:-keep]
        del self.cash_shortfall[:-keep]

    def trailing(self, series: list, window: int = FORECAST_WINDOW) -> list:
        """取最近 window 个月；历史不足时用现有的全部月份。"""
        return series[-max(MIN_FORECAST_WINDOW, min(window, len(series))):]

    @property
    def average_utilisation(self) -> Fraction:
        """过去 3 个月平均产能利用率（§4.7 扩张门槛）。

        只统计**已经结算过的月份**。本月利用率是本月的**结果**，
        把它算进来等于让企业在开工前就知道自己能赚多少，
        正是设计稿 §9.1 明令禁止的「预知未来」。
        结算完成后由调用方 ``record`` 记入，下个月才会被用到。
        """
        window = self.trailing(self.utilisation)
        return sum(window) / len(window) if window else Fraction(0)

    @property
    def average_revenue(self) -> float:
        """平滑销量，供入驻时估计预期营业额。"""
        return float(self.average_utilisation)

    def cash_failure_streak(self) -> int:
        """连续几个月付不出必要现金费用（从最近一个月往回数）。"""
        streak = 0
        for flag in reversed(self.cash_shortfall):
            if not flag:
                break
            streak += 1
        return streak

    def loss_streak(self) -> int:
        """连续几个月税前亏损（从最近一个月往回数）。"""
        streak = 0
        for profit in reversed(self.pre_tax_profit):
            if profit >= 0:
                break
            streak += 1
        return streak


@dataclass(frozen=True)
class FirmDecision:
    """一个行业本月的经营决策及其理由。理由要能显示给玩家看。"""

    sector_id: str
    action: str                 # expand / hold / contract / exit
    reason: str                 # 面向玩家的中文说明
    target_employees: int       # 目标员工数
    changes: int = 0            # 相对当前人数的增减

    ENTER = "expand"
    HOLD = "hold"
    CONTRACT = "contract"
    EXIT = "exit"


def expected_margin(revenue: int, fixed_cost: int, input_cost: int) -> Fraction:
    """预期利润率 = (预期营业额 − 预期采购 − 固定支出) / 预期营业额。

    固定支出含工资、商业租金与规费；采购随规模走，所以这里把它单列。
    营业额为 0 时返回 0 而不是负数——没收入谈不上盈亏。
    """
    if revenue <= 0:
        return Fraction(0)
    return Fraction(max(0, revenue - input_cost - fixed_cost), revenue)


def decide_entry(history: FirmHistory, sector_id: str, target_employees: int,
                 revenue: int, fixed_cost: int, input_cost: int,
                 working_cash: int, margin_min: Fraction = ENTRY_MARGIN_MIN) -> FirmDecision:
    """判断一个新商户能否入驻本城（§9.1）。

    四道门槛全过才允许扩张，缺一道就给理由说明原因——
    玩家需要知道「为什么没人来」，而不是只看到一个不动的数字。
    """
    if target_employees <= 0:
        raise GameError("目标用工人数必须为正。")
    if revenue <= 0:
        return FirmDecision(sector_id, FirmDecision.HOLD, "没有预期营业额，不具备入驻条件。",
                            target_employees)

    margin = expected_margin(revenue, fixed_cost, input_cost)
    if margin <= margin_min:
        return FirmDecision(
            sector_id, FirmDecision.HOLD,
            f"预期利润率 {float(margin):.1%} 未达 {float(margin_min):.0%} 门槛，不入驻。",
            target_employees)

    required_cash = int(float(fixed_cost) * float(LIQUIDITY_MONTHS))
    if working_cash < required_cash:
        return FirmDecision(
            sector_id, FirmDecision.HOLD,
            f"流动资金 {working_cash} 分不足以覆盖 {LIQUIDITY_MONTHS} 个月固定支出。",
            target_employees)

    return FirmDecision(sector_id, FirmDecision.ENTER,
                        f"预期利润率 {float(margin):.1%} 且资金充裕，满足入驻条件。",
                        target_employees, changes=target_employees)


def decide_operation(history: FirmHistory, sector_id: str, current_employees: int,
                     utilisation: Fraction, profit: int,
                     cash_shortfall: bool = False) -> FirmDecision:
    """在营企业的月度决策：扩张、维持、收缩还是退出（§4.7 / §9.1）。

    **先判断、后记录**：扩张与收缩看的是过去 3 个月已结算的利用率，
    本月刚结算出来的利用率只记入历史，供下个月使用。
    把本月结果也算进本月决策，就等于让企业提前知道自己赚了多少，
    正是设计稿 §9.1 禁止的「预知未来」。

    扩张门槛是**过去 3 个月平均利用率 > 85%**——
    只有一个月的繁荣不足以扩张，必须是持续满负荷才值得开新店。
    退出门槛刻意保守：连续 3 个月付不出现金才退出，
    避免一次意外就让全市企业集体倒闭。
    """
    if current_employees < 0:
        raise GameError("在营人数不能为负。")

    decision = _judge(history, sector_id, current_employees)
    history.record(utilisation, profit, cash_shortfall)
    return decision


def _judge(history: FirmHistory, sector_id: str, current_employees: int) -> FirmDecision:
    """只读历史做判断，不写入任何新数据。"""
    if history.cash_failure_streak() >= EXIT_CASH_STREAK:
        return FirmDecision(sector_id, FirmDecision.EXIT,
                            f"连续 {history.cash_failure_streak()} 个月无法支付必要费用，启动退出程序。",
                            0, changes=-current_employees)

    if history.loss_streak() >= EXIT_LOSS_STREAK:
        return FirmDecision(sector_id, FirmDecision.EXIT,
                            f"连续 {history.loss_streak()} 个月税前亏损，启动退出程序。",
                            0, changes=-current_employees)

    average = history.average_utilisation
    if not history.utilisation:
        # 还没有任何已结算的历史：不猜，等第一个月跑完再决策
        return FirmDecision(sector_id, FirmDecision.HOLD, "尚无已结算的历史，维持现状。",
                            current_employees)

    if average > EXPANSION_UTILISATION_MIN:
        return FirmDecision(sector_id, FirmDecision.ENTER,
                            f"近 {FORECAST_WINDOW} 月平均利用率 {float(average):.1%} 超过 "
                            f"{float(EXPANSION_UTILISATION_MIN):.0%}，具备扩张条件。",
                            current_employees * 2, changes=current_employees)

    if average < Fraction(40, 100):
        return FirmDecision(sector_id, FirmDecision.CONTRACT,
                            f"近 {FORECAST_WINDOW} 月平均利用率仅 {float(average):.1%}，缩减规模。",
                            max(1, current_employees // 2),
                            changes=max(1, current_employees // 2) - current_employees)

    return FirmDecision(sector_id, FirmDecision.HOLD,
                        f"近 {FORECAST_WINDOW} 月平均利用率 {float(average):.1%}，维持现状。",
                        current_employees)


__all__ = [
    "ENTRY_MARGIN_MIN", "EXIT_CASH_STREAK", "EXIT_LOSS_STREAK", "EXPANSION_UTILISATION_MIN",
    "FirmDecision", "FirmHistory", "decide_entry", "decide_operation", "expected_margin",
]