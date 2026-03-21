"""Watchdog supervisor — spawns and monitors main.py, restarts on crash."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HEARTBEAT_KEY = "crypto:heartbeat"
HEALTH_CHECK_INTERVAL = 15
HEARTBEAT_STALE_SEC = 90
MAX_BACKOFF_SEC = 300
BACKOFF_STEPS = [5, 15, 60, 120, 300]

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    print(f"[supervisor] Received signal {signum}, shutting down...")
    _shutdown = True


async def send_telegram(message: str) -> None:
    """Best-effort Telegram notification from supervisor."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            )
    except Exception as e:
        print(f"[supervisor] Telegram send failed: {e}")


async def check_heartbeat() -> bool:
    """Check if main process heartbeat is fresh in Redis."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(redis_url, decode_responses=True)
        val = await r.get(HEARTBEAT_KEY)
        await r.aclose()
        if val is None:
            return False
        hb_time = datetime.fromisoformat(val)
        age = (datetime.now(timezone.utc) - hb_time).total_seconds()
        return age < HEARTBEAT_STALE_SEC
    except Exception as e:
        print(f"[supervisor] Redis heartbeat check failed: {e}")
        return False


async def run_supervisor() -> None:
    global _shutdown
    restart_count = 0
    main_script = str(Path(__file__).parent / "main.py")

    print(f"[supervisor] Starting watchdog for {main_script}")
    await send_telegram("🔧 *Supervisor Started* — monitoring crypto trading service")

    while not _shutdown:
        backoff = BACKOFF_STEPS[min(restart_count, len(BACKOFF_STEPS) - 1)]
        print(f"[supervisor] Spawning main.py (attempt #{restart_count + 1})")

        proc = subprocess.Popen(
            [sys.executable or "python3", main_script],
            cwd=str(Path(__file__).parent),
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

        stale_count = 0

        while not _shutdown:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)

            if proc.poll() is not None:
                exit_code = proc.returncode
                print(f"[supervisor] main.py exited with code {exit_code}")
                await send_telegram(
                    f"🚨 *Service Crashed* (exit code: {exit_code})\n"
                    f"Restarting in {backoff}s (attempt #{restart_count + 2})"
                )
                break

            hb_ok = await check_heartbeat()
            if hb_ok:
                stale_count = 0
                if restart_count > 0:
                    restart_count = max(0, restart_count - 1)
            else:
                stale_count += 1
                print(f"[supervisor] Heartbeat stale ({stale_count}x)")
                if stale_count >= 3:
                    print("[supervisor] Killing unresponsive process")
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    await send_telegram(
                        f"🚨 *Service Unresponsive* (no heartbeat for {stale_count * HEALTH_CHECK_INTERVAL}s)\n"
                        f"Restarting in {backoff}s"
                    )
                    break

        if _shutdown:
            if proc.poll() is None:
                print("[supervisor] Terminating main process...")
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
            break

        restart_count += 1
        print(f"[supervisor] Waiting {backoff}s before restart...")
        for _ in range(backoff):
            if _shutdown:
                break
            await asyncio.sleep(1)

    await send_telegram("🛑 *Supervisor Stopped*")
    print("[supervisor] Exiting")


def main() -> None:
    # Load .env.local for the supervisor process
    env_path = Path(__file__).parent.parent / ".env.local"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    asyncio.run(run_supervisor())


if __name__ == "__main__":
    main()
