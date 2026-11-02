"""Hourly market series and the helpers that prepare them from raw market records (docs/data_pipeline.md).

A :class:`TimeSeries` holds, for every hour t of the horizon:
  energy_price   λ^E_t                      $/MWh
  as_price       λ^{AS,cap}_{t,s}            $/MW per hour, one column per product
  winning_rate   w_{t,s}                     fraction of a bid that clears, one column per product
  deployment     delta_{t,s}                 fraction of the cleared bid that is deployed, one column per service
  mileage        M_{t,s}   (optional)        regulation mileage factor, per product
  performance    P_{t,s}   (optional)        regulation performance factor, per product
  mileage_price  λ^{mil}_{t,s} (opt.)        $/MW of mileage, per product
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .parameters import Market, ParameterError, Service

HOURS_PER_YEAR = 8760


@dataclass
class TimeSeries:
    energy_price: np.ndarray
    as_price: pd.DataFrame
    winning_rate: pd.DataFrame
    deployment: pd.DataFrame
    mileage: Optional[pd.DataFrame] = None
    performance: Optional[pd.DataFrame] = None
    mileage_price: Optional[pd.DataFrame] = None
    dt_h: float = 1.0
    index: Optional[pd.DatetimeIndex] = None

    def __post_init__(self) -> None:
        self.energy_price = np.asarray(self.energy_price, dtype=float)
        n = self.energy_price.size
        for name in ("as_price", "winning_rate", "deployment", "mileage", "performance", "mileage_price"):
            df = getattr(self, name)
            if df is None:
                continue
            df = pd.DataFrame(df).reset_index(drop=True).astype(float)
            if len(df) != n:
                raise ParameterError(f"{name} has {len(df)} rows; the energy price has {n}")
            setattr(self, name, df)

    @property
    def hours(self) -> int:
        return int(self.energy_price.size)

    @property
    def annualization(self) -> float:
        """Factor from the horizon to one year, 8760 / (|T| dt) (the scaling of Eq. 6)."""
        return HOURS_PER_YEAR / (self.hours * self.dt_h)

    def validate(self, services: Iterable[Service] = ()) -> None:
        if self.hours < 1:
            raise ParameterError("the time series is empty")
        if not (np.isfinite(self.dt_h) and self.dt_h > 0):
            raise ParameterError("dt_h must be > 0")
        if not np.all(np.isfinite(self.energy_price)):
            raise ParameterError("the energy price has missing or non-finite values (fill gaps first)")
        for s in services:
            col = s.column
            for name, table, key in (("as_price", self.as_price, col), ("winning_rate", self.winning_rate, col),
                                     ("deployment", self.deployment, s.name)):
                if key not in table.columns:
                    raise ParameterError(f"service {s.name}: column '{key}' missing in {name}")
                values = table[key].to_numpy()
                if not np.all(np.isfinite(values)):
                    raise ParameterError(f"service {s.name}: {name} has missing or non-finite values")
            if np.any(self.as_price[col].to_numpy() < 0):
                raise ParameterError(f"service {s.name}: negative capacity price")
            w = self.winning_rate[col].to_numpy()
            d = self.deployment[s.name].to_numpy()
            if np.any((w < 0) | (w > 1)) or np.any((d < 0) | (d > 1)):
                raise ParameterError(f"service {s.name}: winning rates and deployment factors must lie in [0, 1]")
            if s.mileage and self.mileage_price is not None and col in self.mileage_price.columns:
                for name, table in (("mileage_price", self.mileage_price), ("mileage", self.mileage),
                                    ("performance", self.performance)):
                    if table is None or col not in table.columns:
                        raise ParameterError(f"service {s.name}: a mileage payment needs the {name} column '{col}'")
                    if not np.all(np.isfinite(table[col].to_numpy())) or np.any(table[col].to_numpy() < 0):
                        raise ParameterError(f"service {s.name}: {name} must be finite and >= 0")

    def check_against(self, market: Market) -> None:
        self.validate(market.services if market.as_mode != "off" else ())

    def slice(self, start: int, stop: int) -> "TimeSeries":
        """Hours [start, stop) as a new series (e.g. a shorter block of a year)."""
        cut = lambda df: None if df is None else df.iloc[start:stop].reset_index(drop=True)  # noqa: E731
        return TimeSeries(self.energy_price[start:stop], cut(self.as_price), cut(self.winning_rate),
                          cut(self.deployment), cut(self.mileage), cut(self.performance), cut(self.mileage_price),
                          self.dt_h, None if self.index is None else self.index[start:stop])


def scale_prices(ts: TimeSeries, factor: float, include_as: bool = False) -> TimeSeries:
    """Price scenario: multiply the energy price by ``factor`` (and the capacity prices too if ``include_as``)."""
    out = replace(ts, energy_price=ts.energy_price * factor)
    if include_as:
        out.as_price = ts.as_price * factor
    return out


# ---------------------------------------------------------------------------------------------------------------
# preparation helpers (raw records -> hourly series), see docs/data_pipeline.md
# ---------------------------------------------------------------------------------------------------------------

def to_hourly(series: pd.Series, how: str = "mean") -> pd.Series:
    """Aggregate a sub-hourly series (e.g. 5- or 15-minute prices) on a DatetimeIndex to hourly values.

    Each hour is labelled by its start; ``how`` is any pandas resampling reduction ("mean" by default)."""
    if not isinstance(series.index, pd.DatetimeIndex):
        raise ParameterError("to_hourly needs a series on a DatetimeIndex")
    return getattr(series.sort_index().resample("1h"), how)()


def drop_leap_day(series: pd.Series) -> pd.Series:
    """Remove 29 February so that a year has 8760 hours."""
    idx = series.index
    return series[~((idx.month == 2) & (idx.day == 29))]


def fill_gaps(series: pd.Series, limit: Optional[int] = None) -> pd.Series:
    """Linear interpolation of missing values (in time order); leading and trailing gaps take the nearest value."""
    out = series.astype(float).interpolate(method="linear", limit=limit, limit_direction="both")
    return out


def hourly_year(series: pd.Series, year: int, tz: Optional[str] = None) -> pd.Series:
    """Hourly series of one calendar year: reindexed to every hour of ``year`` (in ``tz`` when the index is
    time-zone aware), gaps filled linearly, 29 February dropped. The result has 8760 values."""
    start = pd.Timestamp(year=year, month=1, day=1, tz=tz)
    hours = pd.date_range(start, start + pd.DateOffset(years=1), freq="1h", inclusive="left")
    s = series.copy()
    if s.index.tz is not None and tz is None:
        raise ParameterError("the series is time-zone aware; pass the market's time zone as tz")
    if tz is not None:
        s.index = s.index.tz_convert(tz) if s.index.tz is not None else s.index.tz_localize(tz)
    s = s[~s.index.duplicated(keep="first")].reindex(hours)
    s = drop_leap_day(fill_gaps(s))
    if len(s) != HOURS_PER_YEAR:
        raise ParameterError(f"expected {HOURS_PER_YEAR} hours, got {len(s)}")
    return s


def long_to_wide(records: pd.DataFrame, time_col: str, product_col: str, value_col: str,
                 rename: Optional[dict] = None) -> pd.DataFrame:
    """Turn long market records (one row per time and product) into one column per product.

    ``rename`` maps the market's product labels to the service names used in the parameters (unmapped
    products are dropped when a mapping is given). Duplicate time-product rows are averaged."""
    df = records[[time_col, product_col, value_col]].copy()
    df[time_col] = pd.to_datetime(df[time_col])
    if rename is not None:
        df = df[df[product_col].isin(rename)]
        df[product_col] = df[product_col].map(rename)
    wide = df.pivot_table(index=time_col, columns=product_col, values=value_col, aggfunc="mean")
    wide.columns.name = None
    return wide.sort_index()


__all__ = ["HOURS_PER_YEAR", "TimeSeries", "scale_prices", "to_hourly", "drop_leap_day", "fill_gaps", "hourly_year",
           "long_to_wide"]
