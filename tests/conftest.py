from __future__ import annotations

import os


# Unit and integration tests inject local analyzers or exercise catalog-only paths.
# This value satisfies startup validation without authorizing an external request.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-local-only-do-not-use-000000000000")
