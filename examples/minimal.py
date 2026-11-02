"""Business as usual vs the integrated system on two synthetic weeks (invented data; HiGHS; a few seconds).

Run:  python examples/minimal.py
"""
import pandas as pd

from nuclear_h2 import baseline_operation, balance_residuals, flexibility_metrics, optimize, revenue_table
from nuclear_h2.synthetic import illustrative_plant, synthetic_series

pd.set_option("display.width", 120)

plant = illustrative_plant(battery=True, storage=False, as_mode="bid")    # sized electrolyzer and battery
ts = synthetic_series(hours=336, services=plant.market.services)            # two synthetic summer weeks

bau = baseline_operation(plant, ts)                                         # all steam to the turbine, all power sold
ies = optimize(plant, ts, label="integrated")                               # Eqs. (1)-(46), HiGHS

print("Revenues and costs, million $/yr (annualized from the two weeks):")
print(revenue_table([bau, ies]).round(2).to_string())
print("\nSized capacities:", {k: round(v, 1) for k, v in ies.capacities.items()})
print("\nFlexibility (integrated system):")
for k, v in flexibility_metrics(ies).items():
    print(f"  {k:34s} {v:10.3f}")
print("\nLargest balance residual:", f"{max(balance_residuals(ies).values()):.2e}")
print("Solver:", {k: ies.solver[k] for k in ("name", "version", "termination", "mip_gap")})
