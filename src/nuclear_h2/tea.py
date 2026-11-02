"""Lifecycle techno-economic analysis (TEA): discounted cash flow (Eqs. 47-48), component capital costs (Eq. 49),
replacements, MACRS depreciation, federal credits (Eqs. 50-51 and the production credits), levelized costs
(Eqs. 52-54) and the four case studies (docs/formulation.md, docs/scenarios.md).

Timeline: index i = 0 .. C + N - 1, where C construction years (outlays only) precede N operating years.
Every operating year repeats the annual operation given (the dispatch is optimized once); optionally a second
annual operation is used from the year after the hydrogen production credit ends.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .finance import adjusted_basis, credit_45u, irr, macrs_schedule, payback_years, present_value, scaled_cost
from .parameters import ParameterError
from .results import Operation

GROUPS = ("nuclear", "hydrogen", "battery", "shared")
CASES = ("existing", "retrofit", "greenfield")
SIZE_KEYS = ("electrolyzer_mw", "battery_mwh", "battery_mw", "storage_kg", "reactor_mwe")


@dataclass
class CapexItem:
    """One capital component. Cost = Eq. (49) with ``size`` (a number, or a key of the operation's capacities:
    'electrolyzer_mw', 'battery_mwh', 'battery_mw', 'storage_kg', or 'reactor_mwe' for the turbine rating);
    ``ref_size=None`` means a fixed cost. ``schedule`` (required) maps a year offset relative to the first operating
    year to the share of the cost paid in it: -C .. -1 are the C construction years and 0 the first operating year.
    ``cases`` lists the cases that build the item."""
    name: str
    group: str
    base_cost_usd: float
    schedule: Dict[int, float]
    ref_size: Optional[float] = None
    size: object = None
    exponent: float = 1.0
    macrs_years: Optional[int] = None
    itc_eligible: bool = False
    cases: Tuple[str, ...] = ("retrofit", "greenfield")

    def cost(self, capacities: Dict[str, float]) -> float:
        if self.ref_size is None:
            return float(self.base_cost_usd)
        size = capacities[self.size] if isinstance(self.size, str) else self.size
        if size is None:
            raise ParameterError(f"capex item {self.name}: a scaled item needs a size")
        return scaled_cost(self.base_cost_usd, self.ref_size, float(size), self.exponent)


@dataclass
class Replacement:
    """Replacement of a capital item in given operating years: a fraction of the item's cost (its cost basis) or a
    fixed amount, depreciated with the item's MACRS class from the year it is made. ``cases`` lists the cases in
    which it is charged; by default the cases that build the item. List other cases explicitly to charge
    replacements of an asset whose capital is sunk (e.g. components of an existing reactor in Cases 1-2)."""
    item: str
    years: Tuple[int, ...]
    fraction: Optional[float] = None
    amount_usd: Optional[float] = None
    cases: Optional[Tuple[str, ...]] = None


@dataclass
class FixedCost:
    """Fixed O&M: ``usd_per_unit_yr`` x size (a number or a capacity key; ``size=None`` means a lump sum per year),
    or ``capex_fraction`` x the cost of the item named ``of_item`` (its cost basis, also when its capital is sunk in
    the case); escalated by ``escalation`` per year. ``cases`` lists the cases in which it is charged."""
    name: str
    group: str
    usd_per_unit_yr: float = 0.0
    size: object = None
    capex_fraction: float = 0.0
    of_item: Optional[str] = None
    escalation: float = 0.0
    cases: Tuple[str, ...] = CASES


@dataclass
class Credit45U:
    """Zero-emission nuclear production credit, Eq. (50), for calendar years first_year .. last_year."""
    max_usd_per_mwh: float
    price_low: float
    price_high: float
    first_year: int
    last_year: int
    basis: str = "generation"          # "generation" (turbine output) or "export" (energy sold)


@dataclass
class CreditPerUnit:
    """A production credit paid per unit for the first ``years`` operating years: 45Y-type (per MWh, basis
    'generation' or 'export') or the hydrogen production credit (per kg produced)."""
    usd_per_unit: float
    years: int
    basis: str = "generation"


@dataclass
class Credit48E:
    """Investment credit: ``rate`` x the cost of ITC-eligible items, received in the first operating year, with the
    depreciable basis reduced by ``basis_reduction`` x credit (Eq. 51)."""
    rate: float
    basis_reduction: float


@dataclass
class Credits:
    u45: Optional[Credit45U] = None
    y45: Optional[CreditPerUnit] = None
    e48: Optional[Credit48E] = None
    hydrogen: Optional[CreditPerUnit] = None
    taxable: bool = False              # credits enter after tax (False) or as taxable revenue (True)

    def validate(self) -> None:
        if self.y45 is not None and self.e48 is not None:
            raise ParameterError("45Y and 48E are alternatives; choose one")


@dataclass
class TEAInputs:
    lifetime_years: int
    discount_rate: float
    tax_rate: float
    construction_years: int = 0
    first_operating_year: int = 2030
    nuclear_vom_usd_per_mwh: float = 0.0
    fuel_usd_per_mwh: float = 0.0
    fuel_basis: str = "generation"     # "generation" (MWh_e of turbine output) or "reactor" (reactor heat x heat_to_power)
    capex: List[CapexItem] = field(default_factory=list)
    replacements: List[Replacement] = field(default_factory=list)
    fixed_costs: List[FixedCost] = field(default_factory=list)
    credits: Credits = field(default_factory=Credits)
    case: str = "retrofit"

    def validate(self) -> None:
        if self.case not in CASES:
            raise ParameterError(f"case must be one of {CASES}")
        if self.lifetime_years < 1 or self.construction_years < 0:
            raise ParameterError("lifetime_years >= 1 and construction_years >= 0 needed")
        if self.fuel_basis not in ("generation", "reactor"):
            raise ParameterError("fuel_basis must be 'generation' or 'reactor'")
        self.credits.validate()
        names = [c.name for c in self.capex]
        if len(names) != len(set(names)):
            raise ParameterError("capex item names must be unique")
        for c in self.capex:
            if c.group not in GROUPS:
                raise ParameterError(f"capex item {c.name}: group must be one of {GROUPS}")
            for y, share in c.schedule.items():
                if isinstance(y, bool) or not isinstance(y, (int, np.integer)):
                    raise ParameterError(f"capex item {c.name}: payment year offsets must be integers (got {y!r})")
                if not (np.isfinite(share) and share >= 0):
                    raise ParameterError(f"capex item {c.name}: payment shares must be finite and >= 0")
            if abs(sum(c.schedule.values()) - 1.0) > 1e-9:
                raise ParameterError(f"capex item {c.name}: payment shares must sum to 1")
            if self.case in c.cases:
                for y in c.schedule:
                    if not -self.construction_years <= int(y) <= 0:
                        raise ParameterError(f"capex item {c.name}: payment year {y} outside -{self.construction_years}..0 "
                                             "(construction years and the first operating year)")
        for r in self.replacements:
            if r.item not in names:
                raise ParameterError(f"replacement of unknown item {r.item}")
            if (r.fraction is None) == (r.amount_usd is None):
                raise ParameterError(f"replacement of {r.item}: give fraction or amount_usd")
            if r.cases is not None and not set(r.cases) <= set(CASES):
                raise ParameterError(f"replacement of {r.item}: cases must be among {CASES}")
        for f in self.fixed_costs:
            if f.group not in GROUPS:
                raise ParameterError(f"fixed cost {f.name}: group must be one of {GROUPS}")
            if f.of_item is not None and f.of_item not in names:
                raise ParameterError(f"fixed cost {f.name}: unknown item {f.of_item}")


@dataclass
class TEAResult:
    case: str
    npv: float
    irr: float
    payback_years: float
    lcoe: float
    lcoh: float
    lcos: float
    cash_flows: pd.DataFrame
    capex_by_item: Dict[str, float]

    def summary(self) -> Dict[str, float]:
        return {"case": self.case, "npv_musd": self.npv / 1e6, "irr": self.irr, "payback_years": self.payback_years,
                "lcoe_usd_per_mwh": self.lcoe, "lcoh_usd_per_kg": self.lcoh, "lcos_usd_per_mwh": self.lcos,
                "capex_musd": sum(self.capex_by_item.values()) / 1e6}


def _physical(plant):
    """The plant's physical components with sizing bounds removed (capacities are compared separately); whether each
    component is sized is kept, because a sized store changes the operating rules (Eq. 40)."""
    def strip(part, **none):
        return None if part is None else (replace(part, **none), part.sized)
    return (plant.turbine,
            strip(plant.electrolyzer, capacity_mw=None, capacity_range_mw=None),
            strip(plant.battery, energy_mwh=None, energy_range_mwh=None),
            strip(plant.storage, capacity_kg=None, capacity_range_kg=None))


def _annual(op: Operation) -> Dict[str, float]:
    return op.annual_totals()


def _credit_45u_annual(op: Operation, c: Credit45U) -> float:
    rate = credit_45u(op.ts.energy_price, c.max_usd_per_mwh, c.price_low, c.price_high)
    energy = op.hourly["p_turb"] if c.basis == "generation" else op.hourly["p_sale"]
    return float(np.sum(rate * energy.to_numpy() * op.ts.dt_h)) * op.annualization


def _energy_basis(a: Dict[str, float], basis: str) -> float:
    if basis == "generation":
        return a["generation_mwh"]
    if basis == "export":
        return a["export_mwh"]
    raise ParameterError("a per-MWh credit basis must be 'generation' or 'export'")


def evaluate(operation: Operation, inputs: TEAInputs, operation_after_h2_credit: Optional[Operation] = None) -> TEAResult:
    """Discounted cash flow of one case (Eqs. 47-48) with credits, depreciation and levelized costs (Eqs. 52-54)."""
    inputs.validate()
    C, N = inputs.construction_years, inputs.lifetime_years
    years = C + N
    idx = np.arange(years)
    op_year = idx - C + 1                     # 1..N in operation, <= 0 during construction
    operating = op_year >= 1
    r, tau = inputs.discount_rate, inputs.tax_rate
    cr = inputs.credits
    caps = dict(operation.capacities)
    caps["reactor_mwe"] = float(operation.plant.turbine.p_max_mw)
    if operation_after_h2_credit is not None:
        o2 = operation_after_h2_credit
        same = (_physical(o2.plant) == _physical(operation.plant)
                and o2.ts.hours == operation.ts.hours and o2.ts.dt_h == operation.ts.dt_h)
        if not same:
            raise ParameterError("the second operation must be the same plant: equal turbine and component "
                                 "parameters (only the sizing range may differ) and the same horizon")
        for key, value in operation.capacities.items():
            other = o2.capacities.get(key, 0.0)
            if abs(other - value) > 1e-6 * max(1.0, abs(value)):
                raise ParameterError(f"the second operation has a different {key} ({other} vs {value}); "
                                     "re-dispatch the built plant (scenarios.with_capacities)")
    basis_cost = {c.name: c.cost(caps) for c in inputs.capex}      # cost basis of every asset (built or sunk)
    items = [c for c in inputs.capex if inputs.case in c.cases]    # assets built (paid for) in this case
    item_cost = {c.name: basis_cost[c.name] for c in items}
    by_item = {c.name: c for c in items}

    def zeros():
        return np.zeros(years)

    capex = {g: zeros() for g in GROUPS}
    depreciation = zeros()
    itc = zeros()
    # initial capital, paid over the construction schedule; placed in service at the start of operation
    itc_total = 0.0
    if cr.e48 is not None:
        itc_total = cr.e48.rate * sum(item_cost[c.name] for c in items if c.itc_eligible)
    for c in items:
        cost = item_cost[c.name]
        for y, share in c.schedule.items():
            capex[c.group][C + int(y)] += share * cost     # offset 0 = first operating year
        if c.macrs_years:
            basis = cost
            if cr.e48 is not None and c.itc_eligible:
                basis = adjusted_basis(cost, cr.e48.rate * cost, cr.e48.basis_reduction)       # Eq. (51)
            sched = macrs_schedule(c.macrs_years)
            for j, frac in enumerate(sched):
                i = C + j
                if i < years:
                    depreciation[i] += basis * frac
    if itc_total and N >= 1:
        itc[C] += itc_total
    # replacements in operating years (charged in rep.cases, by default the cases that build the item)
    all_items = {c.name: c for c in inputs.capex}
    for rep in inputs.replacements:
        item = all_items[rep.item]
        if inputs.case not in (rep.cases if rep.cases is not None else item.cases):
            continue
        amount = rep.amount_usd if rep.amount_usd is not None else rep.fraction * basis_cost[rep.item]
        for y in rep.years:
            if 1 <= y <= N:
                i = C + y - 1
                capex[item.group][i] += amount
                if item.macrs_years:
                    for j, frac in enumerate(macrs_schedule(item.macrs_years)):
                        if i + j < years:
                            depreciation[i + j] += amount * frac

    # operating revenues and costs
    a1 = _annual(operation)
    a2 = _annual(operation_after_h2_credit) if operation_after_h2_credit is not None else a1
    h2_years = cr.hydrogen.years if cr.hydrogen is not None else N
    cols = {k: zeros() for k in ("energy_revenue", "h2_sales_revenue", "as_revenue", "variable_opex", "nuclear_vom",
                                 "fuel", "fixed_om", "credit_45u", "credit_45y", "credit_h2", "credit_48e",
                                 "generation_mwh", "h2_kg", "battery_discharge_mwh",
                                 "as_turbine", "as_electrolyzer", "as_battery",
                                 "opex_nuclear", "opex_hydrogen", "opex_battery", "electrolyzer_mwh", "steam_cost")}
    u45_annual = (_credit_45u_annual(operation, cr.u45) if cr.u45 is not None else 0.0,
                  _credit_45u_annual(operation_after_h2_credit, cr.u45)
                  if (cr.u45 is not None and operation_after_h2_credit is not None) else None)
    heat_to_power = operation.plant.turbine.heat_to_power
    for i in idx[operating]:
        k = int(op_year[i])
        late = cr.hydrogen is not None and k > h2_years and operation_after_h2_credit is not None
        a = a2 if late else a1
        cols["energy_revenue"][i] = a["energy_revenue"]
        cols["h2_sales_revenue"][i] = a["h2_sales_revenue"]
        cols["as_turbine"][i] = a["as_revenue_turbine"]
        cols["as_electrolyzer"][i] = a["as_revenue_electrolyzer"]
        cols["as_battery"][i] = a["as_revenue_battery"]
        cols["as_revenue"][i] = a["as_revenue_turbine"] + a["as_revenue_electrolyzer"] + a["as_revenue_battery"]
        h2_var = a["electrolyzer_vom"] + a["water_cost"] + a["startup_cost"] + a["storage_vom"]
        cols["variable_opex"][i] = a["turbine_vom"] + h2_var + a["battery_vom"]
        cols["nuclear_vom"][i] = inputs.nuclear_vom_usd_per_mwh * a["generation_mwh"]
        fuel_energy = a["generation_mwh"] if inputs.fuel_basis == "generation" else a["reactor_heat_mwhth"] * heat_to_power
        cols["fuel"][i] = inputs.fuel_usd_per_mwh * fuel_energy
        cols["generation_mwh"][i] = a["generation_mwh"]
        cols["h2_kg"][i] = a["h2_kg"]
        cols["battery_discharge_mwh"][i] = a["battery_discharge_mwh"]
        cols["electrolyzer_mwh"][i] = a["electrolyzer_mwh"]
        cols["steam_cost"][i] = a["steam_opportunity_cost"]
        cols["opex_nuclear"][i] = a["turbine_vom"] + cols["nuclear_vom"][i] + cols["fuel"][i]
        cols["opex_hydrogen"][i] = h2_var
        cols["opex_battery"][i] = a["battery_vom"]
        if cr.u45 is not None and cr.u45.first_year <= inputs.first_operating_year + k - 1 <= cr.u45.last_year:
            cols["credit_45u"][i] = u45_annual[1] if (late and u45_annual[1] is not None) else u45_annual[0]
        if cr.y45 is not None and k <= cr.y45.years:
            cols["credit_45y"][i] = cr.y45.usd_per_unit * _energy_basis(a, cr.y45.basis)
        if cr.hydrogen is not None and k <= cr.hydrogen.years:
            cols["credit_h2"][i] = cr.hydrogen.usd_per_unit * a["h2_kg"]
    cols["credit_48e"] = itc
    for f in inputs.fixed_costs:
        if inputs.case not in f.cases:
            continue
        if f.of_item is not None:
            base = f.capex_fraction * basis_cost[f.of_item]       # also for an asset whose capital is sunk
        else:
            size = 1.0 if f.size is None else (caps[f.size] if isinstance(f.size, str) else float(f.size))
            base = f.usd_per_unit_yr * size
        for i in idx[operating]:
            k = int(op_year[i])
            amount = base * (1.0 + f.escalation) ** (k - 1)
            cols["fixed_om"][i] += amount
            if f.group != "shared":
                cols[f"opex_{f.group}"][i] += amount

    credits = cols["credit_45u"] + cols["credit_45y"] + cols["credit_h2"] + cols["credit_48e"]
    revenue = cols["energy_revenue"] + cols["h2_sales_revenue"] + cols["as_revenue"]
    opex = cols["variable_opex"] + cols["nuclear_vom"] + cols["fuel"] + cols["fixed_om"]
    capex_total = sum(capex.values())
    if cr.taxable:
        taxable = revenue + credits - opex - depreciation
        cf = np.where(operating, taxable * (1 - tau) + depreciation, 0.0) - capex_total            # Eq. (48)
    else:
        taxable = revenue - opex - depreciation
        cf = np.where(operating, taxable * (1 - tau) + depreciation + credits, 0.0) - capex_total  # Eq. (48)
    table = pd.DataFrame({"operating_year": op_year, "calendar_year": inputs.first_operating_year + op_year - 1,
                          "capex": capex_total, "revenue": revenue, "opex": opex, "depreciation": depreciation,
                          "credits": credits, "cash_flow": cf, **{k: v for k, v in cols.items()},
                          **{f"capex_{g}": capex[g] for g in GROUPS}})
    discount = (1.0 + r) ** idx

    def pv(x):
        return float(np.sum(np.asarray(x) / discount))

    # levelized costs (Eqs. 52-54), pre-tax, discounted to the first year of the timeline
    gen = pv(cols["generation_mwh"])
    lcoe = (pv(capex["nuclear"] + cols["opex_nuclear"] - cols["as_turbine"]) / gen) if gen > 0 else float("nan")
    h2 = pv(cols["h2_kg"])
    elec_cost = cols["electrolyzer_mwh"] * (lcoe if np.isfinite(lcoe) else 0.0)
    lcoh = (pv(capex["hydrogen"] + cols["opex_hydrogen"] + elec_cost + cols["steam_cost"] - cols["as_electrolyzer"]) / h2
            if h2 > 0 else float("nan"))
    dis = pv(cols["battery_discharge_mwh"])
    lcos = (pv(capex["battery"] + cols["opex_battery"] - cols["as_battery"]) / dis) if dis > 0 else float("nan")
    return TEAResult(case=inputs.case, npv=present_value(cf, r), irr=irr(cf), payback_years=payback_years(cf, C),
                     lcoe=lcoe, lcoh=lcoh, lcos=lcos, cash_flows=table, capex_by_item=item_cost)


def case_inputs(base: TEAInputs, case: str, lifetime_years: Optional[int] = None, with_45u: bool = False,
                policy: str = "none", hydrogen_credit: bool = True) -> TEAInputs:
    """Inputs of one case study from a common set (docs/scenarios.md).

    case 'existing' (Case 1): business-as-usual reactor, nuclear capital sunk (no item is built), no hydrogen.
    case 'retrofit' (Case 2): existing reactor with added hydrogen and battery systems, nuclear capital sunk.
    case 'greenfield' (Cases 3-4): new integrated system, all capital; two lifetimes give Cases 3 and 4.
    ``with_45u`` keeps the 45U credit of ``base`` (existing reactors only); ``policy`` for greenfield is
    'none', '45y' or '48e' (the credits of ``base``); ``hydrogen_credit`` keeps the hydrogen production credit.
    """
    if case not in CASES:
        raise ParameterError(f"case must be one of {CASES}")
    if policy not in ("none", "45y", "48e"):
        raise ParameterError("policy must be 'none', '45y' or '48e'")
    if case != "greenfield" and policy != "none":
        raise ParameterError("45Y and 48E apply to greenfield cases")
    if case == "greenfield" and with_45u:
        raise ParameterError("45U applies to existing reactors")
    c = base.credits
    credits = Credits(u45=c.u45 if with_45u else None, y45=c.y45 if policy == "45y" else None,
                      e48=c.e48 if policy == "48e" else None,
                      hydrogen=c.hydrogen if (hydrogen_credit and case != "existing") else None, taxable=c.taxable)
    if with_45u and c.u45 is None:
        raise ParameterError("with_45u needs a 45U credit in the base inputs")
    if policy == "45y" and c.y45 is None or policy == "48e" and c.e48 is None:
        raise ParameterError(f"policy {policy} needs that credit in the base inputs")
    out = replace(base, case=case, credits=credits,
                  lifetime_years=lifetime_years if lifetime_years is not None else base.lifetime_years)
    if case == "existing":
        out = replace(out, construction_years=0)       # capital items are kept as cost basis; none is built
    return out


def results_table(results: Sequence[Tuple[str, TEAResult]]) -> pd.DataFrame:
    """Summary rows (label, NPV in million $, IRR, payback, LCOE, LCOH, LCOS, initial capex)."""
    rows = []
    for label, res in results:
        row = {"label": label}
        row.update(res.summary())
        rows.append(row)
    return pd.DataFrame(rows).set_index("label")


__all__ = ["GROUPS", "CASES", "CapexItem", "Replacement", "FixedCost", "Credit45U", "CreditPerUnit", "Credit48E",
           "Credits", "TEAInputs", "TEAResult", "evaluate", "case_inputs", "results_table"]
