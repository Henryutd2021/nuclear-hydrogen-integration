"""One-at-a-time studies: energy-price scenarios, the electrolyzer capacity limit, and a TEA-only sweep of the
reactor's fixed O&M (invented data).

Run:  python examples/sensitivity.py
"""
import pandas as pd

from nuclear_h2 import (case_inputs, electrolyzer_limit_sweep, flexibility_metrics, price_scenarios,
                        set_fixed_cost, tea_sweep)
from nuclear_h2.synthetic import illustrative_plant, illustrative_tea_inputs, synthetic_series

pd.set_option("display.width", 140)
plant = illustrative_plant(battery=False, as_mode="bid")
ts = synthetic_series(hours=168, services=plant.market.services)

rows = []
for f, op in price_scenarios(plant, ts, factors=(0.85, 1.0, 1.15, 1.25)).items():
    a = op.annual_totals()
    rows.append({"price factor": f, "profit M$/yr": a["profit"] / 1e6, "H2 kt/yr": a["h2_kg"] / 1e6,
                 "electrolyzer MW": op.capacities["electrolyzer_mw"], "energy share": a["energy_revenue"] / a["revenue"]})
print("Energy-price scenarios:\n", pd.DataFrame(rows).round(3).to_string(index=False))

rows = []
for lim, op in electrolyzer_limit_sweep(plant, ts, limits_mw=(40.0, 80.0, 120.0)).items():
    f = flexibility_metrics(op)
    rows.append({"limit MW": lim, "sized MW": op.capacities["electrolyzer_mw"], "profit M$/yr": op.annual("profit") / 1e6,
                 "grid range MW": f["grid_output_range_mw"], "AS revenue M$/yr":
                 (op.annual("as_revenue_turbine") + op.annual("as_revenue_electrolyzer")) / 1e6})
print("\nElectrolyzer capacity limit:\n", pd.DataFrame(rows).round(3).to_string(index=False))

op = price_scenarios(plant, ts, factors=(1.0,))[1.0]
base = case_inputs(illustrative_tea_inputs(), "retrofit")
print("\nReactor fixed O&M ($/MW-yr), retrofit case:")
print(tea_sweep(op, base, set_fixed_cost("nuclear_fixed_om"), (105_000.0, 140_000.0, 175_000.0))
      [["npv_musd", "irr", "lcoe_usd_per_mwh", "lcoh_usd_per_kg"]].round(3).to_string())
