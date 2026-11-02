"""Solve a :class:`~nuclear_h2.model.PlantModel` with HiGHS (default) or Gurobi through linopy."""

from __future__ import annotations

from importlib import metadata
from typing import Mapping, Optional

from .model import PlantModel, build_model
from .parameters import Plant
from .timeseries import TimeSeries

#: HiGHS: quiet, with a tight relative MIP gap (any HiGHS option can be passed instead)
HIGHS_DEFAULTS = {"output_flag": False, "mip_rel_gap": 1e-6}
#: Gurobi: quiet, with the same relative MIP gap
GUROBI_DEFAULTS = {"OutputFlag": 0, "MIPGap": 1e-6}


class SolveError(RuntimeError):
    """The solver did not return an optimal solution."""


def _solver_version(solver: str) -> Optional[str]:
    package = {"highs": "highspy", "gurobi": "gurobipy"}.get(solver)
    try:
        return metadata.version(package) if package else None
    except metadata.PackageNotFoundError:
        return None


def _realized_gap(model, solver: str) -> Optional[float]:
    """Relative MIP gap reported by the solver after an in-memory solve (None if not available)."""
    native = getattr(model, "solver_model", None)
    try:
        if solver == "highs":
            return abs(float(native.getInfo().mip_gap))
        if solver == "gurobi":
            return abs(float(native.MIPGap))
    except Exception:  # attribute missing for this problem type, solver or linopy version
        return None
    return None


def solve(pm: PlantModel, solver: str = "highs", options: Optional[Mapping[str, object]] = None,
          log_file: Optional[str] = None, require_optimal: bool = True) -> PlantModel:
    """Solve in memory. ``options`` go to the solver unchanged and override the defaults above, e.g.
    ``{"time_limit": 600, "mip_rel_gap": 1e-4}`` for HiGHS or ``{"Threads": 8}`` for Gurobi.

    After solving, ``pm.objective`` is the profit Pi of Eq. (1) over the horizon (US$), including the capital
    cost of fixed-size components."""
    defaults = {"highs": HIGHS_DEFAULTS, "gurobi": GUROBI_DEFAULTS}.get(solver, {})
    opts = {**defaults, **(options or {})}
    kwargs = {"io_api": "direct", "progress": False}
    if log_file:
        kwargs["log_fn"] = log_file
    status, termination = pm.model.solve(solver_name=solver, **kwargs, **opts)
    status, termination = str(status), str(termination)
    pm.solver = {"name": solver, "version": _solver_version(solver), "status": status, "termination": termination,
                 "mip_gap_requested": opts.get("mip_rel_gap", opts.get("MIPGap")),
                 "mip_gap": _realized_gap(pm.model, solver)}
    if termination != "optimal":
        if require_optimal:
            raise SolveError(f"solver {solver} returned status={status}, termination={termination}")
        return pm
    obj = pm.model.objective
    value = obj.value if hasattr(obj, "value") else pm.model.objective_value
    pm.objective = -float(value) - pm.fixed_capex
    pm.solver["profit_usd"] = pm.objective
    return pm


def optimize(plant: Plant, ts: TimeSeries, solver: str = "highs", options: Optional[Mapping[str, object]] = None,
             label: str = ""):
    """Build, solve and account one operation: returns a :class:`nuclear_h2.results.Operation`."""
    from .results import operation_of

    pm = solve(build_model(plant, ts), solver=solver, options=options)
    return operation_of(pm, label=label)


__all__ = ["HIGHS_DEFAULTS", "GUROBI_DEFAULTS", "SolveError", "solve", "optimize"]
