"""Study-design tools (docs/scenarios.md): the case matrix (Cases 1-4 with their policy variants), price scenarios,
an electrolyzer-capacity-limit sweep and one-at-a-time TEA sweeps."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from .parameters import ParameterError, Plant
from .results import Operation, baseline_operation
from .solve import optimize
from .tea import TEAInputs, TEAResult, case_inputs, evaluate, results_table
from .timeseries import TimeSeries, scale_prices


@dataclass
class StudyDesign:
    """Lifetimes and construction periods of the case studies."""
    remaining_life_years: int                         # Cases 1 and 2: remaining operating life of the reactor
    retrofit_construction_years: int                  # Case 2
    greenfield_lifetimes_years: Tuple[int, ...]       # Cases 3 and 4 (one lifetime each)
    greenfield_construction_years: int

    def validate(self) -> None:
        if self.remaining_life_years < 1 or not self.greenfield_lifetimes_years:
            raise ParameterError("the study design needs a remaining life and at least one greenfield lifetime")


def with_capacities(plant: Plant, capacities: Mapping[str, float]) -> Plant:
    """The same plant with its sized components fixed at ``capacities`` (an operation's capacities): the sizing
    variables keep their roles (e.g. the constant-sales rule of a sized store) but their bounds coincide."""
    out = plant
    if plant.electrolyzer is not None and plant.electrolyzer.sized:
        c = float(capacities["electrolyzer_mw"])
        out = replace(out, electrolyzer=replace(plant.electrolyzer, capacity_range_mw=(c, c)))
    if plant.battery is not None and plant.battery.sized:
        c = float(capacities["battery_mwh"])
        out = replace(out, battery=replace(plant.battery, energy_range_mwh=(c, c)))
    if plant.storage is not None and plant.storage.sized:
        c = float(capacities["storage_kg"])
        out = replace(out, storage=replace(plant.storage, capacity_range_kg=(c, c)))
    return out


def operations(plant: Plant, ts: TimeSeries, solver: str = "highs", options: Optional[Mapping] = None,
               reoptimize_after_h2_credit: bool = False) -> Dict[str, Operation]:
    """The operations the case matrix needs: business as usual (closed form) and the integrated system (MILP).

    With ``reoptimize_after_h2_credit``, also the integrated system re-dispatched without the hydrogen credit of
    Eq. (3), with the capacities chosen in the first optimization held fixed (the assets are already built)."""
    ops = {"baseline": baseline_operation(plant, ts), "hybrid": optimize(plant, ts, solver, options, label="hybrid")}
    if reoptimize_after_h2_credit:
        built = with_capacities(plant, ops["hybrid"].capacities)
        no_credit = replace(built, market=replace(built.market, h2_credit_usd_per_kg=0.0))
        ops["hybrid_after_credit"] = optimize(no_credit, ts, solver, options, label="hybrid_after_credit")
    return ops


def case_matrix(ops: Mapping[str, Operation], base: TEAInputs, design: StudyDesign,
                include_45u: bool = True) -> Tuple[pd.DataFrame, Dict[str, TEAResult]]:
    """Evaluate Cases 1-4 with their policy variants (docs/scenarios.md). Returns a summary table and the results."""
    design.validate()
    after = ops.get("hybrid_after_credit")
    runs: List[Tuple[str, TEAResult]] = []
    u45 = (False, True) if include_45u and base.credits.u45 is not None else (False,)
    for flag in u45:
        tag = "45U" if flag else "no 45U"
        inp = case_inputs(base, "existing", design.remaining_life_years, with_45u=flag)
        runs.append((f"Case 1 existing, {tag}", evaluate(ops["baseline"], inp)))
        inp = replace(case_inputs(base, "retrofit", design.remaining_life_years, with_45u=flag),
                      construction_years=design.retrofit_construction_years)
        runs.append((f"Case 2 retrofit, {tag}", evaluate(ops["hybrid"], inp, after)))
    policies = ["none"] + [p for p, c in (("45y", base.credits.y45), ("48e", base.credits.e48)) if c is not None]
    names = {"none": "no 45Y/48E", "45y": "45Y", "48e": "48E"}
    for n, life in enumerate(design.greenfield_lifetimes_years):
        for pol in policies:
            inp = replace(case_inputs(base, "greenfield", life, policy=pol),
                          construction_years=design.greenfield_construction_years)
            runs.append((f"Case {3 + n} greenfield {life} yr, {names[pol]}", evaluate(ops["hybrid"], inp, after)))
    return results_table(runs), dict(runs)


def price_scenarios(plant: Plant, ts: TimeSeries, factors: Iterable[float], include_as: bool = False,
                    solver: str = "highs", options: Optional[Mapping] = None) -> Dict[float, Operation]:
    """Re-optimize the integrated system with the energy price scaled by each factor (capacity prices too if
    ``include_as``)."""
    return {f: optimize(plant, scale_prices(ts, f, include_as), solver, options, label=f"price x{f:g}")
            for f in factors}


def electrolyzer_limit_sweep(plant: Plant, ts: TimeSeries, limits_mw: Iterable[float], solver: str = "highs",
                             options: Optional[Mapping] = None) -> Dict[float, Operation]:
    """Re-optimize with different upper bounds of the electrolyzer's sizing range (a sized electrolyzer is required;
    its lower bound is kept, and a limit below it is refused)."""
    el = plant.electrolyzer
    if el is None or not el.sized:
        raise ParameterError("the sweep needs an electrolyzer whose capacity is sized")
    out = {}
    for lim in limits_mw:
        low = el.capacity_range_mw[0]
        if float(lim) < low:
            raise ParameterError(f"limit {lim} MW is below the sizing range's lower bound {low} MW")
        p = replace(plant, electrolyzer=replace(el, capacity_range_mw=(low, float(lim))))
        out[float(lim)] = optimize(p, ts, solver, options, label=f"limit {lim:g} MW")
    return out


def tea_sweep(operation: Operation, base: TEAInputs, setter: Callable[[TEAInputs, float], TEAInputs],
              values: Sequence[float], operation_after_h2_credit: Optional[Operation] = None) -> pd.DataFrame:
    """One-at-a-time TEA sweep without re-optimizing: ``setter(inputs, value)`` returns modified inputs."""
    rows = [(f"{v:g}", evaluate(operation, setter(base, v), operation_after_h2_credit)) for v in values]
    return results_table(rows)


def set_fixed_cost(name: str) -> Callable[[TEAInputs, float], TEAInputs]:
    """Setter for :func:`tea_sweep`: change ``usd_per_unit_yr`` of the fixed cost called ``name``."""
    def setter(inputs: TEAInputs, value: float) -> TEAInputs:
        if not any(f.name == name for f in inputs.fixed_costs):
            raise ParameterError(f"no fixed cost called {name}")
        costs = [replace(f, usd_per_unit_yr=value) if f.name == name else f for f in inputs.fixed_costs]
        return replace(inputs, fixed_costs=costs)
    return setter


__all__ = ["StudyDesign", "with_capacities", "operations", "case_matrix", "price_scenarios", "electrolyzer_limit_sweep", "tea_sweep",
           "set_fixed_cost"]
