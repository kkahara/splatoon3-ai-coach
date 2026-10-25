"""Exact cluster sign-flip test (unblinded stage)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Any


@dataclass(frozen=True)
class SignFlipResult:
    """Observed statistic, discordant clusters and both one-sided p-values."""

    t_obs: int
    f_nz: int
    p_reg: Fraction
    p_imp: Fraction

    def to_dict(self) -> dict[str, Any]:
        """JSON payload (p-values as exact fractions and floats)."""
        payload = asdict(self)
        for key in ("p_reg", "p_imp"):
            value = payload[key]
            payload[key] = float(value)
            payload[f"{key}_exact"] = f"{value.numerator}/{value.denominator}"
        return payload


def sign_flip(differences: Sequence[int]) -> SignFlipResult:
    """Enumerate all 2^F_nz sign vectors over clusters with ``d_i != 0``.

    ``p_reg = Pr(T* <= T_obs)``, ``p_imp = Pr(T* >= T_obs)``, ties included.
    With ``F_nz = 0`` both p-values are 1.
    """
    nonzero = [int(d) for d in differences if int(d) != 0]
    t_obs = sum(nonzero)
    distribution: dict[int, int] = {0: 1}
    for d in nonzero:
        nxt: dict[int, int] = {}
        for total, count in distribution.items():
            for signed in (d, -d):
                nxt[total + signed] = nxt.get(total + signed, 0) + count
        distribution = nxt
    patterns = 2 ** len(nonzero)
    low = sum(c for t, c in distribution.items() if t <= t_obs)
    high = sum(c for t, c in distribution.items() if t >= t_obs)
    return SignFlipResult(
        t_obs, len(nonzero), Fraction(low, patterns), Fraction(high, patterns)
    )
