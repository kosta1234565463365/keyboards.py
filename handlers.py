# -*- coding: utf-8 -*-
import asyncio
import html
import io
import os
import subprocess
import zipfile
from datetime import datetime, timezone, timedelta

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, BufferedInputFile, FSInputFile

import config
import db
import keyboards as kb

router = Router()
MSK = timezone(timedelta(hours=3))
PAGE_SIZE = 10


def _esc(s) -> str:
    return html.escape(str(s or ""))


def _fmt_price(val) -> str:
    v = float(val or 0)
    return f"{int(v)}" if v.is_integer() else f"{v:.2f}"


def _build_home_text(user: dict) -> str:
    role = (user or {}).get("role") or "user"
    role_names = {"admin": "👑 Администратор", "tester": "🕵️ Тестировщик", "user": "👤 Подписчик"}
    pct = int((user or {}).get("min_pct") or 70)
    max_p = float((user or {}).get("max_price") or 0)
    max_txt = f"до {_fmt_price(max_p)} ₽" if max_p > 0 else "выключено"
    uid = user.get("user_id")
    lines = [
        "🛒 <b>Монитор скидок «Пятёрочки»</b>",
        f"Статус: <b>{role_names.get(role, role)}</b>",
        "",
        f"🎯 <b>Режим 1 (Радар):</b> {len(db.radar_list(uid))} поз. (от 5% скидки)",
        f"📉 <b>Режим 2 (Порог скидки):</b> от <b>{pct}%</b>",
        f"💰 <b>Режим 3 (До N ₽):</b> <b>{max_txt}</b>",
        f"🏪 <b>Выбрано магазинов:</b> {len(db.user_store_ids(uid))}",
        f"📂 <b>Отключено категорий:</b> {len(db.get_off_cats(uid))} | 🚫 <b>Стоп-слов:</b> {len(db.get_stop_words(uid))}",
    ]
    if role == "admin" and db.sandbox_on():
        lines.append("\n🛸 <b>Режим «2-е пространство» ВКЛЮЧЕН</b> (уведомления идут только вам с тегом [ТЕСТ]).")
    return "\n".join(lines)


async def _send_fresh_panel(bot: Bot, chat_id: int, user_id: int, text: str, markup):
    old_id = db.get_panel_msg(user_id)
    if old_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_id)
        except Exception:
            pass
    msg = await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup, parse_mode="HTML")
    db.set_panel_msg(user_id, msg.message_id)


async def _edit_panel(cb: CallbackQuery, text: str, markup):
    try:
        await cb.message.edit_text(text=text, reply_markup=markup, parse_mode="HTML")
        db.set_panel_msg(cb.from_user.id, cb.message.message_id)
    except Exception:
        await _send_fresh_panel(cb.bot, cb.message.chat.id, cb.from_user.id, text, markup)
def _get_db_extra_cats() -> list:
    with db.connect() as c:
        rows = c.execute("SELECT DISTINCT cat_name FROM prices WHERE COALESCE(cat_name,'') != '' ORDER BY cat_name").fetchall()
        return [r["cat_name"] for r in rows]


def _render_barcode_png(plu: str) -> bytes:
    import barcode
    from barcode.writer import ImageWriter
    code128 = barcode.get("code128", str(plu), writer=ImageWriter())
    buf = io.BytesIO()
    code128.write(buf, options={"module_height": 12.0, "font_size": 10, "text_distance": 4.0, "quiet_zone": 3.0})
    return buf.getvalue()


def format_product_caption(item: dict, store_id: str, addr: str, tag: str = "", honest_reg: float = 0.0) -> str:
    name = _esc(item.get("name") or "Товар")
    plu = _esc(item.get("plu") or "")
    prc = float(item.get("price") or 0)
    reg = float(honest_reg or item.get("regular") or 0)
    stock = float(item.get("stock") or 0)
    pct = int(item.get("pct") or (round((reg - prc) * 100.0 / reg) if reg > prc > 0 else 0))
    header = f"<b>{_esc(tag)}</b>\n" if tag else f"🔥 <b>Скидка -{pct}%</b>\n"
    stk_str = f"{int(stock)} шт." if stock.is_integer() else f"{stock:.1f} кг/шт."
    warn = "\n⚠️ <b>Минимальный остаток!</b>" if 0 < stock <= 2 else ""
    return "\n".join([
        f"{header}🛍 <b>{name}</b>",
        "",
        f"💸 Цена по акции: <b>{_fmt_price(prc)} ₽</b>",
        f"🏷 Честная регулярная: <s>{_fmt_price(reg)} ₽</s> (-{pct}%)",
        f"📦 Остаток в зале: <b>{stk_str}</b>{warn}",
        f"🔢 Артикул (PLU): <code>{plu}</code>",
        f"🏪 Магазин: <b>[{_esc(store_id)}]</b> {_esc(addr)}",
    ])


async def send_product_card(bot: Bot, chat_id: int, store_id: str, item: dict, role: str, tag: str = "", back_cb: str = "", silent: bool = False):
    st = db.get_store(store_id) or {}
    addr = st.get("addr") or item.get("addr") or store_id
    plu = str(item.get("plu") or "")
    honest_reg = await asyncio.to_thread(db.honest_regular, store_id, plu)
    caption = format_product_caption(item, store_id, addr, tag=tag, honest_reg=honest_reg)
    markup = kb.kb_card(store_id, plu, can_check=(role in ("admin", "tester")), back_cb=back_cb)
    img_url = str(item.get("img_url") or "").strip()
    if img_url.startswith("http"):
        try:
            await bot.send_photo(chat_id=chat_id, photo=img_url, caption=caption, reply_markup=markup, parse_mode="HTML", disable_notification=silent)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=chat_id, text=caption, reply_markup=markup, parse_mode="HTML", disable_notification=silent)


async def _load_store_deals(store_id: str, min_pct: int = 25) -> list:
    items = await asyncio.to_thread(db.latest_items, store_id, 12, True)
    deals = []
    for it in items:
        reg, prc = float(it.get("regular") or 0), float(it.get("price") or 0)
        if reg > prc > 0:
            pct = int(round((reg - prc) * 100.0 / reg))
            if pct >= min_pct:
                c_it = dict(it)
                c_it["pct"] = pct
                deals.append(c_it)
    deals.sort(key=lambda x: (-int(x.get("pct") or 0), float(x.get("price") or 0)))
    return deals[:80]
def _render_slice_text(store: dict, items_page: list, page: int, total_items: int, checked_set: set, is_field: bool) -> str:
    sid = store.get("store_id") or ""
    addr = store.get("addr") or sid
    title = "🧪 <b>Полевой тест в зале</b>" if is_field else "📍 <b>Я у магазина (актуальный срез)</b>"
    lines = [
        f"{title}",
        f"🏪 <b>[{_esc(sid)}] {_esc(addr)}</b>",
        f"Найдено позиций со скидкой: <b>{total_items}</b> (стр. {page + 1})",
        "<i>Нажмите на номер товара ниже, чтобы открыть карточку и штрихкод:</i>",
        "",
    ]
    for i, it in enumerate(items_page, start=1):
        mark = "✅ " if str(it.get("plu") or "") in checked_set else ""
        lines.append(f"{mark}<b>{i}.</b> {_esc((it.get('name') or '')[:42])} — <b>{_fmt_price(it.get('price'))}₽</b> (<s>{_fmt_price(it.get('regular'))}₽</s>, -{int(it.get('pct') or 0)}%)")
    return "\n".join(lines)


def make_zip_backup() -> str:
    ts_str = datetime.now(MSK).strftime("%Y%m%d_%H%M%S")
    zip_path, tmp_db = f"/tmp/5ka_backup_{ts_str}.zip", f"/tmp/5ka_data_{ts_str}.db"
    db.backup_to(tmp_db)
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_db, arcname="5ka_data.db")
            for fname in ("config.py", "collector.py", "db.py", "keyboards.py", "handlers.py", "bot.py"):
                fpath = os.path.join("/opt/5ka_bot", fname)
                if os.path.exists(fpath):
                    zf.write(fpath, arcname=fname)
    finally:
        if os.path.exists(tmp_db):
            os.remove(tmp_db)
    return zip_path


@router.message(CommandStart())
async def cmd_start(msg: Message):
    user = db.ensure_user(msg.from_user.id, msg.from_user.username or "")
    db.state_clear(msg.from_user.id)
    try:
        await msg.delete()
    except Exception:
        pass
    await _send_fresh_panel(msg.bot, msg.chat.id, msg.from_user.id, _build_home_text(user), kb.kb_home(user, db.sandbox_on()))


@router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


@router.callback_query(F.data == "nav:home")
async def cb_home(cb: CallbackQuery):
    user = db.ensure_user(cb.from_user.id, cb.from_user.username or "")
    db.state_clear(cb.from_user.id)
    await _edit_panel(cb, _build_home_text(user), kb.kb_home(user, db.sandbox_on()))
    await cb.answer()


@router.callback_query(F.data == "menu:pct")
async def cb_menu_pct(cb: CallbackQuery):
    user = db.ensure_user(cb.from_user.id, cb.from_user.username or "")
    cur = int(user.get("min_pct") or 70)
    await _edit_panel(cb, f"📉 <b>Режим 2: Порог скидки</b>\nТекущий порог: <b>{cur}%</b>\n\nВыберите минимальный процент скидки или введите свой:", kb.kb_pct(cur))
    await cb.answer()
@router.callback_query(F.data.startswith("set:pct:"))
async def cb_set_pct(cb: CallbackQuery):
    val = int(cb.data.rsplit(":", 1).pop())
    db.set_min_pct(cb.from_user.id, val)
    user = db.get_user(cb.from_user.id)
    await _edit_panel(cb, _build_home_text(user), kb.kb_home(user, db.sandbox_on()))
    await cb.answer(f"Порог скидки установлен: {val}%")


@router.callback_query(F.data == "ask:pct")
async def cb_ask_pct(cb: CallbackQuery):
    db.state_set(cb.from_user.id, "wait_pct")
    await _edit_panel(cb, "✏️ Отправьте в чат целое число от <b>1</b> до <b>99</b> (процент скидки):", kb.kb_back("menu:pct", "❌ Отмена"))
    await cb.answer()


@router.callback_query(F.data == "menu:maxp")
async def cb_menu_maxp(cb: CallbackQuery):
    user = db.ensure_user(cb.from_user.id, cb.from_user.username or "")
    cur = float(user.get("max_price") or 0)
    cur_txt = f"{_fmt_price(cur)} ₽" if cur > 0 else "выключено"
    await _edit_panel(cb, f"💰 <b>Режим 3: Товары до N ₽</b>\nТекущий лимит: <b>{cur_txt}</b>", kb.kb_max_price(cur))
    await cb.answer()


@router.callback_query(F.data.startswith("set:maxp:"))
async def cb_set_maxp(cb: CallbackQuery):
    val = float(cb.data.rsplit(":", 1).pop())
    db.set_max_price(cb.from_user.id, val)
    user = db.get_user(cb.from_user.id)
    await _edit_panel(cb, _build_home_text(user), kb.kb_home(user, db.sandbox_on()))
    await cb.answer("Режим выключен" if val <= 0 else f"Лимит цены: до {_fmt_price(val)} ₽")


@router.callback_query(F.data == "ask:maxp")
async def cb_ask_maxp(cb: CallbackQuery):
    db.state_set(cb.from_user.id, "wait_maxp")
    await _edit_panel(cb, "✏️ Введите максимальную цену в рублях (например, <code>45</code>):", kb.kb_back("menu:maxp", "❌ Отмена"))
    await cb.answer()


@router.callback_query(F.data.startswith("menu:cats:"))
async def cb_menu_cats(cb: CallbackQuery):
    page = int(cb.data.rsplit(":", 1).pop())
    off = db.get_off_cats(cb.from_user.id)
    extra = await asyncio.to_thread(_get_db_extra_cats)
    await _edit_panel(cb, "📂 <b>Фильтр категорий</b>\n✅ — присылать | ❌ — скрыть", kb.kb_cats(page, off, extra))
    await cb.answer()


@router.callback_query(F.data.startswith("tog:cat:"))
async def cb_tog_cat(cb: CallbackQuery):
    parts = cb.data.split(":")
    idx, page = int(parts.pop()), int(parts.pop())
    extra = await asyncio.to_thread(_get_db_extra_cats)
    cat_name = kb.get_cat_by_index(page, idx, extra)
    if cat_name:
        db.toggle_off_cat(cb.from_user.id, cat_name)
    await _edit_panel(cb, "📂 <b>Фильтр категорий</b>\n✅ — присылать | ❌ — скрыть", kb.kb_cats(page, db.get_off_cats(cb.from_user.id), extra))
    await cb.answer()


@router.callback_query(F.data.startswith("cats:all_on:"))
async def cb_cats_all_on(cb: CallbackQuery):
    page = int(cb.data.rsplit(":", 1).pop())
    db.set_off_cats(cb.from_user.id, [])
    extra = await asyncio.to_thread(_get_db_extra_cats)
    await _edit_panel(cb, "📂 <b>Фильтр категорий</b>\n✅ Все категории включены!", kb.kb_cats(page, [], extra))
    await cb.answer("Все категории включены")
@router.callback_query(F.data == "menu:stores")
async def cb_menu_stores(cb: CallbackQuery):
    stores, sel = db.list_stores(active_only=True), set(db.user_store_ids(cb.from_user.id))
    await _edit_panel(cb, f"🏪 <b>Мои магазины</b> (выбрано: <b>{len(sel)}</b>):", kb.kb_user_stores(stores, sel))
    await cb.answer()


@router.callback_query(F.data.startswith("tog:ustore:"))
async def cb_tog_ustore(cb: CallbackQuery):
    db.toggle_user_store(cb.from_user.id, cb.data.rsplit(":", 1).pop())
    stores, sel = db.list_stores(active_only=True), set(db.user_store_ids(cb.from_user.id))
    await _edit_panel(cb, f"🏪 <b>Мои магазины</b> (выбрано: <b>{len(sel)}</b>):", kb.kb_user_stores(stores, sel))
    await cb.answer()


@router.callback_query(F.data == "menu:radar")
async def cb_menu_radar(cb: CallbackQuery):
    items = db.radar_list(cb.from_user.id)
    await _edit_panel(cb, f"🎯 <b>Режим 1: Радар</b> (от 5% скидки)\nАктивных позиций: <b>{len(items)}</b>:", kb.kb_radar(items))
    await cb.answer()


@router.callback_query(F.data == "ask:radar")
async def cb_ask_radar(cb: CallbackQuery):
    db.state_set(cb.from_user.id, "wait_radar")
    await _edit_panel(cb, "🎯 Введите <b>название товара</b> или артикул <b>PLU</b>:", kb.kb_back("menu:radar", "❌ Отмена"))
    await cb.answer()


@router.callback_query(F.data.startswith("del:radar:"))
async def cb_del_radar(cb: CallbackQuery):
    db.radar_remove(cb.from_user.id, int(cb.data.rsplit(":", 1).pop()))
    items = db.radar_list(cb.from_user.id)
    await _edit_panel(cb, f"🎯 <b>Режим 1: Радар</b>\nАктивных позиций: <b>{len(items)}</b>:", kb.kb_radar(items))
    await cb.answer("Удалено")


@router.callback_query(F.data == "menu:stop")
async def cb_menu_stop(cb: CallbackQuery):
    words = db.get_stop_words(cb.from_user.id)
    w_str = ", ".join(f"<code>{_esc(w)}</code>" for w in words) if words else "<i>пусто</i>"
    await _edit_panel(cb, f"🚫 <b>Стоп-слова</b>: {w_str}", kb.kb_stop_words(words))
    await cb.answer()


@router.callback_query(F.data == "ask:stop")
async def cb_ask_stop(cb: CallbackQuery):
    db.state_set(cb.from_user.id, "wait_stop")
    await _edit_panel(cb, "🚫 Введите стоп-слово (или несколько через запятую):", kb.kb_back("menu:stop", "❌ Отмена"))
    await cb.answer()


@router.callback_query(F.data == "clr:stop")
async def cb_clr_stop(cb: CallbackQuery):
    db.clear_stop_words(cb.from_user.id)
    await _edit_panel(cb, "🚫 <b>Стоп-слова</b>: <i>пусто</i>", kb.kb_stop_words([]))
    await cb.answer("Очищено")


@router.callback_query(F.data == "menu:tariffs")
async def cb_tariffs(cb: CallbackQuery):
    await _edit_panel(cb, "💎 <b>Тарифы</b>\n• Старт — 1 точка\n• Базовый — до 15 точек\n• Расширенный — до 40 точек", kb.kb_back("nav:home"))
    await cb.answer()


@router.callback_query(F.data == "menu:here")
async def cb_menu_here(cb: CallbackQuery):
    await _edit_panel(cb, "📍 <b>Я у магазина (срез)</b>\nВыберите магазин:", kb.kb_select_store(db.list_stores(active_only=True), "here:st"))
    await cb.answer()


@router.callback_query(F.data == "adm:field")
async def cb_adm_field(cb: CallbackQuery):
    await _edit_panel(cb, "🧪 <b>Полевой тест</b>\nВыберите магазин:", kb.kb_select_store(db.list_stores(active_only=True), "fld:st"))
    await cb.answer()
async def _show_slice_page(cb: CallbackQuery, store_id: str, page: int, is_field: bool):
    st = db.get_store(store_id)
    deals = await _load_store_deals(store_id, min_pct=25)
    if not st or not deals:
        await _edit_panel(cb, f"В магазине <b>[{_esc(store_id)}]</b> пока нет свежих скидок.", kb.kb_back("adm:field" if is_field else "menu:here"))
        await cb.answer()
        return
    total_pages = max(1, (len(deals) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = deals[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    checked = db.checked_plus(store_id, hours=24) if is_field else set()
    await _edit_panel(cb, _render_slice_text(st, chunk, page, len(deals), checked, is_field), kb.kb_slice_list(store_id, chunk, page, total_pages, checked, is_field))
    await cb.answer()


@router.callback_query(F.data.startswith(("here:st:", "fld:st:")))
async def cb_slice_store(cb: CallbackQuery):
    await _show_slice_page(cb, cb.data.rsplit(":", 1).pop(), 0, cb.data.startswith("fld:"))


@router.callback_query(F.data.startswith(("here:pg:", "fld:pg:")))
async def cb_slice_pg(cb: CallbackQuery):
    parts = cb.data.split(":")
    pg, sid = int(parts.pop()), parts.pop()
    await _show_slice_page(cb, sid, pg, cb.data.startswith("fld:"))


@router.callback_query(F.data.startswith("card:"))
async def cb_open_card(cb: CallbackQuery):
    parts = cb.data.split(":")
    page, plu, sid, mode = int(parts.pop()), parts.pop(), parts.pop(), parts.pop()
    user = db.ensure_user(cb.from_user.id, cb.from_user.username or "")
    item = await asyncio.to_thread(db.get_item, sid, plu)
    if item:
        await send_product_card(cb.bot, cb.message.chat.id, sid, item, user.get("role") or "user", back_cb=f"{mode}:pg:{sid}:{page}")
    await cb.answer()


@router.callback_query(F.data.startswith("bc:"))
async def cb_barcode(cb: CallbackQuery):
    plu = cb.data.rsplit(":", 1).pop()
    try:
        png = await asyncio.to_thread(_render_barcode_png, plu)
        await cb.message.answer_photo(BufferedInputFile(png, filename=f"plu_{plu}.png"), caption=f"📊 Штрихкод Code128 PLU <code>{_esc(plu)}</code>", parse_mode="HTML")
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)
        return
    await cb.answer()


@router.callback_query(F.data.startswith("chk:start:"))
async def cb_chk_start(cb: CallbackQuery):
    parts = cb.data.split(":")
    plu, sid = parts.pop(), parts.pop()
    item = await asyncio.to_thread(db.get_item, sid, plu)
    if item:
        db.state_set(cb.from_user.id, "chk_price", {"store_id": sid, "plu": plu, "name": item.get("name") or "", "api_price": float(item.get("price") or 0), "api_stock": float(item.get("stock") or 0)})
        await cb.message.answer(f"🔍 <b>Сверка (Шаг 1/3)</b>: {_esc(item.get('name'))}\nAPI: <b>{_fmt_price(item.get('price'))} ₽</b>\nВведите <b>реальную цену</b> на сканере:", reply_markup=kb.kb_back("chk:cancel", "❌ Отмена"), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "chk:cancel")
async def cb_chk_cancel(cb: CallbackQuery):
    db.state_clear(cb.from_user.id)
    try:
        await cb.message.delete()
    except Exception:
        pass
    await cb.answer("Отменено")


@router.callback_query(F.data.startswith("chk:stk:"))
async def cb_chk_stock(cb: CallbackQuery):
    st = db.state_get(cb.from_user.id)
    if st.get("state") == "chk_stock":
        data = st.get("data") or {}
        data["real_stock"] = cb.data.rsplit(":", 1).pop()
        db.state_set(cb.from_user.id, "chk_promo", data)
        await cb.message.edit_text("🔍 <b>Сверка (Шаг 3/3)</b>: выберите тип ценника на полке:", reply_markup=kb.kb_check_promo(), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data.startswith("chk:prm:"))
async def cb_chk_promo(cb: CallbackQuery):
    st = db.state_get(cb.from_user.id)
    if st.get("state") == "chk_promo":
        data, promo = st.get("data") or {}, cb.data.rsplit(":", 1).pop()
        db.field_check_add(cb.from_user.id, data.get("store_id") or "", data.get("plu") or "", data.get("name") or "", float(data.get("api_price") or 0), float(data.get("real_price") or 0), float(data.get("api_stock") or 0), data.get("real_stock") or "", promo)
        db.state_clear(cb.from_user.id)
        await cb.message.edit_text(f"✅ <b>Сверка сохранена!</b>\nAPI: {_fmt_price(data.get('api_price'))}₽ → Зал: <b>{_fmt_price(data.get('real_price'))}₽</b> | Ост: {_esc(data.get('real_stock'))} | {_esc(promo)}", parse_mode="HTML")
    await cb.answer("Сохранено")
@router.callback_query(F.data == "adm:checks")
async def cb_adm_checks(cb: CallbackQuery):
    rows = db.field_checks_since(hours=24)
    if not rows:
        await _edit_panel(cb, "📋 <b>Итоги дня (сверки за 24 ч)</b>\nПока сверок не было.", kb.kb_back("nav:home"))
        await cb.answer()
        return
    lines = [f"📋 <b>Сверки за 24 ч ({len(rows)}):</b>\n"]
    for r in rows[:20]:
        lines.append(f"• <b>[{_esc(r.get('store_id'))}]</b> {_esc((r.get('name') or '')[:32])} | API: {_fmt_price(r.get('api_price'))}₽ → Зал: <b>{_fmt_price(r.get('real_price'))}₽</b> ({_esc(r.get('real_stock'))}, {_esc(r.get('promo_type'))})")
    await _edit_panel(cb, "\n".join(lines), kb.kb_back("nav:home"))
    await cb.answer()


@router.callback_query(F.data == "adm:stats")
async def cb_adm_stats(cb: CallbackQuery):
    st = await asyncio.to_thread(db.db_stats)
    mem = subprocess.getoutput("free -m")
    txt = f"📊 <b>Сводка и RAM</b>\nБД: <b>{st.get('db_mb')} МБ</b> | Цен: <b>{st.get('prices')}</b> | Юзеров: <b>{st.get('users')}</b>\n<pre>{_esc(mem)}</pre>"
    await _edit_panel(cb, txt, kb.kb_back("nav:home"))
    await cb.answer()


@router.callback_query(F.data == "adm:sandbox")
async def cb_adm_sandbox(cb: CallbackQuery):
    user = db.ensure_user(cb.from_user.id, cb.from_user.username or "")
    nv = not db.sandbox_on()
    db.set_sandbox(nv)
    await _edit_panel(cb, _build_home_text(user), kb.kb_home(user, nv))
    await cb.answer("2-е пространство ВКЛ" if nv else "2-е пространство ВЫКЛ")


@router.callback_query(F.data == "adm:stores")
async def cb_adm_stores(cb: CallbackQuery):
    await _edit_panel(cb, "🏬 <b>Точки сети</b>:", kb.kb_admin_stores(db.list_stores(active_only=False)))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:togstore:"))
async def cb_adm_togstore(cb: CallbackQuery):
    db.toggle_store(cb.data.rsplit(":", 1).pop())
    await _edit_panel(cb, "🏬 <b>Точки сети</b>:", kb.kb_admin_stores(db.list_stores(active_only=False)))
    await cb.answer()


@router.callback_query(F.data == "ask:addstore")
async def cb_ask_addstore(cb: CallbackQuery):
    db.state_set(cb.from_user.id, "wait_addstore")
    await _edit_panel(cb, "➕ Введите <b>код магазина и адрес</b> (пример: <code>3C0H Москва, ул. Снежная, д. 25</code>):", kb.kb_back("adm:stores", "❌ Отмена"))
    await cb.answer()


@router.callback_query(F.data == "adm:testers")
async def cb_adm_testers(cb: CallbackQuery):
    await _edit_panel(cb, "🕵️ <b>Тестировщики</b>:", kb.kb_admin_testers(db.testers_list()))
    await cb.answer()


@router.callback_query(F.data == "ask:addtester")
async def cb_ask_addtester(cb: CallbackQuery):
    db.state_set(cb.from_user.id, "wait_addtester")
    await _edit_panel(cb, "🕵️ Введите <b>@username</b> тестировщика:", kb.kb_back("adm:testers", "❌ Отмена"))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:deltester:"))
async def cb_adm_deltester(cb: CallbackQuery):
    db.tester_remove(cb.data.rsplit(":", 1).pop())
    await _edit_panel(cb, "🕵️ <b>Тестировщики</b>:", kb.kb_admin_testers(db.testers_list()))
    await cb.answer()


@router.callback_query(F.data == "adm:parser")
async def cb_adm_parser(cb: CallbackQuery):
    st = subprocess.getoutput("systemctl is-active 5ka-collector").strip()
    logs = subprocess.getoutput("journalctl -u 5ka-collector -n 10 --no-pager -o cat")
    await _edit_panel(cb, f"⚙️ <b>Парсер:</b> <b>{_esc(st)}</b>\n<pre>{_esc(logs[-2000:])}</pre>", kb.kb_admin_parser())
    await cb.answer()


@router.callback_query(F.data == "adm:parser_restart")
async def cb_adm_parser_restart(cb: CallbackQuery):
    subprocess.run(["systemctl", "restart", "5ka-collector"], check=False)
    await asyncio.sleep(1)
    await cb_adm_parser(cb)


@router.callback_query(F.data == "adm:backup")
async def cb_adm_backup(cb: CallbackQuery):
    await cb.answer("Создаю архив...")
    zp = await asyncio.to_thread(make_zip_backup)
    try:
        await cb.bot.send_document(cb.message.chat.id, FSInputFile(zp), caption="📦 Бэкап проекта и БД")
    finally:
        if os.path.exists(zp):
            os.remove(zp)


@router.message(F.text)
async def handle_text_input(msg: Message):
    uid, text = msg.from_user.id, (msg.text or "").strip()
    user = db.ensure_user(uid, msg.from_user.username or "")
    st = db.state_get(uid)
    sname = st.get("state") or ""
    try:
        await msg.delete()
    except Exception:
        pass
    if sname == "wait_pct" and text.isdigit() and 1 <= int(text) <= 99:
        db.set_min_pct(uid, int(text))
    elif sname == "wait_maxp":
        try:
            db.set_max_price(uid, max(0.0, float(text.replace(",", "."))))
        except ValueError:
            pass
    elif sname == "wait_radar":
        db.radar_add(uid, text)
    elif sname == "wait_stop":
        for w in text.split(","):
            db.add_stop_word(uid, w)
    elif sname == "wait_addstore" and len(text.split(None, 1)) == 2:
        sid, addr = text.split(None, 1)
        db.add_store(sid, addr)
    elif sname == "wait_addtester":
        db.tester_add(text)
    elif sname == "chk_price":
        try:
            rp = float(text.replace(",", "."))
            data = st.get("data") or {}
            data["real_price"] = rp
            db.state_set(uid, "chk_stock", data)
            await msg.answer(f"🔍 <b>Сверка (Шаг 2/3)</b>: цена <b>{_fmt_price(rp)} ₽</b>. Сколько товара на полке?", reply_markup=kb.kb_check_stock(), parse_mode="HTML")
            return
        except ValueError:
            pass
    db.state_clear(uid)
    user = db.get_user(uid)
    await _send_fresh_panel(msg.bot, msg.chat.id, uid, _build_home_text(user), kb.kb_home(user, db.sandbox_on()))

