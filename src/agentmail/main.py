from __future__ import annotations

import uvicorn

from agentmail.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run("agentmail.api:app", host=settings.bind_host, port=settings.bind_port)


if __name__ == "__main__":
    main()
