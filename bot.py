"""Telegram-магазин: каталог, поиск, корзина, заказ с чеком, админ-панель прямо в боте.

Запуск: BOT_TOKEN=... ADMIN_IDS=123,456 python bot.py
"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db import STATUSES, Shop

STATUS_RU = {"new": "🆕 новый", "paid": "💳 оплачен", "shipped": "🚚 отправлен",
             "done": "✅ выполнен", "cancelled": "❌ отменён"}
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
shop = Shop(os.getenv("DB_PATH", "shop.db"))
router = Router()


def money(x):
    return f"{x:,.0f} ₴".replace(",", " ")


def is_admin(user_id):
    return user_id in ADMIN_IDS


# --- callback-данные ---------------------------------------------------------
class Cat(CallbackData, prefix="cat"):
    id: int


class Prod(CallbackData, prefix="prod"):
    id: int


class Add(CallbackData, prefix="add"):
    id: int


class Qty(CallbackData, prefix="qty"):
    id: int
    delta: int


class Adm(CallbackData, prefix="adm"):
    act: str
    id: int = 0
    arg: str = ""


class Checkout(StatesGroup):
    name = State()
    phone = State()
    address = State()
    receipt = State()


class NewProduct(StatesGroup):
    category = State()
    name = State()
    price = State()
    description = State()
    photo = State()


class Search(StatesGroup):
    query = State()


class NewCategory(StatesGroup):
    name = State()


MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🛍 Каталог"), KeyboardButton(text="🧺 Корзина")],
              [KeyboardButton(text="🔎 Поиск"), KeyboardButton(text="📦 Мои заказы")]],
    resize_keyboard=True)


# --- каталог -------------------------------------------------------------------
@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Привет! Это магазин в Telegram 🛍\nВыбирайте товары, кладите в корзину и оформляйте заказ.",
                   reply_markup=MAIN_MENU)


@router.message(F.text == "🛍 Каталог")
async def catalog(m: Message):
    cats = shop.categories()
    if not cats:
        return await m.answer("Каталог пока пуст.")
    kb = InlineKeyboardBuilder()
    for c in cats:
        kb.button(text=c["name"], callback_data=Cat(id=c["id"]))
    kb.adjust(2)
    await m.answer("Выберите категорию:", reply_markup=kb.as_markup())


async def send_products(target: Message, products):
    if not products:
        return await target.answer("Ничего не найдено.")
    for p in products:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="➕ В корзину", callback_data=Add(id=p["id"]).pack())]])
        text = f"<b>{p['name']}</b>\n{p['description']}\n\n{money(p['price'])}"
        if p["photo_id"]:
            await target.answer_photo(p["photo_id"], caption=text, parse_mode="HTML", reply_markup=kb)
        else:
            await target.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(Cat.filter())
async def open_category(cb: CallbackQuery, callback_data: Cat):
    await cb.answer()
    await send_products(cb.message, shop.products(category_id=callback_data.id))


@router.message(F.text == "🔎 Поиск")
async def search_start(m: Message, state: FSMContext):
    await state.set_state(Search.query)
    await m.answer("Что ищем? Напишите название или слово из описания.")


@router.message(Search.query, F.text)
async def search_run(m: Message, state: FSMContext):
    await state.clear()
    await send_products(m, shop.products(query=m.text))


# --- корзина ---------------------------------------------------------------------
def cart_view(user_id):
    items = shop.cart(user_id)
    if not items:
        return "Корзина пуста 🧺", None
    lines = [f"{r['name']} — {r['qty']} × {money(r['price'])} = {money(r['sum'])}" for r in items]
    text = "🧺 Ваша корзина:\n\n" + "\n".join(lines) + f"\n\nИтого: <b>{money(shop.cart_total(user_id))}</b>"
    kb = InlineKeyboardBuilder()
    for r in items:
        kb.button(text="➖", callback_data=Qty(id=r["id"], delta=-1))
        kb.button(text=f"{r['name'][:14]} ({r['qty']})", callback_data=Prod(id=r["id"]))
        kb.button(text="➕", callback_data=Qty(id=r["id"], delta=1))
    kb.adjust(3)
    kb.row(InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout"))
    return text, kb.as_markup()


@router.callback_query(Add.filter())
async def add_to_cart(cb: CallbackQuery, callback_data: Add):
    try:
        shop.cart_add(cb.from_user.id, callback_data.id)
    except ValueError as e:
        return await cb.answer(str(e), show_alert=True)
    await cb.answer("Добавлено в корзину ✅")


@router.message(F.text == "🧺 Корзина")
async def show_cart(m: Message):
    text, kb = cart_view(m.from_user.id)
    await m.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(Prod.filter())
async def cart_item_info(cb: CallbackQuery, callback_data: Prod):
    p = shop.product(callback_data.id)
    await cb.answer(f"{p['name']}: {money(p['price'])}" if p else "Товар удалён")


@router.callback_query(Qty.filter())
async def change_qty(cb: CallbackQuery, callback_data: Qty):
    uid = cb.from_user.id
    cur = next((r["qty"] for r in shop.cart(uid) if r["id"] == callback_data.id), 0)
    shop.cart_set(uid, callback_data.id, cur + callback_data.delta)
    text, kb = cart_view(uid)
    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()


# --- оформление заказа ---------------------------------------------------------------
@router.callback_query(F.data == "checkout")
async def checkout_start(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    if not shop.cart(cb.from_user.id):
        return await cb.message.answer("Корзина пуста.")
    await state.set_state(Checkout.name)
    await cb.message.answer("Как вас зовут?", reply_markup=ReplyKeyboardRemove())


@router.message(Checkout.name, F.text)
async def co_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await state.set_state(Checkout.phone)
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
                             resize_keyboard=True, one_time_keyboard=True)
    await m.answer("Телефон для связи (кнопкой или текстом):", reply_markup=kb)


@router.message(Checkout.phone)
async def co_phone(m: Message, state: FSMContext):
    phone = m.contact.phone_number if m.contact else (m.text or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 10:
        return await m.answer("Не похоже на номер. Введите ещё раз, например +380501112233.")
    await state.update_data(phone=phone)
    await state.set_state(Checkout.address)
    await m.answer("Адрес доставки (город, отделение или улица):", reply_markup=ReplyKeyboardRemove())


@router.message(Checkout.address, F.text)
async def co_address(m: Message, state: FSMContext):
    await state.update_data(address=m.text.strip())
    await state.set_state(Checkout.receipt)
    await m.answer("Если уже оплатили переводом — пришлите фото чека. Если нет, напишите «пропустить».")


async def finish_order(m: Message, state: FSMContext, receipt_id):
    d = await state.get_data()
    await state.clear()
    try:
        oid = shop.make_order(m.from_user.id, d["name"], d["phone"], d["address"], receipt_id)
    except ValueError as e:
        return await m.answer(str(e), reply_markup=MAIN_MENU)
    await m.answer(f"Заказ №{oid} принят 🎉 Мы свяжемся с вами в ближайшее время.", reply_markup=MAIN_MENU)
    text, items = order_text(oid)
    for admin in ADMIN_IDS:
        try:
            await m.bot.send_message(admin, "🔔 Новый заказ!\n\n" + text, parse_mode="HTML",
                                     reply_markup=status_kb(oid))
            if receipt_id:
                await m.bot.send_photo(admin, receipt_id, caption=f"Чек к заказу №{oid}")
        except Exception:
            logging.exception("Не удалось уведомить администратора %s", admin)


@router.message(Checkout.receipt, F.photo)
async def co_receipt_photo(m: Message, state: FSMContext):
    await finish_order(m, state, m.photo[-1].file_id)


@router.message(Checkout.receipt, F.text)
async def co_receipt_skip(m: Message, state: FSMContext):
    await finish_order(m, state, None)


# --- заказы клиента ------------------------------------------------------------------
def order_text(oid):
    o, items = shop.order(oid)
    lines = [f"• {i['name']} — {i['qty']} × {money(i['price'])}" for i in items]
    text = (f"<b>Заказ №{o['id']}</b> — {STATUS_RU[o['status']]}\n"
            f"{o['name']}, {o['phone']}\n{o['address'] or ''}\n\n" + "\n".join(lines) +
            f"\n\nИтого: <b>{money(o['total'])}</b>")
    return text, items


@router.message(F.text == "📦 Мои заказы")
async def my_orders(m: Message):
    orders = shop.orders(user_id=m.from_user.id)
    if not orders:
        return await m.answer("У вас пока нет заказов.")
    for o in orders[:10]:
        await m.answer(order_text(o["id"])[0], parse_mode="HTML")


# --- админ-панель --------------------------------------------------------------------
def status_kb(oid):
    kb = InlineKeyboardBuilder()
    for s in STATUSES:
        kb.button(text=STATUS_RU[s], callback_data=Adm(act="status", id=oid, arg=s))
    kb.adjust(2)
    return kb.as_markup()


def admin_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Новые заказы", callback_data=Adm(act="orders", arg="new"))
    kb.button(text="📋 Все заказы", callback_data=Adm(act="orders"))
    kb.button(text="➕ Категория", callback_data=Adm(act="newcat"))
    kb.button(text="➕ Товар", callback_data=Adm(act="newprod"))
    kb.button(text="🗂 Товары", callback_data=Adm(act="products"))
    kb.adjust(2)
    return kb.as_markup()


@router.message(Command("admin"))
async def admin(m: Message):
    if not is_admin(m.from_user.id):
        return await m.answer("Нет доступа.")
    await m.answer("Админ-панель:", reply_markup=admin_menu())


@router.callback_query(Adm.filter(F.act == "orders"))
async def adm_orders(cb: CallbackQuery, callback_data: Adm):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    await cb.answer()
    orders = shop.orders(status=callback_data.arg or None)[:15]
    if not orders:
        return await cb.message.answer("Заказов нет.")
    for o in orders:
        await cb.message.answer(order_text(o["id"])[0], parse_mode="HTML", reply_markup=status_kb(o["id"]))


@router.callback_query(Adm.filter(F.act == "status"))
async def adm_status(cb: CallbackQuery, callback_data: Adm):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    shop.set_status(callback_data.id, callback_data.arg)
    o, _ = shop.order(callback_data.id)
    await cb.message.edit_text(order_text(o["id"])[0], parse_mode="HTML", reply_markup=status_kb(o["id"]))
    await cb.answer("Статус обновлён")
    try:  # сообщаем покупателю
        await cb.bot.send_message(o["user_id"], f"Заказ №{o['id']}: {STATUS_RU[o['status']]}")
    except Exception:
        logging.info("Покупатель недоступен")


@router.callback_query(Adm.filter(F.act == "newcat"))
async def adm_newcat(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    await cb.answer()
    await state.set_state(NewCategory.name)
    await cb.message.answer("Название новой категории:")


@router.message(NewCategory.name, F.text)
async def adm_newcat_name(m: Message, state: FSMContext):
    await state.clear()
    shop.add_category(m.text)
    await m.answer(f"Категория «{m.text.strip()}» добавлена ✅", reply_markup=admin_menu())


@router.callback_query(Adm.filter(F.act == "newprod"))
async def adm_newprod(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    await cb.answer()
    cats = shop.categories()
    if not cats:
        return await cb.message.answer("Сначала добавьте категорию.")
    kb = InlineKeyboardBuilder()
    for c in cats:
        kb.button(text=c["name"], callback_data=Adm(act="pickcat", id=c["id"]))
    kb.adjust(2)
    await state.set_state(NewProduct.category)
    await cb.message.answer("Категория товара:", reply_markup=kb.as_markup())


@router.callback_query(NewProduct.category, Adm.filter(F.act == "pickcat"))
async def adm_pickcat(cb: CallbackQuery, callback_data: Adm, state: FSMContext):
    await cb.answer()
    await state.update_data(category_id=callback_data.id)
    await state.set_state(NewProduct.name)
    await cb.message.answer("Название товара:")


@router.message(NewProduct.name, F.text)
async def adm_prod_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await state.set_state(NewProduct.price)
    await m.answer("Цена в гривнах (число):")


@router.message(NewProduct.price, F.text)
async def adm_prod_price(m: Message, state: FSMContext):
    try:
        price = float(m.text.replace(",", ".").replace(" ", ""))
        if price <= 0:
            raise ValueError
    except ValueError:
        return await m.answer("Нужно число больше нуля, например 249.")
    await state.update_data(price=price)
    await state.set_state(NewProduct.description)
    await m.answer("Описание (или «-», если не нужно):")


@router.message(NewProduct.description, F.text)
async def adm_prod_desc(m: Message, state: FSMContext):
    await state.update_data(description="" if m.text.strip() == "-" else m.text.strip())
    await state.set_state(NewProduct.photo)
    await m.answer("Фото товара (или «-», если без фото):")


@router.message(NewProduct.photo)
async def adm_prod_photo(m: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    photo = m.photo[-1].file_id if m.photo else None
    pid = shop.add_product(d["category_id"], d["name"], d["price"], d["description"], photo)
    await m.answer(f"Товар «{d['name']}» добавлен (id {pid}) ✅", reply_markup=admin_menu())


@router.callback_query(Adm.filter(F.act == "products"))
async def adm_products(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    await cb.answer()
    products = shop.products(only_in_stock=False)
    if not products:
        return await cb.message.answer("Товаров нет.")
    for p in products[:30]:
        kb = InlineKeyboardBuilder()
        kb.button(text="⛔ Нет в наличии" if p["in_stock"] else "✅ Вернуть в наличие",
                  callback_data=Adm(act="stock", id=p["id"]))
        kb.button(text="🗑 Удалить", callback_data=Adm(act="delprod", id=p["id"]))
        await cb.message.answer(f"{p['name']} — {money(p['price'])}" + ("" if p["in_stock"] else " (нет в наличии)"),
                                reply_markup=kb.as_markup())


@router.callback_query(Adm.filter(F.act == "stock"))
async def adm_stock(cb: CallbackQuery, callback_data: Adm):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    p = shop.product(callback_data.id)
    shop.update_product(p["id"], in_stock=0 if p["in_stock"] else 1)
    p = shop.product(p["id"])
    kb = InlineKeyboardBuilder()
    kb.button(text="⛔ Нет в наличии" if p["in_stock"] else "✅ Вернуть в наличие",
              callback_data=Adm(act="stock", id=p["id"]))
    kb.button(text="🗑 Удалить", callback_data=Adm(act="delprod", id=p["id"]))
    await cb.message.edit_text(f"{p['name']} — {money(p['price'])}" + ("" if p["in_stock"] else " (нет в наличии)"),
                               reply_markup=kb.as_markup())
    await cb.answer("Готово")


@router.callback_query(Adm.filter(F.act == "delprod"))
async def adm_delprod(cb: CallbackQuery, callback_data: Adm):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Нет доступа", show_alert=True)
    shop.delete_product(callback_data.id)
    await cb.answer("Товар удалён")
    await cb.message.delete()


async def main():
    logging.basicConfig(level=logging.INFO)
    token = os.environ["BOT_TOKEN"]
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(Bot(token))


if __name__ == "__main__":
    asyncio.run(main())
