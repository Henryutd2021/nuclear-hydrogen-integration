# Changelog

## 1.1.0

- **PJM data.** Added `data/pjm/`: the article's 2024 PJM price inputs, unchanged (`energy_price_2024.csv`: hourly energy prices, the mean of the LMP series of 21 PJM zones and of the PJM RTO; `as_price_2024.csv`: hourly clearing prices of PJM's regulation, primary reserve, synchronized reserve and thirty-minute reserve), with a README on source, units, hour labels, values that are not PJM records, provenance, checksums and PJM's terms. The files are data of PJM Interconnection, L.L.C. and are not covered by the repository's licences.
- **Loader.** New module `nuclear_h2.pjm`: `read_pjm_2024` (SHA-256 and format checks) returns `PjmPrices` with the flags of values that are not PJM records, `interpolated()` and `timeseries(...)` for `iso_services("PJM")`.
- **Example.** `examples/pjm_2024.py`: a summary of the PJM series and one week of the illustrative plant on them.
- **Tests.** 27 new tests (181 in total).
- **Documentation.** README, `docs/data_pipeline.md` (PJM market data) and `docs/architecture.md` describe the included PJM data; the earlier note that the repository contains no PJM data no longer applies. Version 1.1.0 in `pyproject.toml`, `CITATION.cff` and `nuclear_h2.__version__`.

## 1.0.0

- First release: formulation (Eqs. 1-56 of the article), data pipeline, study design and architecture documentation; linopy implementation (HiGHS by default, Gurobi optional) of the hourly operation and sizing model with ancillary services; lifecycle TEA with MACRS, 45U, 45Y, 48E and a hydrogen production credit; ancillary-service product sets of the seven U.S. ISO/RTO markets; synthetic data generators, examples and tests.
