"""Technical and market parameters of the integrated energy system (IES).

Symbols follow docs/formulation.md, which follows the article's Methods and Supplementary Tables 1-3.
Units: power MW, steam (thermal power) MW_th, energy MWh, hydrogen kg, hydrogen flow kg/h, money US$.

A plant is a nuclear turbine fed by a reactor at constant thermal output (Eq. 9), plus optional components:
an electrolyzer (high-temperature electrolysis when it also takes steam, low-temperature when its heat demand
is zero), a battery, and a hydrogen store. Each of electrolyzer capacity, battery energy and storage capacity is
either a fixed parameter or a sizing variable (Supplementary Tables 2-3: "parameters or variables").
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

COMPONENTS = ("turbine", "electrolyzer", "battery")
AS_MODES = ("off", "bid", "dispatch")


class ParameterError(ValueError):
    """A parameter set is incomplete or contradictory."""


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ParameterError(msg)


def _finite_nonneg(name: str, value: float) -> None:
    _check(value is not None and math.isfinite(value) and value >= 0, f"{name} must be finite and >= 0 (got {value})")


def _fraction(name: str, value: float, open_low: bool = False) -> None:
    ok = value is not None and math.isfinite(value) and (0 < value if open_low else 0 <= value) and value <= 1
    _check(ok, f"{name} must lie in {'(0' if open_low else '[0'}, 1] (got {value})")


def _increasing(name: str, values) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    _check(arr.ndim == 1 and arr.size >= 2, f"{name} needs at least two breakpoints")
    _check(bool(np.all(np.isfinite(arr))), f"{name} must be finite")
    _check(bool(np.all(np.diff(arr) > 0)), f"{name} must be strictly increasing")
    return arr


def merge_collinear(x: np.ndarray, y: np.ndarray, tol: float = 1e-12) -> Tuple[np.ndarray, np.ndarray]:
    """Drop interior breakpoints that lie on the line through their neighbours (same curve, fewer segments)."""
    keep = [0]
    for i in range(1, len(x) - 1):
        a, b = keep[-1], i + 1
        y_line = y[a] + (y[b] - y[a]) * (x[i] - x[a]) / (x[b] - x[a])
        if abs(y_line - y[i]) > tol * max(1.0, abs(y[i])):
            keep.append(i)
    keep.append(len(x) - 1)
    return x[keep], y[keep]


@dataclass
class Turbine:
    """Nuclear steam turbine (Eqs. 9-16). The reactor delivers ``steam_total_mwth`` in every hour (Eq. 9)."""
    steam_total_mwth: float                    # Q^{steam,total}
    steam_breakpoints_mwth: Tuple[float, ...]  # Q^{turb,bp}_k
    power_breakpoints_mw: Tuple[float, ...]    # P^{turb,bp}_k (power at each steam breakpoint)
    p_min_mw: float                            # P^{turb}_min
    p_max_mw: float                            # P^{turb}_max
    ramp_up_mw_per_h: float                    # R^{turb,up}_max
    ramp_down_mw_per_h: float                  # R^{turb,down}_max
    vom_usd_per_mwh: float = 0.0               # C^{turb,vom}

    def validate(self) -> None:
        q = _increasing("turbine steam breakpoints", self.steam_breakpoints_mwth)
        p = np.asarray(self.power_breakpoints_mw, dtype=float)
        _check(p.shape == q.shape, "turbine power and steam breakpoints need the same length")
        _check(bool(np.all(np.isfinite(p))) and bool(np.all(np.diff(p) >= 0)),
               "turbine power breakpoints must be finite and non-decreasing")
        _check(0 <= self.p_min_mw <= self.p_max_mw, "turbine needs 0 <= p_min_mw <= p_max_mw")
        _check(q[0] <= self.steam_total_mwth <= q[-1],
               "steam_total_mwth must lie within the turbine steam breakpoints")
        for name in ("ramp_up_mw_per_h", "ramp_down_mw_per_h", "vom_usd_per_mwh"):
            _finite_nonneg(f"turbine {name}", getattr(self, name))

    def curve(self) -> Tuple[np.ndarray, np.ndarray]:
        """Breakpoints (steam, power) with collinear interior points removed."""
        return merge_collinear(np.asarray(self.steam_breakpoints_mwth, float), np.asarray(self.power_breakpoints_mw, float))

    def power_at(self, steam_mwth) -> np.ndarray:
        """Turbine power on the piecewise-linear curve of Eqs. (13)-(16)."""
        q, p = self.curve()
        return np.interp(np.asarray(steam_mwth, dtype=float), q, p)

    @property
    def heat_to_power(self) -> float:
        """Average conversion at full reactor steam, MW_e per MW_th (used to value steam diverted to electrolysis)."""
        return float(self.power_at(self.steam_total_mwth)) / self.steam_total_mwth


def _size_choice(name: str, fixed: Optional[float], rng) -> None:
    """Exactly one of a fixed size and a sizing range [low, high] (0 <= low <= high, finite)."""
    _check((fixed is None) != (rng is None), f"{name}: give either a fixed size or a sizing range, not both or neither")
    if fixed is not None:
        _finite_nonneg(name, fixed)
    else:
        _check(len(rng) == 2 and all(v is not None and math.isfinite(v) for v in rng) and 0 <= rng[0] <= rng[1],
               f"{name}: the sizing range must be (low, high) with 0 <= low <= high")


@dataclass
class Electrolyzer:
    """Electrolyzer with piecewise-linear performance (Eqs. 17-29).

    ``electricity_kwh_per_kg`` and ``heat_kwh_per_kg`` give the specific electricity (k_e) and heat (k_t) use at
    each power breakpoint; production at breakpoint j is 1000 P_j / k_e,j kg/h (P_j in MW) and steam use is
    production x k_t,j / 1000 MW_th.
    Heat use of zero everywhere describes a low-temperature electrolyzer.
    The capacity is either fixed (``capacity_mw``) or sized within ``capacity_range_mw`` = (low, high).
    ``initial_state_h`` gives the state before the horizon: on (> 0) or off (< 0), and for how many hours.
    """
    power_breakpoints_mw: Tuple[float, ...]     # P^{elec,bp}_j
    electricity_kwh_per_kg: Tuple[float, ...]   # k_e at each breakpoint
    heat_kwh_per_kg: Tuple[float, ...]          # k_t at each breakpoint (MWh_th per 1000 kg)
    min_on_mw: float                            # P^{elec,on}_min
    initial_state_h: float                      # hours already on (> 0) or off (< 0) at the start: U^{elec}_init
                                                # and the history counted toward the minimum up/down times
    capacity_mw: Optional[float] = None         # P^{elec}_max fixed
    capacity_range_mw: Optional[Tuple[float, float]] = None   # or sized within (low, high)
    ramp_up_fraction_per_h: float = 1.0         # R^{elec,up}_max, fraction of P^{elec}_max per hour
    ramp_down_fraction_per_h: float = 1.0       # R^{elec,down}_max
    min_up_h: float = 1                         # T^{elec,up}_min
    min_down_h: float = 1                       # T^{elec,down}_min
    commitment: bool = True                     # on/off logic of Eqs. (17)-(18), (25)-(28); False = always on
    vom_usd_per_mwh: float = 0.0                # C^{elec,vom}
    startup_cost_usd: float = 0.0               # C^{elec,su}
    water_cost_usd_per_kg: float = 0.0          # C^{H2O}
    capex_usd_per_mw_yr: float = 0.0            # C^{elec,capex} (annualized, e.g. with Eq. 55)
    degradation_per_h_at_full_load: float = 0.0  # D_op
    degradation_per_start: float = 0.0          # D_su
    target_capacity_factor: Optional[float] = None   # CF^{H2}_target (None = no target constraint)

    def validate(self) -> None:
        p = _increasing("electrolyzer power breakpoints", self.power_breakpoints_mw)
        _check(p[0] > 0, "the first electrolyzer power breakpoint must be > 0 (the off state is P = 0)")
        ke = np.asarray(self.electricity_kwh_per_kg, dtype=float)
        kt = np.asarray(self.heat_kwh_per_kg, dtype=float)
        _check(ke.shape == p.shape and kt.shape == p.shape,
               "electricity_kwh_per_kg and heat_kwh_per_kg need one value per power breakpoint")
        _check(bool(np.all(np.isfinite(ke) & (ke > 0))), "electricity_kwh_per_kg must be finite and > 0")
        _check(bool(np.all(np.isfinite(kt) & (kt >= 0))), "heat_kwh_per_kg must be finite and >= 0")
        _check(bool(np.all(np.diff(self.production_breakpoints_kg_per_h()) >= 0)),
               "hydrogen production must not fall as power rises (check electricity_kwh_per_kg)")
        _check(p[0] <= self.min_on_mw, "min_on_mw must be >= the first power breakpoint")
        _check(math.isfinite(self.initial_state_h) and self.initial_state_h != 0,
               "initial_state_h must be non-zero: hours on (> 0) or off (< 0) before the horizon")
        _size_choice("electrolyzer capacity", self.capacity_mw, self.capacity_range_mw)
        _check(self.upper_mw <= p[-1] + 1e-9, "electrolyzer capacity (or its sizing range) must not exceed the last breakpoint")
        if self.capacity_mw is not None:
            _check(self.min_on_mw <= self.capacity_mw, "capacity_mw must be >= min_on_mw")
        for name in ("ramp_up_fraction_per_h", "ramp_down_fraction_per_h", "vom_usd_per_mwh", "startup_cost_usd",
                     "water_cost_usd_per_kg", "capex_usd_per_mw_yr", "degradation_per_h_at_full_load",
                     "degradation_per_start"):
            _finite_nonneg(f"electrolyzer {name}", getattr(self, name))
        _check(math.isfinite(self.min_up_h) and math.isfinite(self.min_down_h) and self.min_up_h > 0
               and self.min_down_h > 0, "minimum up and down times must be > 0")
        if self.target_capacity_factor is not None:
            _fraction("electrolyzer target_capacity_factor", self.target_capacity_factor)

    @property
    def initially_on(self) -> bool:
        return self.initial_state_h > 0

    @property
    def sized(self) -> bool:
        return self.capacity_range_mw is not None

    @property
    def upper_mw(self) -> float:
        """Largest possible capacity: the fixed capacity, or the top of the sizing range."""
        return float(self.capacity_mw) if self.capacity_mw is not None else float(self.capacity_range_mw[1])

    @property
    def lower_mw(self) -> float:
        """Smallest possible capacity: the fixed capacity, or the bottom of the sizing range."""
        return float(self.capacity_mw) if self.capacity_mw is not None else float(self.capacity_range_mw[0])

    def production_breakpoints_kg_per_h(self) -> np.ndarray:
        """M^{H2,bp}_j = 1000 P_j / k_e,j (kg/h, with P_j in MW and k_e in kWh/kg)."""
        return np.asarray(self.power_breakpoints_mw, float) * 1000.0 / np.asarray(self.electricity_kwh_per_kg, float)

    def steam_breakpoints_mwth(self) -> np.ndarray:
        """Steam use at each breakpoint, production x k_t / 1000 (MW_th, with k_t in kWh_th/kg)."""
        return self.production_breakpoints_kg_per_h() * np.asarray(self.heat_kwh_per_kg, float) / 1000.0

    def production_at(self, power_mw) -> np.ndarray:
        """Hydrogen production (kg/h) on the curve of Eqs. (21)-(24); zero when the power is zero."""
        p = np.asarray(power_mw, dtype=float)
        out = np.interp(p, np.asarray(self.power_breakpoints_mw, float), self.production_breakpoints_kg_per_h())
        return np.where(p > 0, out, 0.0)

    def steam_at(self, power_mw) -> np.ndarray:
        """Steam use (MW_th) on the companion curve; zero when the power is zero."""
        p = np.asarray(power_mw, dtype=float)
        out = np.interp(p, np.asarray(self.power_breakpoints_mw, float), self.steam_breakpoints_mwth())
        return np.where(p > 0, out, 0.0)


@dataclass
class Battery:
    """Battery energy storage (Eqs. 30-35). Power capacity = energy capacity x power_per_energy (Eq. 35).
    The energy capacity is either fixed (``energy_mwh``) or sized within ``energy_range_mwh`` = (low, high)."""
    power_per_energy: float                     # PowerRatio, MW per MWh
    charge_efficiency: float                    # eta^{b,ch}
    discharge_efficiency: float                 # eta^{b,dis}
    soc_min_fraction: float                     # SOC_min
    soc_initial_fraction: float                 # E^b_init / E^b_max
    capex_energy_usd_per_mwh_yr: float          # C^{b,capex,E} (annualized)
    capex_power_usd_per_mw_yr: float            # C^{b,capex,P} (annualized)
    energy_mwh: Optional[float] = None          # E^b_max fixed
    energy_range_mwh: Optional[Tuple[float, float]] = None    # or sized within (low, high)
    ramp_fraction_per_h: Optional[float] = None  # R^b_max on the net power, fraction of P^b_max per hour (None = off)
    end_soc_fraction: Optional[float] = None    # state of charge required at the end (None = free); setting it to
                                                # soc_initial_fraction gives a cyclic schedule
    vom_usd_per_mwh: float = 0.0                # C^{b,vom}

    def validate(self) -> None:
        _check(math.isfinite(self.power_per_energy) and self.power_per_energy > 0, "power_per_energy must be > 0")
        _fraction("battery charge_efficiency", self.charge_efficiency, open_low=True)
        _fraction("battery discharge_efficiency", self.discharge_efficiency, open_low=True)
        _fraction("battery soc_min_fraction", self.soc_min_fraction)
        _fraction("battery soc_initial_fraction", self.soc_initial_fraction)
        _check(self.soc_min_fraction <= self.soc_initial_fraction, "soc_initial_fraction must be >= soc_min_fraction")
        if self.end_soc_fraction is not None:
            _fraction("battery end_soc_fraction", self.end_soc_fraction)
            _check(self.end_soc_fraction >= self.soc_min_fraction, "end_soc_fraction must be >= soc_min_fraction")
        _size_choice("battery energy", self.energy_mwh, self.energy_range_mwh)
        if self.ramp_fraction_per_h is not None:
            _finite_nonneg("battery ramp_fraction_per_h", self.ramp_fraction_per_h)
        for name in ("vom_usd_per_mwh", "capex_energy_usd_per_mwh_yr", "capex_power_usd_per_mw_yr"):
            _finite_nonneg(f"battery {name}", getattr(self, name))

    @property
    def sized(self) -> bool:
        return self.energy_range_mwh is not None

    @property
    def upper_mwh(self) -> float:
        return float(self.energy_mwh) if self.energy_mwh is not None else float(self.energy_range_mwh[1])

    @property
    def lower_mwh(self) -> float:
        return float(self.energy_mwh) if self.energy_mwh is not None else float(self.energy_range_mwh[0])


@dataclass
class HydrogenStorage:
    """Hydrogen storage (Eqs. 36-40). The capacity is either fixed (``capacity_kg``) or sized within
    ``capacity_range_kg`` = (low, high); when sized, as stated with Eq. (40), sales leave the store at one constant
    rate and all production passes through the store."""
    charge_max_kg_per_h: float                  # Mdot^{stor,ch}_max
    discharge_max_kg_per_h: float               # Mdot^{stor,dis}_max
    charge_efficiency: float                    # eta^{stor,ch}
    discharge_efficiency: float                 # eta^{stor,dis}
    initial_kg: float                           # M^{stor}_init
    capacity_kg: Optional[float] = None         # M^{stor}_max fixed
    capacity_range_kg: Optional[Tuple[float, float]] = None   # or sized within (low, high)
    inventory_min_kg: float = 0.0               # M^{stor}_min (inventory floor)
    cyclic: bool = False                        # inventory at the end >= initial inventory
    vom_usd_per_kg: float = 0.0                 # C^{stor,vom}, per kg moved in or out
    capex_usd_per_kg_yr: float = 0.0            # C^{stor,capex} (annualized)

    def validate(self) -> None:
        _finite_nonneg("storage charge_max_kg_per_h", self.charge_max_kg_per_h)
        _finite_nonneg("storage discharge_max_kg_per_h", self.discharge_max_kg_per_h)
        _fraction("storage charge_efficiency", self.charge_efficiency, open_low=True)
        _fraction("storage discharge_efficiency", self.discharge_efficiency, open_low=True)
        _finite_nonneg("storage inventory_min_kg", self.inventory_min_kg)
        _finite_nonneg("storage initial_kg", self.initial_kg)
        _check(self.initial_kg >= self.inventory_min_kg, "storage initial_kg must be >= inventory_min_kg")
        _size_choice("storage capacity", self.capacity_kg, self.capacity_range_kg)
        _check(self.initial_kg <= self.upper_kg, "storage initial_kg must not exceed the capacity (or its sizing range)")
        for name in ("vom_usd_per_kg", "capex_usd_per_kg_yr"):
            _finite_nonneg(f"storage {name}", getattr(self, name))

    @property
    def sized(self) -> bool:
        return self.capacity_range_kg is not None

    @property
    def upper_kg(self) -> float:
        return float(self.capacity_kg) if self.capacity_kg is not None else float(self.capacity_range_kg[1])

    @property
    def lower_kg(self) -> float:
        """Smallest admissible capacity: it must hold the inventory floor and the initial inventory."""
        low = float(self.capacity_kg) if self.capacity_kg is not None else float(self.capacity_range_kg[0])
        return max(low, self.inventory_min_kg, self.initial_kg)


@dataclass(frozen=True)
class Service:
    """One ancillary service s (Eqs. 4, 41-46).

    ``product`` names the column of the clearing price, winning rate and (optional) mileage and performance
    tables; it defaults to ``name``. ``price_share`` scales every payment of the product (capacity and mileage).
    A market with one symmetric regulation product is entered as an up and a down service on the same product,
    each with ``price_share`` 0.5 and ``balance_with`` the other (Eq. 44, equal bids), so that the product is
    paid once. ``pair_with`` names the opposite-direction regulation service whose deployment, mileage and
    performance series are generated together with this one (``balance_with`` implies it); declaring the pair on
    one side is enough. ``energy_sign`` multiplies the energy payment of Eq. (4); its default is +1 for upward
    and -1 for downward services (see docs/formulation.md).
    """
    name: str
    direction: str                              # "up" or "down"
    product: Optional[str] = None
    price_share: float = 1.0
    components: Tuple[str, ...] = COMPONENTS
    balance_with: Optional[str] = None          # Eq. (44): equal bids with this service, per component
    pair_with: Optional[str] = None             # regulation partner for jointly generated series
    energy_sign: Optional[float] = None
    mileage: bool = False                       # adds b w M P λ^{mil} when a mileage price is given

    @property
    def column(self) -> str:
        return self.product or self.name

    @property
    def partner(self) -> Optional[str]:
        return self.balance_with or self.pair_with

    @property
    def sign(self) -> float:
        if self.energy_sign is not None:
            return float(self.energy_sign)
        return 1.0 if self.direction == "up" else -1.0

    def validate(self) -> None:
        _check(bool(self.name), "a service needs a name")
        _check(self.direction in ("up", "down"), f"service {self.name}: direction must be 'up' or 'down'")
        _check(math.isfinite(self.price_share) and self.price_share >= 0, f"service {self.name}: price_share >= 0")
        _check(self.energy_sign is None or math.isfinite(self.energy_sign), f"service {self.name}: energy_sign must be finite")
        bad = set(self.components) - set(COMPONENTS)
        _check(not bad, f"service {self.name}: unknown components {sorted(bad)}")


@dataclass
class Market:
    """Hydrogen value, grid limits and ancillary-service settings."""
    h2_price_usd_per_kg: float                  # λ^{H2}
    h2_credit_usd_per_kg: float = 0.0           # λ^{H2,sub}, per kg produced (Eq. 3)
    export_max_mw: float = math.inf             # upper bound of p^{sale}
    import_max_mw: float = math.inf             # upper bound of p^{buy}
    services: Tuple[Service, ...] = ()
    as_mode: str = "bid"                        # "off", "bid" (bids only) or "dispatch" (Eqs. 45-46 executed)
    reserve_duration_h: float = 1.0             # tau^{AS}: hours a battery must sustain its bids
    energy_payment_in_dispatch: bool = False    # also pay Eq. (4)'s energy term in dispatch mode

    def validate(self) -> None:
        _finite_nonneg("h2_price_usd_per_kg", self.h2_price_usd_per_kg)
        _check(math.isfinite(self.h2_credit_usd_per_kg), "h2_credit_usd_per_kg must be finite")
        _check(self.export_max_mw >= 0 and self.import_max_mw >= 0, "grid limits must be >= 0")
        _check(self.as_mode in AS_MODES, f"as_mode must be one of {AS_MODES}")
        _finite_nonneg("reserve_duration_h", self.reserve_duration_h)
        names = [s.name for s in self.services]
        _check(len(names) == len(set(names)), "service names must be unique")
        by_name = {s.name: s for s in self.services}
        partner: dict = {}
        for s in self.services:
            s.validate()
            for kind in ("balance_with", "pair_with"):
                ref = getattr(s, kind)
                if ref is None:
                    continue
                other = by_name.get(ref)
                _check(other is not None, f"service {s.name}: {kind} names an unknown service")
                _check(other.direction != s.direction, f"service {s.name}: {kind} must pair an up and a down service")
                for a, b in ((s.name, ref), (ref, s.name)):
                    _check(partner.setdefault(a, b) == b, f"service {a} is paired with more than one service")
                if kind == "balance_with":
                    _check(set(other.components) == set(s.components),
                           f"services {s.name} and {other.name}: balanced services need the same components")


@dataclass
class Plant:
    """An integrated energy system: turbine and market, plus optional electrolyzer, battery and hydrogen store."""
    turbine: Turbine
    market: Market
    electrolyzer: Optional[Electrolyzer] = None
    battery: Optional[Battery] = None
    storage: Optional[HydrogenStorage] = None

    def validate(self) -> None:
        self.turbine.validate()
        self.market.validate()
        for part in (self.electrolyzer, self.battery, self.storage):
            if part is not None:
                part.validate()
        _check(self.storage is None or self.electrolyzer is not None, "a hydrogen store needs an electrolyzer")
        el = self.electrolyzer
        if el is not None and not el.commitment:
            # always on: the steam left for the turbine at minimum electrolyzer load must stay on the turbine curve
            q, _ = self.turbine.curve()
            left = self.turbine.steam_total_mwth - float(el.steam_at(el.min_on_mw))
            _check(left >= q[0] - 1e-9, "an always-on electrolyzer leaves the turbine below its steam range")

    def components(self) -> Tuple[str, ...]:
        present = ["turbine"]
        if self.electrolyzer is not None:
            present.append("electrolyzer")
        if self.battery is not None:
            present.append("battery")
        return tuple(present)

    def balanced_pairs(self) -> Tuple[Tuple[str, str], ...]:
        """Distinct (up, down) service pairs with equal bids (Eq. 44), however they were declared."""
        by_name = {s.name: s for s in self.market.services}
        pairs = set()
        for s in self.market.services:
            if s.balance_with is not None:
                up, down = (s.name, s.balance_with) if s.direction == "up" else (s.balance_with, s.name)
                pairs.add((up, down))
        active = {s.name for s in self.active_services()}
        return tuple(sorted(p for p in pairs if p[0] in active and p[1] in active and p[0] in by_name))

    def active_services(self) -> Tuple[Service, ...]:
        """Services that at least one present component may offer (none when ``as_mode`` is 'off')."""
        if self.market.as_mode == "off":
            return ()
        present = set(self.components())
        return tuple(s for s in self.market.services if present & set(s.components))


__all__ = ["COMPONENTS", "AS_MODES", "ParameterError", "Turbine", "Electrolyzer", "Battery", "HydrogenStorage",
           "Service", "Market", "Plant", "merge_collinear"]
