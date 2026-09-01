import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "apm_demo.provider_emulator.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )
