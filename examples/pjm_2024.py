"""The 2024 PJM prices used in the article (data/pjm/): a summary, then one week of the illustrative plant on them.

The prices are PJM data (terms in data/pjm/README.md). The plant, the winning rates and the deployment factors are
this package's invented illustrative values, so the operating results say nothing about any real plant.

Run:  python examples/pjm_2024.py
"""
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from nuclear_h2 import baseline_operation, iso_services, optimize, revenue_table
from nuclear_h2.pjm import read_pjm_2024
from nuclear_h2.synthetic import illustrative_plant, regulation_deployment, reserve_deployment

pd.set_option("display.width", 120)

prices = read_pjm_2024(Path(__file__).resolve().parents[1] / "data" / "pjm")   # checks both files' SHA-256
flags = prices.not_observed
print(f"PJM 2024: {len(prices.index)} hours, {prices.index[0]} to {prices.index[-1]} (Eastern prevailing time)")
print(f"energy price ($/MWh): mean {prices.energy_price.mean():.2f}, min {prices.energy_price.min():.2f}, "
      f"max {prices.energy_price.max():.2f}")
print("ancillary-service clearing prices ($/MW per hour):")
print(prices.as_price.describe().loc[["mean", "min", "max"]].round(2).to_string())
print("values that are not PJM records (see data/pjm/README.md):", flags.sum().to_dict())

# one summer week; winning rates and deployment factors are not part of the release, so they are generated here
services = iso_services("PJM")
start, stop = "2024-07-15 00:00", "2024-07-22 00:00"
hours = int(prices.index.get_loc(pd.Timestamp(stop)) - prices.index.get_loc(pd.Timestamp(start)))
rng = np.random.default_rng(2027)
products = list(dict.fromkeys(s.column for s in services))
winning = pd.DataFrame({p: rng.uniform(0.35, 0.85, hours) for p in products})
up, down = regulation_deployment(hours, rng)
deployment = pd.DataFrame({s.name: up if s.name == "Reg_up" else down if s.name == "Reg_down" else
                           reserve_deployment(hours, rng) for s in services})
ts = prices.interpolated().timeseries(winning, deployment, services, start=start, stop=stop)

plant = illustrative_plant(battery=True)
plant = replace(plant, market=replace(plant.market, services=services))
ops = [baseline_operation(plant, ts), optimize(plant, ts, label="integrated")]
print(f"\nIllustrative plant, {start} to {stop} (million $/yr, annualized from one week):")
print(revenue_table(ops).loc[["energy_revenue", "h2_sales_revenue", "as_revenue_turbine",
                              "as_revenue_electrolyzer", "as_revenue_battery", "profit"]].round(2).to_string())
