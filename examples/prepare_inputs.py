"""From raw market records to an input directory (docs/data_pipeline.md).

The raw records below are invented stand-ins in the shape of typical ISO downloads: 5-minute energy prices and
long-format ancillary-service clearing prices. With real data, read the files you downloaded under the market's
terms of use instead (PJM data: see "Market data and PJM" in the README).

Run:  python examples/prepare_inputs.py OUT_DIR
"""
import sys

import numpy as np
import pandas as pd

from nuclear_h2 import TimeSeries, fill_gaps, iso_services, long_to_wide, read_inputs, to_hourly, write_inputs
from nuclear_h2.synthetic import illustrative_plant, regulation_deployment, reserve_deployment

out = sys.argv[1] if len(sys.argv) > 1 else "prepared_inputs"
rng = np.random.default_rng(8)
start, hours = pd.Timestamp("2031-04-06 00:00"), 72

# 1. energy price: 5-minute records with a gap -> hourly means -> linear gap filling
idx5 = pd.date_range(start, periods=hours * 12, freq="5min")
raw = pd.Series(32 + 10 * np.sin(np.arange(idx5.size) / 140) + rng.normal(0, 3, idx5.size), index=idx5)
raw.iloc[300:330] = np.nan
price = fill_gaps(to_hourly(raw))

# 2. ancillary-service clearing prices: long records (one row per hour and product) -> one column per product
services = iso_services("SPP")
products = sorted({s.column for s in services})
hidx = pd.date_range(start, periods=hours, freq="1h")
records = pd.DataFrame([(t, p, max(0.0, rng.normal(6, 2))) for t in hidx for p in products],
                       columns=["interval_start", "product", "clearing_price"])
as_price = long_to_wide(records, "interval_start", "product", "clearing_price").reindex(hidx)
as_price = as_price.apply(fill_gaps).reset_index(drop=True)

# 3. winning rates and deployment factors: no public hourly records; generate them (docs/data_pipeline.md)
winning = pd.DataFrame({p: rng.uniform(0.4, 0.9, hours) for p in products})
up, down = regulation_deployment(hours, rng)
deployment = pd.DataFrame({s.name: (up if s.name == "RegU" else down if s.name == "RegD" else
                                    reserve_deployment(hours, rng)) for s in services})

ts = TimeSeries(energy_price=price.to_numpy(), as_price=as_price, winning_rate=winning, deployment=deployment,
                index=hidx)
plant = illustrative_plant(battery=False)
plant.market = type(plant.market)(**{**plant.market.__dict__, "services": services})
write_inputs(out, plant, ts)
plant2, ts2 = read_inputs(out)                                    # validated on reading
print(f"{ts2.hours} hours, products {products}, written to {out}")
print("next: nuclear-h2 run", out, "--out-dir outputs/prepared")
