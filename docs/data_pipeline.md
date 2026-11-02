# Data pipeline

This page describes how to build the framework's inputs from public sources, the preparation rules, the input
file schemas and the terms of use. The repository itself ships no market, plant or cost data: the examples and
tests use invented series from `nuclear_h2.synthetic`.

## Contents

1. [Inputs and their sources](#inputs-and-their-sources)
2. [Market data by ISO](#market-data-by-iso)
3. [PJM market data](#pjm-market-data)
4. [Preparation rules](#preparation-rules)
5. [Generated ancillary-service series](#generated-ancillary-service-series)
6. [Input directory schema](#input-directory-schema)
7. [Source records](#source-records)
8. [Terms of use](#terms-of-use)

## Inputs and their sources

| Input | Symbol(s) | Public source type | Preparation |
|---|---|---|---|
| hourly electricity price | $`\lambda^{E}_t`$ | the ISO's locational marginal prices (LMPs) at a hub or the plant's node, from the ISO's market data portal | sub-hourly values to hourly means; one calendar year of 8760 hours ([rules](#preparation-rules)) |
| AS capacity prices | $`\lambda^{AS,cap}_{t,s}`$ | the ISO's hourly ancillary-service clearing prices | long records to one column per product (`long_to_wide`), same hourly grid |
| AS winning rates, deployment factors, regulation mileage and performance | $`w_{t,s}`$, $`\delta_{t,s}`$, $`M_{t,s}`$, $`P_{t,s}`$ | generated series informed by ISO publications ([below](#generated-ancillary-service-series)) | generators in `nuclear_h2.synthetic` with documented settings |
| regulation mileage price (optional) | $`\lambda^{mil}_{t,s}`$ | the ISO's regulation market results, where a mileage payment exists | hourly, per product |
| reactor thermal power, turbine rating, remaining life | $`Q^{steam,total}`$, $`P^{turb}_{max}`$ | U.S. NRC reactor data (licensed thermal power, license expiration); U.S. EIA Form 860 (generator nameplate, balancing authority) | turbine curve: breakpoints of power against steam ([below](#plant-parameters)) |
| electrolyzer performance and cost | $`k_{e,j}`$, $`k_{t,j}`$, $`P^{elec,bp}_j`$, costs | public techno-economic literature on high-temperature (solid oxide) electrolysis; the article's Supplementary References list the sources it used | specific electricity and heat use at each load point; annualize capital with Eq. (55) |
| battery and hydrogen storage | efficiencies, costs | Annual Technology Baseline (ATB) of the National Laboratory of the Rockies (NLR, formerly NREL) for batteries; public hydrogen-storage cost literature | annualize with Eq. (55); fixed O&M per MW-yr |
| hydrogen price | $`\lambda^{H_2}`$ | a scenario value chosen by the user | constant or per scenario |
| federal credits | Eqs. (50)–(51), credit rates and periods | the Internal Revenue Code (Sections 45U, 45Y, 48E, 45V) and IRS and Treasury guidance | enter as parameters of `Credits` |
| depreciation | MACRS classes | IRS Publication 946 | recovery class per capital item |
| plant and project costs | Eq. (49) references, fixed O&M, fuel | public cost studies (for example the NLR ATB for nuclear) | reference cost, reference size and scaling exponent per item |

### Plant parameters

- **Turbine curve.** Breakpoints $`(Q^{turb,bp}_k, P^{turb,bp}_k)`$ from the turbine's rating and its conversion efficiency at part load. With only a rated efficiency, two breakpoints (a straight line through the minimum and the rated point) give a constant efficiency. The reactor's licensed thermal power gives $`Q^{steam,total}`$; the remaining life (license expiration minus the analysis year) gives the lifetime of Cases 1–2.
- **Electrolyzer curve.** Power breakpoints from the minimum load to the largest capacity considered, with the specific electricity use $`k_{e,j}`$ (kWh/kg) and heat use $`k_{t,j}`$ (kWh_th/kg) at each. Production at a breakpoint is $`1000\,P_j/k_{e,j}`$ kg/h ($`P_j`$ in MW); steam is production times $`k_{t,j}/1000`$ MW_th.
- **Ramp limits** quoted in percent per minute convert to the fraction per hour used here by multiplying by 60 and dividing by 100 (capped at 1 when the unit can traverse its range within the hour).

## Market data by ISO

The article covers the seven U.S. ISO/RTO markets. Their ancillary-service products, as named in the article's Supplementary Table 6, are available as service sets:

```python
from nuclear_h2 import iso_services
services = iso_services("ERCOT")   # SPP, CAISO, ERCOT, PJM, NYISO, ISONE, MISO
```

| ISO | Energy prices and AS clearing prices (official source) | Products in `iso_services` |
|---|---|---|
| SPP | SPP Marketplace portal | RegU, RegD, RamU, RamD, Spin, Sup, UncU |
| CAISO | CAISO OASIS | RegU, RegD, Spin, NSpin, RMU, RMD |
| ERCOT | ERCOT Market Information System (public reports) | RegU, RegD, Spin (Responsive Reserve), NSpin, ECRS |
| PJM | PJM Data Miner 2 (see [PJM market data](#pjm-market-data)) | Rse, Reg (symmetric), Syn, TMR |
| NYISO | NYISO market data (custom reports) | Spin10, NSpin10, Res30, RegC (symmetric) |
| ISO-NE | ISO New England ISO Express | Spin10, NSpin10, OR30 |
| MISO | MISO market reports | RamU, RamD, Spin, STR, Sup, Reg (symmetric) |

A symmetric regulation product becomes an up and a down service on the same product (formulation, convention 9). Market rules and product definitions change over time; check the products against the ISO's current manuals for the year you study. Third-party aggregators also redistribute ISO data; their own terms then apply in addition to the ISO's.

## PJM market data

**This repository contains no PJM data.** PJM's energy and ancillary-service market data are published through PJM Data Miner 2. PJM's Data Miner terms (Data Miner 2 page on pjm.com, checked on 2 November 2026) state that information and data contained in Data Miner are for internal use only and that redistribution of information or data contained in or derived from Data Miner is prohibited without an active PJM membership (a minimum of Associate Membership). PJM market inputs therefore need authorization from PJM under those terms; they are not redistributed here, and the repository contains no files derived from PJM data and no code that downloads them.

A user who has access to Data Miner 2 under PJM's terms can prepare PJM inputs as follows:

1. **Access.** Use your own PJM account for Data Miner 2 (interactive downloads or its application programming interface). Keep any key or credential outside the repository, for example in an environment variable read by your own download script; never commit it.
2. **Download**, for the study year and in the market's local time:
   - hourly locational marginal prices at the chosen hub or the plant's pricing node (day-ahead, or real-time aggregated to hourly);
   - hourly ancillary-service market results with the clearing prices of the products in `iso_services("PJM")`: regulation, primary reserve, synchronized reserve and thirty-minute reserve;
   - if the regulation mileage payment is used, the regulation mileage data for the same hours.
3. **Convert** with the helpers of `nuclear_h2.timeseries`: `to_hourly` for sub-hourly records, `hourly_year` for one calendar year of 8760 hours (29 February dropped, gaps filled linearly), and `long_to_wide` with a mapping from PJM's product labels to the service product names (`Reg`, `Rse`, `Syn`, `TMR`).
4. **Write** an input directory with `write_inputs` (or the CSV files of the [schema](#input-directory-schema)) in a location outside this repository, or in a folder listed in `.gitignore`.
5. **Keep it internal.** Do not publish the prepared files, and check PJM's terms (or ask PJM) before sharing any file that contains or is derived from Data Miner data. This includes the framework's own outputs: `hourly.csv` carries the `energy_price` column.

The same steps apply to any market whose terms restrict redistribution.

## Preparation rules

These follow the article's description of its time series ("Time-series data generation and collection").

1. **One year, 8760 hourly values** per series. Use the market's local time with hour-beginning labels; `hourly_year(series, year, tz=...)` reindexes to every hour of the year.
2. **Sub-hourly prices** (5- or 15-minute) are aggregated to hourly averages (`to_hourly`, mean by default).
3. **29 February** is removed so that every year has 365 days (`drop_leap_day`).
4. **Missing values** are filled by linear interpolation in time (`fill_gaps`); leading and trailing gaps take the nearest value. Record how many values were filled.
5. **Negative and very high prices** are kept as published.
6. **Units:** energy prices in \$/MWh; capacity prices in \$/MW per hour; winning rates and deployment factors as fractions in [0, 1]; mileage as a multiplier; mileage prices in \$/MW of mileage.
7. **Product names** in `as_price.csv`, `winning_rate.csv`, `mileage*.csv` and `performance.csv` are the service products (`Service.column`); deployment factors are per service (`Service.name`), so a symmetric regulation product has two deployment columns (up and down).
8. **Price scenarios** scale the energy price by a factor (`scale_prices`), and the capacity prices too when `include_as=True`.

## Generated ancillary-service series

Hourly deployment records for all services are not publicly available, so the article generates deployment-related inputs (deployment factors, regulation mileage and performance factors) from public ISO documents, historical operation data and the literature. `nuclear_h2.synthetic` provides generators of the same forms:

| Series | Generator | Settings |
|---|---|---|
| regulation up/down deployment | `regulation_deployment`: smoothed random total with intra-day cycles and a weekly pattern, split between up and down around a target share, capped in sum | mean total, up share, cycles per day, weekly amplitude, noise, smoothing, cap |
| reserve deployment | `reserve_deployment`: Bernoulli event per hour with a uniform magnitude | event probability, magnitude range |
| regulation mileage factor | `mileage_factors`: base value with Gaussian noise, clipped | base, noise fraction, clip range |
| regulation performance factor | `performance_factors`: truncated normal | mean, standard deviation, bounds |
| AS capacity price (when no market record is used) | `as_capacity_prices`: Eq. (56) | `AsPriceSettings` |

Regulation-up and regulation-down services that form a pair (`balance_with` or `pair_with`) are generated together, and products linked by such pairs share one mileage, performance and mileage-price profile. The defaults are this package's own round numbers. For a study, calibrate the settings to the ISO's published statistics (for example, regulation mileage ratios and reserve deployment frequencies in ISO reports) and record the settings and the random seed with the inputs. Winning rates (the cleared share of a bid) can be set from market clearing data where available, or treated as a scenario.

## Input directory schema

Written by `write_inputs` and `nuclear-h2 synthetic` (to a new or empty directory, written in full or not at all; nothing is overwritten), read and validated by `read_inputs`:

| File | Content |
|---|---|
| `parameters.json` | `turbine`, `electrolyzer`, `battery`, `storage` (each optional except the turbine) and `market` with its `services`; field names and units as in `nuclear_h2.parameters` ("inf" for unbounded grid limits) |
| `energy_price.csv` | `energy_price` (\$/MWh); optional `timestamp` (written in UTC when the series has a time zone) |
| `as_price.csv` | one column per product (\$/MW per hour) |
| `winning_rate.csv` | one column per product, in [0, 1] |
| `deployment.csv` | one column per service, in [0, 1] |
| `mileage.csv`, `performance.csv`, `mileage_price.csv` | optional; one column per product |
| `series.json` | `dt_h` (hours per period, default 1); for a time-zone-aware index its IANA name (`timezone`) or fixed offset (`utc_offset_minutes`), restored on reading (other zones are refused) |
| `tea.json` | optional: `inputs` (TEA inputs: capital items, replacements, fixed costs, credits) and `design` (lifetimes and construction periods of the case studies) |

Without ancillary services, the AS tables are omitted. All series must have the same number of rows. Reading checks finite values, ranges in [0, 1], non-negative capacity prices and the presence of every column the services need.

## Source records

Keep, next to each input directory (and outside any public repository if the data may not be redistributed), a short record per file: source organization and dataset, URL, retrieval date, data version or market year, node or hub, processing steps (aggregation, filled values, dropped hours), the generator settings and seeds, and the terms of use.

## Terms of use

- **ISO and RTO market data:** each ISO's website terms apply; several restrict redistribution. PJM: see [above](#pjm-market-data).
- **U.S. NRC and U.S. EIA data:** U.S. government works; cite the agency and the dataset.
- **NLR Annual Technology Baseline:** openly licensed; cite the ATB edition used.
- **Cost literature and reports:** cite them; do not redistribute their files.
- **Statutes and IRS publications:** cite the section and the publication year.
