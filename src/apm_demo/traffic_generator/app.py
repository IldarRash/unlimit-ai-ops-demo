from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field

from apm_demo.traffic_generator.config import GeneratorSettings
from apm_demo.traffic_generator.generator import TrafficGenerator, TrafficResult
from apm_demo.traffic_generator.metrics import ClientMetrics


class GeneratorControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    requests_per_second: float = Field(gt=0, le=100)


class GeneratorControlPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    requests_per_second: float | None = Field(default=None, gt=0, le=100)


def create_app(
    *,
    settings: GeneratorSettings | None = None,
    metrics: ClientMetrics | None = None,
    generator: TrafficGenerator | None = None,
    start_background: bool = True,
) -> FastAPI:
    settings = settings or GeneratorSettings()
    metrics = metrics or ClientMetrics()
    generator = generator or TrafficGenerator(settings, metrics)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        tasks: list[asyncio.Task[None]] = []
        if start_background:
            tasks = [
                asyncio.create_task(generator.run_traffic()),
                asyncio.create_task(generator.run_healthchecks()),
            ]
        yield
        await generator.stop()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(
        title="Synthetic APM Traffic Generator",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.generator = generator
    app.state.metrics = metrics

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "traffic-generator"}

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        return Response(
            content=generate_latest(metrics.registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    @app.get("/admin/generator", response_model=GeneratorControl)
    async def get_control() -> GeneratorControl:
        return GeneratorControl(
            enabled=generator.enabled,
            requests_per_second=generator.requests_per_second,
        )

    @app.patch("/admin/generator", response_model=GeneratorControl)
    async def patch_control(patch: GeneratorControlPatch) -> GeneratorControl:
        await generator.update_control(
            enabled=patch.enabled,
            requests_per_second=patch.requests_per_second,
        )
        return GeneratorControl(
            enabled=generator.enabled,
            requests_per_second=generator.requests_per_second,
        )

    @app.post("/admin/tick")
    async def send_single_request() -> dict[str, str | int | float | None]:
        result: TrafficResult = await generator.send_one()
        return {
            "provider": result.provider.value,
            "payment_method": result.payment_method.value,
            "outcome": result.outcome,
            "status_code": result.status_code,
            "duration_seconds": result.duration_seconds,
            "response_code": result.response_code,
            "region": result.region,
        }

    return app


app = create_app()
