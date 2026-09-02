from __future__ import annotations

import asyncio

from pydantic import ValidationError

from apm_demo.common.contracts import (
    HealthMode,
    ProviderBehavior,
    ProviderBehaviorPatch,
    ProviderId,
    ProviderSnapshot,
    ScenarioName,
    PROVIDER_BY_ID,
)


BASELINE_BEHAVIORS: dict[ProviderId, ProviderBehavior] = {
    ProviderId.ATLAS_PAY: ProviderBehavior(
        base_latency_ms=120,
        jitter_ms=35,
        timeout_rate=0.005,
        success_rate=0.96,
        soft_decline_rate=0.02,
        hard_decline_rate=0.01,
        provider_error_rate=0.01,
    ),
    ProviderId.NOVA_BANK: ProviderBehavior(
        base_latency_ms=220,
        jitter_ms=60,
        timeout_rate=0.01,
        success_rate=0.93,
        soft_decline_rate=0.04,
        hard_decline_rate=0.02,
        provider_error_rate=0.01,
    ),
    ProviderId.ORBIT_WALLET: ProviderBehavior(
        base_latency_ms=340,
        jitter_ms=100,
        timeout_rate=0.01,
        success_rate=0.92,
        soft_decline_rate=0.05,
        hard_decline_rate=0.02,
        provider_error_rate=0.01,
    ),
}


class ProviderRuntime:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._behaviors = {
            provider: behavior.model_copy(deep=True)
            for provider, behavior in BASELINE_BEHAVIORS.items()
        }

    async def get_behavior(self, provider: ProviderId) -> ProviderBehavior:
        async with self._lock:
            return self._behaviors[provider].model_copy(deep=True)

    async def list_snapshots(self) -> list[ProviderSnapshot]:
        async with self._lock:
            return [
                ProviderSnapshot(
                    provider=provider,
                    definition=PROVIDER_BY_ID[provider],
                    behavior=self._behaviors[provider],
                )
                for provider in ProviderId
            ]

    async def patch_behavior(
        self, provider: ProviderId, patch: ProviderBehaviorPatch
    ) -> ProviderBehavior:
        async with self._lock:
            try:
                updated = patch.apply_to(self._behaviors[provider])
            except ValidationError:
                raise
            self._behaviors[provider] = updated
            return updated.model_copy(deep=True)

    async def apply_scenario(
        self, scenario: ScenarioName, provider: ProviderId
    ) -> ProviderBehavior:
        async with self._lock:
            baseline = BASELINE_BEHAVIORS[provider]
            current = self._behaviors[provider]

            if scenario in (ScenarioName.NORMAL, ScenarioName.RECOVER):
                updated = baseline.model_copy(deep=True)
            elif scenario is ScenarioName.SLOW_PROVIDER:
                updated = current.model_copy(
                    update={
                        "base_latency_ms": 1_600,
                        "jitter_ms": 450,
                        "timeout_rate": 0.02,
                    }
                )
            elif scenario is ScenarioName.PROVIDER_ERRORS:
                updated = current.model_copy(
                    update={
                        "success_rate": 0.42,
                        "soft_decline_rate": 0.08,
                        "hard_decline_rate": 0.05,
                        "provider_error_rate": 0.45,
                    }
                )
            elif scenario is ScenarioName.PROVIDER_TIMEOUT:
                updated = current.model_copy(
                    update={"timeout_rate": 0.65, "timeout_delay_ms": 5_000}
                )
            elif scenario is ScenarioName.HEALTHCHECK_DOWN:
                updated = current.model_copy(
                    update={"health_mode": HealthMode.UNHEALTHY}
                )
            elif scenario is ScenarioName.HEALTHCHECK_TIMEOUT:
                updated = current.model_copy(
                    update={
                        "health_mode": HealthMode.TIMEOUT,
                        "timeout_delay_ms": 5_000,
                    }
                )
            else:  # pragma: no cover - exhaustive StrEnum guard
                raise ValueError(f"unsupported scenario: {scenario}")

            # model_copy does not re-run validators, so validate every scenario result.
            updated = ProviderBehavior.model_validate(updated.model_dump())
            self._behaviors[provider] = updated
            return updated.model_copy(deep=True)
