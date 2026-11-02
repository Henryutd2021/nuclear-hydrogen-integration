"""Lifecycle TEA: cash flows recomputed by hand for small cases, credit timing, depreciation, replacements and
levelized costs."""

import numpy as np
import pytest

from nuclear_h2 import (CapexItem, Credit45U, Credit48E, CreditPerUnit, Credits, FixedCost, ParameterError,
                        Replacement, StudyDesign, TEAInputs, baseline_operation, case_inputs, case_matrix,
                        credit_45u, evaluate, macrs_schedule, optimize)

from conftest import battery, electrolyzer, market, plant, series, storage

PRICES = np.array([24.0, 31.0, 47.0, 36.0, 19.0, 52.0])


@pytest.fixture(scope="module")
def baseline():
    return baseline_operation(plant(), series(PRICES))


@pytest.fixture(scope="module")
def hybrid():
    return optimize(plant(battery=battery(), market=market(as_mode="bid")), series(PRICES, as_price=None))


@pytest.fixture(scope="module")
def hybrid_no_credit():
    p = plant(battery=battery(), market=market(as_mode="bid", h2_credit_usd_per_kg=0.0))
    return optimize(p, series(PRICES, as_price=None))


def pv(x, r):
    x = np.asarray(x, dtype=float)
    return float(np.sum(x / (1 + r) ** np.arange(x.size)))


def test_existing_case_by_hand(baseline):
    a = baseline.annual_totals()
    inp = TEAInputs(lifetime_years=4, discount_rate=0.09, tax_rate=0.0, nuclear_vom_usd_per_mwh=1.1,
                    fuel_usd_per_mwh=6.0, fixed_costs=[FixedCost("fom", "nuclear", usd_per_unit_yr=2.2e6, escalation=0.03)],
                    case="existing")
    res = evaluate(baseline, inp)
    gen = a["generation_mwh"]
    opex = a["turbine_vom"] + (1.1 + 6.0) * gen + 2.2e6 * 1.03 ** np.arange(4)
    cf = a["energy_revenue"] - opex
    assert res.cash_flows["cash_flow"].to_numpy() == pytest.approx(cf)
    assert res.npv == pytest.approx(pv(cf, 0.09))
    # LCOE, Eq. (52): nuclear costs over generation, both discounted
    lcoe = pv(a["turbine_vom"] + (1.1 + 6.0) * gen + 2.2e6 * 1.03 ** np.arange(4), 0.09) / pv([gen] * 4, 0.09)
    assert res.lcoe == pytest.approx(lcoe)
    assert np.isnan(res.lcoh) and np.isnan(res.lcos)


def test_tax_depreciation_and_construction(hybrid):
    a = hybrid.annual_totals()
    item = CapexItem("electrolyzer", "hydrogen", 30e6, macrs_years=3, schedule={-2: 0.4, -1: 0.6})
    inp = TEAInputs(lifetime_years=5, discount_rate=0.08, tax_rate=0.3, construction_years=3, capex=[item],
                    case="retrofit")
    res = evaluate(hybrid, inp)
    cf = res.cash_flows
    assert cf["capex"].tolist()[:3] == pytest.approx([0.0, 12e6, 18e6])
    dep = np.zeros(8)
    dep[3:7] = 30e6 * macrs_schedule(3)
    revenue = a["energy_revenue"] + a["h2_sales_revenue"] + a["as_revenue_turbine"] + a["as_revenue_electrolyzer"] \
        + a["as_revenue_battery"]
    opex = a["turbine_vom"] + a["electrolyzer_vom"] + a["water_cost"] + a["startup_cost"] + a["battery_vom"] + a["storage_vom"]
    expected = np.where(np.arange(8) >= 3, (revenue - opex - dep) * 0.7 + dep, 0.0) - cf["capex"].to_numpy()  # Eq. (48)
    assert cf["cash_flow"].to_numpy() == pytest.approx(expected)
    assert res.npv == pytest.approx(pv(expected, 0.08))


def test_investment_credit_and_basis_reduction(hybrid):
    item = CapexItem("plant", "nuclear", 50e6, {-1: 0.7, 0: 0.3}, macrs_years=5, itc_eligible=True, cases=("greenfield",))
    other = CapexItem("battery", "battery", 10e6, {0: 1.0}, macrs_years=5, cases=("greenfield",))
    base = TEAInputs(lifetime_years=8, discount_rate=0.07, tax_rate=0.25, construction_years=1, capex=[item, other],
                     credits=Credits(e48=Credit48E(rate=0.4, basis_reduction=0.45)), case="greenfield")
    with_itc = evaluate(hybrid, base)
    without = evaluate(hybrid, case_inputs(base, "greenfield", policy="none"))
    cf1, cf0 = with_itc.cash_flows, without.cash_flows
    assert cf1["credit_48e"].tolist() == pytest.approx([0.0, 0.4 * 50e6] + [0.0] * 7)
    # Eq. (51): depreciable basis falls by 0.45 x credit, only for the eligible item
    assert (cf0["depreciation"] - cf1["depreciation"]).sum() == pytest.approx(0.45 * 0.4 * 50e6)


def test_credit_timing(hybrid, hybrid_no_credit):
    u45 = Credit45U(max_usd_per_mwh=11.0, price_low=26.0, price_high=44.0, first_year=2034, last_year=2035)
    h2c = CreditPerUnit(usd_per_unit=0.8, years=2)
    inp = TEAInputs(lifetime_years=5, discount_rate=0.05, tax_rate=0.2, first_operating_year=2033,
                    credits=Credits(u45=u45, hydrogen=h2c), case="retrofit")
    res = evaluate(hybrid, inp, operation_after_h2_credit=hybrid_no_credit)
    cf = res.cash_flows
    rate = credit_45u(PRICES, 11.0, 26.0, 44.0)
    early = np.sum(rate * hybrid.hourly["p_turb"]) * hybrid.annualization
    late = np.sum(rate * hybrid_no_credit.hourly["p_turb"]) * hybrid.annualization
    # calendar years 2034 and 2035 = operating years 2 and 3; year 3 already uses the operation without the credit
    assert cf["credit_45u"].tolist() == pytest.approx([0.0, early, late, 0.0, 0.0])
    assert cf["credit_h2"].tolist() == pytest.approx([0.8 * hybrid.annual("h2_kg")] * 2 + [0.0] * 3)
    # after the credit ends, the second operation is used
    assert cf["energy_revenue"].iloc[3] == pytest.approx(hybrid_no_credit.annual("energy_revenue"))
    assert cf["energy_revenue"].iloc[0] == pytest.approx(hybrid.annual("energy_revenue"))
    # credits enter after tax unless declared taxable
    taxed = evaluate(hybrid, TEAInputs(**{**inp.__dict__, "credits": Credits(u45=u45, hydrogen=h2c, taxable=True)}),
                     operation_after_h2_credit=hybrid_no_credit)
    diff = res.cash_flows["cash_flow"] - taxed.cash_flows["cash_flow"]
    assert diff.to_numpy() == pytest.approx(0.2 * cf["credits"].to_numpy())


def test_per_mwh_credit_years(hybrid):
    y45 = CreditPerUnit(usd_per_unit=17.0, years=3, basis="export")
    inp = TEAInputs(lifetime_years=6, discount_rate=0.05, tax_rate=0.0, credits=Credits(y45=y45), case="greenfield")
    cf = evaluate(hybrid, inp).cash_flows
    assert cf["credit_45y"].tolist() == pytest.approx([17.0 * hybrid.annual("export_mwh")] * 3 + [0.0] * 3)


def test_replacements_and_fixed_costs(hybrid):
    items = [CapexItem("stack", "hydrogen", 8e6, {0: 1.0}, ref_size=20.0, size="electrolyzer_mw", exponent=1.0, macrs_years=5)]
    reps = [Replacement("stack", years=(3, 9), fraction=0.25), Replacement("stack", years=(4,), amount_usd=1e6)]
    fixed = [FixedCost("stack_fom", "hydrogen", capex_fraction=0.018, of_item="stack", escalation=0.01),
             FixedCost("site", "shared", usd_per_unit_yr=3e5)]
    inp = TEAInputs(lifetime_years=6, discount_rate=0.065, tax_rate=0.0, capex=items, replacements=reps,
                    fixed_costs=fixed, case="retrofit")
    res = evaluate(hybrid, inp)
    cost = 8e6 * hybrid.capacities["electrolyzer_mw"] / 20.0
    assert res.capex_by_item["stack"] == pytest.approx(cost)
    capex = res.cash_flows["capex"].to_numpy()
    assert capex == pytest.approx([cost, 0, 0.25 * cost, 1e6, 0, 0])
    fom = res.cash_flows["fixed_om"].to_numpy()
    assert fom == pytest.approx(0.018 * cost * 1.01 ** np.arange(6) + 3e5)


def test_levelized_hydrogen_and_storage_costs(hybrid):
    items = [CapexItem("stack", "hydrogen", 5.5e6, {0: 1.0}, macrs_years=5), CapexItem("bess", "battery", 4e6, {0: 1.0}, macrs_years=5),
             CapexItem("core", "nuclear", 2e7, {0: 1.0}, macrs_years=5)]
    inp = TEAInputs(lifetime_years=5, discount_rate=0.055, tax_rate=0.25, capex=items, fuel_usd_per_mwh=5.0,
                    case="greenfield")
    res = evaluate(hybrid, inp)
    cf = res.cash_flows
    r = 0.055
    lcoe = pv(cf["capex_nuclear"] + cf["opex_nuclear"] - cf["as_turbine"], r) / pv(cf["generation_mwh"], r)
    assert res.lcoe == pytest.approx(lcoe)
    num = cf["capex_hydrogen"] + cf["opex_hydrogen"] + cf["electrolyzer_mwh"] * lcoe + cf["steam_cost"] - cf["as_electrolyzer"]
    assert res.lcoh == pytest.approx(pv(num, r) / pv(cf["h2_kg"], r))                       # Eq. (53)
    lcos = pv(cf["capex_battery"] + cf["opex_battery"] - cf["as_battery"], r) / pv(cf["battery_discharge_mwh"], r)
    assert res.lcos == pytest.approx(lcos)                                                   # Eq. (54)


def test_reactor_heat_fuel_basis(baseline):
    a = baseline.annual_totals()
    inp = TEAInputs(lifetime_years=2, discount_rate=0.05, tax_rate=0.0, fuel_usd_per_mwh=4.0, fuel_basis="reactor",
                    case="existing")
    fuel = evaluate(baseline, inp).cash_flows["fuel"]
    assert fuel.tolist() == pytest.approx([4.0 * a["reactor_heat_mwhth"] * baseline.plant.turbine.heat_to_power] * 2)


def test_case_inputs_rules():
    base = TEAInputs(lifetime_years=12, discount_rate=0.05, tax_rate=0.2, construction_years=3,
                     capex=[CapexItem("h2", "hydrogen", 1e6, schedule={-3: 0.3, -1: 0.7}),
                            CapexItem("core", "nuclear", 1e9, schedule={-3: 1.0}, cases=("greenfield",))],
                     fixed_costs=[FixedCost("h2_fom", "hydrogen", capex_fraction=0.01, of_item="h2")],
                     credits=Credits(u45=Credit45U(9.0, 20.0, 30.0, 2030, 2031), y45=CreditPerUnit(12.0, 4),
                                     e48=Credit48E(0.3, 0.4), hydrogen=CreditPerUnit(0.7, 3)))
    ex = case_inputs(base, "existing", with_45u=True)
    assert ex.construction_years == 0 and ex.capex == base.capex       # kept as cost basis; nothing is built
    assert ex.credits.u45 is not None and ex.credits.hydrogen is None
    g = case_inputs(base, "greenfield", 35, policy="48e")
    assert g.lifetime_years == 35 and g.credits.e48 is not None and g.credits.y45 is None and g.credits.u45 is None
    assert case_inputs(base, "retrofit", hydrogen_credit=False).credits.hydrogen is None
    for kw in (dict(case="retrofit", policy="45y"), dict(case="greenfield", with_45u=True), dict(case="other")):
        with pytest.raises(ParameterError):
            case_inputs(base, **kw)
    with pytest.raises(ParameterError):
        TEAInputs(lifetime_years=5, discount_rate=0.05, tax_rate=0.2,
                  credits=Credits(y45=CreditPerUnit(1.0, 1), e48=Credit48E(0.1, 0.1))).validate()
    with pytest.raises(ParameterError):
        TEAInputs(lifetime_years=5, discount_rate=0.05, tax_rate=0.2, construction_years=1,
                  capex=[CapexItem("x", "hydrogen", 1.0, schedule={-3: 1.0})]).validate()
    with pytest.raises(ParameterError):
        TEAInputs(lifetime_years=5, discount_rate=0.05, tax_rate=0.2,
                  capex=[CapexItem("x", "reactor", 1.0, {0: 1.0})]).validate()


def test_case_matrix_labels(baseline, hybrid):
    base = TEAInputs(lifetime_years=12, discount_rate=0.05, tax_rate=0.2,
                     capex=[CapexItem("h2", "hydrogen", 1e6, {0: 1.0}),
                            CapexItem("core", "nuclear", 4e8, schedule={-3: 0.4, -2: 0.3, -1: 0.3}, cases=("greenfield",))],
                     credits=Credits(u45=Credit45U(9.0, 20.0, 30.0, 2030, 2031), y45=CreditPerUnit(12.0, 4),
                                     e48=Credit48E(0.3, 0.4), hydrogen=CreditPerUnit(0.7, 3)))
    design = StudyDesign(remaining_life_years=12, retrofit_construction_years=1, greenfield_lifetimes_years=(35, 45),
                         greenfield_construction_years=3)
    table, results = case_matrix({"baseline": baseline, "hybrid": hybrid}, base, design)
    assert len(table) == 2 * 2 + 2 * 3
    assert table.loc["Case 1 existing, no 45U", "capex_musd"] == 0.0
    assert table.loc["Case 4 greenfield 45 yr, 48E", "case"] == "greenfield"
    assert "Case 3 greenfield 35 yr, no 45Y/48E" in table.index
    assert results["Case 2 retrofit, 45U"].cash_flows["credit_45u"].sum() > 0


def test_reoptimized_operation_keeps_the_built_capacities():
    from nuclear_h2 import operations

    p = plant(electrolyzer=electrolyzer(capacity_mw=None, capacity_range_mw=(2.0, 36.0)),
              battery=battery(energy_mwh=None, energy_range_mwh=(3.0, 60.0)), storage=storage(capacity_kg=None, capacity_range_kg=(300.0, 5600.0)),
              market=market(as_mode="bid", h2_credit_usd_per_kg=2.5))
    ops = operations(p, series(PRICES, as_price=None), reoptimize_after_h2_credit=True)
    assert ops["hybrid_after_credit"].capacities == pytest.approx(ops["hybrid"].capacities, abs=1e-6)
    inp = TEAInputs(lifetime_years=4, discount_rate=0.05, tax_rate=0.2, credits=Credits(hydrogen=CreditPerUnit(0.5, 2)))
    evaluate(ops["hybrid"], inp, ops["hybrid_after_credit"])
    with pytest.raises(ParameterError):
        evaluate(ops["hybrid"], inp, ops["baseline"])              # different capacities are refused


def test_sunk_asset_keeps_its_fixed_om(baseline):
    core = CapexItem("core", "nuclear", 3.4e8, {0: 1.0}, cases=("greenfield",))
    fom = FixedCost("core_fom", "nuclear", capex_fraction=0.012, of_item="core")
    base = TEAInputs(lifetime_years=3, discount_rate=0.05, tax_rate=0.0, capex=[core], fixed_costs=[fom])
    res = evaluate(baseline, case_inputs(base, "existing"))
    assert res.cash_flows["fixed_om"].tolist() == pytest.approx([0.012 * 3.4e8] * 3)
    assert res.cash_flows["capex"].sum() == 0.0


def test_replacement_of_a_sunk_asset_when_listed(baseline):
    core = CapexItem("core", "nuclear", 2.3e8, {0: 1.0}, macrs_years=5, cases=("greenfield",))
    reps = [Replacement("core", years=(2,), fraction=0.04, cases=("existing", "retrofit", "greenfield")),
            Replacement("core", years=(3,), amount_usd=5e5)]                     # default: greenfield only
    base = TEAInputs(lifetime_years=3, discount_rate=0.05, tax_rate=0.1, capex=[core], replacements=reps)
    ex = evaluate(baseline, case_inputs(base, "existing")).cash_flows
    assert ex["capex"].tolist() == pytest.approx([0.0, 0.04 * 2.3e8, 0.0])
    assert ex["depreciation"].tolist()[1:] == pytest.approx([0.04 * 2.3e8 * f for f in macrs_schedule(5)[:2]])
    gf = evaluate(baseline, case_inputs(base, "greenfield")).cash_flows
    assert gf["capex"].tolist() == pytest.approx([2.3e8, 0.04 * 2.3e8, 5e5])
    with pytest.raises(ParameterError):
        TEAInputs(lifetime_years=3, discount_rate=0.05, tax_rate=0.1, capex=[core],
                  replacements=[Replacement("core", years=(2,), fraction=0.1, cases=("other",))]).validate()


def test_second_operation_must_be_the_same_plant(baseline, hybrid):
    from conftest import turbine

    other = optimize(plant(turbine=turbine(vom_usd_per_mwh=2.3), battery=battery(), market=market(as_mode="bid")),
                     series(PRICES, as_price=None))
    with pytest.raises(ParameterError):
        evaluate(hybrid, TEAInputs(lifetime_years=2, discount_rate=0.05, tax_rate=0.1,
                                   credits=Credits(hydrogen=CreditPerUnit(0.5, 1))), other)


def test_second_operation_with_other_component_parameters_is_refused():
    from nuclear_h2 import operations

    p = plant(battery=battery(), market=market(as_mode="bid"))
    ops = operations(p, series(PRICES, as_price=None))
    changed = plant(electrolyzer=electrolyzer(electricity_kwh_per_kg=(20.0, 19.5, 19.25)), battery=battery(),
                    market=market(as_mode="bid"))
    other = optimize(changed, series(PRICES, as_price=None))
    inp = TEAInputs(lifetime_years=2, discount_rate=0.05, tax_rate=0.1, credits=Credits(hydrogen=CreditPerUnit(0.5, 1)))
    with pytest.raises(ParameterError):
        evaluate(ops["hybrid"], inp, other)


def test_second_operation_with_another_storage_rule_is_refused():
    from nuclear_h2.scenarios import with_capacities

    sized = plant(storage=storage(capacity_kg=None, capacity_range_kg=(300.0, 5600.0)), market=market(as_mode="off"))
    first = optimize(sized, series(PRICES))
    built = with_capacities(sized, first.capacities)
    same_rule = optimize(built, series(PRICES))
    fixed = plant(storage=storage(capacity_kg=first.capacities["storage_kg"]), market=market(as_mode="off"))
    other_rule = optimize(fixed, series(PRICES))
    inp = TEAInputs(lifetime_years=2, discount_rate=0.05, tax_rate=0.1, credits=Credits(hydrogen=CreditPerUnit(0.5, 1)))
    evaluate(first, inp, same_rule)
    with pytest.raises(ParameterError):
        evaluate(first, inp, other_rule)                 # a fixed store sells directly: another operating rule


def test_payment_schedule_offsets():
    from conftest import plant as make_plant

    op = baseline_operation(make_plant(), series(PRICES))
    item = CapexItem("x", "shared", 9e6, {-2: 0.2, -1: 0.3, 0: 0.5}, cases=("existing",))
    cf = evaluate(op, TEAInputs(lifetime_years=2, discount_rate=0.05, tax_rate=0.0, construction_years=4, capex=[item],
                                case="existing")).cash_flows
    assert cf["capex"].tolist() == pytest.approx([0.0, 0.0, 1.8e6, 2.7e6, 4.5e6, 0.0])
    for bad in ({-2: 1.0}, {0.5: 1.0}, {-1: -0.5, 0: 1.5}, {0: float("nan")}, {True: 1.0}):
        with pytest.raises(ParameterError):
            TEAInputs(lifetime_years=2, discount_rate=0.05, tax_rate=0.0, construction_years=1, case="existing",
                      capex=[CapexItem("x", "shared", 1.0, bad, cases=("existing",))]).validate()
