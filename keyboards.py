# -*- coding: utf-8 -*-
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

MAIN_CATS = [
    ("🥨", "Снеки и чипсы"),
    ("🥖", "Хлеб и выпечка"),
    ("🥤", "Вода и напитки"),
    ("🍫", "Сладости"),
    ("🍱", "Готовая еда"),
    ("❄️", "Замороженные продукты"),
    ("🥛", "Молочные продукты, яйца"),
    ("🍏", "Овощи, фрукты, орехи"),
    ("🍝", "Бакалея"),
    ("🥩", "Мясо, птица, колбасы"),
    ("🐟", "Рыба и морепродукты"),
    ("🥗", "Здоровый выбор"),
    ("🍼", "Детские товары"),
    ("🧹", "Бытовая химия, уборка"),
    ("🧴", "Красота, гигиена, аптека"),
]

EXTRA_DEFAULT_CATS = [
    ("🐾", "Для животных"),
    ("🍳", "Кухня, дом, дача"),
]


def _btn(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb)


def kb_back(cb: str = "nav:home", label: str = "⬅️ Назад в меню") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(label, cb)]])


def kb_home(user: dict, sandbox: bool = False) -> InlineKeyboardMarkup:
    role = (user or {}).get("role") or "user"
    pct = int((user or {}).get("min_pct") or 70)
    max_p = float((user or {}).get("max_price") or 0)
    max_lbl = f"до {int(max_p)}₽" if max_p > 0 else "выкл"
    rows = [
        [_btn(f"📉 Порог скидки ({pct}%)", "menu:pct"), _btn(f"💰 Товары до N ₽ ({max_lbl})", "menu:maxp")],
        [_btn("🎯 Радар (мои товары)", "menu:radar"), _btn("🚫 Стоп-слова", "menu:stop")],
        [_btn("📂 Категории (15+)", "menu:cats:0"), _btn("🏪 Мои магазины", "menu:stores")],
        [_btn("📍 Я у магазина (срез)", "menu:here")],
    ]
    if role == "user":
        rows.append([_btn("💎 Тарифы и подписка", "menu:tariffs")])
    if role == "admin":
        sb_lbl = "🛸 2-е пространство: ВКЛ" if sandbox else "🛸 2-е пространство: ВЫКЛ"
        rows.extend([
            [_btn("🧪 Начать полевой тест", "adm:field"), _btn("📋 Итоги дня (сверки)", "adm:checks")],
            [_btn("📊 Сводка и RAM", "adm:stats"), _btn(sb_lbl, "adm:sandbox")],
            [_btn("🏬 Точки сети", "adm:stores"), _btn("🕵️ Тестировщики", "adm:testers")],
            [_btn("⚙️ Парсер", "adm:parser"), _btn("📦 Бэкап .zip", "adm:backup")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
  def kb_pct(cur_pct: int) -> InlineKeyboardMarkup:
    rows, row = [], []
    for v in (10, 20, 30, 40, 50, 60, 70, 80, 90):
        mark = "✅ " if v == int(cur_pct) else ""
        row.append(_btn(f"{mark}{v}%", f"set:pct:{v}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_btn("✏️ Ввести вручную (1–99%)", "ask:pct")])
    rows.append([_btn("⬅️ Назад в меню", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_max_price(cur_max: float) -> InlineKeyboardMarkup:
    cur = int(cur_max or 0)
    rows, row = [], []
    for v in (10, 15, 20, 30, 50, 100, 200):
        mark = "✅ " if v == cur else ""
        row.append(_btn(f"{mark}{v} ₽", f"set:maxp:{v}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_btn("✏️ Своя цена", "ask:maxp"), _btn("❌ Выключить", "set:maxp:0")])
    rows.append([_btn("⬅️ Назад в меню", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_extra_cats(extra_db_cats: list) -> list:
    seen = {name.lower() for _, name in MAIN_CATS}
    extras = []
    for emo, name in EXTRA_DEFAULT_CATS:
        extras.append((emo, name))
        seen.add(name.lower())
    for cname in (extra_db_cats or []):
        low = str(cname).strip().lower()
        if low and low not in seen:
            extras.append(("🏷", str(cname).strip()))
            seen.add(low)
    return extras


def kb_cats(page: int, off_cats: list, extra_db_cats: list) -> InlineKeyboardMarkup:
    off_set = {c.lower() for c in (off_cats or [])}
    rows = []
    if page == 0:
        for idx, (emo, name) in enumerate(MAIN_CATS):
            mark = "❌" if name.lower() in off_set else "✅"
            rows.append([_btn(f"{mark} {emo} {name}", f"tog:cat:0:{idx}")])
        rows.append([_btn("➡️ Ещё категории", "menu:cats:1")])
    else:
        for idx, (emo, name) in enumerate(_build_extra_cats(extra_db_cats)):
            mark = "❌" if name.lower() in off_set else "✅"
            rows.append([_btn(f"{mark} {emo} {name}", f"tog:cat:1:{idx}")])
        rows.append([_btn("⬅️ Основные категории (15)", "menu:cats:0")])
    rows.append([_btn("✅ Включить все", f"cats:all_on:{page}"), _btn("⬅️ В меню", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_cat_by_index(page: int, idx: int, extra_db_cats: list) -> str:
    items = MAIN_CATS if page == 0 else _build_extra_cats(extra_db_cats)
    for i, (_, name) in enumerate(items):
        if i == idx:
            return name
    return ""
def kb_user_stores(stores: list, selected_ids: set) -> InlineKeyboardMarkup:
    rows = []
    for st in (stores or []):
        sid = st.get("store_id") or ""
        mark = "✅" if sid in selected_ids else "⬜️"
        rows.append([_btn(f"{mark} [{sid}] {st.get('addr') or sid}", f"tog:ustore:{sid}")])
    rows.append([_btn("⬅️ Назад в меню", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_radar(items: list) -> InlineKeyboardMarkup:
    rows = [[_btn("➕ Добавить товар или PLU", "ask:radar")]]
    for it in (items or []):
        rows.append([_btn(f"🗑 {it.get('query') or ''}", f"del:radar:{it.get('id')}")])
    rows.append([_btn("⬅️ Назад в меню", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_stop_words(words: list) -> InlineKeyboardMarkup:
    rows = [[_btn("➕ Добавить стоп-слово", "ask:stop")]]
    if words:
        rows.append([_btn("🧹 Очистить все стоп-слова", "clr:stop")])
    rows.append([_btn("⬅️ Назад в меню", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_select_store(stores: list, prefix: str) -> InlineKeyboardMarkup:
    rows = [[_btn(f"🏪 [{st.get('store_id')}] {st.get('addr')}", f"{prefix}:{st.get('store_id')}")] for st in (stores or [])]
    rows.append([_btn("⬅️ Назад в меню", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_slice_list(store_id: str, items_page: list, page: int, total_pages: int, checked_set: set = None, is_field: bool = False) -> InlineKeyboardMarkup:
    checked = checked_set or set()
    rows, num_row = [], []
    mode = "fld" if is_field else "here"
    for i, it in enumerate(items_page, start=1):
        plu = str(it.get("plu") or "")
        mark = "✅" if plu in checked else ""
        num_row.append(_btn(f"{mark}{i}", f"card:{mode}:{store_id}:{plu}:{page}"))
        if len(num_row) == 5:
            rows.append(num_row)
            num_row = []
    if num_row:
        rows.append(num_row)
    nav_row = []
    if page > 0:
        nav_row.append(_btn("⬅️ Назад", f"{mode}:pg:{store_id}:{page - 1}"))
    nav_row.append(_btn(f"{page + 1}/{max(1, total_pages)}", "noop"))
    if page + 1 < total_pages:
        nav_row.append(_btn("Вперёд ➡️", f"{mode}:pg:{store_id}:{page + 1}"))
    rows.append(nav_row)
    back_cb = "adm:field" if is_field else "menu:here"
    rows.append([_btn("🔄 Другой магазин", back_cb), _btn("🏠 В меню", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_card(store_id: str, plu: str, can_check: bool = False, back_cb: str = "") -> InlineKeyboardMarkup:
    rows = [[_btn("📊 Показать штрихкод ⬇️", f"bc:{plu}")]]
    if can_check:
        rows.append([_btn("🔍 Сверить в зале (ТСД)", f"chk:start:{store_id}:{plu}")])
    if back_cb:
        rows.append([_btn("⬅️ Назад к списку", back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_check_stock() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("0 (пусто)", "chk:stk:0"), _btn("1–2 шт.", "chk:stk:1-2"), _btn("3–5 шт.", "chk:stk:3-5")],
        [_btn("Много (6+)", "chk:stk:6+"), _btn("Нет товара / не нашли", "chk:stk:нет")],
        [_btn("❌ Отмена", "chk:cancel")],
    ])


def kb_check_promo() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🏷 Жёлтый акционный", "chk:prm:Жёлтый акционный")],
        [_btn("⚪️ Белый регулярный", "chk:prm:Белый регулярный")],
        [_btn("🔴 Уценка / Скидка магазина", "chk:prm:Уценка")],
        [_btn("🚫 Ценник отсутствует", "chk:prm:Нет ценника")],
        [_btn("❌ Отмена", "chk:cancel")],
    ])


def kb_admin_stores(stores: list) -> InlineKeyboardMarkup:
    rows = [[_btn("➕ Добавить магазин", "ask:addstore")]]
    for st in (stores or []):
        sid = st.get("store_id") or ""
        mark = "✅" if int(st.get("active") or 0) == 1 else "❌"
        rows.append([_btn(f"{mark} [{sid}] {st.get('addr') or sid}", f"adm:togstore:{sid}")])
    rows.append([_btn("⬅️ Назад в меню", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_admin_testers(testers: list) -> InlineKeyboardMarkup:
    rows = [[_btn("➕ Добавить тестировщика (@username)", "ask:addtester")]]
    for t in (testers or []):
        uname = t.get("username") or ""
        st = "активен" if t.get("user_id") else "ожидает /start"
        rows.append([_btn(f"🗑 @{uname} ({st})", f"adm:deltester:{uname}")])
    rows.append([_btn("⬅️ Назад в меню", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_admin_parser() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🔄 Обновить статус и лог", "adm:parser")],
        [_btn("🔁 Перезапустить 5ka-collector", "adm:parser_restart")],
        [_btn("⬅️ Назад в меню", "nav:home")],
    ])
