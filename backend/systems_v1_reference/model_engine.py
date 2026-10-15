"""实验室状态与装配；所有基准和规则取自配置。"""
from dataclasses import dataclass, field
from backend.config import LABOR_CONSUMPTION, REFERENCE_CITY, read_json, ROOT
from backend.model.economy import cr_to_cents
from backend.systems.model_ledger import split, split_bounded

@dataclass
class SectorState:
    sector_id: str
    name: str
    firm_count: int
    employees: int
    wage_per_employee_cents: int
    capacity_units: int
    capital_units: int
    depreciation_cents: int
    fees_cents: int
    commercial_rent_cents: int
    cash_cents: int
    base: dict = field(default_factory=dict)
    planned_jobs: int = 0
    price: float = 1.0
    utilisation: float = 0.0
    readiness: float = 1.0
    revenue_cents: int = 0
    input_cost_cents: int = 0
    wage_bill_cents: int = 0
    profit_before_tax_cents: int = 0
    profit_after_tax_cents: int = 0
    profit_tax_cents: int = 0
    dividend_cents: int = 0
    wage_paid: int = 0
    rent_paid: int = 0
    fees_paid: int = 0
    imports_paid: int = 0
    capex_paid: int = 0
    arrears: int = 0
    loss_streak: int = 0
    cash_streak: int = 0
    sales_history: list = field(default_factory=list)
    investment_reserve: int = 0
    unmet: int = 0

    @property
    def account(self):
        return 'firms:' + self.sector_id

    @property
    def cash_runway_months(self):
        fixed = self.wage_bill_cents + self.commercial_rent_cents + self.fees_cents
        return min(999.0, self.cash_cents / fixed) if fixed else 999.0

@dataclass
class CohortState:
    band_id: str
    name: str
    population: int
    households: int
    employed: int
    unemployed: int
    non_labour: int
    wage_weight: float
    asset_weight: float
    property_tax_share: float
    relief_share: float
    consumption_ratio: float
    basket: dict
    skill_level: str
    cash_cents: int = 0
    wage_income_cents: int = 0
    asset_income_cents: int = 0
    last_disposable_cents: int = 0
    disposable_cents: int = 0
    consumption_paid: int = 0
    rental_due: int = 0
    donation_paid: int = 0
    taxes_paid: int = 0
    relief_paid: int = 0
    budget: int = 0

    @property
    def account(self):
        return 'households:' + self.band_id

    @property
    def gross_income_cents(self):
        return self.wage_income_cents + self.asset_income_cents

    @property
    def per_capita_cents(self):
        return self.gross_income_cents / self.population if self.population else 0

@dataclass
class ResourceState:
    resource_id: str
    name: str
    unit: str
    unit_price_cents: int
    stock: float
    base_production: float
    base_use: float
    base_import: float
    base_export: float
    produced: float = 0
    used: float = 0
    imported: float = 0
    exported: float = 0
    wasted: float = 0

    @property
    def closing(self):
        return self.stock

@dataclass
class SimulationResult:
    month: int
    metrics: dict
    sectors: list
    cohorts: list
    events: list
    transactions: list = field(default_factory=list)
    resources: list = field(default_factory=list)

    def get(self, key):
        return self.metrics.get(key)

def build_sectors(params):
    rows = REFERENCE_CITY['firms']['sectors']
    public_share = REFERENCE_CITY['public_sector']['employees'] / REFERENCE_CITY['demographics']['employed']
    counts = split(round(params.get('population.employed') * (1 - public_share)), [r['employees'] for r in rows])
    openings = {r['sector']: r['opening_cash'] for r in read_json(ROOT / 'data/economy/calibration_overrides.json')['opening_balances']['firms']}
    result = []
    for row, count in zip(rows, counts):
        scale = count / row['employees']
        result.append(SectorState(row['id'], row['name'], round(row['firms'] * scale), count,
                                  cr_to_cents(row['wage_per_employee']),
                                  round(cr_to_cents(row['revenue']) * scale / params.get('capacity.reference_utilisation')),
                                  round(cr_to_cents(row['revenue']) * scale),
                                  round(cr_to_cents(row['depreciation']) * scale),
                                  round(cr_to_cents(row['fees']) * scale),
                                  round(cr_to_cents(row['commercial_rent']) * scale),
                                  round(cr_to_cents(openings[row['id']]) * scale * params.get('feedback.firm_cash_multiplier')),
                                  dict(row), count))
    return result

def build_cohorts(params, public_wage_cents=0):
    rows = LABOR_CONSUMPTION['income_bands']
    populations = split(params.get('population.start'), [r['population'] for r in rows])
    emps = split_bounded(params.get('population.employed'), [r['employed'] for r in rows], populations)
    uns = split_bounded(params.get('population.unemployed'), [r['unemployed'] for r in rows], [n - e for n, e in zip(populations, emps)])
    hhs = split(params.get('population.households'), [r['households'] for r in rows])
    result = []
    for i, row in enumerate(rows):
        result.append(CohortState(row['id'], row['name'], populations[i], hhs[i], emps[i], uns[i],
                                  populations[i] - emps[i] - uns[i], row['wage_income'], row['asset_income'],
                                  row['property_tax_share'], row['relief_share'], row['spend_ratio'], dict(row['basket']),
                                  params.get('labour.skill_levels')[i]))
    return result

def build_resources(params):
    return [ResourceState(r['id'], r['name'], r['unit'], cr_to_cents(r['unit_price']), float(r['opening']),
                          float(r['produced']), float(r['used']), float(r['imported']), float(r['exported']))
            for r in params.get('resources.items')]
