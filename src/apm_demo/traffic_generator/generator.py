from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from time import perf_counter
from uuid import uuid4

import httpx

from apm_demo.common.contracts import (
    PaymentMethod,
    PaymentOutcome,
    PaymentRequest,
    ProviderDefinition,
    ProviderId,
    PROVIDER_DEFINITIONS,
)
from apm_demo.traffic_generator.config import GeneratorSettings
from apm_demo.traffic_generator.metrics import ClientMetrics


MERCHANTS = ("merchant-demo-01", "merchant-demo-02", "merchant-demo-03")
REGIONS = ("BR", "NL", "DE")


@dataclass(frozen=True)
class TrafficResult:
    provider: ProviderId
    payment_method: PaymentMethod
    outcome: str
    status_code: int | None
    duration_seconds: float
    response_code: str
    region: str


class TrafficGenerator:
    def __init__(
        self,
        settings: GeneratorSettings,
        metrics: ClientMetrics,
        *,
        client: httpx.AsyncClient | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.settings = settings
        self.metrics = metrics
        self.rng = rng or random.Random(settings.random_seed)
        self.client = client or httpx.AsyncClient(
            base_url=settings.provider_base_url.rstrip("/"),
            timeout=settings.request_timeout_seconds,
        )
        self._owns_client = client is None
        self._enabled = settings.generator_enabled
        self._requests_per_second = settings.requests_per_second
        self._stop = asyncio.Event()
        self._semaphore = asyncio.Semaphore(settings.max_in_flight)
        self._in_flight: set[asyncio.Task[TrafficResult]] = set()
        self._control_lock = asyncio.Lock()
        self._update_control_metrics()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def requests_per_second(self) -> float:
        return self._requests_per_second

    async def update_control(
        self, *, enabled: bool | None = None, requests_per_second: float | None = None
    ) -> None:
        async with self._control_lock:
            if enabled is not None:
                self._enabled = enabled
            if requests_per_second is not None:
                self._requests_per_second = requests_per_second
            self._update_control_metrics()

    def _update_control_metrics(self) -> None:
        self.metrics.generator_enabled.set(1 if self._enabled else 0)
        self.metrics.target_rps.set(self._requests_per_second)

    def select_provider(self) -> ProviderDefinition:
        return self.rng.choices(
            PROVIDER_DEFINITIONS,
            weights=[definition.traffic_weight for definition in PROVIDER_DEFINITIONS],
            k=1,
        )[0]

    def build_request(
        self, definition: ProviderDefinition
    ) -> tuple[PaymentMethod, PaymentRequest]:
        payment_method = self.rng.choice(definition.supported_methods)
        request = PaymentRequest(
            transaction_id=f"tx-{uuid4().hex[:16]}",
            merchant=self.rng.choice(MERCHANTS),
            payment_method=payment_method,
            region=self.rng.choice(REGIONS),
        )
        return payment_method, request

    async def send_one(
        self, definition: ProviderDefinition | None = None
    ) -> TrafficResult:
        definition = definition or self.select_provider()
        payment_method, request = self.build_request(definition)
        started = perf_counter()
        status_code: int | None = None
        response_code = "TRANSPORT_ERROR"

        try:
            response = await self.client.post(
                f"/providers/{definition.provider.value}/payments",
                json=request.model_dump(mode="json"),
                timeout=self.settings.request_timeout_seconds,
            )
            status_code = response.status_code
            payload = response.json()
            raw_outcome = payload.get("outcome", PaymentOutcome.PROVIDER_ERROR.value)
            response_code = str(payload.get("response_code", "UNKNOWN_RESPONSE"))[:48]
            outcome = (
                raw_outcome
                if raw_outcome in {item.value for item in PaymentOutcome}
                else PaymentOutcome.PROVIDER_ERROR.value
            )
        except httpx.TimeoutException:
            outcome = PaymentOutcome.TIMEOUT.value
            response_code = "PROVIDER_TIMEOUT"
            self.metrics.timeouts.labels(
                provider=definition.provider.value,
                payment_method=payment_method.value,
            ).inc()
        except (httpx.RequestError, ValueError):
            outcome = "transport-error"
            response_code = "TRANSPORT_ERROR"
            self.metrics.transport_errors.labels(
                provider=definition.provider.value,
                payment_method=payment_method.value,
            ).inc()

        duration_seconds = perf_counter() - started
        self.metrics.requests.labels(
            provider=definition.provider.value,
            payment_method=payment_method.value,
            outcome=outcome,
        ).inc()
        self.metrics.duration.labels(
            provider=definition.provider.value,
            payment_method=payment_method.value,
        ).observe(duration_seconds)

        return TrafficResult(
            provider=definition.provider,
            payment_method=payment_method,
            outcome=outcome,
            status_code=status_code,
            duration_seconds=duration_seconds,
            response_code=response_code,
            region=request.region,
        )

    async def _bounded_send(self) -> TrafficResult:
        async with self._semaphore:
            return await self.send_one()

    def _discard_task(self, task: asyncio.Task[TrafficResult]) -> None:
        self._in_flight.discard(task)
        if not task.cancelled():
            task.exception()

    async def run_traffic(self) -> None:
        while not self._stop.is_set():
            if self._enabled:
                task = asyncio.create_task(self._bounded_send())
                self._in_flight.add(task)
                task.add_done_callback(self._discard_task)
            interval = 1.0 / self._requests_per_second
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                pass

    async def check_provider_health(self, provider: ProviderId) -> bool:
        started = perf_counter()
        try:
            response = await self.client.get(
                f"/providers/{provider.value}/health",
                timeout=self.settings.healthcheck_timeout_seconds,
            )
            healthy = response.status_code == 200
        except (httpx.RequestError, httpx.TimeoutException):
            healthy = False

        duration_seconds = perf_counter() - started
        self.metrics.provider_health.labels(provider=provider.value).set(
            1 if healthy else 0
        )
        self.metrics.health_duration.labels(provider=provider.value).observe(
            duration_seconds
        )
        return healthy

    async def run_healthchecks(self) -> None:
        while not self._stop.is_set():
            await asyncio.gather(
                *(self.check_provider_health(provider) for provider in ProviderId)
            )
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.settings.healthcheck_interval_seconds,
                )
            except TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()
        if self._in_flight:
            await asyncio.gather(*self._in_flight, return_exceptions=True)
        if self._owns_client:
            await self.client.aclose()
