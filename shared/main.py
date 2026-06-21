"""Shared service entrypoint: runs circuit breaker + equity tracker concurrently."""
import asyncio
import logging

from circuit_breaker import monitor_loop
from equity_tracker import snapshot_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


async def main() -> None:
    await asyncio.gather(monitor_loop(), snapshot_loop())


if __name__ == "__main__":
    asyncio.run(main())
