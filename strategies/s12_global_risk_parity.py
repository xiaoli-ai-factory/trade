"""S12 cross-asset ETF risk parity strategy.

S12 intentionally reuses the same inverse-volatility implementation as S9.
The distinction is the configured seven-ETF cross-asset pool in
``configs/strategy_addon.yaml``.
"""

from __future__ import annotations

from strategies.s9_risk_parity import S9RiskParityStrategy, orders_for_target_weights


class S12GlobalRiskParityStrategy(S9RiskParityStrategy):
    """Cross-asset monthly inverse-volatility risk parity."""

