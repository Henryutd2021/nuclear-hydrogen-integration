"""Hourly operation and sizing model of the integrated energy system (Eqs. 1-46) as a linopy MILP.

Constraint names carry the article's equation numbers (docs/formulation.md). The model maximizes the profit of
Eq. (1); linopy minimizes, so the objective is -Pi without the constant capital cost of fixed-size components
(added back in :func:`nuclear_h2.solve.solve`).

Integer structure, as in the article:
  * the turbine curve (Eqs. 13-16) and the electrolyzer curves (Eqs. 21-24) are SOS2 piecewise-linear maps; here
    they are written with the incremental (delta) formulation, an exact MILP equivalent that any MILP solver
    accepts (no native SOS2 support needed);
  * electrolyzer on/off, startup and shutdown (Eqs. 17-18, 25-28);
  * battery charge/discharge status (Eqs. 31-33).
Sizing variables (electrolyzer power, battery energy, storage capacity) enter bilinear products with binaries in
Eqs. (18) and (31)-(32); these are written exactly with a sizing bound (P <= P_max and P <= P_bar * u).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

import linopy
import numpy as np
import pandas as pd
import xarray as xr

from .parameters import Plant, Service
from .timeseries import HOURS_PER_YEAR, TimeSeries

Number = Union[float, int]


@dataclass
class PlantModel:
    """A built (and, after :func:`nuclear_h2.solve.solve`, solved) model."""
    plant: Plant
    ts: TimeSeries
    model: linopy.Model
    services: Tuple[Service, ...]
    bids: Dict[str, Dict[str, linopy.Variable]]           # bids[component][service]
    coef: Dict[str, Dict[str, np.ndarray]]                # per-service hourly coefficient arrays
    fixed_capex: float                                     # horizon capital cost of fixed-size components, $
    objective: float = float("nan")                        # profit Pi over the horizon, $
    solver: Dict[str, object] = field(default_factory=dict)


def _pwl_incremental(m: linopy.Model, name: str, t: pd.RangeIndex, x: np.ndarray, ys: List[np.ndarray],
                     on: Optional[Union[linopy.Variable, Number]] = None):
    """Incremental (delta) form of an SOS2 piecewise-linear map.

    Returns pairs (expression, constant) for X and each Y with X = x_0 * on + sum_k delta_k (x_k - x_{k-1}),
    0 <= delta_k <= 1, delta_{k+1} <= z_k <= delta_k (z binary) and delta_1 <= on. With on = 0 every term is zero
    (the off state); with on = 1 the point moves along the curve from its first breakpoint. ``on=None`` means
    always on.
    """
    K = len(x) - 1
    kdim = f"{name}_k"
    k = pd.RangeIndex(K, name=kdim)
    delta = m.add_variables(lower=0.0, upper=1.0, coords=[t, k], name=f"{name}_delta")
    if K > 1:
        # segment k+1 may be used only if segment k is full: delta[k+1] <= z[k] <= delta[k]
        z = m.add_variables(coords=[t, k], name=f"{name}_z", binary=True)
        inner = xr.DataArray(np.arange(K) < K - 1, coords=[k])
        m.add_constraints(delta.roll(**{kdim: -1}) - z <= 0, name=f"{name}_order_next", mask=inner)
        m.add_constraints(z - delta <= 0, name=f"{name}_order_this", mask=inner)
        m.add_constraints(z <= 0, name=f"{name}_unused_binary", mask=~inner)
    variable_on = on is not None and not isinstance(on, (int, float))
    if variable_on:
        m.add_constraints(delta - on <= 0, name=f"{name}_on", mask=xr.DataArray(np.arange(K) == 0, coords=[k]))
    out = []
    for arr in [np.asarray(x, float)] + [np.asarray(y, float) for y in ys]:
        steps = xr.DataArray(np.diff(arr), coords=[k])
        expr = (steps * delta).sum(kdim)
        if variable_on:
            out.append((expr + float(arr[0]) * on, 0.0))
        else:
            out.append((expr, float(arr[0]) * (1.0 if on is None else float(on))))
    return out


def build_model(plant: Plant, ts: TimeSeries) -> PlantModel:
    """Build the MILP of Eqs. (1)-(46) for ``plant`` on the hours of ``ts``."""
    plant.validate()
    ts.check_against(plant.market)
    H, dt = ts.hours, ts.dt_h
    t = pd.RangeIndex(H, name="t")
    m = linopy.Model()

    def da(values) -> xr.DataArray:
        return xr.DataArray(np.broadcast_to(np.asarray(values, dtype=float), (H,)).copy(), coords=[t])

    first = np.zeros(H, dtype=bool)
    first[0] = True
    mask_first = xr.DataArray(first, coords=[t])
    mask_rest = xr.DataArray(~first, coords=[t])
    mask_last = xr.DataArray(first[::-1].copy(), coords=[t])
    price = da(ts.energy_price)
    mk = plant.market
    services = plant.active_services()
    dispatch = mk.as_mode == "dispatch"
    pay_energy = (mk.as_mode == "bid") or (dispatch and mk.energy_payment_in_dispatch)
    horizon_share = 1.0 / ts.annualization                       # |T| dt / 8760 of Eq. (6)

    revenue = []   # linear terms of R^E + R^H2 + R^AS
    cost = []      # linear terms of C^OPEX + C^CAPEX
    fixed_capex = 0.0

    # ---- turbine: Eqs. (10)-(16) -------------------------------------------------------------------------
    tb = plant.turbine
    q_bp, p_bp = tb.curve()
    q_turb = m.add_variables(lower=float(q_bp[0]), upper=float(q_bp[-1]), coords=[t], name="q_turb")
    p_turb = m.add_variables(lower=tb.p_min_mw, upper=tb.p_max_mw, coords=[t], name="p_turb")     # Eq. (10)
    (qx, qc), (px, pc) = _pwl_incremental(m, "turb", t, q_bp, [p_bp])
    m.add_constraints(q_turb - qx == qc, name="eq13_turbine_steam")
    m.add_constraints(p_turb - px == pc, name="eq14_turbine_power")                              # (15)-(16): delta form
    if H > 1:
        m.add_constraints(p_turb - p_turb.roll(t=1) <= tb.ramp_up_mw_per_h * dt, name="eq11_turbine_ramp_up",
                          mask=mask_rest)
        m.add_constraints(p_turb.roll(t=1) - p_turb <= tb.ramp_down_mw_per_h * dt, name="eq12_turbine_ramp_down",
                          mask=mask_rest)
    cost.append(dt * tb.vom_usd_per_mwh * p_turb.sum())

    elec_supply = 1.0 * p_turb      # left-hand side of Eq. (7), built term by term
    steam_use = 1.0 * q_turb        # left-hand side of Eq. (9)

    # ---- electrolyzer: Eqs. (17)-(29) ------------------------------------------------------------------------
    el = plant.electrolyzer
    p_elec = u = h2 = cap = None
    if el is not None:
        upper = el.upper_mw
        if el.sized:
            cap = m.add_variables(lower=el.lower_mw, upper=upper, name="P_elec_max")
            cost.append(horizon_share * el.capex_usd_per_mw_yr * cap)
        else:
            cap = float(el.capacity_mw)
            fixed_capex += horizon_share * el.capex_usd_per_mw_yr * cap
        if el.commitment:
            u = m.add_variables(coords=[t], name="u_elec", binary=True)
            v = m.add_variables(coords=[t], name="v_elec", binary=True)
            w = m.add_variables(coords=[t], name="w_elec", binary=True)
            if H > 1:
                m.add_constraints(u - u.roll(t=1) - v + w == 0, name="eq25_startup_shutdown", mask=mask_rest)
            u0 = 1.0 if el.initially_on else 0.0
            m.add_constraints(u - v + w == u0, name="eq25_startup_shutdown_first", mask=mask_first)
            # time already spent in the initial state counts toward the minimum up or down time
            remaining = (el.min_up_h if el.initially_on else el.min_down_h) - abs(el.initial_state_h)
            n_hold = min(H, int(np.ceil(remaining / dt - 1e-9))) if remaining > 0 else 0
            if n_hold > 0:
                m.add_constraints(u == u0, name="initial_state_hold",
                                  mask=xr.DataArray(np.arange(H) < n_hold, coords=[t]))
            m.add_constraints(v + w <= 1, name="eq26_not_both")
            # minimum up and down times (Eqs. 27-28), in the startup/shutdown window form of this implementation
            # (docs/formulation.md, convention 3): a start in the last T_up periods requires the unit to be on now,
            # a stop in the last T_down periods requires it to be off now. Hours are converted to periods.
            for hours, name, var, on_side in ((el.min_up_h, "eq27_min_up", v, True),
                                             (el.min_down_h, "eq28_min_down", w, False)):
                T_min = int(np.ceil(float(hours) / dt - 1e-9))
                if T_min > 1 and H > 1:
                    window = 1.0 * var
                    for lag in range(1, min(T_min, H)):
                        # var[t - lag]; the coefficient 0 removes the terms that roll() wraps from the end
                        window = window + da(np.arange(H) >= lag) * var.roll(t=lag)
                    if on_side:
                        m.add_constraints(window - u <= 0, name=name)
                    else:
                        m.add_constraints(window + u <= 1, name=name)
            on: Union[linopy.Variable, float] = u
            cost.append(el.startup_cost_usd * v.sum())
        else:
            on = 1.0
        p_bp_e = np.asarray(el.power_breakpoints_mw, float)
        (ex, ec), (hx, hc), (sx, sc) = _pwl_incremental(m, "elec", t, p_bp_e,
                                                        [el.production_breakpoints_kg_per_h(), el.steam_breakpoints_mwth()],
                                                        on=on)
        p_elec = m.add_variables(lower=0.0, upper=upper, coords=[t], name="p_elec")
        h2 = m.add_variables(lower=0.0, coords=[t], name="h2_production")
        q_elec = m.add_variables(lower=0.0, coords=[t], name="q_elec")
        m.add_constraints(p_elec - ex == ec, name="eq21_electrolyzer_power")
        m.add_constraints(h2 - hx == hc, name="eq22_hydrogen_production")
        m.add_constraints(q_elec - sx == sc, name="electrolyzer_steam")                    # companion curve
        if el.commitment:
            m.add_constraints(p_elec - el.min_on_mw * u >= 0, name="eq17_electrolyzer_min")
            m.add_constraints(p_elec - upper * u <= 0, name="eq18_electrolyzer_max")
        else:
            m.add_constraints(p_elec >= el.min_on_mw, name="eq17_electrolyzer_min")
        if el.sized:
            m.add_constraints(p_elec - cap <= 0, name="eq18_electrolyzer_capacity")
        if H > 1:
            m.add_constraints(p_elec - p_elec.roll(t=1) - el.ramp_up_fraction_per_h * dt * cap <= 0,
                              name="eq19_electrolyzer_ramp_up", mask=mask_rest)
            m.add_constraints(p_elec.roll(t=1) - p_elec - el.ramp_down_fraction_per_h * dt * cap <= 0,
                              name="eq20_electrolyzer_ramp_down", mask=mask_rest)
        if el.target_capacity_factor is not None:
            m.add_constraints(dt * p_elec.sum() - el.target_capacity_factor * H * dt * cap >= 0,
                              name="hydrogen_target_capacity_factor")
        elec_supply = elec_supply - p_elec
        steam_use = steam_use + q_elec
        cost.append(dt * el.vom_usd_per_mwh * p_elec.sum())
        cost.append(dt * el.water_cost_usd_per_kg * h2.sum())

    # ---- steam balance: Eq. (9) ------------------------------------------------------------------------------
    m.add_constraints(steam_use == tb.steam_total_mwth, name="eq9_steam_balance")

    # ---- hydrogen storage and sales: Eqs. (36)-(40) ----------------------------------------------------------
    st = plant.storage
    if h2 is not None:
        if st is not None:
            m_in = m.add_variables(lower=0.0, upper=st.charge_max_kg_per_h, coords=[t], name="h2_to_storage")
            m_out = m.add_variables(lower=0.0, upper=st.discharge_max_kg_per_h, coords=[t], name="h2_from_storage")
            inv = m.add_variables(lower=st.inventory_min_kg, upper=st.upper_kg, coords=[t], name="h2_inventory")
            m_mkt = m.add_variables(lower=0.0, upper=0.0 if st.sized else np.inf, coords=[t], name="h2_to_market")
            flow = dt * (st.charge_efficiency * m_in - (1.0 / st.discharge_efficiency) * m_out)
            if H > 1:
                m.add_constraints(inv - inv.roll(t=1) - flow == 0, name="eq36_storage_balance", mask=mask_rest)
            m.add_constraints(inv - flow == st.initial_kg, name="eq36_storage_balance_first", mask=mask_first)
            m.add_constraints(h2 - m_mkt - m_in == 0, name="eq37_production_split")
            if st.sized:
                m_cap = m.add_variables(lower=st.lower_kg, upper=st.upper_kg, name="M_stor_max")
                m.add_constraints(inv - m_cap <= 0, name="eq38_storage_capacity")
                m_const = m.add_variables(lower=0.0, name="h2_sales_constant")
                m.add_constraints(m_out - m_const == 0, name="eq40_constant_sales")
                cost.append(horizon_share * st.capex_usd_per_kg_yr * m_cap)
            else:
                fixed_capex += horizon_share * st.capex_usd_per_kg_yr * float(st.capacity_kg)
            if st.cyclic:
                m.add_constraints(inv >= st.initial_kg, name="storage_cyclic_end", mask=mask_last)
            sold = m_out.sum() + m_mkt.sum()
            cost.append(dt * st.vom_usd_per_kg * (m_in.sum() + m_out.sum()))
        else:
            sold = h2.sum()
        revenue.append(dt * mk.h2_price_usd_per_kg * sold)                                    # Eq. (3)
        revenue.append(dt * mk.h2_credit_usd_per_kg * h2.sum())

    # ---- battery: Eqs. (30)-(35) -----------------------------------------------------------------------------
    bt = plant.battery
    p_ch = p_dis = soc = e_cap = p_cap = None
    if bt is not None:
        p_bar = bt.power_per_energy * bt.upper_mwh
        if bt.sized:
            e_cap = m.add_variables(lower=bt.lower_mwh, upper=bt.upper_mwh, name="E_b_max")
            p_cap = bt.power_per_energy * e_cap                                                   # Eq. (35)
            cost.append(horizon_share * (bt.capex_energy_usd_per_mwh_yr * e_cap + bt.capex_power_usd_per_mw_yr * p_cap))
        else:
            e_cap = float(bt.energy_mwh)
            p_cap = bt.power_per_energy * e_cap
            fixed_capex += horizon_share * (bt.capex_energy_usd_per_mwh_yr * e_cap + bt.capex_power_usd_per_mw_yr * p_cap)
        p_ch = m.add_variables(lower=0.0, upper=p_bar, coords=[t], name="p_charge")
        p_dis = m.add_variables(lower=0.0, upper=p_bar, coords=[t], name="p_discharge")
        u_ch = m.add_variables(coords=[t], name="u_charge", binary=True)
        u_dis = m.add_variables(coords=[t], name="u_discharge", binary=True)
        m.add_constraints(p_ch - p_bar * u_ch <= 0, name="eq31_charge_status")
        m.add_constraints(p_dis - p_bar * u_dis <= 0, name="eq32_discharge_status")
        if bt.sized:
            m.add_constraints(p_ch - p_cap <= 0, name="eq31_charge_capacity")
            m.add_constraints(p_dis - p_cap <= 0, name="eq32_discharge_capacity")
        m.add_constraints(u_ch + u_dis <= 1, name="eq33_no_simultaneous")
        soc = m.add_variables(lower=0.0, upper=bt.upper_mwh, coords=[t], name="soc")
        flow = dt * (bt.charge_efficiency * p_ch - (1.0 / bt.discharge_efficiency) * p_dis)
        e_init = bt.soc_initial_fraction * e_cap
        if H > 1:
            m.add_constraints(soc - soc.roll(t=1) - flow == 0, name="eq30_soc", mask=mask_rest)
        m.add_constraints(soc - flow - e_init == 0, name="eq30_soc_first", mask=mask_first)
        m.add_constraints(soc - bt.soc_min_fraction * e_cap >= 0, name="eq34_soc_min")
        if bt.sized:
            m.add_constraints(soc - e_cap <= 0, name="eq34_soc_max")
        if bt.end_soc_fraction is not None:
            m.add_constraints(soc - bt.end_soc_fraction * e_cap == 0, name="battery_end_soc", mask=mask_last)
        if bt.ramp_fraction_per_h is not None and H > 1:
            net = p_dis - p_ch
            m.add_constraints(net - net.roll(t=1) - bt.ramp_fraction_per_h * dt * p_cap <= 0,
                              name="battery_ramp_up", mask=mask_rest)
            m.add_constraints(net.roll(t=1) - net - bt.ramp_fraction_per_h * dt * p_cap <= 0,
                              name="battery_ramp_down", mask=mask_rest)
        elec_supply = elec_supply + p_dis - p_ch
        cost.append(dt * bt.vom_usd_per_mwh * 0.5 * (p_ch.sum() + p_dis.sum()))

    # ---- ancillary services: Eqs. (4), (41)-(46) -------------------------------------------------------------
    present = {"turbine": True, "electrolyzer": el is not None, "battery": bt is not None}
    bids: Dict[str, Dict[str, linopy.Variable]] = {c: {} for c in present if present[c]}
    coef: Dict[str, Dict[str, np.ndarray]] = {}
    for s in services:
        W = ts.winning_rate[s.column].to_numpy()
        D = ts.deployment[s.name].to_numpy()
        cap_price = s.price_share * ts.as_price[s.column].to_numpy()
        mil = np.zeros(H)
        if s.mileage and ts.mileage_price is not None and s.column in ts.mileage_price.columns:
            mil = s.price_share * (ts.mileage[s.column].to_numpy() * ts.performance[s.column].to_numpy()
                                   * ts.mileage_price[s.column].to_numpy())
        energy = s.sign * D * ts.energy_price if pay_energy else np.zeros(H)
        coef[s.name] = {"winning": W, "deployment": D, "deploy": W * D, "capacity": W * cap_price,
                        "energy": W * energy, "mileage": W * mil}
        unit_revenue = dt * W * (cap_price + energy + mil)
        for c in bids:
            if c in s.components:
                b = m.add_variables(lower=0.0, coords=[t], name=f"bid_{c}_{s.name}")
                bids[c][s.name] = b
                revenue.append((da(unit_revenue) * b).sum())
    for up_name, down_name in plant.balanced_pairs():                                       # Eq. (44)
        for c in bids:
            if up_name in bids[c] and down_name in bids[c]:
                m.add_constraints(bids[c][up_name] - bids[c][down_name] == 0,
                                  name=f"eq44_regulation_balance_{c}_{up_name}")

    def totals(c: str, direction: str):
        """(sum of bids, sum of expected deployments) of component c in one direction (Eqs. 43, 45); None if none."""
        bid = dep = None
        for s in services:
            if s.direction == direction and s.name in bids.get(c, {}):
                b = bids[c][s.name]
                d = da(coef[s.name]["deploy"]) * b
                bid = 1.0 * b if bid is None else bid + b
                dep = d if dep is None else dep + d
        return bid, dep

    def shifted(base, minus, plus):
        """base - minus + plus, skipping missing terms (set point from actual power and deployments)."""
        out = 1.0 * base
        if minus is not None:
            out = out - minus
        if plus is not None:
            out = out + plus
        return out

    def plus(expr, other):
        return expr if other is None else expr + other

    def minus(expr, other):
        return expr if other is None else expr - other

    if services:
        # Headroom around the set point (Eq. 41, Eq. 42 and their counterparts, docs/formulation.md convention 6):
        # both sides are imposed whenever a component offers any service, so that the set point itself stays
        # within the component's limits even when it offers in one direction only.
        # turbine
        up_b, up_d = totals("turbine", "up")
        dn_b, dn_d = totals("turbine", "down")
        if up_b is not None or dn_b is not None:
            p_sp = shifted(p_turb, up_d, dn_d) if dispatch else 1.0 * p_turb                  # Eq. (46) analogue
            m.add_constraints(plus(p_sp, up_b) <= tb.p_max_mw, name="eq41_turbine_up_headroom")
            m.add_constraints(minus(p_sp, dn_b) >= tb.p_min_mw, name="turbine_down_headroom")
            if up_b is not None:
                m.add_constraints(up_b <= tb.ramp_up_mw_per_h * dt, name="turbine_up_ramp")
            if dn_b is not None:
                m.add_constraints(dn_b <= tb.ramp_down_mw_per_h * dt, name="turbine_down_ramp")
        # electrolyzer; upward = load reduction, downward = load increase
        if el is not None:
            up_b, up_d = totals("electrolyzer", "up")
            dn_b, dn_d = totals("electrolyzer", "down")
            if up_b is not None or dn_b is not None:
                p_sp = shifted(p_elec, dn_d, up_d) if dispatch else 1.0 * p_elec                # Eq. (46)
                on_term = u if el.commitment else 1.0
                m.add_constraints(plus(p_sp, dn_b) - upper * on_term <= 0, name="eq42_electrolyzer_down_headroom")
                if el.sized:
                    m.add_constraints(plus(p_sp, dn_b) - cap <= 0, name="eq42_electrolyzer_down_capacity")
                m.add_constraints(minus(p_sp, up_b) - el.min_on_mw * on_term >= 0, name="electrolyzer_up_headroom")
                if dn_b is not None:
                    m.add_constraints(dn_b - el.ramp_up_fraction_per_h * dt * cap <= 0, name="electrolyzer_down_ramp")
                if up_b is not None:
                    m.add_constraints(up_b - el.ramp_down_fraction_per_h * dt * cap <= 0, name="electrolyzer_up_ramp")
        # battery: power headroom around the set point and energy to sustain the bids for tau^AS hours
        if bt is not None:
            up_b, up_d = totals("battery", "up")
            dn_b, dn_d = totals("battery", "down")
            if up_b is not None or dn_b is not None:
                g = p_dis - p_ch
                g_sp = shifted(g, up_d, dn_d) if dispatch else g
                tau = mk.reserve_duration_h
                m.add_constraints(plus(g_sp, up_b) - p_cap <= 0, name="battery_up_headroom")
                m.add_constraints(minus(g_sp, dn_b) + p_cap >= 0, name="battery_down_headroom")
                if up_b is not None:
                    m.add_constraints(soc - bt.soc_min_fraction * e_cap - (tau / bt.discharge_efficiency) * up_b >= 0,
                                      name="battery_up_energy")
                if dn_b is not None:
                    # variables first: a fixed capacity is a float, and the oldest supported linopy needs expr + float
                    m.add_constraints(-1.0 * soc - (tau * bt.charge_efficiency) * dn_b + e_cap >= 0,
                                      name="battery_down_energy")

    # ---- grid exchange: Eqs. (2), (7)-(8) --------------------------------------------------------------------
    p_sale = m.add_variables(lower=0.0, upper=mk.export_max_mw, coords=[t], name="p_sale")
    p_buy = m.add_variables(lower=0.0, upper=mk.import_max_mw, coords=[t], name="p_buy")
    m.add_constraints(elec_supply - p_sale + p_buy == 0, name="eq7_8_power_balance")
    revenue.append((da(dt * ts.energy_price) * (p_sale - p_buy)).sum())                     # Eq. (2)

    # ---- objective: Eq. (1), minimized as -Pi ----------------------------------------------------------------
    objective = 1.0 * cost[0]
    for term in cost[1:]:
        objective = objective + term
    for term in revenue:
        objective = objective - term
    m.add_objective(objective)
    return PlantModel(plant=plant, ts=ts, model=m, services=services, bids=bids, coef=coef, fixed_capex=fixed_capex)


__all__ = ["PlantModel", "build_model"]
