"""Synthetic market series and illustrative parameters for examples and tests.

Everything here is invented. The generators follow the forms described in the article's Methods
("Data generation and collection"): ancillary-service capacity prices tied to the energy price (Eq. 56), paired
regulation deployment with daily and weekly patterns, Bernoulli-gated reserve deployment, noisy mileage factors and
truncated-normal performance factors. Their default settings are this package's own round numbers; they say
nothing about any real market, plant or year.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .parameters import Battery, Electrolyzer, HydrogenStorage, Market, Plant, Service, Turbine
from .timeseries import TimeSeries

DEFAULT_SEED = 2027


# ---------------------------------------------------------------------------------------------------------------
# generators
# ---------------------------------------------------------------------------------------------------------------

def energy_prices(hours: int, rng: np.random.Generator, base: float = 38.0, daily: float = 9.0,
                  weekly: float = 3.0, noise: float = 6.0, spike_prob: float = 0.01,
                  spike_range: Tuple[float, float] = (60.0, 220.0), dip_prob: float = 0.02,
                  dip_range: Tuple[float, float] = (-25.0, 5.0)) -> np.ndarray:
    """Hourly energy price ($/MWh): a daily and a weekly shape, Gaussian noise, rare spikes and low or negative dips."""
    h = np.arange(hours)
    shape = base + daily * np.sin(2 * np.pi * ((h % 24) - 9) / 24) + weekly * np.cos(2 * np.pi * h / 168)
    price = shape + rng.normal(0.0, noise, hours)
    spikes = rng.random(hours) < spike_prob
    price[spikes] += rng.uniform(*spike_range, spikes.sum())
    dips = (rng.random(hours) < dip_prob) & ~spikes
    price[dips] = rng.uniform(*dip_range, dips.sum())
    return price


@dataclass(frozen=True)
class AsPriceSettings:
    """Settings of the capacity-price generator of Eq. (56):
    price = max(0, LMP x U(ratio_low, ratio_high) + N(0, noise_sd) + spike),
    spike = U(spike_low, spike_high) with probability spike_prob in each hour, otherwise 0."""
    ratio_low: float = 0.06
    ratio_high: float = 0.22
    noise_sd: float = 2.0
    spike_prob: float = 0.015
    spike_low: float = 8.0
    spike_high: float = 35.0


def as_capacity_prices(lmp: np.ndarray, rng: np.random.Generator,
                       settings: AsPriceSettings = AsPriceSettings()) -> np.ndarray:
    """One series of ancillary-service capacity prices ($/MW per hour) by the form of Eq. (56)."""
    n = lmp.size
    ratio = rng.uniform(settings.ratio_low, settings.ratio_high, n)
    spike = np.where(rng.random(n) < settings.spike_prob, rng.uniform(settings.spike_low, settings.spike_high, n), 0.0)
    return np.maximum(0.0, lmp * ratio + rng.normal(0.0, settings.noise_sd, n) + spike)


def _smooth(x: np.ndarray, window: int) -> np.ndarray:
    """Centred moving average of the same length (the window shrinks at the ends and for short series)."""
    n = x.size
    w = min(int(window), n)
    if w <= 1:
        return x
    kernel = np.ones(w)
    return np.convolve(x, kernel, mode="same") / np.convolve(np.ones(n), kernel, mode="same")


def regulation_deployment(hours: int, rng: np.random.Generator, mean_total: float = 0.35, up_share: float = 0.55,
                          cycles_per_day: int = 2, weekly_amplitude: float = 0.1, noise_sd: float = 0.15,
                          smoothing_h: int = 3, total_cap: float = 0.9) -> Tuple[np.ndarray, np.ndarray]:
    """Paired regulation-up and regulation-down deployment factors.

    A smoothed random series with ``cycles_per_day`` intra-day cycles and a weekly pattern sets the hourly
    total; ``up_share`` of the mean total goes to regulation up. Each hour's up + down is capped at ``total_cap``.
    """
    h = np.arange(hours)
    pattern = 1 + 0.3 * np.sin(2 * np.pi * cycles_per_day * h / 24) + weekly_amplitude * np.sin(2 * np.pi * h / 168)
    total = mean_total * pattern * (1 + _smooth(rng.normal(0.0, noise_sd, hours), smoothing_h))
    total = np.clip(total, 0.0, total_cap)
    split = np.clip(up_share + _smooth(rng.normal(0.0, 0.08, hours), smoothing_h), 0.05, 0.95)
    up, down = total * split, total * (1 - split)
    scale = np.minimum(1.0, total_cap / np.maximum(up + down, 1e-12))
    return up * scale, down * scale


def reserve_deployment(hours: int, rng: np.random.Generator, probability: float = 0.06,
                       magnitude: Tuple[float, float] = (0.15, 0.6)) -> np.ndarray:
    """Reserve deployment factors: a Bernoulli event in each hour with a uniformly drawn magnitude, else zero."""
    events = rng.random(hours) < probability
    return np.where(events, rng.uniform(magnitude[0], magnitude[1], hours), 0.0)


def mileage_factors(hours: int, rng: np.random.Generator, base: float = 4.0, noise_fraction: float = 0.12,
                    clip: Tuple[float, float] = (0.5, 2.0)) -> np.ndarray:
    """Regulation mileage factor: ``base`` with Gaussian noise of ``noise_fraction`` x base, clipped to
    ``clip`` x base."""
    m = base * (1 + rng.normal(0.0, noise_fraction, hours))
    return np.clip(m, clip[0] * base, clip[1] * base)


def performance_factors(hours: int, rng: np.random.Generator, mean: float = 0.9, sd: float = 0.05,
                        low: float = 0.6, high: float = 0.99) -> np.ndarray:
    """Regulation performance factor from a normal distribution truncated to [low, high] (by resampling)."""
    out = rng.normal(mean, sd, hours)
    bad = (out < low) | (out > high)
    while bad.any():
        out[bad] = rng.normal(mean, sd, bad.sum())
        bad = (out < low) | (out > high)
    return out


# ---------------------------------------------------------------------------------------------------------------
# illustrative services, parameters and a complete synthetic data set
# ---------------------------------------------------------------------------------------------------------------

def illustrative_services() -> Tuple[Service, ...]:
    """A small invented service set: symmetric regulation (one product, entered as a balanced up/down pair with a
    mileage payment), a spinning-type and a non-spinning-type upward reserve."""
    return (
        Service("RegUp", "up", product="Reg", price_share=0.5, balance_with="RegDown", mileage=True),
        Service("RegDown", "down", product="Reg", price_share=0.5, balance_with="RegUp", mileage=True),
        Service("Spin", "up"),
        Service("NonSpin", "up", components=("electrolyzer", "battery")),
    )


def illustrative_plant(battery: bool = True, storage: bool = False, as_mode: str = "bid") -> Plant:
    """Round, invented parameters for a mid-size reactor with a steam-fed electrolyzer (sized), an optional battery
    (sized) and an optional hydrogen store (sized). For demonstrations only."""
    turbine = Turbine(steam_total_mwth=900.0, steam_breakpoints_mwth=(260.0, 600.0, 950.0),
                      power_breakpoints_mw=(85.0, 220.0, 345.0), p_min_mw=85.0, p_max_mw=345.0,
                      ramp_up_mw_per_h=60.0, ramp_down_mw_per_h=60.0, vom_usd_per_mwh=2.5)
    electrolyzer = Electrolyzer(power_breakpoints_mw=(15.0, 60.0, 120.0),
                                electricity_kwh_per_kg=(38.6, 37.6, 37.2),
                                heat_kwh_per_kg=(6.5, 6.5, 6.5), min_on_mw=15.0, initial_state_h=12.0,
                                capacity_range_mw=(20.0, 120.0), ramp_up_fraction_per_h=0.3,
                                ramp_down_fraction_per_h=0.3, min_up_h=3, min_down_h=3,
                                vom_usd_per_mwh=5.5, startup_cost_usd=450.0, water_cost_usd_per_kg=0.04,
                                capex_usd_per_mw_yr=95_000.0, degradation_per_h_at_full_load=0.0002,
                                degradation_per_start=0.8)
    bat = Battery(power_per_energy=0.5, charge_efficiency=0.94, discharge_efficiency=0.94, soc_min_fraction=0.05,
                  soc_initial_fraction=0.4, capex_energy_usd_per_mwh_yr=15_000.0,
                  capex_power_usd_per_mw_yr=40_000.0, energy_range_mwh=(10.0, 200.0),
                  vom_usd_per_mwh=1.5) if battery else None
    store = HydrogenStorage(charge_max_kg_per_h=3_000.0, discharge_max_kg_per_h=3_000.0, charge_efficiency=0.99,
                            discharge_efficiency=0.99, initial_kg=1_000.0, capacity_range_kg=(2_000.0, 60_000.0),
                            inventory_min_kg=1_000.0, vom_usd_per_kg=0.02, capex_usd_per_kg_yr=11.0) if storage else None
    market = Market(h2_price_usd_per_kg=2.5, h2_credit_usd_per_kg=1.5, export_max_mw=600.0, import_max_mw=150.0,
                    services=illustrative_services(), as_mode=as_mode, reserve_duration_h=1.0)
    return Plant(turbine=turbine, market=market, electrolyzer=electrolyzer, battery=bat, storage=store)


def synthetic_series(hours: int = 336, seed: int = DEFAULT_SEED, services: Optional[Sequence[Service]] = None,
                     start: str = "2030-07-01", as_settings: AsPriceSettings = AsPriceSettings()) -> TimeSeries:
    """A complete synthetic :class:`TimeSeries` for ``services`` (default: :func:`illustrative_services`; an empty
    tuple gives a series without ancillary services)."""
    if hours < 1:
        raise ValueError("hours must be >= 1")
    rng = np.random.default_rng(seed)
    services = tuple(services if services is not None else illustrative_services())
    by_name = {s.name: s for s in services}
    rows = pd.RangeIndex(hours)
    lmp = energy_prices(hours, rng)
    products = list(dict.fromkeys(s.column for s in services))
    as_price = pd.DataFrame({p: as_capacity_prices(lmp, rng, as_settings) for p in products}, index=rows)
    winning = pd.DataFrame({p: rng.uniform(0.35, 0.85, hours) for p in products}, index=rows)
    # regulation partners (balance_with or pair_with, declared on either side) are generated together
    partner = {}
    for s in services:
        if s.partner is not None and s.partner in by_name:
            partner[s.name], partner[s.partner] = s.partner, s.name
    deployment: Dict[str, np.ndarray] = {}
    for s in services:
        if s.name in deployment:
            continue
        if s.name in partner:
            other = by_name[partner[s.name]]
            up, down = regulation_deployment(hours, rng)
            up_s, down_s = (s, other) if s.direction == "up" else (other, s)
            deployment[up_s.name], deployment[down_s.name] = up, down
        else:
            deployment[s.name] = reserve_deployment(hours, rng)
    # mileage and performance per product; products linked by service pairs share one profile (one profile per
    # connected group of products)
    mileage_products = list(dict.fromkeys(s.column for s in services if s.mileage))
    root: Dict[str, str] = {}

    def find(p):
        while root[p] != p:
            root[p] = root[root[p]]
            p = root[p]
        return p

    for s in services:                                 # connect products through every service pair
        root.setdefault(s.column, s.column)
    for s in services:
        if s.name in partner:
            a, b = find(s.column), find(by_name[partner[s.name]].column)
            root[a] = b
    mil, perf, mprice = {}, {}, {}
    profiles = {}
    for col in mileage_products:
        key = find(col)
        if key not in profiles:
            profiles[key] = (mileage_factors(hours, rng), performance_factors(hours, rng),
                             np.maximum(0.0, rng.normal(0.4, 0.15, hours)))
        mil[col], perf[col], mprice[col] = profiles[key]
    mileage = pd.DataFrame(mil, index=rows) if mil else None
    performance = pd.DataFrame(perf, index=rows) if perf else None
    mileage_price = pd.DataFrame(mprice, index=rows) if mprice else None
    index = pd.date_range(start, periods=hours, freq="1h")
    return TimeSeries(energy_price=lmp, as_price=as_price, winning_rate=winning,
                      deployment=pd.DataFrame({s.name: deployment[s.name] for s in services}, index=rows),
                      mileage=mileage, performance=performance, mileage_price=mileage_price, dt_h=1.0, index=index)


def illustrative_tea_inputs():
    """Round, invented TEA inputs (capital items, replacements, fixed costs and credits) for the examples."""
    from .finance import exponent_from_learning_rate
    from .tea import (CapexItem, Credit45U, Credit48E, CreditPerUnit, Credits, FixedCost, Replacement,
                      TEAInputs)

    capex = [
        CapexItem("electrolyzer", "hydrogen", 52e6, {-3: 0.2, -2: 0.4, -1: 0.4}, ref_size=40.0,
                  size="electrolyzer_mw", exponent=0.9,
                  macrs_years=5),
        CapexItem("h2_storage", "hydrogen", 9e6, {-2: 0.3, -1: 0.7}, ref_size=40_000.0, size="storage_kg",
                  macrs_years=5),
        CapexItem("battery_energy", "battery", 14e6, {-1: 0.6, 0: 0.4}, ref_size=120.0, size="battery_mwh",
                  macrs_years=5),
        CapexItem("battery_power", "battery", 9e6, {-1: 0.6, 0: 0.4}, ref_size=60.0, size="battery_mw",
                  macrs_years=5),
        CapexItem("grid_interconnection", "shared", 4e6, {-3: 0.5, -2: 0.25, -1: 0.25}, macrs_years=20),
        CapexItem("plant_modifications", "shared", 3.5e6, {-2: 0.6, -1: 0.4}, macrs_years=20, cases=("retrofit",)),
        CapexItem("reactor_plant", "nuclear", 5.2e9, {-6: 0.1, -5: 0.15, -4: 0.2, -3: 0.2, -2: 0.2, -1: 0.15},
                  ref_size=400.0, size="reactor_mwe",
                  exponent=exponent_from_learning_rate(0.04), macrs_years=20, itc_eligible=True,
                  cases=("greenfield",)),
        CapexItem("site_preparation", "nuclear", 2.4e8, {-6: 0.6, -5: 0.4}, ref_size=400.0, size="reactor_mwe",
                  exponent=exponent_from_learning_rate(0.025), macrs_years=20, cases=("greenfield",)),
    ]
    replacements = [Replacement("electrolyzer", years=(8, 16, 24, 32, 40, 48, 56, 64), fraction=0.35),
                    Replacement("battery_energy", years=(12, 24, 36, 48, 60), fraction=0.5),
                    Replacement("h2_storage", years=(17, 34, 51), amount_usd=3e6)]
    fixed = [FixedCost("nuclear_fixed_om", "nuclear", usd_per_unit_yr=140_000.0, size="reactor_mwe", escalation=0.015),
             FixedCost("electrolyzer_fixed_om", "hydrogen", capex_fraction=0.03, of_item="electrolyzer"),
             FixedCost("battery_fixed_om", "battery", usd_per_unit_yr=12_000.0, size="battery_mw")]
    credits = Credits(u45=Credit45U(max_usd_per_mwh=12.0, price_low=22.0, price_high=40.0, first_year=2031,
                                    last_year=2036),
                      y45=CreditPerUnit(usd_per_unit=25.0, years=8),
                      e48=Credit48E(rate=0.4, basis_reduction=0.45),
                      hydrogen=CreditPerUnit(usd_per_unit=1.5, years=8, basis="kg"))
    return TEAInputs(lifetime_years=25, discount_rate=0.07, tax_rate=0.25, construction_years=3,
                     first_operating_year=2031, nuclear_vom_usd_per_mwh=2.0, fuel_usd_per_mwh=7.5,
                     fuel_basis="generation", capex=capex, replacements=replacements, fixed_costs=fixed,
                     credits=credits, case="retrofit")


def illustrative_design():
    """Invented lifetimes and construction periods for the case matrix."""
    from .scenarios import StudyDesign

    return StudyDesign(remaining_life_years=25, retrofit_construction_years=3, greenfield_lifetimes_years=(50, 70),
                       greenfield_construction_years=6)


__all__ = ["DEFAULT_SEED", "energy_prices", "AsPriceSettings", "as_capacity_prices", "regulation_deployment",
           "reserve_deployment", "mileage_factors", "performance_factors", "illustrative_services",
           "illustrative_plant", "synthetic_series", "illustrative_tea_inputs", "illustrative_design"]
