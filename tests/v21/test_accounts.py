"""V2.1 阶段 3 账户体系验收测试（2026-10-15）。

【验收清单】（V2.1 §1.5 + §7.3 + §14.12）
- [x] Account/AccountBook 数据结构
- [x] 八行业独立经营现金钱包（firms:AGR/IND/SRV/BNK/MAG，CHU 共用 church）
- [x] 八群体居民储蓄钱包（cohorts:H0..H7）
- [x] PROP 物业通道（property_pass，月末清零）
- [x] 政府 + 教会 + 海洋 + 邻城 边界账户
- [x] 行业经营现金与同名行业居民储蓄是不同账户
- [x] 负现金不能自动由居民储蓄填补（V2.1 §7.3）
- [x] ocean 免透支检查；邻城受透支检查
- [x] peer_flows 邻城过账记录
- [x] PROP 月末清零（end_month()）
- [x] 期初资金匹配 V2.1 §14.12：GOV 100,000 / 教会 20,000 / 居民 300,000
"""
import pytest

from backend.systems.v21.accounts import (
    Account, AccountBook,
    OCEAN_ACCOUNT, OCEAN, PEER, EXTERNAL,
    MUNICIPAL, FIRM, HOUSEHOLD, CHURCH_KIND, PROP_KIND, PEER_KIND,
    firm_account_id, cohort_account_id, church_account_id,
    government_account_id, property_pass_account_id, peer_account,
    is_ocean, is_peer,
)
from backend.systems.v21.ledger import Ledger, PeerFlow, build_industrial_city_accounts
from backend.systems.v21.loader import load_wallets_config, ECONOMY_V2_DIR


# ===== Account 数据结构 =====

def test_account_basic_construction():
    """阶段 3：Account 基础构造。"""
    a = Account('firms:IND', FIRM, 'IND', city_id='city1', cash_cents=10000)
    assert a.account_id == 'firms:IND'
    assert a.owner_kind == FIRM
    assert a.cash_cents == 10000
    assert a.is_external is False
    assert a.is_ocean is False
    assert a.is_peer is False


def test_account_is_ocean():
    """阶段 3：海洋账户 is_ocean=True。"""
    a = Account(OCEAN_ACCOUNT, EXTERNAL, 'ocean', city_id=None, cash_cents=0)
    assert a.is_ocean is True
    assert a.is_external is True


def test_account_is_peer():
    """阶段 3：邻城账户 is_peer=True（V2.1 §1.5 城外付款方）。"""
    a = Account('city:B', PEER, 'B', city_id='B', cash_cents=0)
    assert a.is_peer is True
    # 邻城也属于外部账户（V2.1 §1.5 WORLD/OTHER_CITY 含海洋和邻城）
    assert a.is_external is True   # 命名空间 city: 视为外部
    # 邻城受透支检查，不免检
    assert a.is_ocean is False


def test_account_is_property_pass():
    """阶段 3：PROP 物业通道 is_property_pass=True。"""
    a = Account('property_pass', PROP_KIND, 'landlords_aggregate', city_id='c1', cash_cents=0)
    assert a.is_property_pass is True


def test_account_available_cents():
    """阶段 3：available_cents = cash - reserved。"""
    a = Account('firms:IND', FIRM, 'IND', city_id='c1', cash_cents=10000, reserved_cents=3000)
    assert a.available_cents == 7000


def test_account_negative_cash_raises_on_open():
    """阶段 3：开账户时负现金抛 ValueError。"""
    book = AccountBook(city_id='c1')
    with pytest.raises(ValueError):
        book.open('firms:IND', FIRM, 'IND', cash_cents=-100)


# ===== AccountBook 账户集合 =====

def test_account_book_default_city_id():
    """阶段 3：本地账户默认 city_id 设为 book.city_id。"""
    book = AccountBook(city_id='c1')
    book.open('firms:IND', FIRM, 'IND', cash_cents=100)
    assert book['firms:IND'].city_id == 'c1'


def test_account_book_local_leaves():
    """阶段 3：local_leaves 仅本地账户。"""
    book = AccountBook(city_id='c1')
    book.open('firms:IND', FIRM, 'IND', cash_cents=100)
    book.open('government', MUNICIPAL, 'municipal', cash_cents=200)
    book.open(OCEAN_ACCOUNT, EXTERNAL, 'ocean', city_id=None, cash_cents=0)
    locals_ = list(book.local_leaves())
    assert len(locals_) == 2
    assert {a.account_id for a in locals_} == {'firms:IND', 'government'}


def test_account_book_external_leaves():
    """阶段 3：external_leaves 仅海洋/邻城。"""
    book = AccountBook(city_id='c1')
    book.open('firms:IND', FIRM, 'IND', cash_cents=100)
    book.open(OCEAN_ACCOUNT, EXTERNAL, 'ocean', city_id=None, cash_cents=0)
    book.open('city:B', PEER, 'B', city_id='B', cash_cents=0)
    externals = list(book.external_leaves())
    assert len(externals) == 2
    assert {a.account_id for a in externals} == {'ocean', 'city:B'}


def test_account_book_snapshot():
    """阶段 3：snapshot 返回期初余额快照。"""
    book = AccountBook(city_id='c1')
    book.open('firms:IND', FIRM, 'IND', cash_cents=100)
    book.open('government', MUNICIPAL, 'municipal', cash_cents=200)
    snap = book.snapshot()
    assert snap == {'firms:IND': 100, 'government': 200}


def test_account_book_add_duplicate_raises():
    """阶段 3：重复账户 ID 抛 KeyError。"""
    book = AccountBook(city_id='c1')
    book.open('firms:IND', FIRM, 'IND', cash_cents=100)
    with pytest.raises(KeyError):
        book.open('firms:IND', FIRM, 'IND', cash_cents=200)


def test_account_book_add_unknown_kind_raises():
    """阶段 3：未知 owner_kind 抛 ValueError。"""
    book = AccountBook(city_id='c1')
    with pytest.raises(ValueError):
        book.open('firms:IND', 'unknown_kind', 'IND', cash_cents=100)


def test_account_book_add_ocean_requires_no_city_id():
    """阶段 3：海洋账户 city_id=None。"""
    book = AccountBook(city_id='c1')
    with pytest.raises(ValueError):
        # 海洋账户不能有 city_id
        book.add(Account(OCEAN_ACCOUNT, EXTERNAL, 'ocean', city_id='c1', cash_cents=0))


def test_account_book_add_peer_requires_city_id():
    """阶段 3：邻城账户必须有 city_id。"""
    book = AccountBook(city_id='c1')
    with pytest.raises(ValueError):
        book.add(Account('city:B', PEER, 'B', city_id=None, cash_cents=0))


# ===== 工厂函数：build_industrial_city_accounts =====

def test_build_industrial_city_accounts_has_all_required():
    """阶段 3：build_industrial_city_accounts 创建 V2.1 §1.5 完整账户体系。"""
    book = build_industrial_city_accounts()
    expected = {
        'government',
        OCEAN_ACCOUNT,
        'church',
        'property_pass',
        # 六非公务员行业
        'firms:AGR', 'firms:IND', 'firms:SRV', 'firms:BNK', 'firms:MAG',
        # 八群体居民
        'cohorts:H0', 'cohorts:H1', 'cohorts:H2', 'cohorts:H3',
        'cohorts:H4', 'cohorts:H5', 'cohorts:H6', 'cohorts:H7',
    }
    actual = set(book.snapshot().keys())
    assert expected.issubset(actual), f"缺失账户：{expected - actual}"


def test_build_industrial_city_accounts_no_gov_firm():
    """阶段 3：GOV 没有独立经营钱包（V2.1 §1.5 公务员工资由 GOV 真实支付）。"""
    book = build_industrial_city_accounts()
    # 行业独立钱包：AGR/IND/SRV/BNK/MAG（5 个）；CHU 共用 church；GOV 没有
    firm_ids = [a.account_id for a in book if a.owner_kind == FIRM]
    assert 'firms:GOV' not in firm_ids
    # 应该恰好是 5 个非 GOV 行业（CHU 共用 church 钱包）
    assert len(firm_ids) == 5
    assert set(firm_ids) == {'firms:AGR', 'firms:IND', 'firms:SRV', 'firms:BNK', 'firms:MAG'}


def test_build_industrial_city_accounts_chu_uses_church():
    """阶段 3：CHU 行业经营现金与 church 教会钱包共用同一物理账户。"""
    book = build_industrial_city_accounts()
    # 不应有 firms:CHU
    assert 'firms:CHU' not in book
    # church 钱包存在，owner_kind=church
    assert 'church' in book
    assert book['church'].owner_kind == CHURCH_KIND
    assert book['church'].cash_cents == 2_000_000   # 20,000 CR


def test_build_industrial_city_accounts_opening_v21_14_12():
    """阶段 3：期初资金严格匹配 V2.1 §14.12。"""
    book = build_industrial_city_accounts()
    assert book['government'].cash_cents == 10_000_000     # 100,000 CR
    assert book['church'].cash_cents == 2_000_000          # 20,000 CR
    assert book['firms:AGR'].cash_cents == 7_000_000       # 70,000 CR
    assert book['firms:IND'].cash_cents == 25_000_000      # 250,000 CR
    assert book['firms:SRV'].cash_cents == 17_000_000      # 170,000 CR
    assert book['firms:BNK'].cash_cents == 6_000_000       # 60,000 CR
    assert book['firms:MAG'].cash_cents == 3_500_000       # 35,000 CR
    assert book[OCEAN_ACCOUNT].cash_cents == 0
    assert book['property_pass'].cash_cents == 0


def test_build_industrial_city_accounts_cohorts_opening():
    """阶段 3：八群体期初储蓄 300,000 CR（V2.1 §14.2）。每人 100 CR = 10000 分。"""
    book = build_industrial_city_accounts(
        cohort_opening_cents={
            0: 700_000, 1: 4_800_000, 2: 11_200_000, 3: 10_500_000,
            4: 400_000, 5: 450_000, 6: 900_000, 7: 1_050_000,
        }
    )
    total_cohort_cents = sum(
        book[f'cohorts:H{h}'].cash_cents for h in range(8)
    )
    assert total_cohort_cents == 30_000_000   # 300,000 CR = 30,000,000 分


def test_build_industrial_city_accounts_custom_opening():
    """阶段 3：可自定义期初资金（覆盖默认）。"""
    book = build_industrial_city_accounts(
        industry_opening_cents={'AGR': 99_000_000},
        cohort_opening_cents={0: 999},
    )
    assert book['firms:AGR'].cash_cents == 99_000_000
    assert book['cohorts:H0'].cash_cents == 999
    # 其它不变
    assert book['firms:IND'].cash_cents == 25_000_000


# ===== Ledger 完整账本 =====

def test_ledger_initialization():
    """阶段 3：Ledger 初始化。"""
    book = build_industrial_city_accounts()
    led = Ledger('industrial_city', book)
    assert led.city_id == 'industrial_city'
    assert led.month == 0
    assert led.transactions == []
    assert led.shortfall == {}
    assert led.peer_flows == []


def test_ledger_cash():
    """阶段 3：cash() 返回账户当前余额。"""
    book = build_industrial_city_accounts()
    led = Ledger('industrial_city', book)
    assert led.cash('government') == 10_000_000


def test_ledger_available_ocean_unlimited():
    """阶段 3：ocean 免检（V2.1 §1.5 海平面可无限支付）。"""
    book = build_industrial_city_accounts()
    led = Ledger('industrial_city', book)
    assert led.available('ocean', 999_999_999) == 999_999_999


def test_ledger_available_peer_uses_available_cents():
    """阶段 3：邻城账户受 available_cents 上限。"""
    book = build_industrial_city_accounts()
    # 手动开一个余额 1000 分的邻城
    book.add(Account('city:B', PEER, 'B', city_id='B', cash_cents=1000))
    led = Ledger('industrial_city', book)
    assert led.available('city:B', 5000) == 1000   # 5000 分 > 1000 → 截断为 1000


def test_ledger_available_local_uses_available_cents():
    """阶段 3：本地账户受 available_cents 上限（含预留）。"""
    book = build_industrial_city_accounts()
    led = Ledger('industrial_city', book)
    # government 100,000 CR = 10,000,000 分
    assert led.available('government', 9_000_000) == 9_000_000
    assert led.available('government', 11_000_000) == 10_000_000   # 截断


def test_ledger_pay_basic():
    """阶段 3：基本 pay 流程。"""
    book = build_industrial_city_accounts()
    led = Ledger('industrial_city', book)
    paid = led.pay('government', 'firms:IND', 1_000_000, 'public_procurement')
    assert paid == 1_000_000
    assert led.cash('government') == 9_000_000
    assert led.cash('firms:IND') == 25_000_000 + 1_000_000
    assert led.transactions[-1] == ('government', 'firms:IND', 1_000_000, 'public_procurement')


def test_ledger_pay_shortfall():
    """阶段 3：pay 超过余额时短付并记录 shortfall。"""
    book = build_industrial_city_accounts()
    led = Ledger('industrial_city', book)
    # government 10,000,000 分，付 99,999,999 分 → 短付 89,999,999
    paid = led.pay('government', 'firms:IND', 99_999_999, 'public_procurement')
    assert paid == 10_000_000
    assert 'government->firms:IND:public_procurement' in led.shortfall
    assert led.shortfall['government->firms:IND:public_procurement'] == 89_999_999


def test_ledger_pay_ocean_unlimited():
    """阶段 3：ocean 付任意金额都通过。"""
    book = build_industrial_city_accounts()
    led = Ledger('industrial_city', book)
    paid = led.pay('ocean', 'firms:IND', 1_000_000_000, 'export')
    assert paid == 1_000_000_000
    assert led.cash('firms:IND') == 25_000_000 + 1_000_000_000
    # ocean 余额可能变负（V2.1 §1.5 海平面近似恒定；但实际不守恒）
    # 这种情况是 V2.1 §11 TEST-04 的断言对象


def test_ledger_pay_peer_records_outbound_flow():
    """阶段 3：本城付给 city:B 时登记 direction='out' PeerFlow。"""
    book = build_industrial_city_accounts()
    book.add(Account('city:B', PEER, 'B', city_id='B', cash_cents=0))
    led = Ledger('industrial_city', book)
    led.pay('firms:IND', 'city:B', 500_000, 'export')
    outs = [f for f in led.peer_flows if f.direction == 'out' and f.peer_city_id == 'B']
    assert len(outs) == 1
    assert outs[0].amount_cents == 500_000
    assert outs[0].category == 'export'


def test_ledger_pay_from_peer_records_inbound_flow():
    """阶段 3：city:B 付给本城账户时登记 direction='in' PeerFlow。"""
    book = build_industrial_city_accounts()
    book.add(Account('city:B', PEER, 'B', city_id='B', cash_cents=1_000_000))
    led = Ledger('industrial_city', book)
    led.pay('city:B', 'government', 200_000, 'grant')
    ins = [f for f in led.peer_flows if f.direction == 'in' and f.peer_city_id == 'B']
    assert len(ins) == 1
    assert ins[0].amount_cents == 200_000
    assert ins[0].category == 'grant'


def test_ledger_pay_ocean_does_not_record_peer_flow():
    """阶段 3：本地 ↔ 海洋交易不记 peer_flow。"""
    book = build_industrial_city_accounts()
    led = Ledger('industrial_city', book)
    led.pay('firms:IND', 'ocean', 500_000, 'export')
    assert led.peer_flows == []


def test_ledger_pay_negative_amount_returns_zero():
    """阶段 3：pay 0 或负数返回 0。"""
    book = build_industrial_city_accounts()
    led = Ledger('industrial_city', book)
    assert led.pay('government', 'firms:IND', 0, 'x') == 0
    assert led.pay('government', 'firms:IND', -100, 'x') == 0


def test_ledger_pay_invalid_account_raises():
    """阶段 3：pay 引用不存在的账户抛 GameError。"""
    from backend.core.errors import GameError
    book = build_industrial_city_accounts()
    led = Ledger('industrial_city', book)
    with pytest.raises(GameError):
        led.pay('nonexistent', 'firms:IND', 100, 'x')


def test_ledger_pay_non_int_raises():
    """阶段 3：pay 非整数分抛 GameError。"""
    from backend.core.errors import GameError
    book = build_industrial_city_accounts()
    led = Ledger('industrial_city', book)
    with pytest.raises(GameError):
        led.pay('government', 'firms:IND', 100.5, 'x')


def test_ledger_begin_month_resets():
    """阶段 3：begin_month 重置 transactions / shortfall（peer_flows 累积）。"""
    book = build_industrial_city_accounts()
    led = Ledger('industrial_city', book)
    led.pay('government', 'firms:IND', 1_000_000, 'x')
    led.begin_month()
    assert led.transactions == []
    assert led.shortfall == {}


def test_ledger_ocean_net():
    """阶段 3：ocean_net = 海洋账户现金变化。"""
    book = build_industrial_city_accounts()
    led = Ledger('industrial_city', book)
    led.begin_month()
    opening_ocean = led.cash('ocean')
    led.pay('firms:IND', 'ocean', 500_000, 'export')
    assert led.ocean_net() == led.cash('ocean') - opening_ocean


def test_ledger_local_total():
    """阶段 3：local_total = 本地账户现金合计（不含 ocean/peer）。"""
    book = build_industrial_city_accounts()
    led = Ledger('industrial_city', book)
    total = sum(a.cash_cents for a in book.local_leaves())
    assert led.local_total() == total


# ===== PROP 月末清零（V2.1 §1.5 + §7.5）=====

def test_property_pass_clears_at_end_month():
    """阶段 3：end_month() 把 PROP 物业通道余额清零（V2.1 §1.5 + §7.5）。

    PROP 是同月内收入分配通道，月末清零，不创造房东人口。
    """
    book = build_industrial_city_accounts(
        cohort_opening_cents={0: 1_000_000}   # H0 给 10,000 CR 用于支付租金
    )
    led = Ledger('industrial_city', book)
    led.begin_month()
    # 物业租金 50,000 分 (500 CR) 从 H0 进入 PROP
    led.pay('cohorts:H0', 'property_pass', 50_000, 'rent')
    assert led.cash('property_pass') == 50_000
    led.end_month()
    assert led.cash('property_pass') == 0


def test_property_pass_no_owner_population():
    """阶段 3：PROP 账户 owner_kind=property_pass，不绑定居民人口。"""
    book = build_industrial_city_accounts()
    assert book['property_pass'].owner_kind == PROP_KIND
    # 不应被算作"居民人口"（V2.1 §1.5 PROP 不创造房东人口）


# ===== 行业 vs 居民储蓄：不同账户（V2.1 §7.3）=====

def test_industry_cash_separate_from_cohort_savings():
    """阶段 3：行业经营现金与同名行业居民储蓄是**不同账户**（V2.1 §7.3）。"""
    book = build_industrial_city_accounts()
    # IND 行业 vs IND 居民：两个独立账户
    assert 'firms:IND' in book
    assert 'cohorts:H2' in book   # H2 = IND 居民
    assert book['firms:IND'].account_id != book['cohorts:H2'].account_id
    # 它们的 cash 互不影响
    led = Ledger('industrial_city', book)
    initial_industry = led.cash('firms:IND')
    initial_cohort = led.cash('cohorts:H2')
    # 行业收钱
    led.pay('government', 'firms:IND', 5_000_000, 'public_procurement')
    assert led.cash('firms:IND') == initial_industry + 5_000_000
    assert led.cash('cohorts:H2') == initial_cohort  # 不变


def test_industry_negative_cash_does_not_autofill_from_cohort():
    """阶段 3：行业现金为负时不能自动由同名行业居民储蓄填补（V2.1 §7.3）。"""
    book = build_industrial_city_accounts()
    led = Ledger('industrial_city', book)
    # 故意把 firms:IND 现金压到 0
    initial = led.cash('firms:IND')
    led.pay('firms:IND', 'government', initial, 'x')  # 全转出
    assert led.cash('firms:IND') == 0
    # 此时行业亏损，居民储蓄应不变（不自动填补）
    cohort_h2_initial = led.cash('cohorts:H2')
    # 再次尝试从行业扣款
    led.pay('firms:IND', 'government', 1000, 'x')
    assert led.cash('firms:IND') == 0    # 仍是 0（短付）
    assert led.cash('cohorts:H2') == cohort_h2_initial  # 居民储蓄**不**变


# ===== JSON 加载器 =====

def test_wallets_json_file_exists():
    """阶段 3：data/economy_v2/wallets.json 存在。"""
    assert (ECONOMY_V2_DIR / 'wallets.json').is_file()


def test_load_wallets_config_basic():
    """阶段 3：load_wallets_config 返回 _meta + openings。"""
    config = load_wallets_config()
    assert config['_meta']['version'] == '2.1.0'
    assert config['_meta']['archetype'] == 'industrial_city'
    assert 'openings' in config


def test_load_wallets_config_matches_v21_14_12():
    """阶段 3：JSON openings 字段匹配 V2.1 §14.12。"""
    config = load_wallets_config()
    o = config['openings']
    assert o['government'] == 10_000_000
    assert o['church'] == 2_000_000
    assert o['ocean'] == 0
    assert o['property_pass'] == 0
    assert o['firms']['AGR'] == 7_000_000
    assert o['firms']['IND'] == 25_000_000
    assert o['firms']['SRV'] == 17_000_000
    assert o['firms']['BNK'] == 6_000_000
    assert o['firms']['MAG'] == 3_500_000
    # 居民合计 300,000 CR = 30,000,000 分
    cohort_total = sum(o['cohorts'].values())
    assert cohort_total == 30_000_000


def test_load_wallets_config_missing_file():
    """阶段 3：缺失文件抛 FileNotFoundError。"""
    import pathlib
    with pytest.raises(FileNotFoundError):
        load_wallets_config(path=pathlib.Path('/nonexistent/wallets.json'))


# ===== v21 包整体可被 import =====

def test_v21_package_imports_accounts():
    """阶段 3：v21 包导出阶段 0+1+2+3 全部符号。"""
    import backend.systems.v21 as v21
    assert v21.Account is Account
    assert v21.AccountBook is AccountBook
    assert v21.Ledger is Ledger
    assert v21.PeerFlow is PeerFlow
    assert callable(v21.build_industrial_city_accounts)
    assert callable(v21.load_wallets_config)
    assert callable(v21.is_ocean)
    assert callable(v21.is_peer)
    assert callable(v21.peer_account)
    assert callable(v21.peer_city_id_of)
