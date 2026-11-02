import json

import numpy as np
import pandas as pd
import pytest

from nuclear_h2 import ParameterError, read_inputs, read_tea, write_inputs
from nuclear_h2.cli import main
from nuclear_h2.io import plant_from_dict, plant_to_dict
from nuclear_h2.synthetic import illustrative_design, illustrative_plant, illustrative_tea_inputs, synthetic_series

from conftest import battery, plant, series, storage


def test_plant_round_trip():
    p = plant(battery=battery(energy_mwh=None, energy_range_mwh=(3.0, 70.0)), storage=storage())
    q = plant_from_dict(json.loads(json.dumps(plant_to_dict(p))))
    assert q == p


def test_input_directory_round_trip(tmp_path):
    p = illustrative_plant(storage=True)
    ts = synthetic_series(30, services=p.market.services)
    write_inputs(tmp_path, p, ts, tea=illustrative_tea_inputs(), design=illustrative_design())
    p2, ts2 = read_inputs(tmp_path)
    assert p2 == p
    assert np.allclose(ts2.energy_price, ts.energy_price)
    pd.testing.assert_frame_equal(ts2.as_price, ts.as_price, check_dtype=False)
    assert ts2.index is not None and len(ts2.index) == 30
    tea, design = read_tea(tmp_path)
    assert tea == illustrative_tea_inputs() and design == illustrative_design()


def test_read_inputs_errors(tmp_path):
    with pytest.raises(ParameterError):
        read_inputs(tmp_path)
    p = plant()
    write_inputs(tmp_path, p, series([30.0, 31.0]))
    (tmp_path / "deployment.csv").write_text("RegUp\n0.1\n0.2\n")            # columns missing for other services
    with pytest.raises(ParameterError):
        read_inputs(tmp_path)


def test_cli_synthetic_run_tea(tmp_path, capsys):
    inputs, out = tmp_path / "in", tmp_path / "out"
    assert main(["synthetic", str(inputs), "--hours", "48", "--storage"]) == 0
    assert main(["run", str(inputs), "--out-dir", str(out), "--as-mode", "dispatch", "--price-factor", "1.15"]) == 0
    summary = json.loads((out / "summary.json").read_text())
    assert summary["as_mode"] == "dispatch" and summary["hours"] == 48
    assert max(summary["balance_residuals"].values()) < 1e-5
    hourly = pd.read_csv(out / "hourly.csv")
    assert len(hourly) == 48 and {"p_turb", "p_elec", "h2_production", "p_ies"} <= set(hourly.columns)
    tea_out = tmp_path / "tea"
    assert main(["tea", str(inputs), "--out-dir", str(tea_out), "--reoptimize-after-credit"]) == 0
    cases = pd.read_csv(tea_out / "cases.csv", index_col=0)
    assert len(cases) == 10
    assert any(p.name.startswith("cash_flows_Case_2") for p in tea_out.iterdir())
    ops = json.loads((tea_out / "operations.json").read_text())
    assert set(ops) == {"baseline", "hybrid", "hybrid_after_credit"}


def test_cli_reports_bad_inputs(tmp_path, capsys):
    assert main(["run", str(tmp_path), "--out-dir", str(tmp_path / "o")]) == 2
    assert "error" in capsys.readouterr().err


def test_json_has_no_nan(tmp_path):
    inputs, out = tmp_path / "in", tmp_path / "out"
    main(["synthetic", str(inputs), "--hours", "24"])
    main(["tea", str(inputs), "--out-dir", str(out)])
    text = (out / "operations.json").read_text()
    json.loads(text)
    assert "NaN" not in text and "Infinity" not in text


def test_time_zone_round_trip_across_daylight_saving(tmp_path):
    p = plant()
    idx = pd.date_range("2031-03-08", periods=72, freq="1h", tz="US/Central")
    ts = series(np.full(72, 33.0))
    ts.index = idx
    write_inputs(tmp_path, p, ts)
    _, ts2 = read_inputs(tmp_path)
    assert ts2.index.equals(idx)


def test_no_services_round_trip_and_run(tmp_path):
    from nuclear_h2 import Market, optimize

    p = plant(market=Market(h2_price_usd_per_kg=2.2, h2_credit_usd_per_kg=0.4))
    ts = synthetic_series(24, services=())
    write_inputs(tmp_path, p, ts)
    assert not (tmp_path / "as_price.csv").exists()
    p2, ts2 = read_inputs(tmp_path)
    op = optimize(p2, ts2)
    assert op.totals["as_capacity_revenue"] == 0.0
    assert main(["run", str(tmp_path), "--out-dir", str(tmp_path / "out")]) == 0


def test_existing_input_directory_is_never_overwritten(tmp_path):
    from nuclear_h2 import Market

    p = plant()
    write_inputs(tmp_path / "d", p, series(np.full(24, 30.0)))
    before = {f.name: f.read_bytes() for f in (tmp_path / "d").iterdir()}
    for other_plant, other_ts in ((p, series(np.full(3, 31.0))),
                                  (plant(market=Market(h2_price_usd_per_kg=2.0)), synthetic_series(3, services=()))):
        with pytest.raises(ParameterError):
            write_inputs(tmp_path / "d", other_plant, other_ts)
    assert {f.name: f.read_bytes() for f in (tmp_path / "d").iterdir()} == before
    read_inputs(tmp_path / "d")
    assert [f.name for f in tmp_path.iterdir()] == ["d"]                  # no temporary directory left behind
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "notes.txt").write_text("x")
    with pytest.raises(ParameterError):
        write_inputs(tmp_path / "other", p, series(np.full(3, 31.0)))      # not empty


@pytest.mark.parametrize("existing_empty", [False, True])
def test_failed_write_leaves_nothing_behind(tmp_path, monkeypatch, existing_empty):
    import pathlib

    target = tmp_path / "t"
    if existing_empty:
        target.mkdir()
    original = pathlib.Path.write_text

    def failing(self, text, *args, **kwargs):
        if self.name == "as_price.csv":
            raise PermissionError("injected")
        return original(self, text, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", failing)
    with pytest.raises(PermissionError):
        write_inputs(target, plant(), series(np.full(4, 30.0)))
    monkeypatch.undo()
    assert [f.name for f in tmp_path.iterdir()] == (["t"] if existing_empty else [])
    if existing_empty:
        assert list(target.iterdir()) == []
    write_inputs(target, plant(), series(np.full(4, 30.0)))               # a retry works
    read_inputs(target)


def test_sub_second_timestamps_round_trip(tmp_path):
    p = plant()
    ts = series(np.full(3, 30.0))
    ts.index = pd.DatetimeIndex(["2031-01-01 00:00:00.123456", "2031-01-01 01:00:00.123456",
                                 "2031-01-01 02:00:00.123456"], tz="UTC")
    write_inputs(tmp_path, p, ts)
    assert read_inputs(tmp_path)[1].index.equals(ts.index)


def test_time_zone_kinds(tmp_path):
    from dateutil import tz

    p = plant()
    for n, zone in enumerate((tz.tzutc(), tz.tzoffset("custom", 19800))):
        ts = series(np.full(3, 30.0))
        ts.index = pd.date_range("2031-02-01", periods=3, freq="1h", tz=zone)
        write_inputs(tmp_path / f"z{n}", p, ts)
        back = read_inputs(tmp_path / f"z{n}")[1].index
        assert (back == ts.index).all() and back[0].utcoffset() == ts.index[0].utcoffset()
    import datetime

    from nuclear_h2.io import _tz_meta

    class Seasonal(datetime.tzinfo):                                        # neither a name nor a fixed offset
        def utcoffset(self, dt):
            return datetime.timedelta(hours=-5 if dt is not None and 4 <= dt.month <= 10 else -6)

        def dst(self, dt):
            return datetime.timedelta(0)

        def tzname(self, dt):
            return None

    with pytest.raises(ParameterError):
        _tz_meta(Seasonal())
    assert _tz_meta(tz.tzoffset(None, -7200)) == {"utc_offset_minutes": -120.0}


def test_rule_based_zones_without_a_name_are_refused():
    from dateutil import tz

    from nuclear_h2.io import _tz_meta

    with pytest.raises(ParameterError):
        _tz_meta(tz.tzstr("XST0XDT,M3.1.0/0,M5.1.0/0"))                 # a rule, not a fixed offset or a name
    assert _tz_meta(tz.tzutc()) == {"utc_offset_minutes": 0.0}
    import datetime

    named = datetime.timezone(datetime.timedelta(hours=2), "UTC")             # a fixed offset with a misleading name
    assert _tz_meta(named) == {"utc_offset_minutes": 120.0}
