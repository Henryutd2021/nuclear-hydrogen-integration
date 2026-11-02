"""Command line: ``nuclear-h2 synthetic | run | tea`` (see README)."""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path
from typing import List, Optional

from .io import read_inputs, read_tea, write_inputs, write_json
from .parameters import ParameterError
from .results import as_revenue_by_service, balance_residuals, flexibility_metrics, revenue_table
from .scenarios import case_matrix, operations
from .solve import SolveError, optimize
from .synthetic import (DEFAULT_SEED, illustrative_design, illustrative_plant, illustrative_tea_inputs,
                        synthetic_series)
from .timeseries import scale_prices


def _cmd_synthetic(args) -> int:
    plant = illustrative_plant(battery=not args.no_battery, storage=args.storage, as_mode=args.as_mode)
    ts = synthetic_series(hours=args.hours, seed=args.seed, services=plant.market.services)
    write_inputs(args.directory, plant, ts, tea=illustrative_tea_inputs(), design=illustrative_design())
    print(f"synthetic inputs ({args.hours} h, seed {args.seed}) written to {args.directory}")
    return 0


def _apply_overrides(plant, ts, args):
    if args.as_mode is not None:
        plant = dataclasses.replace(plant, market=dataclasses.replace(plant.market, as_mode=args.as_mode))
    if args.price_factor is not None:
        ts = scale_prices(ts, args.price_factor, include_as=args.scale_as)
    return plant, ts


def _cmd_run(args) -> int:
    plant, ts = read_inputs(args.directory)
    plant, ts = _apply_overrides(plant, ts, args)
    op = optimize(plant, ts, solver=args.solver, label="run")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    op.hourly.to_csv(out / "hourly.csv")
    as_revenue_by_service(plant, ts, op.hourly).to_csv(out / "as_revenue.csv", index=False)
    revenue_table([op]).to_csv(out / "revenue_musd_per_yr.csv")
    write_json(out / "summary.json", {"profit_usd_horizon": op.profit, "annual": op.annual_totals(),
                                      "capacities": op.capacities, "flexibility": flexibility_metrics(op),
                                      "balance_residuals": balance_residuals(op), "solver": op.solver,
                                      "hours": ts.hours, "as_mode": plant.market.as_mode})
    print(revenue_table([op]).round(3).to_string())
    print(f"capacities: {op.capacities}")
    print(f"results written to {out}")
    return 0


def _cmd_tea(args) -> int:
    plant, ts = read_inputs(args.directory)
    plant, ts = _apply_overrides(plant, ts, args)
    inputs, design = read_tea(args.directory)
    if design is None:
        raise ParameterError("tea.json needs a 'design' block for the case matrix")
    ops = operations(plant, ts, solver=args.solver, reoptimize_after_h2_credit=args.reoptimize_after_credit)
    table, results = case_matrix(ops, inputs, design)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "cases.csv")
    for label, res in results.items():
        safe = "".join(ch if ch.isalnum() else "_" for ch in label).strip("_")
        res.cash_flows.to_csv(out / f"cash_flows_{safe}.csv", index=False)
    write_json(out / "operations.json", {k: {"annual": op.annual_totals(), "capacities": op.capacities}
                                         for k, op in ops.items()})
    print(table.round(3).to_string())
    print(f"results written to {out}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="nuclear-h2", description="Nuclear-hydrogen integrated energy system framework")
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("synthetic", help="write synthetic series and illustrative parameters to a directory")
    s.add_argument("directory")
    s.add_argument("--hours", type=int, default=336)
    s.add_argument("--seed", type=int, default=DEFAULT_SEED)
    s.add_argument("--storage", action="store_true", help="include a sized hydrogen store")
    s.add_argument("--no-battery", action="store_true")
    s.add_argument("--as-mode", choices=("off", "bid", "dispatch"), default="bid")
    s.set_defaults(func=_cmd_synthetic)
    for name, func, text in (("run", _cmd_run, "optimize the operation of an input directory"),
                             ("tea", _cmd_tea, "operations plus the case matrix of the lifecycle TEA")):
        p = sub.add_parser(name, help=text)
        p.add_argument("directory")
        p.add_argument("--out-dir", required=True)
        p.add_argument("--solver", choices=("highs", "gurobi"), default="highs")
        p.add_argument("--as-mode", choices=("off", "bid", "dispatch"), default=None)
        p.add_argument("--price-factor", type=float, default=None, help="scale the energy price")
        p.add_argument("--scale-as", action="store_true", help="scale the capacity prices with --price-factor")
        if name == "tea":
            p.add_argument("--reoptimize-after-credit", action="store_true",
                           help="use a second operation without the hydrogen credit after it ends")
        p.set_defaults(func=func)
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (ParameterError, SolveError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
