"""Input directories and output files (schemas in docs/data_pipeline.md).

An input directory holds:
  parameters.json      plant and market parameters (turbine, electrolyzer, battery, storage, market, services)
  energy_price.csv     column ``energy_price`` ($/MWh); optional column ``timestamp`` (UTC when series.json names a
                       time zone, which is then restored on reading)
  as_price.csv         one column per product ($/MW per hour)
  winning_rate.csv     one column per product (fraction)
  deployment.csv       one column per service (fraction)
  mileage.csv, performance.csv, mileage_price.csv   optional, one column per product
  (tables of a series without ancillary services are omitted)
  tea.json             optional: TEA inputs and study design
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import math
import os
import shutil
import tempfile
import zoneinfo
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from .parameters import Battery, Electrolyzer, HydrogenStorage, Market, ParameterError, Plant, Service, Turbine
from .timeseries import TimeSeries


def _encode(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _encode(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): _encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_encode(v) for v in obj]
    if isinstance(obj, (np.floating, np.integer)):
        obj = obj.item()
    if isinstance(obj, float) and math.isinf(obj):
        return "inf" if obj > 0 else "-inf"
    if isinstance(obj, float) and math.isnan(obj):
        return None                       # undefined values (e.g. an IRR that does not exist) are written as null
    return obj


def _decode_number(v: Any) -> Any:
    if v == "inf":
        return math.inf
    if v == "-inf":
        return -math.inf
    return v


def _build(cls, data: Optional[Dict[str, Any]]):
    if data is None:
        return None
    names = {f.name: f for f in dataclasses.fields(cls)}
    unknown = set(data) - set(names)
    if unknown:
        raise ParameterError(f"{cls.__name__}: unknown fields {sorted(unknown)}")
    kwargs = {}
    for k, v in data.items():
        v = _decode_number(v)
        if isinstance(v, list):
            v = tuple(_decode_number(x) for x in v)
        kwargs[k] = v
    return cls(**kwargs)


def plant_to_dict(plant: Plant) -> Dict[str, Any]:
    return _encode(plant)


def plant_from_dict(d: Dict[str, Any]) -> Plant:
    market = dict(d["market"])
    services = tuple(_build(Service, s) for s in market.pop("services", []))
    m = _build(Market, market)
    m = dataclasses.replace(m, services=services)
    return Plant(turbine=_build(Turbine, d["turbine"]), market=m, electrolyzer=_build(Electrolyzer, d.get("electrolyzer")),
                 battery=_build(Battery, d.get("battery")), storage=_build(HydrogenStorage, d.get("storage")))


def tea_to_dict(inputs, design=None) -> Dict[str, Any]:
    out = {"inputs": _encode(inputs)}
    if design is not None:
        out["design"] = _encode(design)
    return out


def tea_from_dict(d: Dict[str, Any]):
    from .scenarios import StudyDesign
    from .tea import (CapexItem, Credit45U, Credit48E, CreditPerUnit, Credits, FixedCost, Replacement,
                      TEAInputs)

    inp = dict(d["inputs"])
    capex = []
    for c in inp.pop("capex", []):
        c = dict(c)
        if "schedule" not in c:
            raise ParameterError(f"capex item {c.get('name')}: a payment schedule is required")
        schedule = {}
        for k, v in c["schedule"].items():
            try:
                y = int(str(k).strip())
            except ValueError:
                raise ParameterError(f"capex item {c.get('name')}: payment year {k!r} is not an integer") from None
            if y in schedule:
                raise ParameterError(f"capex item {c.get('name')}: payment year {y} appears twice")
            schedule[y] = float(v)
        c["schedule"] = schedule
        capex.append(_build(CapexItem, c))
    reps = [_build(Replacement, r) for r in inp.pop("replacements", [])]
    fixed = [_build(FixedCost, f) for f in inp.pop("fixed_costs", [])]
    cr = dict(inp.pop("credits", {}))
    credits = Credits(u45=_build(Credit45U, cr.get("u45")), y45=_build(CreditPerUnit, cr.get("y45")),
                      e48=_build(Credit48E, cr.get("e48")), hydrogen=_build(CreditPerUnit, cr.get("hydrogen")),
                      taxable=bool(cr.get("taxable", False)))
    inputs = TEAInputs(capex=capex, replacements=reps, fixed_costs=fixed, credits=credits, **inp)
    design = _build(StudyDesign, d.get("design"))
    return inputs, design


TABLES = ("as_price", "winning_rate", "deployment", "mileage", "performance", "mileage_price")


def _fixed_offset(tz) -> Optional[datetime.timedelta]:
    """The offset of a time zone of a known fixed-offset type (datetime.timezone, dateutil tzutc/tzoffset, pytz
    FixedOffset); None for any other type, whatever its offsets on sampled dates."""
    if isinstance(tz, datetime.timezone):
        return tz.utcoffset(None)
    try:
        from dateutil import tz as dtz
        if isinstance(tz, (dtz.tzutc, dtz.tzoffset)):
            return tz.utcoffset(datetime.datetime(2000, 1, 1))
    except ImportError:  # pragma: no cover - dateutil ships with pandas
        pass
    if type(tz).__name__ == "_FixedOffset" and hasattr(tz, "_minutes"):          # pytz.FixedOffset
        return datetime.timedelta(minutes=tz._minutes)
    return None


def _tz_meta(tz) -> Dict[str, Any]:
    """series.json entry for a time zone: the offset of a known fixed-offset type (checked first, whatever its
    display name), or an IANA name; anything else is refused before writing."""
    offset = _fixed_offset(tz)
    if offset is not None:
        return {"utc_offset_minutes": offset.total_seconds() / 60.0}
    name = getattr(tz, "key", None) or getattr(tz, "zone", None)
    if name is None:
        try:
            zoneinfo.ZoneInfo(str(tz))
            name = str(tz)
        except Exception:
            name = None
    if name is not None:
        return {"timezone": str(name)}
    raise ParameterError(f"time zone {tz!r} cannot be stored: use an IANA name (e.g. 'America/Chicago') or a fixed offset")


INPUT_FILES = ("parameters.json", "energy_price.csv", "series.json", "tea.json") + tuple(f"{n}.csv" for n in TABLES)


def write_inputs(directory, plant: Plant, ts: TimeSeries, tea=None, design=None) -> Path:
    """Write a new input directory (see the module docstring).

    Inputs are validated first. The target must not exist or must be an empty directory; nothing is ever
    overwritten. All files are written to a temporary sibling directory, which is then renamed onto the target;
    if anything fails, the temporary directory is removed and the target is left as it was."""
    plant.validate()
    ts.check_against(plant.market)
    d = Path(directory)
    if d.exists() and (not d.is_dir() or any(d.iterdir())):
        raise ParameterError(f"{d} exists and is not an empty directory; write to a new or empty directory")
    price = pd.DataFrame({"energy_price": ts.energy_price})
    meta = {"dt_h": ts.dt_h}
    if ts.index is not None:
        idx = pd.DatetimeIndex(ts.index)
        if idx.tz is not None:                       # time-zone aware: stored in UTC, zone kept in series.json
            meta.update(_tz_meta(idx.tz))
            idx = idx.tz_convert("UTC").tz_localize(None)
        price.insert(0, "timestamp", idx.astype(str))   # ISO format with full sub-second precision
    files = {"parameters.json": json.dumps(plant_to_dict(plant), indent=2) + "\n",
             "energy_price.csv": price.to_csv(index=False),
             "series.json": json.dumps(meta, indent=2) + "\n"}
    for name in TABLES:
        table = getattr(ts, name)
        if table is not None and len(table.columns) > 0:
            files[f"{name}.csv"] = table.to_csv(index=False)
    if tea is not None:
        files["tea.json"] = json.dumps(tea_to_dict(tea, design), indent=2) + "\n"
    d.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{d.name}.", dir=d.parent))
    try:
        for name, text in files.items():
            (tmp / name).write_text(text)
        os.replace(tmp, d)                             # onto a missing or empty target directory
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)         # only the temporary directory created above
        raise
    return d


def read_inputs(directory) -> Tuple[Plant, TimeSeries]:
    """Read and validate an input directory: returns (plant, time series)."""
    d = Path(directory)
    if not (d / "parameters.json").exists():
        raise ParameterError(f"{d} has no parameters.json")
    plant = plant_from_dict(json.loads((d / "parameters.json").read_text()))
    price = pd.read_csv(d / "energy_price.csv")
    if "energy_price" not in price.columns:
        raise ParameterError("energy_price.csv needs a column 'energy_price'")
    meta = json.loads((d / "series.json").read_text()) if (d / "series.json").exists() else {}
    index = None
    if "timestamp" in price.columns:
        if meta.get("timezone") or "utc_offset_minutes" in meta:
            tz = meta.get("timezone") or datetime.timezone(datetime.timedelta(minutes=float(meta["utc_offset_minutes"])))
            index = pd.DatetimeIndex(pd.to_datetime(price["timestamp"], utc=True)).tz_convert(tz)
        else:
            index = pd.DatetimeIndex(pd.to_datetime(price["timestamp"]))
    tables = {name: pd.read_csv(d / f"{name}.csv") if (d / f"{name}.csv").exists() else None for name in TABLES}
    for name in ("as_price", "winning_rate", "deployment"):
        if tables[name] is None:                     # no ancillary services: empty tables with one row per hour
            tables[name] = pd.DataFrame(index=range(len(price)))
    ts = TimeSeries(energy_price=price["energy_price"].to_numpy(), dt_h=float(meta.get("dt_h", 1.0)), index=index,
                    **tables)
    plant.validate()
    ts.check_against(plant.market)
    return plant, ts


def read_tea(directory):
    """Read ``tea.json`` of an input directory: returns (TEA inputs, study design or None)."""
    path = Path(directory) / "tea.json"
    if not path.exists():
        raise ParameterError(f"{directory} has no tea.json")
    return tea_from_dict(json.loads(path.read_text()))


def write_json(path, data) -> None:
    def default(o):
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
        raise TypeError(type(o))
    Path(path).write_text(json.dumps(_encode(data), indent=2, default=default, allow_nan=False) + "\n")


__all__ = ["INPUT_FILES", "plant_to_dict", "plant_from_dict", "tea_to_dict", "tea_from_dict", "write_inputs", "read_inputs",
           "read_tea", "write_json"]
