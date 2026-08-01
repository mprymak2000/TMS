import asyncio
import sys
from tasks import app

# Windows uses ProactorEventLoop by default, which psycopg3 can't use.
# SelectorEventLoop is compatible. This line is a no-op on Linux/Mac in production.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def main():
    # open_async() opens the psycopg3 connection pool to Postgres.
    # run_worker_async() starts two loops:
    #   - scheduler loop: checks cron expressions every minute, inserts job rows when due
    #   - execution loop: polls procrastinate_jobs for pending rows, runs the task function
    #     in a thread pool (so sync task functions don't block the async loop),
    #     then marks the row succeeded or failed. Failed jobs are retried with backoff.
    async with app.open_async():
        await app.run_worker_async()


if __name__ == "__main__":
    # To apply Procrastinate schema on a fresh DB (one-time, after docker compose up):
    # $env:PYTHONPATH = "."
    # procrastinate --app tasks:app schema --apply
    asyncio.run(main())
