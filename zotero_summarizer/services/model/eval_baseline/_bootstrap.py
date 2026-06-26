"""BCa (bias-corrected accelerated) bootstrap confidence interval.

Reference: Efron 1987, "Better Bootstrap Confidence Intervals" (JASA 82(397)).
Pure-NumPy implementation; no external dependency.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm


def bca_ci(
    values: np.ndarray,
    *,
    n_bootstrap: int,
    alpha: float = 0.05,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Return (point_estimate, ci_low, ci_high) for the mean of ``values``.

    Raises ValueError if ``values`` is empty — refuse to bootstrap nothing.
    """
    if values.size == 0:
        raise ValueError("cannot bootstrap an empty array of fold values")
    rng = np.random.default_rng(seed)
    point = float(np.mean(values))

    n = values.size
    boot_means = np.empty(n_bootstrap, dtype=np.float64)
    for b in range(n_bootstrap):
        idx = rng.integers(0, n, size=n)
        boot_means[b] = float(np.mean(values[idx]))

    proportion_less = float(np.sum(boot_means < point)) / n_bootstrap
    proportion_less = min(
        max(proportion_less, 1.0 / (n_bootstrap + 1)),
        1.0 - 1.0 / (n_bootstrap + 1),
    )
    z0 = norm.ppf(proportion_less)

    jackknife = np.empty(n, dtype=np.float64)
    for i in range(n):
        jackknife[i] = float(np.mean(np.delete(values, i)))
    jack_mean = float(np.mean(jackknife))
    numer = float(np.sum((jack_mean - jackknife) ** 3))
    denom = 6.0 * (float(np.sum((jack_mean - jackknife) ** 2)) ** 1.5)
    acceleration = 0.0 if denom == 0.0 else numer / denom

    z_low = norm.ppf(alpha / 2)
    z_high = norm.ppf(1 - alpha / 2)
    a1 = norm.cdf(z0 + (z0 + z_low) / (1 - acceleration * (z0 + z_low)))
    a2 = norm.cdf(z0 + (z0 + z_high) / (1 - acceleration * (z0 + z_high)))
    sorted_boot = np.sort(boot_means)
    ci_low = float(sorted_boot[max(0, int(a1 * n_bootstrap))])
    ci_high = float(sorted_boot[min(n_bootstrap - 1, int(a2 * n_bootstrap))])
    return point, ci_low, ci_high
