"""Ancillary-service participation: no services, bids only, and executed deployment (Eqs. 4, 41-46).

Run:  python examples/ancillary_services.py
"""
import pandas as pd

from nuclear_h2 import as_revenue_by_service, optimize, revenue_table
from nuclear_h2.synthetic import illustrative_plant, synthetic_series

pd.set_option("display.width", 120)

ops = {}
for mode in ("off", "bid", "dispatch"):
    plant = illustrative_plant(battery=True, as_mode=mode)
    ts = synthetic_series(hours=168, services=plant.market.services)
    ops[mode] = optimize(plant, ts, label=mode)

print("Million $/yr by mode:")
print(revenue_table(ops.values()).loc[["energy_revenue", "h2_sales_revenue", "as_revenue_turbine",
                                       "as_revenue_electrolyzer", "as_revenue_battery", "profit"]].round(2).to_string())

op = ops["dispatch"]
print("\nDispatch mode, revenue by component and service ($ over the week):")
print(as_revenue_by_service(op.plant, op.ts, op.hourly).round(1).to_string(index=False))

h = op.hourly
balanced = all(((h[f"bid_{c}_RegUp"] - h[f"bid_{c}_RegDown"]).abs() < 1e-9).all()
               for c in ("turbine", "electrolyzer", "battery"))
print("\nRegulation bids are balanced (Eq. 44):", balanced)
print("Electrolyzer actual = set point - up + down deployment (Eq. 46), max deviation:",
      f"{(h['p_elec'] - (h['p_elec_setpoint'] - h['deployed_up_electrolyzer'] + h['deployed_down_electrolyzer'])).abs().max():.1e}")
