"""
Claude Overlay Service.

Runs once per day, calls Claude API with web_search for each pair,
writes structured results to overlay_log table.

Architecture:
  - APScheduler triggers at 06:00 UTC daily
  - For each pair: ask Claude to assess news/sentiment
  - Write result → DB
  - Deterministic logic in strategy reads DB (no AI in the hot path)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import anthropic
import asyncpg
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

PAIRS = [
    "BTC/USDC",
    "ETH/USDC",
    "SOL/USDC",
    "BNB/USDC",
    "AVAX/USDC",
]

SYSTEM_PROMPT = """You are a risk assessment overlay for a cryptocurrency trading system.
Your task: evaluate whether there are any significant news events, anomalies, or sentiment shifts
for the given trading pair that a price-only ML model would not detect.

Focus on:
- Protocol hacks, exploits, or security incidents
- Regulatory actions, investigations, or exchange delistings
- Major negative (or positive) narrative shifts
- Unusual on-chain activity or liquidity events

You have access to web_search. Use it to find the latest news from the past 24 hours.

Return ONLY a JSON object matching this exact schema — no prose, no markdown:
{
  "pair": "<symbol>",
  "sentiment": <float -1.0 to 1.0>,
  "anomaly_flag": <boolean>,
  "anomaly_reason": <string or null>,
  "confidence": <float 0.0 to 1.0>
}

Rules:
- anomaly_flag = true ONLY for hacks, delistings, regulatory bans, or comparable hard events
- sentiment: -1.0 extremely negative, 0.0 neutral, 1.0 extremely positive
- If you found no relevant news, return sentiment=0.0, anomaly_flag=false, confidence=0.3
- Do NOT set anomaly_flag=true for general bearish sentiment or price drops
"""


async def assess_pair(client: anthropic.AsyncAnthropic, pair: str) -> dict:
    """Call Claude with web_search to assess a trading pair."""
    base_asset = pair.split("/")[0]
    user_msg = (
        f"Assess the current risk and sentiment for {pair} ({base_asset} cryptocurrency). "
        f"Search for significant news from the past 24 hours about {base_asset}. "
        f"Pay special attention to security incidents, exchange actions, and regulatory news."
    )

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": user_msg}],
        )

        # Extract the final text block (after tool use)
        text_content = ""
        for block in response.content:
            if hasattr(block, "text"):
                text_content = block.text

        result = json.loads(text_content)
        result["pair"] = pair  # ensure pair is set correctly
        return result

    except json.JSONDecodeError as exc:
        logger.error("Claude returned non-JSON for %s: %s", pair, exc)
        return _fallback(pair)
    except Exception as exc:
        logger.error("Claude API error for %s: %s", pair, exc)
        return _fallback(pair)


def _fallback(pair: str) -> dict:
    """Fail-safe: no overlay effect when API is unavailable."""
    return {
        "pair": pair,
        "sentiment": 0.0,
        "anomaly_flag": False,
        "anomaly_reason": None,
        "confidence": 0.0,
    }


def _determine_action(result: dict, sentiment_threshold: float) -> str:
    if result["anomaly_flag"]:
        return "veto"
    if result["sentiment"] < sentiment_threshold:
        return "reduce_50"
    return "pass"


async def run_overlay(pool: asyncpg.Pool) -> None:
    sentiment_threshold = float(os.environ.get("OVERLAY_SENTIMENT_THRESHOLD", "-0.5"))

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set — skipping overlay run")
        return

    client = anthropic.AsyncAnthropic(api_key=api_key)
    logger.info("Starting overlay run for %d pairs", len(PAIRS))

    for pair in PAIRS:
        result = await assess_pair(client, pair)
        action = _determine_action(result, sentiment_threshold)

        await pool.execute(
            """INSERT INTO overlay_log
               (pair, sentiment, anomaly_flag, anomaly_reason, confidence, action, raw_response)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            pair,
            result.get("sentiment", 0.0),
            result.get("anomaly_flag", False),
            result.get("anomaly_reason"),
            result.get("confidence", 0.0),
            action,
            json.dumps(result),
        )

        if action == "veto":
            logger.warning("VETO for %s: %s", pair, result.get("anomaly_reason"))
        elif action == "reduce_50":
            logger.info("Size reduced 50%% for %s (sentiment=%.2f)", pair, result.get("sentiment", 0))
        else:
            logger.info("PASS for %s (sentiment=%.2f)", pair, result.get("sentiment", 0))

        # Small delay between API calls to avoid rate limits
        await asyncio.sleep(2)


async def main() -> None:
    if os.environ.get("CLAUDE_OVERLAY_ENABLED", "false").lower() != "true":
        logger.info("Claude overlay is disabled (CLAUDE_OVERLAY_ENABLED != true). Sleeping indefinitely.")
        while True:
            await asyncio.sleep(3600)

    pool = await asyncpg.create_pool(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "trader"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        database=os.environ.get("POSTGRES_DB", "trader_v5"),
        min_size=1,
        max_size=3,
    )

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(run_overlay, "cron", hour=6, minute=0, args=[pool])
    scheduler.start()

    logger.info("Claude overlay scheduler started — runs daily at 06:00 UTC")

    # Also run immediately on startup (for dry-run / forward-observation mode)
    observe_only = os.environ.get("OVERLAY_OBSERVE_ONLY", "false").lower() == "true"
    if observe_only:
        logger.info("OBSERVE_ONLY mode: overlay results logged but NOT applied to trading")
    await run_overlay(pool)

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
