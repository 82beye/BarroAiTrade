
import asyncio
import os
import sys

# Add the project root to the sys.path to allow imports from backend and scripts
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__))))

from scripts.finance.telegram_integration import scheduler

async def main():
    print("Attempting to send daily report now...")
    await scheduler.send_report_now()
    print("Daily report send attempt completed.")

if __name__ == "__main__":
    asyncio.run(main())
