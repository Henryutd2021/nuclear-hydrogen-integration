"""Hourly solution tables, revenue and cost accounting (Eqs. 2-6, recomputed from the solution), balance checks and
flexibility metrics.

The accounting in :func:`account` is computed from the hourly table with NumPy, independently of the solver's
objective value; the tests check that both agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

from .parameters import Market, Plant, ParameterError
from .timeseries import TimeSeries

MONEY_KEYS = ("energy_revenue", "h2_sales_revenue", "h2_credit_revenue", "as_revenue_turbine",
              "as_revenue_electrolyzer", "as_revenue_battery", "turbine_vom", "electrolyzer_vom", "water_cost",
              "startup_cost", "battery_vom", "storage_vom", "capex_electrolyzer", "capex_battery", "capex_storage")


@dataclass
class Operation:
    """One evaluated operation over the horizon of the time series.

    ``totals`` holds horizon sums (US$, MWh, kg); :meth:`annual` scales them to one year (Eq. 6 convention)."""
    label: str
    plant: Plant
    ts: TimeSeries
    hourly: pd.DataFrame
    capacities: Dict[str, float]
    totals: Dict[str, float]
    solver: Dict[str, object] = field(default_factory=dict)

    @property
    def annualization(self) -> float:
        return self.ts.annualization

    def annual(self, key: str) -> float:
        return float(self.totals[key]) * self.annualization

    def annual_totals(self) -> Dict[str, float]:
        return {k: float(v) * self.annualization for k, v in self.totals.items()}

    @property
    def profit(self) -> float:
        """Profit Pi of Eq. (1) over the horizon, US$."""
        return float(self.totals["profit"])


# ---------------------------------------------------------------------------------------------------------------
# solution table
# ---------------------------------------------------------------------------------------------------------------

def _values(model, name: str, size: int) -> np.ndarray:
    if name in set(model.variables):
        return np.asarray(model.variables[name].solution.values, dtype=float).reshape(-1)
    return np.zeros(size)


def _scalar(model, name: str, default: float) -> float:
    if name in set(model.variables):
        return float(np.asarray(model.variables[name].solution.values, dtype=float))
    return float(default)


def degradation(plant: Plant, p_elec: np.ndarray, starts: np.ndarray, capacity_mw: float, dt_h: float) -> np.ndarray:
    """Cumulative electrolyzer degradation state of Eq. (29), starting from zero."""
    el = plant.electrolyzer
    if el is None or capacity_mw <= 0:
        return np.zeros_like(p_elec)
    step = el.degradation_per_h_at_full_load * p_elec / capacity_mw * dt_h + el.degradation_per_start * starts
    return np.cumsum(step)


def _clean(a: np.ndarray, tol: float = 1e-9) -> np.ndarray:
    """Round solver noise (|x| < tol) to zero."""
    a = np.asarray(a, dtype=float).copy()
    a[np.abs(a) < tol] = 0.0
    return a


def solution_frame(pm) -> pd.DataFrame:
    """Hourly solution of a solved :class:`~nuclear_h2.model.PlantModel`."""
    m, plant, ts = pm.model, pm.plant, pm.ts
    H = ts.hours
    get = lambda name: _clean(_values(m, name, H))  # noqa: E731
    h = pd.DataFrame(index=ts.index if ts.index is not None else pd.RangeIndex(H, name="t"))
    h["energy_price"] = ts.energy_price
    h["p_turb"] = get("p_turb")
    h["q_turb"] = get("q_turb")
    h["p_elec"] = get("p_elec")
    h["q_elec"] = get("q_elec")
    h["h2_production"] = get("h2_production")
    el = plant.electrolyzer
    if el is not None and el.commitment:
        h["elec_on"] = np.round(get("u_elec"))
        h["elec_start"] = np.round(get("v_elec"))
        h["elec_stop"] = np.round(get("w_elec"))
    else:
        h["elec_on"] = 1.0 if el is not None else 0.0
        h["elec_start"] = 0.0
        h["elec_stop"] = 0.0
    h["p_charge"] = get("p_charge")
    h["p_discharge"] = get("p_discharge")
    h["soc"] = get("soc")
    if plant.storage is not None:
        h["h2_to_storage"] = get("h2_to_storage")
        h["h2_from_storage"] = get("h2_from_storage")
        h["h2_to_market"] = get("h2_to_market")
        h["h2_inventory"] = get("h2_inventory")
    else:
        h["h2_to_storage"] = 0.0
        h["h2_from_storage"] = 0.0
        h["h2_to_market"] = h["h2_production"]
        h["h2_inventory"] = 0.0
    h["p_sale"] = get("p_sale")
    h["p_buy"] = get("p_buy")
    for c, by_service in pm.bids.items():
        for s, var in by_service.items():
            h[f"bid_{c}_{s}"] = _clean(np.asarray(var.solution.values, dtype=float))
    _add_derived(h, plant, ts, capacities_of(pm))
    return h


def capacities_of(pm) -> Dict[str, float]:
    m, plant = pm.model, pm.plant
    caps = {"electrolyzer_mw": 0.0, "battery_mwh": 0.0, "battery_mw": 0.0, "storage_kg": 0.0}
    el, bt, st = plant.electrolyzer, plant.battery, plant.storage
    # solved sizes are snapped into their declared bounds (removes solver-tolerance noise)
    if el is not None:
        caps["electrolyzer_mw"] = (float(np.clip(_scalar(m, "P_elec_max", 0.0), el.lower_mw, el.upper_mw))
                                   if el.sized else float(el.capacity_mw))
    if bt is not None:
        caps["battery_mwh"] = (float(np.clip(_scalar(m, "E_b_max", 0.0), bt.lower_mwh, bt.upper_mwh))
                               if bt.sized else float(bt.energy_mwh))
        caps["battery_mw"] = caps["battery_mwh"] * bt.power_per_energy
    if st is not None:
        caps["storage_kg"] = (float(np.clip(_scalar(m, "M_stor_max", 0.0), st.lower_kg, st.upper_kg))
                              if st.sized else float(st.capacity_kg))
    return {k: float(_clean(np.array([v]), 1e-7)[0]) for k, v in caps.items()}


def _add_derived(h: pd.DataFrame, plant: Plant, ts: TimeSeries, caps: Dict[str, float]) -> None:
    """Net exchange, expected deployments, set points and the degradation state (Eq. 29)."""
    h["p_ies"] = h["p_sale"] - h["p_buy"]
    services = plant.active_services()
    dispatch = plant.market.as_mode == "dispatch"
    for c in ("turbine", "electrolyzer", "battery"):
        up = np.zeros(len(h))
        dn = np.zeros(len(h))
        for s in services:
            col = f"bid_{c}_{s.name}"
            if col in h:
                dep = h[col].to_numpy() * ts.winning_rate[s.column].to_numpy() * ts.deployment[s.name].to_numpy()
                h[f"dep_{c}_{s.name}"] = dep
                if s.direction == "up":
                    up += dep
                else:
                    dn += dep
        h[f"deployed_up_{c}"] = up
        h[f"deployed_down_{c}"] = dn
    if dispatch:
        h["p_elec_setpoint"] = h["p_elec"] + h["deployed_up_electrolyzer"] - h["deployed_down_electrolyzer"]
        h["p_turb_setpoint"] = h["p_turb"] - h["deployed_up_turbine"] + h["deployed_down_turbine"]
    else:
        h["p_elec_setpoint"] = h["p_elec"]
        h["p_turb_setpoint"] = h["p_turb"]
    h["degradation"] = degradation(plant, h["p_elec"].to_numpy(), h["elec_start"].to_numpy(),
                                   caps.get("electrolyzer_mw", 0.0), ts.dt_h)


# ---------------------------------------------------------------------------------------------------------------
# accounting
# ---------------------------------------------------------------------------------------------------------------

def as_revenue_by_service(plant: Plant, ts: TimeSeries, h: pd.DataFrame) -> pd.DataFrame:
    """Ancillary-service revenue of Eq. (4) per (component, service): capacity, energy and mileage parts, US$."""
    mk: Market = plant.market
    pay_energy = mk.as_mode == "bid" or (mk.as_mode == "dispatch" and mk.energy_payment_in_dispatch)
    rows = []
    dt = ts.dt_h
    for s in plant.active_services():
        W = ts.winning_rate[s.column].to_numpy()
        D = ts.deployment[s.name].to_numpy()
        price = s.price_share * ts.as_price[s.column].to_numpy()
        mil = np.zeros(len(h))
        if s.mileage and ts.mileage_price is not None and s.column in ts.mileage_price.columns:
            mil = s.price_share * (ts.mileage[s.column].to_numpy() * ts.performance[s.column].to_numpy()
                                   * ts.mileage_price[s.column].to_numpy())
        for c in ("turbine", "electrolyzer", "battery"):
            col = f"bid_{c}_{s.name}"
            if col not in h:
                continue
            b = h[col].to_numpy()
            rows.append({"component": c, "service": s.name,
                         "capacity": float(np.sum(dt * b * W * price)),
                         "energy": float(np.sum(dt * b * W * s.sign * D * ts.energy_price)) if pay_energy else 0.0,
                         "mileage": float(np.sum(dt * b * W * mil)),
                         "mean_bid_mw": float(np.mean(b)),
                         "expected_deployed_mwh": float(np.sum(dt * b * W * D))})
    df = pd.DataFrame(rows, columns=["component", "service", "capacity", "energy", "mileage", "mean_bid_mw",
                                     "expected_deployed_mwh"])
    df["total"] = df["capacity"] + df["energy"] + df["mileage"]
    return df


def account(plant: Plant, ts: TimeSeries, h: pd.DataFrame, caps: Dict[str, float]) -> Dict[str, float]:
    """Revenues, costs, profit (Eqs. 1-6) and quantities over the horizon, from the hourly table."""
    dt = ts.dt_h
    share = 1.0 / ts.annualization
    mk = plant.market
    tot: Dict[str, float] = {}
    tot["energy_revenue"] = float(np.sum(dt * ts.energy_price * (h["p_sale"] - h["p_buy"])))          # Eq. (2)
    sold = h["h2_from_storage"] + h["h2_to_market"] if plant.storage is not None else h["h2_production"]
    tot["h2_sales_revenue"] = float(np.sum(dt * mk.h2_price_usd_per_kg * sold))                        # Eq. (3)
    tot["h2_credit_revenue"] = float(np.sum(dt * mk.h2_credit_usd_per_kg * h["h2_production"]))
    asr = as_revenue_by_service(plant, ts, h)                                                           # Eq. (4)
    for c in ("turbine", "electrolyzer", "battery"):
        tot[f"as_revenue_{c}"] = float(asr.loc[asr["component"] == c, "total"].sum())
    tot["as_capacity_revenue"] = float(asr["capacity"].sum())
    tot["as_energy_payment"] = float(asr["energy"].sum())
    tot["as_mileage_revenue"] = float(asr["mileage"].sum())
    tb, el, bt, st = plant.turbine, plant.electrolyzer, plant.battery, plant.storage
    tot["turbine_vom"] = float(np.sum(dt * tb.vom_usd_per_mwh * h["p_turb"]))                         # Eq. (5)
    tot["electrolyzer_vom"] = float(np.sum(dt * el.vom_usd_per_mwh * h["p_elec"])) if el else 0.0
    tot["water_cost"] = float(np.sum(dt * el.water_cost_usd_per_kg * h["h2_production"])) if el else 0.0
    tot["startup_cost"] = float(el.startup_cost_usd * h["elec_start"].sum()) if el else 0.0
    tot["battery_vom"] = float(np.sum(dt * bt.vom_usd_per_mwh * 0.5 * (h["p_charge"] + h["p_discharge"]))) if bt else 0.0
    tot["storage_vom"] = float(np.sum(dt * st.vom_usd_per_kg * (h["h2_to_storage"] + h["h2_from_storage"]))) if st else 0.0
    tot["capex_electrolyzer"] = share * el.capex_usd_per_mw_yr * caps["electrolyzer_mw"] if el else 0.0  # Eq. (6)
    tot["capex_battery"] = (share * (bt.capex_energy_usd_per_mwh_yr * caps["battery_mwh"]
                                     + bt.capex_power_usd_per_mw_yr * caps["battery_mw"])) if bt else 0.0
    tot["capex_storage"] = share * st.capex_usd_per_kg_yr * caps["storage_kg"] if st else 0.0
    revenue = (tot["energy_revenue"] + tot["h2_sales_revenue"] + tot["h2_credit_revenue"]
               + tot["as_revenue_turbine"] + tot["as_revenue_electrolyzer"] + tot["as_revenue_battery"])
    opex = (tot["turbine_vom"] + tot["electrolyzer_vom"] + tot["water_cost"] + tot["startup_cost"]
            + tot["battery_vom"] + tot["storage_vom"])
    capex = tot["capex_electrolyzer"] + tot["capex_battery"] + tot["capex_storage"]
    tot["revenue"], tot["opex"], tot["capex"] = revenue, opex, capex
    tot["profit"] = revenue - opex - capex                                                              # Eq. (1)
    # quantities
    tot["generation_mwh"] = float(np.sum(dt * h["p_turb"]))
    tot["export_mwh"] = float(np.sum(dt * h["p_sale"]))
    tot["import_mwh"] = float(np.sum(dt * h["p_buy"]))
    tot["electrolyzer_mwh"] = float(np.sum(dt * h["p_elec"]))
    tot["h2_kg"] = float(np.sum(dt * h["h2_production"]))
    tot["h2_sold_kg"] = float(np.sum(dt * sold))
    tot["steam_to_electrolyzer_mwhth"] = float(np.sum(dt * h["q_elec"]))
    tot["reactor_heat_mwhth"] = float(tb.steam_total_mwth * dt * len(h))
    tot["battery_charge_mwh"] = float(np.sum(dt * h["p_charge"]))
    tot["battery_discharge_mwh"] = float(np.sum(dt * h["p_discharge"]))
    tot["electrolyzer_startups"] = float(h["elec_start"].sum())
    tot["as_deployed_mwh"] = float(asr["expected_deployed_mwh"].sum())
    # value of the steam diverted to electrolysis at the hourly energy price (used by the LCOH of Eq. 53)
    tot["steam_opportunity_cost"] = float(np.sum(dt * h["q_elec"] * tb.heat_to_power * ts.energy_price))
    return tot


def operation_of(pm, label: str = "") -> Operation:
    """Hourly table, capacities and accounting of a solved model."""
    from .solve import SolveError

    if pm.solver.get("termination") != "optimal":
        raise SolveError("the model has no optimal solution")
    h = solution_frame(pm)
    caps = capacities_of(pm)
    tot = account(pm.plant, pm.ts, h, caps)
    return Operation(label=label, plant=pm.plant, ts=pm.ts, hourly=h, capacities=caps, totals=tot,
                     solver=dict(pm.solver))


def baseline_plant(plant: Plant) -> Plant:
    """The same turbine and market without electrolyzer, battery, store and ancillary services (business as usual)."""
    return Plant(turbine=plant.turbine, market=replace(plant.market, as_mode="off"))


def baseline_operation(plant: Plant, ts: TimeSeries, label: str = "baseline") -> Operation:
    """Business-as-usual operation in closed form: all reactor steam to the turbine, all power sold."""
    base = baseline_plant(plant)
    base.validate()
    ts.validate(())
    tb = base.turbine
    p = float(tb.power_at(tb.steam_total_mwth))
    if not (tb.p_min_mw - 1e-9 <= p <= tb.p_max_mw + 1e-9):
        raise ParameterError("full reactor steam gives a turbine power outside [p_min_mw, p_max_mw]")
    if p > base.market.export_max_mw + 1e-9:
        raise ParameterError("the turbine output exceeds export_max_mw; the business-as-usual plant is infeasible")
    H = ts.hours
    h = pd.DataFrame(index=ts.index if ts.index is not None else pd.RangeIndex(H, name="t"))
    h["energy_price"] = ts.energy_price
    h["p_turb"] = p
    h["q_turb"] = tb.steam_total_mwth
    for col in ("p_elec", "q_elec", "h2_production", "elec_on", "elec_start", "elec_stop", "p_charge", "p_discharge",
                "soc", "h2_to_storage", "h2_from_storage", "h2_to_market", "h2_inventory", "p_buy"):
        h[col] = 0.0
    h["p_sale"] = p
    caps = {"electrolyzer_mw": 0.0, "battery_mwh": 0.0, "battery_mw": 0.0, "storage_kg": 0.0}
    _add_derived(h, base, ts, caps)
    tot = account(base, ts, h, caps)
    return Operation(label=label, plant=base, ts=ts, hourly=h, capacities=caps, totals=tot,
                     solver={"name": "closed form"})


# ---------------------------------------------------------------------------------------------------------------
# checks and metrics
# ---------------------------------------------------------------------------------------------------------------

def balance_residuals(op: Operation) -> Dict[str, float]:
    """Largest absolute residual of each balance and curve, recomputed from the hourly table."""
    h, plant, ts = op.hourly, op.plant, op.ts
    dt = ts.dt_h
    out = {}
    out["power_balance_eq7_8"] = float(np.max(np.abs(h["p_turb"] + h["p_discharge"] - h["p_elec"] - h["p_charge"]
                                                     - h["p_sale"] + h["p_buy"])))
    out["steam_balance_eq9"] = float(np.max(np.abs(h["q_turb"] + h["q_elec"] - plant.turbine.steam_total_mwth)))
    out["turbine_curve_eq13_16"] = float(np.max(np.abs(h["p_turb"] - plant.turbine.power_at(h["q_turb"]))))
    el = plant.electrolyzer
    if el is not None:
        out["hydrogen_curve_eq21_24"] = float(np.max(np.abs(h["h2_production"] - el.production_at(h["p_elec"]))))
        out["electrolyzer_steam_curve"] = float(np.max(np.abs(h["q_elec"] - el.steam_at(h["p_elec"]))))
    bt = plant.battery
    if bt is not None:
        e0 = bt.soc_initial_fraction * op.capacities["battery_mwh"]
        prev = np.concatenate([[e0], h["soc"].to_numpy()[:-1]])
        flow = dt * (bt.charge_efficiency * h["p_charge"] - h["p_discharge"] / bt.discharge_efficiency)
        out["battery_soc_eq30"] = float(np.max(np.abs(h["soc"] - prev - flow)))
    st = plant.storage
    if st is not None:
        prev = np.concatenate([[st.initial_kg], h["h2_inventory"].to_numpy()[:-1]])
        flow = dt * (st.charge_efficiency * h["h2_to_storage"] - h["h2_from_storage"] / st.discharge_efficiency)
        out["storage_balance_eq36"] = float(np.max(np.abs(h["h2_inventory"] - prev - flow)))
        out["production_split_eq37"] = float(np.max(np.abs(h["h2_production"] - h["h2_to_market"] - h["h2_to_storage"])))
    return out


def flexibility_metrics(op: Operation, ramp_threshold_mw: float = 5.0) -> Dict[str, float]:
    """Operating-flexibility indicators of one operation (definitions in docs/formulation.md)."""
    h, dt = op.hourly, op.ts.dt_h
    g = h["p_ies"].to_numpy()
    ramp = np.diff(g) / dt if len(g) > 1 else np.zeros(0)
    mean = float(np.mean(g))
    out = {"grid_output_mean_mw": mean, "grid_output_min_mw": float(np.min(g)), "grid_output_max_mw": float(np.max(g)),
           "grid_output_range_mw": float(np.max(g) - np.min(g)),
           "grid_output_cv": float(np.std(g) / mean) if mean > 0 else float("nan"),
           "max_ramp_up_mw_per_h": max(0.0, float(ramp.max())) if ramp.size else 0.0,
           "max_ramp_down_mw_per_h": max(0.0, float(-ramp.min())) if ramp.size else 0.0,
           "mean_abs_ramp_mw_per_h": float(np.mean(np.abs(ramp))) if ramp.size else 0.0,
           "ramp_events": int(np.sum(np.abs(ramp) > ramp_threshold_mw)),
           "turbine_range_mw": float(h["p_turb"].max() - h["p_turb"].min())}
    cap = op.capacities.get("electrolyzer_mw", 0.0)
    out["electrolyzer_capacity_factor"] = (float(h["p_elec"].sum() / (cap * len(h))) if cap > 0 else 0.0)
    out["electrolyzer_on_fraction"] = float(h["elec_on"].mean())
    out["electrolyzer_startups"] = int(h["elec_start"].sum())
    out["as_deployed_mwh"] = float(op.totals.get("as_deployed_mwh", 0.0))
    out["as_deployed_share_of_generation"] = (op.totals.get("as_deployed_mwh", 0.0) / op.totals["generation_mwh"]
                                             if op.totals["generation_mwh"] > 0 else 0.0)
    return out


def revenue_table(ops: Iterable[Operation], annual: bool = True) -> pd.DataFrame:
    """Revenues, costs and profit of several operations side by side (million US$; per year if ``annual``)."""
    cols = {}
    for op in ops:
        vals = op.annual_totals() if annual else dict(op.totals)
        cols[op.label or f"op{len(cols)}"] = {k: vals[k] / 1e6 for k in MONEY_KEYS + ("revenue", "opex", "capex", "profit")}
    return pd.DataFrame(cols)


__all__ = ["Operation", "solution_frame", "capacities_of", "degradation", "as_revenue_by_service", "account",
           "operation_of", "baseline_plant", "baseline_operation", "balance_residuals", "flexibility_metrics",
           "revenue_table", "MONEY_KEYS"]
