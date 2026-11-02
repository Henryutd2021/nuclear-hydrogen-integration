# Architecture

## Modules

| Module | Role |
|---|---|
| `parameters` | dataclasses `Turbine`, `Electrolyzer`, `Battery`, `HydrogenStorage`, `Service`, `Market`, `Plant` with validation; units in field names |
| `services` | ancillary-service product lists of the seven ISO/RTO markets (Supplementary Table 6) as `Service` sets |
| `timeseries` | `TimeSeries` (energy price, AS prices, winning rates, deployment, mileage, performance) with validation; preparation helpers `to_hourly`, `drop_leap_day`, `fill_gaps`, `hourly_year`, `long_to_wide`, `scale_prices` |
| `synthetic` | invented series and parameters: energy prices, Eq. (56) capacity prices, deployment, mileage and performance generators; illustrative plant, TEA inputs and study design |
| `model` | `build_model`: the MILP of Eqs. (1)–(46) in linopy |
| `solve` | `solve` (HiGHS or Gurobi, solver metadata) and `optimize` (build, solve, account) |
| `results` | hourly table, capacities, accounting recomputed from the solution, closed-form business-as-usual operation, balance residuals, flexibility indicators, revenue tables |
| `finance` | Eqs. (47)–(51) and (55): NPV, IRR, payback, CRF, cost scaling, MACRS, 45U schedule, basis reduction |
| `tea` | lifecycle cash flow of a case (`evaluate`), capital items, replacements, fixed costs, credits, levelized costs (Eqs. 52–54), `case_inputs` |
| `scenarios` | `operations`, `case_matrix`, `price_scenarios`, `electrolyzer_limit_sweep`, `tea_sweep`, `StudyDesign` |
| `io` | input directories (JSON parameters, CSV series, TEA inputs) |
| `cli` | the commands `nuclear-h2 synthetic`, `nuclear-h2 run` and `nuclear-h2 tea` |

## Data flow

```text
public sources ──(data_pipeline.md)──► input directory ──read_inputs──► Plant, TimeSeries
                                          │                                  │
                                          └─ tea.json ─read_tea─► TEAInputs, StudyDesign
                                                                             │
Plant, TimeSeries ──build_model──► linopy MILP ──solve (HiGHS | Gurobi)──► solution
solution ──results──► Operation: hourly table, capacities, accounting, residuals, flexibility
Plant, TimeSeries ──baseline_operation──► Operation (business as usual, closed form)
Operation(s) + TEAInputs ──case_inputs / evaluate──► TEAResult: cash flows, NPV, IRR, payback, LCOE, LCOH, LCOS
```

## Model structure

Per hour and component (dimension `t`; the curve dimensions `turb_k`, `elec_k`):

| Block | Continuous variables | Binaries | Equations |
|---|---|---|---|
| turbine | steam, power, curve increments | one per inner breakpoint | (9)–(16) |
| electrolyzer | power, hydrogen, steam, curve increments | on, start, stop; one per inner breakpoint | (17)–(29) |
| battery | charge, discharge, state of charge | charging, discharging | (30)–(35) |
| hydrogen storage | in, out, direct sales, inventory | – | (36)–(40) |
| grid | sale, purchase | – | (2), (7)–(8) |
| ancillary services | one bid per component and service | – | (4), (41)–(46) |
| sizing | electrolyzer capacity, battery energy, storage capacity, constant sales rate | – | (6), (35), (40) |

The objective is the profit of Eq. (1) (minimized as its negative). Capital of fixed-size components is a constant, added back after solving. Every revenue and cost is recomputed from the hourly table by `results.account`, independently of the solver's objective value; the tests require both to agree.

## Solvers

linopy builds the model and passes it in memory to the solver:

- **HiGHS** (default, open source; installed with the package);
- **Gurobi** (optional: `pip install -e ".[gurobi]"` and a licence; `solver="gurobi"` or `--solver gurobi`).

Default options: quiet, relative MIP gap 1e-6. Any solver option can be passed (`options={"time_limit": 600, "mip_rel_gap": 1e-4}` for HiGHS). The result records the solver name, version, status, termination, requested and reported MIP gap (`Operation.solver`).

## Problem size

A full year (8,760 hours) of the illustrative synthetic plant on one laptop (Apple silicon, HiGHS 1.15.1, requested gap 1e-4):

| Instance | Variables | Constraints | Binaries | HiGHS |
|---|---|---|---|---|
| sized electrolyzer (three breakpoints) and battery, four services, bid mode | 297,842 | 429,236 | 78,840 | about 2.5 min |
| sized electrolyzer only, no services | 157,681 | 218,996 | 61,320 | about 3 s |

Harder price years, more breakpoints, more services or a sized hydrogen store can take much longer. Remedies: a looser MIP gap, Gurobi, fewer curve breakpoints (collinear turbine breakpoints are merged automatically), or blocks shorter than a year (capital is scaled to the horizon by Eq. 6 and results by `Operation.annual`).

## Outputs

`nuclear-h2 run DIR --out-dir OUT` writes:

| File | Content |
|---|---|
| `hourly.csv` | the hourly table (prices, powers, steam, hydrogen, storage, battery, bids, expected deployments, set points, degradation) |
| `as_revenue.csv` | AS revenue by component and service: capacity, energy, mileage, mean bid, expected deployed energy |
| `revenue_musd_per_yr.csv` | revenues, costs and profit, million \$/yr |
| `summary.json` | annual totals, capacities, flexibility indicators, balance residuals, solver |

`nuclear-h2 tea DIR --out-dir OUT` writes `cases.csv` (NPV, IRR, payback, LCOE, LCOH, LCOS, initial capital per case and variant), one `cash_flows_<case>.csv` per case and `operations.json`. Undefined values (for example an IRR that does not exist) are written as empty cells in CSV files and `null` in JSON.

## Tests

`pytest` runs:

- parameter validation and the ISO service sets;
- the finance functions (CRF, cost scaling, MACRS against the published 5-year table and its general properties, 45U schedule, basis reduction, NPV, IRR, payback);
- the preparation helpers and the generators (paired regulation series, short horizons, no services);
- the model: a hand-computed case, business as usual by MILP equal to the closed form, balance and curve residuals of every solution, accounting equal to the solver objective, on/off logic (legal start-stop cycles accepted, short cycles refused, times in periods, initial state carried into the minimum times), ramp limits, sizing (a sized store holds its initial inventory), storage and battery rules, AS headroom in both modes including one-sided offers, Eq. (44) declared on either side, Eq. (46), energy-payment signs, mileage paid once per product, degradation, low-temperature electrolysis, HiGHS against Gurobi (when installed);
- the TEA: cash flows by hand with tax, depreciation, construction years and payment offsets, the investment credit and basis reduction, credit timing and the second operation (same built capacities), replacements, fixed costs (also on sunk assets), levelized costs, fuel basis, case inputs and the case matrix;
- input directories (time-zone-aware indices across daylight-saving changes, sub-second timestamps, series without services, nothing overwritten, failed writes leave nothing behind) and the command line;
- parameter validation: fixed size or sizing range, required settings, finite mileage prices and energy signs.
