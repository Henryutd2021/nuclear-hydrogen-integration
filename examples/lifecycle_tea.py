"""Lifecycle TEA of the four case studies with their policy variants (invented costs and credits).

Run:  python examples/lifecycle_tea.py
"""
import pandas as pd

from nuclear_h2 import case_matrix, operations
from nuclear_h2.synthetic import (illustrative_design, illustrative_plant, illustrative_tea_inputs,
                                  synthetic_series)

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 12)

plant = illustrative_plant(battery=True, storage=True, as_mode="bid")
ts = synthetic_series(hours=168, services=plant.market.services)   # one synthetic week

# business as usual (closed form), the integrated system (MILP) and, for the years after the hydrogen credit,
# the integrated system re-optimized without the credit
ops = operations(plant, ts, reoptimize_after_h2_credit=True)
table, results = case_matrix(ops, illustrative_tea_inputs(), illustrative_design())
print(table.round(3).to_string())

cf = results["Case 2 retrofit, 45U"].cash_flows
print("\nCase 2 with 45U, first operating years (million $):")
cols = ["calendar_year", "capex", "revenue", "opex", "depreciation", "credit_45u", "credit_h2", "cash_flow"]
print((cf[cols].set_index("calendar_year") / 1e6).head(11).round(2).to_string())
