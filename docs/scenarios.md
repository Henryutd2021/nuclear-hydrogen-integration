# Study design

This page describes the article's study design and how to express each part with the framework. It does not
repeat the article's input values or results; for those, see the article and its Supplementary Information.

## Contents

1. [Fleet analysis](#fleet-analysis)
2. [Case studies and policy variants](#case-studies-and-policy-variants)
3. [Sensitivity analyses](#sensitivity-analyses)
4. [Outputs and indicators](#outputs-and-indicators)
5. [Reading the results](#reading-the-results)

## Fleet analysis

The article analyzes operating U.S. reactors with a minimum remaining operating life in the seven ISO/RTO markets. For each reactor:

- nuclear parameters (thermal power, turbine rating, conversion) are plant-specific, from the U.S. NRC;
- the electricity and ancillary-service prices are those of the reactor's market for one historical year (hub or node prices), with the ancillary-service products of that market (Supplementary Table 6);
- the integrated system (HTE, hydrogen storage, battery) is sized and dispatched hourly by the model of Eqs. (1)–(46), with the HTE capacity limited by an upper bound;
- the optimized year feeds the lifecycle TEA of Eqs. (47)–(55).

With the framework, one reactor is one plant and one input directory:

```python
from nuclear_h2 import case_matrix, iso_services, operations, read_inputs, read_tea

for directory in reactor_directories:                 # one prepared input directory per reactor
    plant, ts = read_inputs(directory)                # plant-specific parameters; the market's series and services
    tea, design = read_tea(directory)                 # costs, credits; remaining life of this reactor
    ops = operations(plant, ts)                       # business as usual (closed form) + integrated system (MILP)
    table, results = case_matrix(ops, tea, design)    # Cases 1-4 with their policy variants
```

`iso_services(iso)` gives the product set of each market; the [data pipeline](data_pipeline.md) describes how to prepare the series.

## Case studies and policy variants

| Case | System | Capital | Life | Policy variants | `case_matrix` label |
|---|---|---|---|---|---|
| 1 | existing reactor, business as usual: all power sold at hourly prices, no hydrogen | nuclear capital sunk | remaining life | without and with 45U | `Case 1 existing, ...` |
| 2 | existing reactor retrofitted with HTE, hydrogen storage and battery; sells electricity, hydrogen and ancillary services | added systems only (nuclear sunk) | remaining life | without and with 45U | `Case 2 retrofit, ...` |
| 3 | new integrated nuclear-hydrogen-battery system | all capital, including the reactor | a standard design life | no 45Y/48E, 45Y, 48E (each with the hydrogen credit if configured) | `Case 3 greenfield ...` |
| 4 | as Case 3 | as Case 3 | an extended life | as Case 3 | `Case 4 greenfield ...` |

Credits:

- **45U** (existing reactors): hourly, price-dependent, Eq. (50), for the calendar years of the credit.
- **45Y or 48E** (new plants, alternatives): a production credit per MWh for the first years of operation, or an investment credit on the nuclear generation equipment with the basis reduction of Eq. (51).
- **Hydrogen production credit**, modeled after Section 45V, per kilogram for the first years of operation. The article assumes it applies uniformly across all scenarios, so every case with hydrogen production includes it, among them the Case 2 variants without 45U. In the framework it is `Credits.hydrogen` in the TEA, and $`\lambda^{H_2,sub}`$ (`Market.h2_credit_usd_per_kg`) in the operation model; `case_inputs(..., hydrogen_credit=False)` removes it from the TEA for a comparison.
- Hydrogen-related and nuclear-related credits can be combined for an integrated plant.

Variants of a case use the same operation and the same inputs and differ only in their credits. The case inputs come from one common set (`TEAInputs`) through `case_inputs`: Case 1 builds nothing; Case 2 builds the items marked for `retrofit`; Cases 3–4 build the items marked for `greenfield` (including the reactor). Fixed O&M is charged in the cases each `FixedCost` lists, also for assets whose capital is sunk. Lifetimes and construction periods come from `StudyDesign`.

After the hydrogen credit ends, every operating year repeats the optimized operation by default. `operations(..., reoptimize_after_h2_credit=True)` adds a second operation of the same built plant, re-dispatched without the credit with the capacities of the first optimization held fixed (`with_capacities`), which `case_matrix` uses for the later years.

## Sensitivity analyses

The article reports three groups:

| Group | What changes | Scope in the article | Framework |
|---|---|---|---|
| 1 | the reactor's fixed O&M cost | a representative reactor with stylized plant parameters in one market; NPV of business as usual and hybrid operation, and LCOE | `tea_sweep(op, inputs, set_fixed_cost("nuclear_fixed_om"), values)` (no re-optimization: fixed O&M does not change the dispatch) |
| 2 | the upper bound on HTE capacity | the same representative reactor; sizing, hydrogen production and sales, revenue and its composition, AS bids and deployment, grid output range, ramp rates | `electrolyzer_limit_sweep(plant, ts, limits)`; then `revenue_table`, `as_revenue_by_service`, `flexibility_metrics` |
| 3 | the level of electricity prices | all reactors; a set of scaling factors of the baseline year's prices, with more upward than downward cases | `price_scenarios(plant, ts, factors)` per reactor, then the TEA of each operation; `include_as=True` scales the capacity prices as well |

Any other one-at-a-time study follows the same pattern: change a parameter with `dataclasses.replace` and re-optimize, or change a TEA input with a setter and call `tea_sweep`.

## Outputs and indicators

| Output | Function |
|---|---|
| hourly dispatch, set points, bids and deployments | `Operation.hourly` |
| sized capacities | `Operation.capacities` |
| revenue and cost by stream (Eqs. 2–6), per year | `revenue_table`, `Operation.annual_totals()` |
| AS revenue by component and service (capacity, energy, mileage parts) | `as_revenue_by_service` |
| flexibility indicators | `flexibility_metrics` (definitions in [formulation.md](formulation.md#flexibility-indicators)) |
| NPV, IRR, payback, LCOE, LCOH, LCOS, cash-flow table | `evaluate`, `case_matrix`, `TEAResult.cash_flows` |
| balance and curve residuals | `balance_residuals` |
| solver name, version, status and MIP gap | `Operation.solver` |

Record, with every result set, the solver and its gap, the generator settings and seeds of the generated AS series, and the source records of the inputs.

## Reading the results

- The plant is a price taker: prices, including hydrogen, are exogenous, and large-scale adoption would change them.
- Capacity-market revenues and accreditation are outside the model.
- Results with hydrogen production depend on the hydrogen price and on the hydrogen production credit assumed; report which credits each result includes.
- Ancillary-service revenue depends on the generated deployment, winning-rate and mileage series and on the market's product rules; report their settings.
- The TEA repeats one optimized year over the project life; price scenarios show how sensitive the conclusions are to that year.
- Levelized costs use separate accounting per subsystem; the electrolyzer's electricity is charged at the reactor's LCOE and its steam at its value as electricity, so the LCOH is not a marginal cost.
