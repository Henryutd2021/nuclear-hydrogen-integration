# Nuclear–hydrogen integration framework: hourly operation, ancillary services and lifecycle economics of nuclear plants with electrolysis

Author: [Honglin Li](https://henryutd2021.github.io/)

A documented, runnable implementation of the modelling framework in:

> Honglin Li, Jianqiao Huang, Bikash Poudel, Mucun Sun, Tyler Westover, Daniel Wendt and Jie Zhang. **Rethinking the economics and flexibility of U.S. nuclear power through hydrogen integration and policy support.** *Nature Communications* 17, 7090 (2026). [doi:10.1038/s41467-026-73630-y](https://doi.org/10.1038/s41467-026-73630-y) (open access).

A nuclear plant can do more than sell electricity around the clock. With high-temperature electrolysis (HTE), part of its electricity and steam can make hydrogen; with hydrogen storage and a battery, it can shift its output and offer ancillary services to the grid.

The framework has two parts:

- **Operation and sizing.** An hourly mixed-integer program co-optimizes the turbine, the electrolyzer (with its on/off logic and part-load curves), hydrogen storage, a battery, grid sales and purchases, and bids in the market's ancillary services.
- **Lifecycle economics.** A discounted-cash-flow analysis turns the optimized year into NPV, IRR, payback and levelized costs (LCOE, LCOH, LCOS), with component capital, replacements, MACRS depreciation and federal credits (Sections 45U, 45Y, 48E and a hydrogen production credit).

## Research question

Can existing and new U.S. nuclear power plants improve their economics and operating flexibility by adding high-temperature electrolysis, hydrogen storage and batteries, and by selling energy, hydrogen and ancillary services? How do market design, policy credits and costs shape the answer?

## System and scope

| Aspect | In the article | In this framework |
|---|---|---|
| Plants | operating U.S. reactors in the seven ISO/RTO markets, with plant-specific parameters; a representative reactor for sensitivity analyses | any reactor and turbine given by thermal power and a piecewise-linear turbine curve |
| Markets | hourly energy prices and the ancillary services of each ISO, one historical year | any hourly series; the product sets of the seven ISOs are included (`iso_services`) |
| Hydrogen | HTE with piecewise-linear electricity and steam use, on/off with minimum up and down times, ramps, startup cost, degradation; hydrogen storage; hydrogen sales and a production credit | same (low-temperature electrolysis as zero heat use) |
| Battery | battery storage with charge/discharge status | same |
| Ancillary services | bids by turbine, electrolyzer and battery; headroom and ramp limits; regulation balance; dispatch execution mode | same, plus a bid-only mode ([conventions](docs/formulation.md#conventions-of-this-implementation)) |
| Sizing | electrolyzer power, battery energy and hydrogen storage capacity as parameters or variables | same |
| Objective | profit: energy, hydrogen and ancillary-service revenue minus operating and annualized capital costs | same, Eq. (1) |
| TEA | discounted cash flow, component capital with scaling and replacements, MACRS, 45U, 45Y, 48E, hydrogen production credit, LCOE, LCOH, LCOS | same, Eqs. (47)–(55) |
| Cases | existing reactor (Case 1), retrofit (Case 2), greenfield with two lifetimes (Cases 3–4), with policy variants | `case_matrix` |
| Sensitivity | reactor fixed O&M, the HTE capacity bound, electricity price levels | `tea_sweep`, `electrolyzer_limit_sweep`, `price_scenarios` |
| Solver | mixed-integer linear program | linopy; HiGHS by default, Gurobi optional |

The complete mathematics is in [docs/formulation.md](docs/formulation.md). Equation numbers in this repository refer to the article's Methods: (1)–(46) operation, (47)–(55) TEA, (56) synthetic ancillary-service prices.

## What is in this repository

| Path | Content |
|---|---|
| [`docs/formulation.md`](docs/formulation.md) | sets, parameters and variables; Eqs. (1)–(56); conventions of this implementation; flexibility indicators; scope stated in the article |
| [`docs/data_pipeline.md`](docs/data_pipeline.md) | how each input is built from public sources, the ISO data portals, PJM market data (the included 2024 prices and how to prepare others), preparation rules, generated ancillary-service series, input schemas, terms of use |
| [`docs/scenarios.md`](docs/scenarios.md) | the article's study design (fleet analysis, Cases 1–4, policy variants, sensitivity groups) and how to express it; reading the results |
| [`docs/architecture.md`](docs/architecture.md) | modules, data flow, model structure, solvers, problem size, outputs, tests |
| [`src/nuclear_h2/`](src/nuclear_h2/) | the implementation: parameters, ISO service sets, time series and preparation helpers, the PJM data loader, the MILP (linopy), solving, accounting and indicators, finance, TEA, scenario tools, command line |
| [`data/pjm/`](data/pjm/) | the article's 2024 PJM price inputs (hourly energy prices and ancillary-service clearing prices), with their [README](data/pjm/README.md): source, units, hour labels, provenance, checksums and PJM's terms. Not covered by the repository's licences |
| [`examples/`](examples/) | `minimal.py`, `ancillary_services.py`, `lifecycle_tea.py`, `sensitivity.py`, `prepare_inputs.py`, `pjm_2024.py` |
| [`tests/`](tests/) | 181 tests: hand-computed operation and cash flows, balances recomputed from every solution, accounting against the solver objective, unit-commitment and storage rules, AS headroom and modes, credits, depreciation, levelized costs, input files, command line, the PJM data files |

## Quick start

```bash
git clone https://github.com/Henryutd2021/nuclear-hydrogen-integration.git
cd nuclear-hydrogen-integration
python3 -m venv .venv && source .venv/bin/activate    # needs Python 3.10+ (check: python3 --version); if older, use e.g. python3.12
pip install -e ".[test]"
python examples/minimal.py                              # business as usual vs the integrated system, two synthetic weeks, HiGHS
python examples/pjm_2024.py                             # the article's 2024 PJM prices: summary and one week of operation
pytest
```

With conda instead: `conda env create -f environment.yml && conda activate nuclear-h2 && pip install -e .`

The command line works on input directories:

```bash
nuclear-h2 synthetic my_inputs/ --storage                    # synthetic series, illustrative parameters and TEA inputs
nuclear-h2 run my_inputs/ --out-dir outputs/base             # hourly operation: dispatch, sizing, revenues, flexibility
nuclear-h2 run my_inputs/ --as-mode dispatch --price-factor 1.15 --out-dir outputs/price_up
nuclear-h2 tea my_inputs/ --out-dir outputs/tea              # Cases 1-4 with their policy variants
```

In Python:

```python
from nuclear_h2 import (baseline_operation, case_matrix, flexibility_metrics, iso_services, operations,
                        optimize, read_inputs, read_tea, revenue_table)

plant, ts = read_inputs("my_inputs/")                  # your own data, see docs/data_pipeline.md
bau = baseline_operation(plant, ts)                    # business as usual, closed form
ies = optimize(plant, ts)                              # Eqs. (1)-(46); optimize(plant, ts, solver="gurobi")
print(revenue_table([bau, ies]))                       # million $/yr by revenue and cost stream
print(ies.capacities, flexibility_metrics(ies))

tea, design = read_tea("my_inputs/")
table, results = case_matrix(operations(plant, ts), tea, design)
print(table)                                           # NPV, IRR, payback, LCOE, LCOH, LCOS per case and variant
```

**About the example data:** `nuclear_h2.synthetic` generates invented series (two synthetic weeks by default) and round illustrative parameters, costs and credit values. They show how the model behaves and how to read its output. Their numbers say nothing about any real plant, market, policy or year.

## Using your own data

1. **Market series.** Prepare hourly energy prices and ancillary-service clearing prices from the ISO's data portal, and the generated deployment, winning-rate and mileage series, as described in [docs/data_pipeline.md](docs/data_pipeline.md) (see `examples/prepare_inputs.py`; for PJM in 2024, the article's prices are in `data/pjm/`, see `examples/pjm_2024.py`). The format is validated when read.
2. **Plant and costs.** Put the plant's parameters in `parameters.json` and the TEA inputs in `tea.json`, in the units named by each field. The article lists the parameters it used in its Supplementary Tables 4–5 and its Methods.
3. **Runs.** Run the operation and the case matrix, then the sensitivity studies ([docs/scenarios.md](docs/scenarios.md)). Check the balance residuals (`balance_residuals`) and record the solver and gap, and the settings and seeds of generated series, with the results.

A full-year instance is a mixed-integer program with tens of thousands of binaries. HiGHS solves the synthetic full year in seconds to minutes; harder instances can take much longer. Gurobi is supported, and shorter blocks with annualized costs are a fallback ([docs/architecture.md](docs/architecture.md#problem-size)).

## Market data and PJM

Market data come from each ISO's portal under that ISO's terms of use. This repository includes one set of market data: **the article's 2024 PJM price inputs** in [`data/pjm/`](data/pjm/), unchanged (hourly energy prices, the mean of the LMP series of 21 PJM zones and of the PJM RTO, and the hourly clearing prices of PJM's regulation, primary, synchronized and thirty-minute reserves).

```python
from nuclear_h2 import read_pjm_2024
prices = read_pjm_2024("data/pjm")       # checks the files' SHA-256; prices.energy_price, prices.as_price
```

- **Source and terms.** The files are data of PJM Interconnection, L.L.C., published through PJM Data Miner 2. They are not covered by this repository's MIT or CC BY licences; PJM's terms of use apply, and PJM's Data Miner terms restrict redistribution by non-members. PJM provides its data as is, without warranty, and the files are provided here in the same way. Read [data/pjm/README.md](data/pjm/README.md) before reusing them.
- **What to know.** Hours are labelled in Eastern Prevailing Time on a calendar of 365 days of 24 hours; the values of one hour (a daylight-saving clock hour, in both files) and of one day of ancillary-service prices (a day without reserve-market records) are not PJM records; the loader flags them and can interpolate them. Details, provenance and checksums: [data/pjm/README.md](data/pjm/README.md).
- **Other years, locations or markets.** Prepare your own inputs with the steps in [docs/data_pipeline.md](docs/data_pipeline.md#pjm-market-data), under each data provider's terms. Keep files whose terms do not allow redistribution, and outputs that contain their prices, private.

## What this repository does not contain

This repository presents the modelling framework. Apart from the PJM price inputs in `data/pjm/`, it does not contain the code, the input data or the results used for the article. For the article's results, see the article.

## Citation

If you use this framework, please cite the article above. Citation metadata for both the article and this software are in [`CITATION.cff`](CITATION.cff). If you use the PJM data in `data/pjm/`, also cite PJM as their source ([data/pjm/README.md](data/pjm/README.md#how-to-cite)).

Changes between versions: [CHANGELOG.md](CHANGELOG.md).

## Licence

- Code: [MIT](LICENSE).
- Documentation (`README.md`, `docs/`, `data/pjm/README.md`): [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- PJM data (`data/pjm/energy_price_2024.csv`, `data/pjm/as_price_2024.csv`): not licensed by this repository; PJM's terms of use apply ([data/pjm/README.md](data/pjm/README.md#terms-of-use-and-notices)).
