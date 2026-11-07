# PJM market prices, 2024

Two hourly price series of the PJM market for the calendar year 2024. They are the PJM price inputs of the model runs in the article below, unchanged (the same bytes; checksums in [Files](#files)):

> Honglin Li, Jianqiao Huang, Bikash Poudel, Mucun Sun, Tyler Westover, Daniel Wendt and Jie Zhang. Rethinking the economics and flexibility of U.S. nuclear power through hydrogen integration and policy support. *Nature Communications* 17, 7090 (2026). [doi:10.1038/s41467-026-73630-y](https://doi.org/10.1038/s41467-026-73630-y)

They are provided so that the article's PJM price inputs can be inspected and used with this framework.

**Source: PJM Interconnection, L.L.C.** The files contain market data published by PJM (the ancillary-service prices equal records of PJM's [Data Miner 2](https://dataminer2.pjm.com)) and an average computed from PJM's zonal and RTO prices. PJM® is a registered trademark of PJM Interconnection, L.L.C. PJM has not reviewed or endorsed these files or this repository.

## Contents

1. [Terms of use and notices](#terms-of-use-and-notices)
2. [Files](#files)
3. [Columns and units](#columns-and-units)
4. [Hour labels and time zone](#hour-labels-and-time-zone)
5. [Values that are not PJM records](#values-that-are-not-pjm-records)
6. [Provenance](#provenance)
7. [Use with the framework](#use-with-the-framework)
8. [How to cite](#how-to-cite)

## Terms of use and notices

- **Licence.** The MIT licence of this repository's code and the CC BY 4.0 licence of its documentation do **not** apply to `energy_price_2024.csv` and `as_price_2024.csv`. This repository grants no licence to PJM's data; PJM reserves its rights in the data. This README, `SHA256SUMS` and the loader code are covered by the repository's licences.
- **PJM's terms.** PJM's data are subject to PJM's terms of use, read on 7 November 2026:
  - the [Data Miner 2 page](https://www.pjm.com/markets-and-operations/etools/data-miner-2) states that information and data contained in Data Miner are for internal use only and that redistribution of information or data contained in or derived from Data Miner is prohibited without an active PJM membership (a minimum of Associate Membership);
  - the [Data Miner 2 terms of use](https://www.pjm.com/-/media/DotCom/etools/edatafeed/data-license-agreement-edata-feed-data-miner-2.pdf) (section 1) permit internal business use and commercial purposes, including publishing and making derivatives of the data, and state that members may republish data while non-members are prohibited from republishing it; section 2 forbids removing any proprietary notices included in the data.

  Anyone who reuses or redistributes these files is responsible for complying with PJM's terms. Check them, or ask PJM, before you republish the files or data derived from them.
- **No warranty.** PJM provides its data "as is" and disclaims all warranties, express or implied, including that the data are accurate, complete or error free (Data Miner 2 terms of use, section 8). These files are provided in the same way: as is, without warranty of any kind. Neither PJM nor the author of this repository is liable for any use of them.
- **Grid Status.** The ancillary-service records were retrieved through the Grid Status API ([Provenance](#provenance)). Data provided by [Grid Status](https://www.gridstatus.io/). Grid Status's own terms of use also apply to data obtained from its services.
- **Contact.** A rights holder with a concern about these files can open an issue in this repository.

## Files

| File | Columns | Rows | Bytes | SHA-256 |
|---|---|---|---|---|
| `energy_price_2024.csv` | `Timestamp`, `Price ($/MWh)` | 8,760 | 235,327 | `8f5e12cdc1d3d8dbfdc7ca85ff324164bfdfcef25cdb790b627fcc2e30656c3c` |
| `as_price_2024.csv` | `Timestamp`, `p_RegUp_PJM`, `loc_RegUp_PJM`, `p_RegDown_PJM`, `loc_RegDown_PJM`, `p_Syn_PJM`, `loc_Syn_PJM`, `p_Rse_PJM`, `loc_Rse_PJM`, `p_TMR_PJM`, `loc_TMR_PJM` | 8,760 | 482,759 | `10748408f180e93e5cf78249f9ae47f6e593cc9ae42dcdb234cfd41aa2f7a0d9` |

`SHA256SUMS` lists the same checksums (check with `shasum -a 256 -c SHA256SUMS` in this folder). The file names were chosen for this repository; the contents are byte-identical to the article's input files.

## Columns and units

**`energy_price_2024.csv`**

| Column | Content | Unit |
|---|---|---|
| `Timestamp` | hour label (next section) | – |
| `Price ($/MWh)` | hourly energy price: the arithmetic mean, rounded to three decimals, of 22 hourly locational marginal price (LMP) series of PJM: 21 transmission-zone series and the PJM RTO series (list below). Exception: the flagged hour of 10 March ([below](#values-that-are-not-pjm-records)) | \$/MWh |

The 22 series are the LMPs of the 21 PJM transmission zones Allegheny Power System, American Electric Power, American Transmission Systems, Atlantic Electric, Baltimore Gas and Electric, ComEd, Dayton Power and Light, Delmarva Power and Light, Dominion Energy, Duke Energy Ohio/Kentucky, Duquesne Light, East Kentucky Power Cooperative, Jersey Central Power and Light, Metropolitan Edison, Ohio Valley Electric, PECO Energy, PPL Electric Utilities, Pennsylvania Electric, Potomac Electric Power, Public Service Electric and Gas and Rockland Electric, and the PJM RTO LMP (labelled "PJM Total" in the study's table), each with equal weight. The study's upstream table of these 22 series is not published here; its download and part of its preparation are not recorded.

- **Market (inferred).** The study's files do not record whether the series are day-ahead or real-time prices. They are consistent with PJM's real-time market: hour by hour over 2024, the Dominion, Allegheny Power System and PJM RTO series correlate at 0.94–0.96 with PJM's real-time hourly LMPs at a hub (Dominion Hub for the Dominion series, Western Hub for the other two; Data Miner 2 feed `rt_hrl_lmps`), against 0.64–0.69 for the Dominion and PJM RTO series with PJM's day-ahead LMPs of the same zone (feed `da_hrl_lmps`). The hour-beginning labels are inferred the same way (the correlation drops to 0.55–0.66 when shifted by one hour).
- **Negative prices (inferred).** The 22 upstream series contain no negative values but 546 zero values (in 406 hours). Of the zeros checked against PJM's real-time hub LMPs, all 3 in the Dominion series, all 3 in the PJM RTO series and 3 of the 5 in the Allegheny Power System series fall in hours with negative real-time prices at the hub used for the comparison; the other 535 zeros were not checked. Negative prices may therefore have been set to zero in the upstream table before averaging. The energy price has no negative values; its minimum, 0, occurs in two hours (16 June 2024, 05:00 and 06:00).

**`as_price_2024.csv`**

| Column | PJM product | Content | Unit |
|---|---|---|---|
| `Timestamp` | – | hour label (next section) | – |
| `p_RegUp_PJM`, `p_RegDown_PJM` | Regulation | regulation market clearing price; the same value in both columns (PJM has one regulation product, offered in both directions), except in the flagged hour of 10 March | \$/MW per hour |
| `p_Syn_PJM` | Synchronized Reserve | market clearing price | \$/MW per hour |
| `p_Rse_PJM` | Primary Reserve | market clearing price | \$/MW per hour |
| `p_TMR_PJM` | Thirty Minutes Reserve | market clearing price | \$/MW per hour |
| `loc_*` | – | all zero; columns of the study's input format, not PJM data and not used | – |

Each hourly value is the market clearing price (field `mcp`) of the first five-minute interval of the hour (the interval starting at hh:00) in PJM's real-time Reserve Market Results for the PJM RTO reserve zone (Data Miner 2 feed `reserve_market_results`, locale `PJM_RTO`). PJM's regulation market clearing price combines the capability and the performance clearing prices; if you add a separate regulation mileage payment in the framework, split the price first so the performance part is not paid twice.

In the framework, the products are `Reg`, `Syn`, `Rse` and `TMR` of `iso_services("PJM")`; regulation becomes the services `Reg_up` and `Reg_down` on the product `Reg` (half the payment each, equal bids).

## Hour labels and time zone

- **Labels.** `Timestamp` (`YYYY-MM-DD HH:MM:SS`) labels each hour by its start, in Eastern Prevailing Time (EPT: UTC−5 in standard time, UTC−4 in daylight saving time).
- **Calendar.** 365 days of 24 labels, 8,760 rows. 29 February 2024 is removed, following the article's convention that every year has 8,760 hours.
- **Daylight saving time.** The labels are clock labels, not a run of 8,760 consecutive hours:
  - on 10 March 2024 the clock skips from 02:00 to 03:00 EPT, but the files carry a label `2024-03-10 02:00:00` (its values are not PJM records, see the next section);
  - on 3 November 2024 the hour 01:00 occurs twice; the files carry one `2024-11-03 01:00:00` label, which holds the second occurrence (01:00 EST, UTC−5). The first occurrence (01:00 EDT) is not in the files.
- To place a label in UTC, add 5 hours in standard time and 4 hours in daylight saving time; the label of 10 March 02:00 has no UTC equivalent.

## Values that are not PJM records

| Hour labels | File and columns | Values | Origin |
|---|---|---|---|
| `2024-03-10 02:00:00` (1 hour) | both files, all price columns | energy price 102.885…; RegUp 19.012, RegDown 24.3756, Syn 23.7547, Rse 17.7386, TMR 25.1539 | this clock hour does not exist in EPT; the values were carried over from an earlier version of the study's input files and are not PJM data |
| `2024-12-12 00:00:00` to `2024-12-12 23:00:00` (24 hours) | `as_price_2024.csv`, all price columns | Regulation 29.8976, Syn 2.7948, Rse 2.4913, TMR 0 in every hour (rounded here; the file holds more digits) | PJM's 2024 Reserve Market Results downloads used by the study have no records for 12 December 2024; each product was filled with its 2024 mean over all five-minute intervals |

All other ancillary-service values equal PJM's Reserve Market Results records exactly ([Provenance](#provenance)). All other energy prices are the equal-weight average of the 22 upstream series described above. The article's runs used the values as they are in the files. `read_pjm_2024` flags the values above in `PjmPrices.not_observed`, and `PjmPrices.interpolated()` replaces them by linear interpolation between the neighbouring PJM records.

## Provenance

- **Energy prices.** The study prepared the 22 series as one table of hourly values for 2024 (file dated 19 May 2025); its files do not record the download tool or the access date. The hourly mean of the 22 series, rounded to three decimals and matched on the month, day and hour of each label, gives `Price ($/MWh)`.
- **Ancillary-service prices.** The study retrieved PJM's real-time ancillary-service market results for 2024 through the Grid Status API (dataset `pjm_as_market_results_real_time`, locale "PJM RTO Reserve Zone"; file dated 28 April 2025) and took the first five-minute interval of each hour. Checked on 7 November 2026: for all four products, the values of the 8,735 hours not listed in the previous section equal exactly the first-interval clearing prices in an archived PJM Data Miner 2 download of the Reserve Market Results for 2024 (file dated 20 May 2025).
- **Files.** Both files were last changed on 20 May 2025, before the article's model runs, and are published here without change.

## Use with the framework

```python
from nuclear_h2 import iso_services, read_pjm_2024

prices = read_pjm_2024("data/pjm")               # checks both SHA-256 checksums
prices.energy_price, prices.as_price             # $/MWh; $/MW per hour (Reg, Rse, Syn, TMR)
prices.not_observed                              # True where a value is not a PJM record
ts = prices.interpolated().timeseries(           # a TimeSeries for the model
    winning_rate=0.6, deployment=0.1,            # not part of this release: your own assumptions or series
    services=iso_services("PJM"), start="2024-07-15 00:00", stop="2024-07-22 00:00")
```

The CSV files are not part of the installed Python package. `read_pjm_2024()` without a path looks for `data/pjm` below the current directory and next to the source tree (an editable install); otherwise pass the path of the `data/pjm` folder of a clone of this repository. The loader never downloads data.

`examples/pjm_2024.py` summarizes the series and runs one week of the illustrative plant on them. For other years or other PJM locations, prepare the inputs yourself from Data Miner 2 under PJM's terms ([docs/data_pipeline.md](../../docs/data_pipeline.md#pjm-market-data)).

## How to cite

Cite the article above for these files, and PJM as the source of the data, for example: PJM Interconnection, L.L.C., *Data Miner 2*: zonal locational marginal prices and Reserve Market Results, 2024, https://dataminer2.pjm.com.
