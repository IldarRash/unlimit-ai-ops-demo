from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from time import perf_counter

from fastapi import FastAPI, HTTPException, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ValidationError

from apm_demo.common.contracts import (
    HealthMode,
    PaymentOutcome,
    PaymentRequest,
    PaymentResponse,
    ProviderBehavior,
    ProviderBehaviorPatch,
    ProviderId,
    ProviderSnapshot,
    ScenarioApplied,
    ScenarioName,
    ScenarioRequest,
    select_non_timeout_outcome,
)
from apm_demo.provider_emulator.metrics import ProviderMetrics
from apm_demo.provider_emulator.state import BASELINE_BEHAVIORS, ProviderRuntime


Sleep = Callable[[float], Awaitable[None]]

RESPONSE_CODES: dict[PaymentOutcome, str] = {
    PaymentOutcome.SUCCESS: "APPROVED",
    PaymentOutcome.SOFT_DECLINE: "DO_NOT_HONOR",
    PaymentOutcome.HARD_DECLINE: "INVALID_ACCOUNT",
    PaymentOutcome.PROVIDER_ERROR: "UPSTREAM_ERROR",
    PaymentOutcome.TIMEOUT: "PROVIDER_TIMEOUT",
}


def create_app(
    *,
    runtime: ProviderRuntime | None = None,
    metrics: ProviderMetrics | None = None,
    rng: random.Random | None = None,
    sleep: Sleep = asyncio.sleep,
) -> FastAPI:
    runtime = runtime or ProviderRuntime()
    metrics = metrics or ProviderMetrics()
    rng = rng or random.Random()
    for provider, behavior in BASELINE_BEHAVIORS.items():
        metrics.update_behavior(provider, behavior)
        metrics.set_active_scenario(provider, ScenarioName.NORMAL)

    app = FastAPI(
        title="Synthetic APM Provider Emulator",
        version="0.1.0",
        description="Configurable synthetic APM providers for latency and failure demos.",
    )

    app.state.runtime = runtime
    app.state.metrics = metrics

    @app.get("/health")
    async def service_health() -> dict[str, str]:
        return {"status": "ok", "service": "provider-emulator"}

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        return Response(
            content=generate_latest(metrics.registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    @app.get("/admin/providers", response_model=list[ProviderSnapshot])
    async def list_providers() -> list[ProviderSnapshot]:
        return await runtime.list_snapshots()

    @app.get("/admin/providers/{provider}", response_model=ProviderSnapshot)
    async def get_provider(provider: ProviderId) -> ProviderSnapshot:
        behavior = await runtime.get_behavior(provider)
        snapshots = await runtime.list_snapshots()
        return next(snapshot for snapshot in snapshots if snapshot.provider is provider)

    @app.patch(
        "/admin/providers/{provider}/behavior", response_model=ProviderBehavior
    )
    async def patch_provider_behavior(
        provider: ProviderId, patch: ProviderBehaviorPatch
    ) -> ProviderBehavior:
        try:
            behavior = await runtime.patch_behavior(provider, patch)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(
                    include_url=False,
                    include_context=False,
                    include_input=False,
                ),
            ) from exc
        metrics.update_behavior(provider, behavior)
        return behavior

    @app.post(
        "/admin/scenarios/{scenario}", response_model=ScenarioApplied
    )
    async def apply_scenario(
        scenario: ScenarioName, request: ScenarioRequest
    ) -> ScenarioApplied:
        behavior = await runtime.apply_scenario(scenario, request.provider)
        metrics.update_behavior(request.provider, behavior)
        metrics.record_scenario(request.provider, scenario)
        return ScenarioApplied(
            scenario=scenario,
            provider=request.provider,
            behavior=behavior,
        )

    @app.get("/providers/{provider}/health")
    async def provider_health(provider: ProviderId) -> Response:
        behavior = await runtime.get_behavior(provider)
        started = perf_counter()
        if behavior.health_mode is HealthMode.TIMEOUT:
            await sleep(behavior.timeout_delay_ms / 1_000)
            result = "timeout"
            status_code = status.HTTP_504_GATEWAY_TIMEOUT
        else:
            await sleep(behavior.health_latency_ms / 1_000)
            if behavior.health_mode is HealthMode.UNHEALTHY:
                result = "unhealthy"
                status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            else:
                result = "healthy"
                status_code = status.HTTP_200_OK

        metrics.health_checks.labels(provider=provider.value, result=result).inc()
        metrics.health_duration.labels(provider=provider.value).observe(
            perf_counter() - started
        )
        return Response(
            content=f'{{"provider":"{provider.value}","status":"{result}"}}',
            status_code=status_code,
            media_type="application/json",
        )

    @app.post(
        "/providers/{provider}/payments",
        response_model=PaymentResponse,
        responses={502: {"model": PaymentResponse}, 504: {"model": PaymentResponse}},
    )
    async def create_payment(
        provider: ProviderId, request: PaymentRequest, response: Response
    ) -> PaymentResponse:
        behavior = await runtime.get_behavior(provider)
        metrics.update_behavior(provider, behavior)
        metrics.requests.labels(
            provider=provider.value, payment_method=request.payment_method.value
        ).inc()

        if rng.random() < behavior.timeout_rate:
            outcome = PaymentOutcome.TIMEOUT
            delay_ms = behavior.timeout_delay_ms
        else:
            outcome = select_non_timeout_outcome(behavior, rng.random())
            delay_ms = max(
                0,
                round(
                    rng.uniform(
                        behavior.base_latency_ms - behavior.jitter_ms,
                        behavior.base_latency_ms + behavior.jitter_ms,
                    )
                ),
            )

        started = perf_counter()
        await sleep(delay_ms / 1_000)
        observed_seconds = perf_counter() - started

        metrics.responses.labels(provider=provider.value, outcome=outcome.value).inc()
        metrics.duration.labels(
            provider=provider.value, payment_method=request.payment_method.value
        ).observe(observed_seconds)

        if outcome is PaymentOutcome.PROVIDER_ERROR:
            response.status_code = status.HTTP_502_BAD_GATEWAY
        elif outcome is PaymentOutcome.TIMEOUT:
            response.status_code = status.HTTP_504_GATEWAY_TIMEOUT

        return PaymentResponse(
            transaction_id=request.transaction_id,
            provider=provider,
            outcome=outcome,
            response_code=(
                behavior.provider_error_code
                if outcome is PaymentOutcome.PROVIDER_ERROR
                else RESPONSE_CODES[outcome]
            ),
            processing_time_ms=delay_ms,
        )

    return app


app = create_app()
