"""
Main entry point for TG Monitor Pro.
Runs the FastAPI server + Telegram Bot concurrently.
"""
import asyncio
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Ensure we're running from the right directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def run_api(port: int):
    import uvicorn
    from tgbot.app.api.main import app
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    logger.info(f"Starting API server on port {port}")
    await server.serve()

async def run_bot():
    from aiogram import Bot, Dispatcher
    from aiogram.fsm.storage.memory import MemoryStorage
    from tgbot.app.core.config import settings

    if not settings.BOT_TOKEN:
        logger.error("BOT_TOKEN is not set! Bot will not start.")
        return

    logger.info("Starting Telegram Bot...")

    try:
        import importlib
        bot_module = importlib.import_module("tgbot.app.bot.main")

        bot = Bot(token=settings.BOT_TOKEN)
        dp = Dispatcher(storage=MemoryStorage())
        dp.include_router(bot_module.router)

        logger.info("Bot started successfully!")
        await dp.start_polling(bot, allowed_updates=["message", "callback_query", "pre_checkout_query"])
    except Exception as e:
        logger.error(f"Bot error: {e}", exc_info=True)

async def run_listener():
    from tgbot.app.listener.main import MonitoringService
    logger.info("Starting Monitoring Listener...")
    try:
        service = MonitoringService()
        await service.run()
    except Exception as e:
        logger.error(f"Listener error: {e}", exc_info=True)

async def init_database():
    logger.info("Initializing database...")
    try:
        from tgbot.app.core.init_db import init_db
        await init_db()
        logger.info("Database initialized successfully!")
    except Exception as e:
        logger.error(f"Database init error: {e}", exc_info=True)

async def main():
    # Init DB first
    await init_database()

    # Primary port from environment (default 8000)
    primary_port = int(os.environ.get("PORT", 8000))

    # Also bind on port 8081 for Replit's external port 80 mapping
    tasks = [
        run_api(primary_port),
        run_bot(),
        run_listener(),
    ]
    if primary_port != 8081:
        tasks.append(run_api(8081))

    await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == "__main__":
    asyncio.run(main())
