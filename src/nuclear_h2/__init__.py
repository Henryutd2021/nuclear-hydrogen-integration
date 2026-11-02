"""nuclear_h2: hourly operation and sizing of a nuclear plant integrated with electrolysis, hydrogen storage and a
battery in energy and ancillary-service markets, linked to a lifecycle techno-economic analysis.

Framework of: H. Li et al., "Rethinking the economics and flexibility of U.S. nuclear power through hydrogen
integration and policy support", Nature Communications 17, 7090 (2026), doi:10.1038/s41467-026-73630-y.
Equation numbers in this package refer to that article (docs/formulation.md).
"""

from .finance import (adjusted_basis, annualized_capex, credit_45u, crf, exponent_from_learning_rate, irr,
                      macrs_schedule, npv, payback_years, present_value, scaled_cost)
from .io import read_inputs, read_tea, write_inputs
from .model import PlantModel, build_model
from .parameters import (Battery, Electrolyzer, HydrogenStorage, Market, ParameterError, Plant, Service,
                         Turbine)
from .results import (Operation, as_revenue_by_service, balance_residuals, baseline_operation, baseline_plant,
                      flexibility_metrics, revenue_table)
from .scenarios import (StudyDesign, case_matrix, electrolyzer_limit_sweep, operations, price_scenarios,
                        set_fixed_cost, tea_sweep)
from .services import iso_services
from .solve import SolveError, optimize, solve
from .tea import (CapexItem, Credit45U, Credit48E, CreditPerUnit, Credits, FixedCost, Replacement, TEAInputs,
                  TEAResult, case_inputs, evaluate, results_table)
from .timeseries import TimeSeries, drop_leap_day, fill_gaps, hourly_year, long_to_wide, scale_prices, to_hourly

__version__ = "1.0.0"
