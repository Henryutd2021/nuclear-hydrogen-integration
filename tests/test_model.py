"""Optimization model: hand-computed cases, balance equations recomputed from every solution, accounting equal
to the solver objective, unit-commitment logic, storage and battery rules, ancillary-service headroom and modes."""

import importlib.util
from dataclasses import replace

import numpy as np
import pytest

from nuclear_h2 import (Service, balance_residuals, baseline_operation, baseline_plant, build_model,
                        flexibility_metrics, optimize, revenue_table, solve)
from nuclear_h2.results import as_revenue_by_service, operation_of

from conftest import SERVICES, battery, electrolyzer, market, plant, series, storage, turbine

TOL = 1e-6


def check(op):
    """Residuals of every balance and curve are ~0, and the accounting equals the solver's profit."""
    res = balance_residuals(op)
    assert max(res.values()) < 1e-5, res
    assert op.totals["profit"] == pytest.approx(op.solver["profit_usd"], rel=1e-7, abs=1e-4)


def test_baseline_closed_form_equals_milp(varied_prices):
    p = plant()
    ts = series(varied_prices)
    closed = baseline_operation(p, ts)
    milp = optimize(baseline_plant(p), ts)
    check(milp)
    assert closed.profit == pytest.approx(milp.profit, rel=1e-9)
    power = p.turbine.power_at(p.turbine.steam_total_mwth)
    assert closed.profit == pytest.approx(np.sum(varied_prices * power) - 1.5 * power * len(varied_prices))


def test_hand_computed_always_on_electrolyzer():
    prices = np.array([20.0, 45.0, 30.0])
    p = plant(electrolyzer=electrolyzer(commitment=False), market=market(as_mode="off"))
    op = optimize(p, series(prices))
    check(op)
    # hydrogen is worth more than power at these prices: the electrolyzer runs at capacity in every hour
    h2 = 40_000.0 / 38.5
    q_elec = h2 * 7.0 / 1000.0
    p_turb = 125.0 + (500.0 - q_elec - 350.0) / 170.0 * 60.0
    h = op.hourly
    assert h["p_elec"].tolist() == pytest.approx([40.0] * 3)
    assert h["p_turb"].tolist() == pytest.approx([p_turb] * 3)
    profit = (np.sum(prices * (p_turb - 40.0)) + 3 * h2 * (2.2 + 0.9) - 3 * (1.5 * p_turb + 4.0 * 40.0 + 0.02 * h2)
              - 3 / 8760 * 70_000.0 * 40.0)
    assert op.profit == pytest.approx(profit, rel=1e-9)


def test_electrolyzer_turns_off_when_power_is_dear():
    prices = np.array([30.0, 30.0, 400.0, 400.0, 30.0, 30.0])
    op = optimize(plant(market=market(as_mode="off")), series(prices))
    check(op)
    h = op.hourly
    assert h["elec_on"].tolist() == [1, 1, 0, 0, 1, 1]
    assert h["elec_start"].sum() == 1 and h["elec_stop"].sum() == 1
    assert op.totals["startup_cost"] == pytest.approx(120.0)
    assert h.loc[h["elec_on"] == 0, ["p_elec", "h2_production", "q_elec"]].abs().max().max() < TOL


@pytest.mark.parametrize("state, expected", [
    (2.0, [1, 1, 1, 0, 0, 0, 0]),          # on for 2 h with a 5 h minimum up time: 3 more hours on
    (7.0, [0, 0, 0, 0, 0, 0, 0]),          # minimum up time already met: free to stop at once
])
def test_initial_state_counts_toward_minimum_times(state, expected):
    p = plant(electrolyzer=electrolyzer(initial_state_h=state, min_up_h=5), market=market(as_mode="off"))
    op = optimize(p, series(np.full(7, 400.0)))
    check(op)
    assert op.hourly["elec_on"].astype(int).tolist() == expected
    assert op.hourly["elec_start"].sum() == 0 and op.hourly["elec_stop"].sum() == 1


def runs(on):
    """Lengths of the on and off runs that start after the first hour."""
    out, start = [], 0
    for t in range(1, len(on) + 1):
        if t == len(on) or on[t] != on[start]:
            out.append((on[start], t - start, start, t == len(on)))
            start = t
    return out


def test_minimum_up_and_down_times():
    prices = np.array([30.0] * 6 + [400.0] * 2 + [30.0] * 2 + [400.0] + [30.0] * 6)
    p = plant(electrolyzer=electrolyzer(min_up_h=3, min_down_h=3, initial_state_h=8.0), market=market(as_mode="off"))
    op = optimize(p, series(prices))
    check(op)
    on = op.hourly["elec_on"].astype(int).tolist()
    for state, length, start, at_end in runs(on):
        if start >= 3 and not at_end:
            assert length >= 3, on


def test_ramp_limits_hold(varied_prices):
    p = plant(electrolyzer=electrolyzer(ramp_up_fraction_per_h=0.2, ramp_down_fraction_per_h=0.25, initial_state_h=8.0,
                                        commitment=False), turbine=turbine(ramp_up_mw_per_h=1.5, ramp_down_mw_per_h=1.0))
    op = optimize(p, series(varied_prices))
    check(op)
    dp = np.diff(op.hourly["p_elec"])
    assert dp.max() <= 0.2 * 40 + TOL and -dp.min() <= 0.25 * 40 + TOL
    dt_ = np.diff(op.hourly["p_turb"])
    assert dt_.max() <= 1.5 + TOL and -dt_.min() <= 1.0 + TOL


def test_sized_components_and_accounting(varied_prices):
    p = plant(electrolyzer=electrolyzer(capacity_mw=None, capacity_range_mw=(2.0, 36.0)),
              battery=battery(energy_mwh=None, energy_range_mwh=(3.0, 80.0)), storage=storage(capacity_kg=None, capacity_range_kg=(300.0, 9000.0)))
    op = optimize(p, series(varied_prices, as_price=None))
    check(op)
    caps = op.capacities
    assert 0 <= caps["electrolyzer_mw"] <= 36.0 + TOL and 0 <= caps["battery_mwh"] <= 80.0 + TOL
    assert caps["battery_mw"] == pytest.approx(0.4 * caps["battery_mwh"])
    h = op.hourly
    # sized store: one constant sales rate, everything through the store (text with Eq. 40)
    assert np.ptp(h["h2_from_storage"]) < 1e-6 and h["h2_to_market"].abs().max() < TOL
    assert h["h2_inventory"].max() <= caps["storage_kg"] + 1e-6
    assert h["h2_inventory"].min() >= 200.0 - 1e-6


@pytest.mark.parametrize("end_soc", [0.45, None])
def test_fixed_store_and_battery_rules(end_soc, varied_prices):
    p = plant(battery=battery(end_soc_fraction=end_soc), storage=storage(cyclic=True))
    op = optimize(p, series(varied_prices, as_price=None))
    check(op)
    h = op.hourly
    if end_soc is not None:
        assert h["soc"].iloc[-1] == pytest.approx(end_soc * 50.0)           # required end state of charge
    assert h["h2_inventory"].iloc[-1] >= 400.0 - 1e-6                         # cyclic store
    assert ((h["p_charge"] > TOL) & (h["p_discharge"] > TOL)).sum() == 0      # Eq. (33)
    assert h["soc"].min() >= 0.08 * 50.0 - 1e-6
    assert max(h["p_charge"].max(), h["p_discharge"].max()) <= 20.0 + 1e-6


def test_target_capacity_factor():
    prices = np.array([30.0, 300.0, 300.0, 300.0])
    free = optimize(plant(market=market(as_mode="off")), series(prices))
    assert free.hourly["p_elec"].sum() < 0.5 * 40 * 4
    p = plant(electrolyzer=electrolyzer(target_capacity_factor=0.7), market=market(as_mode="off"))
    forced = optimize(p, series(prices))
    check(forced)
    assert forced.hourly["p_elec"].sum() >= 0.7 * 40 * 4 - 1e-6


def test_import_limit_and_grid_purchases():
    prices = np.array([-20.0, -20.0])
    p = plant(battery=battery(), market=market(as_mode="off", import_max_mw=5.0))
    op = optimize(p, series(prices))
    check(op)
    assert op.hourly["p_buy"].max() <= 5.0 + TOL


@pytest.mark.parametrize("mode", ["bid", "dispatch"])
def test_ancillary_service_headroom(mode, varied_prices):
    p = plant(battery=battery(), market=market(as_mode=mode))
    ts = series(varied_prices, as_price=None)
    op = optimize(p, ts)
    check(op)
    h = op.hourly
    names = {s.name: s for s in p.active_services()}
    up = lambda c: sum(h[f"bid_{c}_{s}"] for s in names if f"bid_{c}_{s}" in h and names[s].direction == "up")  # noqa
    dn = lambda c: sum(h[f"bid_{c}_{s}"] for s in names if f"bid_{c}_{s}" in h and names[s].direction == "down")  # noqa
    tb, el, bt = p.turbine, p.electrolyzer, p.battery
    assert np.all(h["p_turb_setpoint"] + up("turbine") <= tb.p_max_mw + 1e-6)                # Eq. (41)
    assert np.all(h["p_turb_setpoint"] - dn("turbine") >= tb.p_min_mw - 1e-6)
    assert np.all(h["p_elec_setpoint"] + dn("electrolyzer") <= 40.0 * h["elec_on"] + 1e-6)    # Eq. (42)
    assert np.all(h["p_elec_setpoint"] - up("electrolyzer") >= 8.0 * h["elec_on"] - 1e-6)
    g = h["p_discharge"] - h["p_charge"]
    g_sp = g - h["deployed_up_battery"] + h["deployed_down_battery"] if mode == "dispatch" else g
    assert np.all(g_sp + up("battery") <= 20.0 + 1e-6) and np.all(g_sp - dn("battery") >= -20.0 - 1e-6)
    assert np.all(h["soc"] - 0.08 * 50 - 0.75 / 0.93 * up("battery") >= -1e-6)
    assert np.all(50 - h["soc"] - 0.75 * 0.95 * dn("battery") >= -1e-6)
    for c in ("turbine", "electrolyzer", "battery"):                                      # Eq. (44)
        assert np.allclose(h[f"bid_{c}_RegUp"], h[f"bid_{c}_RegDown"])
    assert "bid_turbine_Ramp" not in h                                                    # not offered by the turbine
    assert h.filter(like="bid_").to_numpy().sum() > 0


def test_dispatch_mode_moves_power_by_deployment(varied_prices):
    p = plant(market=market(as_mode="dispatch"))
    op = optimize(p, series(varied_prices, as_price=None))
    check(op)
    h = op.hourly
    # Eq. (46): actual = set point - upward deployment + downward deployment
    assert np.allclose(h["p_elec"], h["p_elec_setpoint"] - h["deployed_up_electrolyzer"] + h["deployed_down_electrolyzer"])
    asr = as_revenue_by_service(p, op.ts, h)
    assert asr["energy"].abs().max() == 0.0                    # deployed energy is settled through Eq. (2) only
    paid = replace(p, market=replace(p.market, energy_payment_in_dispatch=True))
    op2 = optimize(paid, series(varied_prices, as_price=None))
    check(op2)


def test_bid_mode_energy_payment_sign(varied_prices):
    p = plant(market=market(as_mode="bid"))
    ts = series(varied_prices, as_price=None)
    op = optimize(p, ts)
    check(op)
    asr = as_revenue_by_service(p, ts, op.hourly).set_index(["component", "service"])
    h = op.hourly
    s = "Ramp"                                                 # a downward service: deployed energy is absorbed
    b = h[f"bid_electrolyzer_{s}"].to_numpy()
    expected = -np.sum(b * ts.winning_rate[s].to_numpy() * ts.deployment[s].to_numpy() * ts.energy_price)
    assert asr.loc[("electrolyzer", s), "energy"] == pytest.approx(expected)
    reg = asr.loc[("electrolyzer", "RegUp")]
    hb = h["bid_electrolyzer_RegUp"].to_numpy()
    mil = 3.0 * 0.85 * 0.3                          # mileage x performance x mileage price of the product
    assert reg["mileage"] == pytest.approx(np.sum(hb * ts.winning_rate["Reg"].to_numpy() * 0.5 * mil))
    # the symmetric product is paid once: up and down halves together = one payment on the balanced bid
    both = asr.loc[("electrolyzer", "RegUp"), "mileage"] + asr.loc[("electrolyzer", "RegDown"), "mileage"]
    assert both == pytest.approx(np.sum(hb * ts.winning_rate["Reg"].to_numpy() * mil))
    assert reg["capacity"] == pytest.approx(np.sum(hb * ts.winning_rate["Reg"].to_numpy() * 0.5 * ts.as_price["Reg"].to_numpy()))


def test_no_bids_when_off():
    prices = np.array([30.0, 500.0, 500.0, 500.0, 30.0])
    p = plant(market=market(as_mode="bid"), electrolyzer=electrolyzer(initial_state_h=8.0))
    op = optimize(p, series(prices, as_price=40.0))
    check(op)
    h = op.hourly
    off = h["elec_on"] == 0
    assert off.any()
    assert h.loc[off].filter(like="bid_electrolyzer_").abs().max().max() < TOL


def test_services_off_and_revenue_table(varied_prices):
    p = plant(market=market(as_mode="off"))
    op = optimize(p, series(varied_prices))
    assert not any(c.startswith("bid_") for c in op.hourly.columns)
    assert op.totals["as_revenue_electrolyzer"] == 0.0
    table = revenue_table([op])
    assert table.loc["profit"].iloc[0] == pytest.approx(op.annual("profit") / 1e6)


def test_degradation_state(varied_prices):
    p = plant(electrolyzer=electrolyzer(degradation_per_h_at_full_load=0.004, degradation_per_start=0.3),
              market=market(as_mode="off"))
    op = optimize(p, series(varied_prices))
    h = op.hourly
    expected = np.cumsum(0.004 * h["p_elec"] / 40.0 + 0.3 * h["elec_start"])            # Eq. (29)
    assert np.allclose(h["degradation"], expected)


def test_flexibility_metrics(varied_prices):
    op = optimize(plant(battery=battery(), market=market(as_mode="off")), series(varied_prices))
    f = flexibility_metrics(op, ramp_threshold_mw=3.0)
    g = op.hourly["p_ies"]
    assert f["grid_output_range_mw"] == pytest.approx(g.max() - g.min())
    assert f["ramp_events"] == int((np.abs(np.diff(g)) > 3.0).sum())
    assert 0 <= f["electrolyzer_capacity_factor"] <= 1


def test_low_temperature_electrolyzer_takes_no_steam(varied_prices):
    p = plant(electrolyzer=electrolyzer(heat_kwh_per_kg=(0.0, 0.0, 0.0)), market=market(as_mode="off"))
    op = optimize(p, series(varied_prices))
    check(op)
    assert op.hourly["q_elec"].abs().max() < TOL
    assert np.allclose(op.hourly["q_turb"], 500.0)


@pytest.mark.skipif(importlib.util.find_spec("gurobipy") is None, reason="gurobipy not installed")
def test_gurobi_and_highs_agree():
    prices = np.array([28.0, 33.0, 90.0, 26.0, 31.0, 150.0, 29.0, 27.0])
    p = plant(battery=battery(), market=market(as_mode="bid"))
    ts = series(prices, as_price=None)
    a = optimize(p, ts, solver="highs")
    b = optimize(p, ts, solver="gurobi")
    assert a.profit == pytest.approx(b.profit, rel=1e-6)


def test_solver_metadata(varied_prices):
    pm = solve(build_model(plant(market=market(as_mode="off")), series(varied_prices)))
    op = operation_of(pm)
    assert op.solver["name"] == "highs" and op.solver["termination"] == "optimal"
    assert op.solver["mip_gap_requested"] == 1e-6


# ---- regression tests: minimum up/down times, one-sided offers, pairing, storage floor ---------------------------

def test_minimum_up_and_down_times_allow_legal_cycles():
    prices = np.array([30.0] * 6 + [400.0] * 3 + [30.0] * 6)
    p = plant(electrolyzer=electrolyzer(min_up_h=3, min_down_h=3, initial_state_h=8.0), market=market(as_mode="off"))
    op = optimize(p, series(prices))
    check(op)
    assert op.hourly["elec_on"].astype(int).tolist() == [1] * 6 + [0] * 3 + [1] * 6


def test_minimum_times_reject_short_cycles():
    prices = np.array([30.0] * 6 + [400.0] * 2 + [30.0] * 6 + [400.0] * 2 + [30.0] * 4)
    p = plant(electrolyzer=electrolyzer(min_up_h=5, min_down_h=3, initial_state_h=8.0), market=market(as_mode="off"))
    on = optimize(p, series(prices)).hourly["elec_on"].astype(int).tolist()
    for state, length, start, at_end in runs(on):
        if start > 0 and not at_end:
            assert length >= (5 if state == 1 else 3), on


def test_minimum_times_count_periods():
    prices = np.repeat([30.0, 30.0, 400.0, 30.0, 30.0], 2)            # half-hour periods
    p = plant(electrolyzer=electrolyzer(min_up_h=1.0, min_down_h=1.5, initial_state_h=8.0), market=market(as_mode="off"))
    on = optimize(p, series(prices, dt=0.5)).hourly["elec_on"].astype(int).tolist()
    for state, length, start, at_end in runs(on):
        if start > 0 and not at_end and state == 0:
            assert length >= 3, on                                     # 1.5 h = 3 half-hour periods


@pytest.mark.parametrize("mode", ["bid", "dispatch"])
def test_one_sided_offers_keep_set_points_within_limits(mode, varied_prices):
    down_only = (Service("Dn", "down", components=("turbine", "battery")),)
    p = plant(battery=battery(), market=market(as_mode=mode, services=down_only))
    op = optimize(p, series(varied_prices, services=down_only, as_price=60.0, winning=0.9, deployment=0.6))
    check(op)
    h = op.hourly
    assert h["p_turb_setpoint"].max() <= 185.0 + 1e-6 and h["p_turb_setpoint"].min() >= 50.0 - 1e-6
    g_sp = h["p_discharge"] - h["p_charge"] - h["deployed_up_battery"] + h["deployed_down_battery"]
    if mode == "bid":
        g_sp = h["p_discharge"] - h["p_charge"]
    assert g_sp.abs().max() <= 20.0 + 1e-6
    up_only = (Service("Up", "up", components=("electrolyzer",)),)
    prices = np.array([30.0, 500.0, 500.0, 500.0, 30.0])
    p = plant(market=market(as_mode=mode, services=up_only), electrolyzer=electrolyzer(initial_state_h=8.0))
    op = optimize(p, series(prices, services=up_only, as_price=80.0, winning=0.9, deployment=0.6))
    check(op)
    h = op.hourly
    off = h["elec_on"] == 0
    assert off.any()
    assert h.loc[off, "bid_electrolyzer_Up"].abs().max() < TOL and h.loc[off, "p_elec_setpoint"].abs().max() < TOL
    assert (h["p_elec_setpoint"] <= 40.0 * h["elec_on"] + 1e-6).all()


@pytest.mark.parametrize("names", [("Zup", "Adown"), ("Aup", "Zdown")])
def test_balance_declared_on_one_side(names, varied_prices):
    up, down = names
    services = (Service(up, "up", product="R", price_share=0.5, balance_with=down),
                Service(down, "down", product="R", price_share=0.5))
    p = plant(market=market(services=services))
    op = optimize(p, series(varied_prices, services=services, as_price=30.0, winning=0.8, deployment=0.1))
    check(op)
    for c in ("turbine", "electrolyzer"):
        assert np.allclose(op.hourly[f"bid_{c}_{up}"], op.hourly[f"bid_{c}_{down}"])


def test_sized_store_holds_its_initial_inventory():
    prices = np.array([30.0, 32.0, 31.0, 29.0])
    p = plant(storage=storage(capacity_kg=None, capacity_range_kg=(300.0, 9000.0), inventory_min_kg=100.0, initial_kg=700.0,
                              capex_usd_per_kg_yr=500.0), market=market(as_mode="off"))
    op = optimize(p, series(prices))
    check(op)
    assert op.capacities["storage_kg"] >= 700.0 - 1e-6


def test_capacity_limit_sweep_keeps_the_lower_bound(varied_prices):
    from nuclear_h2 import ParameterError, electrolyzer_limit_sweep

    p = plant(electrolyzer=electrolyzer(capacity_mw=None, capacity_range_mw=(12.0, 40.0)), market=market(as_mode="off"))
    ts = series(varied_prices)
    ops = electrolyzer_limit_sweep(p, ts, (12.0, 30.0))
    assert ops[30.0].plant.electrolyzer.capacity_range_mw == (12.0, 30.0)
    assert 12.0 - 1e-6 <= ops[12.0].capacities["electrolyzer_mw"] <= 12.0 + 1e-6
    with pytest.raises(ParameterError):
        electrolyzer_limit_sweep(p, ts, (10.0,))


def test_directional_ramps_are_not_negative():
    from nuclear_h2.results import Operation

    op = optimize(plant(market=market(as_mode="off")), series(np.full(4, 30.0)))
    for values in ([1.0, 2.0, 3.0, 4.0], [4.0, 3.0, 2.0, 1.0]):
        h = op.hourly.copy()
        h["p_ies"] = values
        f = flexibility_metrics(Operation(op.label, op.plant, op.ts, h, op.capacities, op.totals))
        assert f["max_ramp_up_mw_per_h"] >= 0 and f["max_ramp_down_mw_per_h"] >= 0
