import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web
from dotenv import load_dotenv

from database import cleanup_old_data, close_pool, init_db
from handlers import categories, common, expenses, export, limits, menu, stats
from periods import run_period_checks

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

NETWORK_HINT = (
    "Не удалось подключиться к api.telegram.org.\n"
    "Проверьте интернет или сетевые ограничения. "
    "Для работы 24/7 бота лучше держать на Railway, а не на локальном ПК."
)


async def background_tasks(bot: Bot) -> None:
    while True:
        try:
            # Сначала отчёт/смена периода, потом очистка — чтобы не удалить данные
            # завершившегося периода до отправки отчёта.
            await run_period_checks(bot)
            await cleanup_old_data()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка в фоновых задачах")
        await asyncio.sleep(1800)


async def health_server() -> None:
    async def handle(request):
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/health", handle)
    port = int(os.getenv("PORT", "8080"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Health-сервер слушает порт %s", port)
    await asyncio.Event().wait()


async def main() -> None:
    await init_db()

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN не задан. Укажите его в .env или в Variables на Railway.")

    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(common.router)
    dp.include_router(menu.router)
    dp.include_router(expenses.router)
    dp.include_router(stats.router)
    dp.include_router(limits.router)
    dp.include_router(categories.router)
    dp.include_router(export.router)

    tasks = [
        asyncio.create_task(background_tasks(bot)),
        asyncio.create_task(health_server()),
    ]

    logger.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    except TelegramUnauthorizedError:
        logger.error("Telegram отклонил токен. Проверьте BOT_TOKEN (возможно, он отозван).")
        raise SystemExit(1)
    except TelegramNetworkError as error:
        logger.error("%s\nДетали: %s", NETWORK_HINT, error)
        raise SystemExit(1)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await bot.session.close()
        await close_pool()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
