"""Financial building blocks of the techno-economic analysis (Eqs. 47-51 and 55, MACRS depreciation).

All functions are plain NumPy and independent of the optimization model.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np

from .parameters import ParameterError


def crf(rate: float, years: float) -> float:
    """Capital recovery factor, Eq. (55): r (1 + r)^L / ((1 + r)^L - 1); 1 / L when r = 0."""
    if years <= 0:
        raise ParameterError("the lifetime must be > 0")
    if rate == 0:
        return 1.0 / years
    g = (1.0 + rate) ** years
    return rate * g / (g - 1.0)


def annualized_capex(overnight_cost: float, rate: float, years: float) -> float:
    """Annualized capital cost: overnight cost x CRF (Eq. 55), e.g. the C^{...,capex} parameters of Eq. (6)."""
    return overnight_cost * crf(rate, years)


def scaled_cost(ref_cost: float, ref_size: float, size: float, exponent: float) -> float:
    """Component cost scaled from a reference size, Eq. (49): C_ref (S / S_ref)^alpha (zero for zero size)."""
    if ref_size <= 0:
        raise ParameterError("the reference size must be > 0")
    if size <= 0:
        return 0.0
    return ref_cost * (size / ref_size) ** exponent


def exponent_from_learning_rate(learning_rate: float) -> float:
    """Scaling exponent alpha of Eq. (49) from a learning rate LR (convention of this implementation):
    alpha = 1 + log2(1 - LR), i.e. the cost per unit of size falls by the fraction LR for every doubling of size."""
    if not 0 <= learning_rate < 1:
        raise ParameterError("the learning rate must lie in [0, 1)")
    return 1.0 + math.log2(1.0 - learning_rate)


def macrs_schedule(recovery_years: int, declining_balance: Optional[float] = None) -> np.ndarray:
    """MACRS general-depreciation-system fractions with the half-year convention (recovery_years + 1 values).

    Declining balance at ``declining_balance`` / recovery_years (default: 2.0 for classes up to 10 years and
    1.5 above), switching to straight line when that is larger; the first and the last year are half years.
    The fractions sum to one. Rounded official tables can differ in the last digit."""
    n = int(recovery_years)
    if n < 1:
        raise ParameterError("the recovery period must be >= 1 year")
    factor = declining_balance if declining_balance is not None else (2.0 if n <= 10 else 1.5)
    rate = factor / n
    remaining = 1.0
    out = []
    for year in range(1, n + 2):
        if year == n + 1:
            dep = remaining
        else:
            portion = 0.5 if year == 1 else 1.0
            db = remaining * rate * portion
            life_left = n - (year - 1) + (0.0 if year == 1 else 0.5)    # years of recovery left at the start of year
            sl = remaining / life_left * portion
            dep = max(db, sl)
        dep = min(dep, remaining)
        out.append(dep)
        remaining -= dep
    return np.asarray(out)


def credit_45u(price, max_credit: float, price_low: float, price_high: float) -> np.ndarray:
    """Hourly zero-emission nuclear production credit, Eq. (50), $/MWh, written with parameters:
    max_credit below price_low, falling linearly to zero at price_high, zero above."""
    if not price_high > price_low:
        raise ParameterError("price_high must exceed price_low")
    p = np.asarray(price, dtype=float)
    slope = (p - price_low) / (price_high - price_low)
    return np.where(p < price_low, max_credit, np.where(p > price_high, 0.0, max_credit * (1.0 - slope)))


def adjusted_basis(capex: float, itc_value: float, reduction: float) -> float:
    """Depreciable basis after an investment credit, Eq. (51): CAPEX - reduction x ITC (``reduction`` is the
    basis-reduction fraction)."""
    return capex - reduction * itc_value


def present_value(values: Sequence[float], rate: float) -> float:
    """sum_t values_t / (1 + rate)^t with t = 0, 1, ... (the discounting of Eq. 47)."""
    v = np.asarray(values, dtype=float)
    return float(np.sum(v / (1.0 + rate) ** np.arange(v.size)))


def npv(cash_flows: Sequence[float], rate: float) -> float:
    """Net present value, Eq. (47)."""
    return present_value(cash_flows, rate)


def irr(cash_flows: Sequence[float], low: float = -0.99, high: float = 10.0, tol: float = 1e-10,
        points: int = 2000) -> float:
    """Internal rate of return: a rate in [``low``, ``high``] at which Eq. (47) is zero.

    The search starts at 0 (or at the bound nearest to 0) and moves outward: the smallest non-negative rate where
    the NPV is zero or changes sign, otherwise the negative rate closest to 0; each bracket is refined by
    bisection. The sign of the NPV is evaluated in logarithmic scale (each discounted term divided by the largest
    one), so cash flows of very different magnitudes and long horizons neither overflow nor vanish. Cash flows
    without a sign change have no root. Several roots are possible when
    the sign changes more than once, and two roots closer together than the grid spacing (``points`` per side) can
    be missed. NaN when no root is found."""
    if not low < high:
        raise ParameterError("need low < high")
    cf = np.asarray(cash_flows, dtype=float)
    if cf.size == 0 or not np.all(np.isfinite(cf)):
        return float("nan")
    nz = np.flatnonzero(cf)
    if nz.size == 0 or not (np.any(cf > 0) and np.any(cf < 0)):
        return float("nan")                            # no sign change: no root
    t = nz.astype(float)
    sign = np.sign(cf[nz])
    log_abs = np.log(np.abs(cf[nz]))

    def scaled_terms(r: float) -> np.ndarray:
        """Discounted terms divided by the largest one (positive factor: signs and the root set are unchanged)."""
        logs = log_abs - t * np.log1p(r)
        return sign * np.exp(logs - np.max(logs))

    def f(r: float) -> float:
        return float(np.sum(scaled_terms(r)))

    def is_root(r: float) -> bool:
        terms = scaled_terms(r)
        return abs(float(np.sum(terms))) <= 1e-12 * float(np.sum(np.abs(terms)))

    def root(a: float, b: float) -> float:
        fa = f(a)
        for _ in range(300):
            mid = 0.5 * (a + b)
            fm = f(mid)
            if fm == 0 or abs(b - a) < tol:
                return float(mid)
            if np.sign(fm) == np.sign(fa):
                a, fa = mid, fm
            else:
                b = mid
        return float(0.5 * (a + b))

    start = min(max(0.0, low), high)
    sides = []
    if high > start:
        dense = np.linspace(start, min(high, max(start, 1.0)), points // 2 + 1)
        tail = np.linspace(dense[-1], high, points // 2 + 1)[1:] if high > dense[-1] else np.array([])
        sides.append(np.concatenate([dense, tail]))
    else:
        sides.append(np.array([start]))
    if low < start:
        sides.append(np.linspace(start, low, points + 1))
    for grid in sides:
        vals = np.array([f(r) for r in grid])
        for i in range(len(grid)):
            if is_root(grid[i]):
                return float(grid[i])
            if i + 1 < len(grid) and np.isfinite(vals[i]) and np.isfinite(vals[i + 1]) \
                    and np.sign(vals[i]) * np.sign(vals[i + 1]) < 0:
                a, b = sorted((grid[i], grid[i + 1]))
                return root(a, b)
    return float("nan")


def payback_years(cash_flows: Sequence[float], first_operating_index: int = 0) -> float:
    """Undiscounted payback: years after the start of operation until the cumulative cash flow (from the first
    year of the timeline) turns non-negative, interpolated within the year. NaN if it never does."""
    cum = np.cumsum(np.asarray(cash_flows, dtype=float))
    for i in range(max(first_operating_index, 0), cum.size):
        if cum[i] >= 0:
            if i == 0:
                return 0.0
            prev, step = cum[i - 1], cum[i] - cum[i - 1]
            frac = (-prev / step) if prev < 0 and step > 0 else 0.0
            return float(i - first_operating_index + frac)
    return float("nan")


__all__ = ["crf", "annualized_capex", "scaled_cost", "exponent_from_learning_rate", "macrs_schedule", "credit_45u",
           "adjusted_basis", "present_value", "npv", "irr", "payback_years"]
