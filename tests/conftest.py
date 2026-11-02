"""Shared fixtures: a small invented plant and helpers to build short time series by hand."""

import numpy as np
import pandas as pd
import pytest

from nuclear_h2 import Battery, Electrolyzer, HydrogenStorage, Market, Plant, Service, TimeSeries, Turbine


def turbine(**kw):
    args = dict(steam_total_mwth=500.0, steam_breakpoints_mwth=(150.0, 350.0, 520.0),
                power_breakpoints_mw=(50.0, 125.0, 185.0), p_min_mw=50.0, p_max_mw=185.0,
                ramp_up_mw_per_h=40.0, ramp_down_mw_per_h=40.0, vom_usd_per_mwh=1.5)
    args.update(kw)
    return Turbine(**args)


def electrolyzer(**kw):
    args = dict(power_breakpoints_mw=(8.0, 20.0, 40.0), electricity_kwh_per_kg=(40.0, 39.0, 38.5),
                heat_kwh_per_kg=(7.0, 7.0, 7.0), min_on_mw=8.0, initial_state_h=6.0, capacity_mw=40.0,
                ramp_up_fraction_per_h=1.0, ramp_down_fraction_per_h=1.0, min_up_h=1, min_down_h=1, vom_usd_per_mwh=4.0,
                startup_cost_usd=120.0, water_cost_usd_per_kg=0.02, capex_usd_per_mw_yr=70_000.0)
    args.update(kw)
    return Electrolyzer(**args)


def battery(**kw):
    args = dict(power_per_energy=0.4, charge_efficiency=0.95, discharge_efficiency=0.93, soc_min_fraction=0.08,
                soc_initial_fraction=0.3, capex_energy_usd_per_mwh_yr=9_000.0,
                capex_power_usd_per_mw_yr=21_000.0, energy_mwh=50.0, vom_usd_per_mwh=1.2)
    args.update(kw)
    return Battery(**args)


def storage(**kw):
    args = dict(charge_max_kg_per_h=800.0, discharge_max_kg_per_h=900.0, charge_efficiency=0.97,
                discharge_efficiency=0.96, capacity_kg=6_000.0, inventory_min_kg=200.0, initial_kg=400.0,
                vom_usd_per_kg=0.015, capex_usd_per_kg_yr=7.0)
    args.update(kw)
    return HydrogenStorage(**args)


SERVICES = (
    Service("RegUp", "up", product="Reg", price_share=0.5, balance_with="RegDown", mileage=True),
    Service("RegDown", "down", product="Reg", price_share=0.5, balance_with="RegUp", mileage=True),
    Service("Spin", "up"),
    Service("Ramp", "down", components=("electrolyzer", "battery")),
)


def market(**kw):
    args = dict(h2_price_usd_per_kg=2.2, h2_credit_usd_per_kg=0.9, export_max_mw=400.0, import_max_mw=60.0,
                services=SERVICES, as_mode="bid", reserve_duration_h=0.75)
    args.update(kw)
    return Market(**args)


def plant(**kw):
    args = dict(turbine=turbine(), market=market(), electrolyzer=electrolyzer(), battery=None, storage=None)
    args.update(kw)
    return Plant(**args)


def series(prices, services=SERVICES, as_price=7.0, winning=0.6, deployment=0.2, dt=1.0, seed=11):
    """Hand-made series: constant AS settings (or a random draw when ``as_price`` is None)."""
    prices = np.asarray(prices, dtype=float)
    H = prices.size
    rng = np.random.default_rng(seed)
    products = list(dict.fromkeys(s.column for s in services))
    if as_price is None:
        asp = pd.DataFrame({p: rng.uniform(2.0, 12.0, H) for p in products})
        win = pd.DataFrame({p: rng.uniform(0.3, 0.9, H) for p in products})
        dep = pd.DataFrame({s.name: rng.uniform(0.0, 0.4, H) for s in services})
    else:
        asp = pd.DataFrame({p: np.full(H, as_price) for p in products})
        win = pd.DataFrame({p: np.full(H, winning) for p in products})
        dep = pd.DataFrame({s.name: np.full(H, deployment) for s in services})
    mil_products = list(dict.fromkeys(s.column for s in services if s.mileage))
    mil = pd.DataFrame({p: np.full(H, 3.0) for p in mil_products}) if mil_products else None
    perf = pd.DataFrame({p: np.full(H, 0.85) for p in mil_products}) if mil_products else None
    mp = pd.DataFrame({p: np.full(H, 0.3) for p in mil_products}) if mil_products else None
    return TimeSeries(energy_price=prices, as_price=asp, winning_rate=win, deployment=dep, mileage=mil,
                      performance=perf, mileage_price=mp, dt_h=dt)


@pytest.fixture
def varied_prices():
    rng = np.random.default_rng(19)
    p = 35 + 12 * np.sin(np.arange(36) * 2 * np.pi / 24) + rng.normal(0, 4, 36)
    p[[7, 20, 29]] = [140.0, 160.0, -12.0]
    return p
