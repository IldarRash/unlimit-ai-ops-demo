from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


PRESETS = {
    "provider-status": (
        "Provider status page reports degradation",
        "The provider reports degraded payment processing on its public status page.",
    ),
    "support-ticket": (
        "Support ticket volume is elevated",
        "Sanitized support aggregation reports repeated payment failures for this provider.",
    ),
    "slack-escalation": (
        "Operations channel escalation",
        "A sanitized operations escalation reports provider-specific payment failures.",
    ),
    "email-escalation": (
        "Email escalation from payment operations",
        "A sanitized email summary reports a provider-specific degradation pattern.",
    ),
    "merchant-complaint": (
        "Merchant complaints are elevated",
        "Aggregated merchant reports indicate an unusual provider-specific failure pattern.",
    ),
    "operations-report": (
        "Operations report confirms degradation",
        "A sanitized manual operations check confirms symptoms seen in monitoring.",
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send one sanitized external signal to the incident pipeline."
    )
    parser.add_argument("--base-url", default="http://localhost:8002")
    parser.add_argument("--provider", required=True, choices=("atlas-pay", "nova-bank", "orbit-wallet"))
    parser.add_argument("--type", required=True, choices=tuple(PRESETS))
    parser.add_argument("--title")
    parser.add_argument("--summary")
    parser.add_argument("--source-ref", default="demo://external-signal")
    parser.add_argument("--severity", default="warning", choices=("info", "warning", "critical"))
    parser.add_argument("--confidence", type=float, default=0.8)
    parser.add_argument("--reported-count", type=int, default=1)
    parser.add_argument("--region", choices=("BR", "NL", "GB", "DE", "PL"))
    parser.add_argument("--token-file", type=Path)
    return parser.parse_args()


def load_token(path: Path | None) -> str:
    token = os.environ.get("APM_INCIDENT_EXTERNAL_SIGNAL_TOKEN", "").strip()
    if not token and path is not None:
        token = path.read_text(encoding="utf-8").strip()
    if len(token) < 20:
        raise SystemExit(
            "Provide APM_INCIDENT_EXTERNAL_SIGNAL_TOKEN or --token-file with at least 20 characters."
        )
    return token


def main() -> int:
    args = parse_args()
    token = load_token(args.token_file)
    default_title, default_summary = PRESETS[args.type]
    payload = {
        "signal_id": f"ext_{uuid4().hex}",
        "provider": args.provider,
        "signal_type": args.type,
        "title": args.title or default_title,
        "summary": args.summary or default_summary,
        "source_ref": args.source_ref,
        "severity": args.severity,
        "confidence": args.confidence,
        "reported_count": args.reported_count,
        "region": args.region,
        "contains_customer_data": False,
    }
    request = Request(
        f"{args.base_url.rstrip('/')}/api/v1/external-signals",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read())
    except HTTPError as error:
        raise SystemExit(f"Incident API rejected the signal with HTTP {error.code}.") from error
    except URLError as error:
        raise SystemExit("Incident API is unavailable.") from error
    print(
        json.dumps(
            {
                "accepted": True,
                "signal_id": result["signal_id"],
                "provider": result["provider"],
                "signal_type": result["signal_type"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
