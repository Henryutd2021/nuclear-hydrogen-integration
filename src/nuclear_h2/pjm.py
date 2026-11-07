"""The 2024 PJM market prices used in the article, shipped in ``data/pjm/`` (documentation: data/pjm/README.md).

``data/pjm/`` holds the article's PJM price inputs, byte for byte:

  energy_price_2024.csv   hourly energy price: the mean of 21 PJM zonal and the PJM RTO locational marginal price
                          series ($/MWh)
  as_price_2024.csv       hourly ancillary-service clearing prices of the PJM RTO reserve zone ($/MW per hour)

They are data of PJM Interconnection, L.L.C. They are not covered by this package's MIT licence or by the CC BY 4.0
licence of the documentation: PJM's terms apply (data/pjm/README.md).

Hours are labelled by their start in Eastern Prevailing Time on a calendar of 365 days of 24 hours (29 February
removed). The labels are clock labels, not instants: the label 2024-03-10 02:00 does not exist in Eastern time, and
the label 2024-11-03 01:00 stands for the second (standard-time) 01:00 hour of that day. The index is therefore
kept without a time zone.

Values that are not PJM records are flagged in :attr:`PjmPrices.not_observed`; :meth:`PjmPrices.interpolated`
replaces them by linear interpolation between PJM records.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Union

import numpy as np
import pandas as pd

from .parameters import ParameterError, Service
from .services import iso_services
from .timeseries import HOURS_PER_YEAR, TimeSeries, fill_gaps

ENERGY_FILE = "energy_price_2024.csv"
AS_FILE = "as_price_2024.csv"
# SHA-256 of the published files (also in data/pjm/SHA256SUMS and data/pjm/README.md)
SHA256 = {
    ENERGY_FILE: "8f5e12cdc1d3d8dbfdc7ca85ff324164bfdfcef25cdb790b627fcc2e30656c3c",
    AS_FILE: "10748408f180e93e5cf78249f9ae47f6e593cc9ae42dcdb234cfd41aa2f7a0d9",
}
ENERGY_COLUMNS = ["Timestamp", "Price ($/MWh)"]
AS_COLUMNS = ["Timestamp", "p_RegUp_PJM", "loc_RegUp_PJM", "p_RegDown_PJM", "loc_RegDown_PJM", "p_Syn_PJM",
              "loc_Syn_PJM", "p_Rse_PJM", "loc_Rse_PJM", "p_TMR_PJM", "loc_TMR_PJM"]
# file column -> product name of iso_services("PJM"). The file repeats the regulation clearing price as RegUp and
# RegDown; PJM's regulation is one symmetric product (Reg), entered as Reg_up and Reg_down on that product.
PRODUCTS = {"p_RegUp_PJM": "Reg", "p_Rse_PJM": "Rse", "p_Syn_PJM": "Syn", "p_TMR_PJM": "TMR"}

# Hours whose values are not PJM records (data/pjm/README.md, "Values that are not PJM records")
NONEXISTENT_HOUR = pd.Timestamp("2024-03-10 02:00")              # clock hour skipped by daylight saving time
AS_GAP_DAY = "2024-12-12"                                        # no reserve-market records on that day
_ALL_2024 = pd.date_range("2024-01-01 00:00", "2024-12-31 23:00", freq="h")
_HOURS_2024 = _ALL_2024[~((_ALL_2024.month == 2) & (_ALL_2024.day == 29))]   # 8760 clock labels


def file_sha256(path: Union[str, Path]) -> str:
    """SHA-256 of a file (hex digest)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def default_directory() -> Path:
    """``data/pjm`` of the repository: below the current directory, else next to the source tree (editable
    installs). Pass the directory explicitly when neither applies."""
    candidates = [Path.cwd() / "data" / "pjm", Path(__file__).resolve().parents[2] / "data" / "pjm"]
    for c in candidates:
        if (c / ENERGY_FILE).is_file() and (c / AS_FILE).is_file():
            return c
    raise ParameterError("data/pjm not found; run from the repository root or pass the directory "
                         "(e.g. read_pjm_2024('path/to/nuclear-hydrogen-integration/data/pjm'))")


@dataclass(frozen=True)
class PjmPrices:
    """Hourly PJM prices of 2024 on hour labels (see the module docstring for the labels).

    energy_price  $/MWh
    as_price      $/MW per hour, columns Reg, Rse, Syn, TMR (the products of ``iso_services("PJM")``)
    not_observed  True where a value is not a PJM record; columns energy_price, Reg, Rse, Syn, TMR
    """
    energy_price: pd.Series
    as_price: pd.DataFrame
    not_observed: pd.DataFrame

    @property
    def index(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.energy_price.index)

    def interpolated(self) -> "PjmPrices":
        """A copy in which every value that is not a PJM record is replaced by linear interpolation in time
        between the neighbouring PJM records (``fill_gaps``); all other values are unchanged."""
        energy = fill_gaps(self.energy_price.mask(self.not_observed["energy_price"]))
        as_price = self.as_price.copy()
        for col in as_price.columns:
            as_price[col] = fill_gaps(as_price[col].mask(self.not_observed[col]))
        return PjmPrices(energy, as_price, self.not_observed.copy())

    def timeseries(self, winning_rate: Union[float, pd.DataFrame, Mapping[str, Iterable[float]]],
                   deployment: Union[float, pd.DataFrame, Mapping[str, Iterable[float]]],
                   services: Optional[Iterable[Service]] = None, start: Union[int, str, pd.Timestamp] = 0,
                   stop: Optional[Union[int, str, pd.Timestamp]] = None, mileage=None, performance=None,
                   mileage_price=None) -> TimeSeries:
        """A :class:`~nuclear_h2.timeseries.TimeSeries` of hours [start, stop) with these prices.

        Winning rates and deployment factors are not part of this release (docs/data_pipeline.md, "Generated
        ancillary-service series"), so they are arguments: a number (constant), or a table / mapping with one
        column per product (winning rates) or per service (deployment) and one row per selected hour.
        ``start`` and ``stop`` are row numbers or hour labels (``stop`` excluded). ``services`` defaults to
        ``iso_services("PJM")``; only their products' capacity prices are kept. The result is validated."""
        services = tuple(iso_services("PJM") if services is None else services)
        lo, hi = self._row(start, 0), self._row(stop, len(self.energy_price))
        if not 0 <= lo < hi <= len(self.energy_price):
            raise ParameterError(f"empty or out-of-range selection of hours: rows {lo} to {hi}")
        n = hi - lo
        products = list(dict.fromkeys(s.column for s in services))
        missing = [p for p in products if p not in self.as_price.columns]
        if missing:
            raise ParameterError(f"no PJM prices for products {missing}; PJM products: {list(self.as_price.columns)}")

        def table(value, columns, name):
            if np.isscalar(value):
                return pd.DataFrame({c: np.full(n, float(value)) for c in columns}, index=pd.RangeIndex(n))
            df = pd.DataFrame(value).reset_index(drop=True)
            absent = [c for c in columns if c not in df.columns]
            if absent:
                raise ParameterError(f"{name}: columns {absent} missing")
            if len(df) != n:
                raise ParameterError(f"{name} has {len(df)} rows; {n} hours are selected")
            return df[list(columns)].astype(float)

        ts = TimeSeries(energy_price=self.energy_price.to_numpy()[lo:hi],
                        as_price=self.as_price[products].iloc[lo:hi].reset_index(drop=True),
                        winning_rate=table(winning_rate, products, "winning_rate"),
                        deployment=table(deployment, [s.name for s in services], "deployment"),
                        mileage=mileage, performance=performance, mileage_price=mileage_price, dt_h=1.0,
                        index=self.index[lo:hi])
        ts.validate(services)
        return ts

    def _row(self, key, default: int) -> int:
        if key is None:
            return default
        if isinstance(key, (int, np.integer)) and not isinstance(key, bool):
            return int(key)
        label = pd.Timestamp(key)
        if label not in self.index:
            raise ParameterError(f"hour label {label} is not in the PJM 2024 series")
        return int(self.index.get_loc(label))


def _read(path: Path, columns, verify: bool) -> pd.DataFrame:
    if verify:
        digest = file_sha256(path)
        if digest != SHA256[path.name]:
            raise ParameterError(f"{path.name}: SHA-256 {digest} differs from the published file "
                                 f"({SHA256[path.name]}); pass verify=False to read a modified copy")
    df = pd.read_csv(path)
    if list(df.columns) != columns:
        raise ParameterError(f"{path.name}: expected columns {columns}, found {list(df.columns)}")
    if len(df) != HOURS_PER_YEAR:
        raise ParameterError(f"{path.name}: expected {HOURS_PER_YEAR} rows, found {len(df)}")
    idx = pd.DatetimeIndex(pd.to_datetime(df["Timestamp"], format="%Y-%m-%d %H:%M:%S"), name="hour")
    if not idx.equals(_HOURS_2024):
        raise ParameterError(f"{path.name}: the hour labels must be every hour of 2024 without 29 February, in order")
    values = df.drop(columns="Timestamp").astype(float)
    if not np.all(np.isfinite(values.to_numpy())):
        raise ParameterError(f"{path.name}: missing or non-finite values")
    values.index = idx
    return values


def read_pjm_2024(directory: Optional[Union[str, Path]] = None, verify: bool = True) -> PjmPrices:
    """Read ``energy_price_2024.csv`` and ``as_price_2024.csv`` from ``directory`` (default: ``data/pjm`` of the
    repository, :func:`default_directory`).

    With ``verify=True`` (default) both files must match their published SHA-256, so the values are exactly the
    article's PJM inputs. The ``loc_*`` columns of the ancillary-service file are all zero and are not used."""
    d = Path(directory) if directory is not None else default_directory()
    energy = _read(d / ENERGY_FILE, ENERGY_COLUMNS, verify)
    services = _read(d / AS_FILE, AS_COLUMNS, verify)
    if not energy.index.equals(services.index):
        raise ParameterError("the two files have different hour labels")
    differ = services.index[(services["p_RegUp_PJM"] != services["p_RegDown_PJM"])
                            & (services.index != NONEXISTENT_HOUR)]
    if len(differ):
        raise ParameterError(f"{AS_FILE}: p_RegUp_PJM and p_RegDown_PJM differ at {list(differ[:3])}; "
                             "the loader expects one regulation price")
    as_price = services[list(PRODUCTS)].rename(columns=PRODUCTS)
    if (as_price.to_numpy() < 0).any():
        raise ParameterError(f"{AS_FILE}: negative clearing prices")
    idx = energy.index
    flags = pd.DataFrame(False, index=idx, columns=["energy_price", *as_price.columns])
    flags.loc[idx == NONEXISTENT_HOUR, :] = True
    flags.loc[idx.strftime("%Y-%m-%d") == AS_GAP_DAY, list(as_price.columns)] = True
    return PjmPrices(energy_price=energy["Price ($/MWh)"].rename("energy_price"), as_price=as_price,
                     not_observed=flags)


__all__ = ["ENERGY_FILE", "AS_FILE", "SHA256", "PRODUCTS", "PjmPrices", "file_sha256", "default_directory",
           "read_pjm_2024"]
