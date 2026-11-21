# -*- coding: utf-8 -*-
import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

from aiogram import Bot, Dispatcher
from aiogram.types import FSInputFile

import config
import db
import handlers

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
MSK = timezone(timedelta(hours=3))


def _resolve_token() -> str:
    for attr in ("TOKEN", "BOT_TOKEN", "TG_TOKEN", "API_TOKEN"):
        val = getattr(config, attr, "")
        if val:
            return str(val).strip()
    raise RuntimeError("Не найден токен бота в config.py")


async def sender_loop(bot: Bot):
    while True:
        try:
            pending = await asyncio.to_thread(db.notifications_pending, 30)
            if pending:
                sent_ids = []
                for row in pending:
                    nid = int(row.get("id") or 0)
                    uid = int(row.get("user_id") or 0)
                    sid = str(row.get("store_id") or "")
                    silent = int(row.get("silent") or 0) == 1
                    tag = str(row.get("tag") or "")
                    user = db.get_user(uid) or {}
                    role = user.get("role") or "user"
                    item = {
                        "plu": row.get("plu"),
                        "name": row.get("name"),
                        "regular": row.get("reg"),
                        "price": row.get("price"),
                        "pct": row.get("pct"),
                        "stock": row.get("stock"),
                        "img_url": row.get("img_url"),
                        "addr": row.get("addr"),
                    }
                    try:
                        await handlers.send_product_card(
                            bot=bot, chat_id=uid, store_id=sid,
                            item=item, role=role, tag=tag, silent=silent,
                        )
                    except Exception as e:
                        logging.warning("Ошибка отправки %s: %s", nid, e)
                    sent_ids.append(nid)
                    await asyncio.sleep(0.15)
                await asyncio.to_thread(db.notifications_mark_sent, sent_ids)
                await asyncio.to_thread(db.notifications_trim, 3000)
        except Exception as e:
            logging.error("Ошибка sender_loop: %s", e)
        await asyncio.sleep(5)


async def auto_backup_loop(bot: Bot):
    last_day = ""
    admin_id = int(getattr(config, "ADMIN_ID", 0))
    while True:
        try:
            now_msk = datetime.now(MSK)
            day_key = now_msk.strftime("%Y-%m-%d")
            if now_msk.hour == 3 and now_msk.minute == 30 and last_day != day_key and admin_id:
                last_day = day_key
                zip_path = await asyncio.to_thread(handlers.make_zip_backup)
                try:
                    await bot.send_document(admin_id, document=FSInputFile(zip_path), caption="📦 Автобэкап")
                finally:
                    if os.path.exists(zip_path):
                        os.remove(zip_path)
        except Exception as e:
            logging.error("Ошибка автобэкапа: %s", e)
        await asyncio.sleep(25)


async def main():
    db.init_db()
    bot = Bot(token=_resolve_token())
    dp = Dispatcher()
    dp.include_router(handlers.router)
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(sender_loop(bot))
    asyncio.create_task(auto_backup_loop(bot))
    logging.info("Бот 5ka запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

