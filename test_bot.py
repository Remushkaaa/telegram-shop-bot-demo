"""Сквозной тест бота без интернета: подменяем сессию Telegram и прогоняем настоящие апдейты."""
import asyncio
import os
import time

os.environ["ADMIN_IDS"] = "999"
os.environ["DB_PATH"] = ":memory:"

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.methods import (AnswerCallbackQuery, DeleteMessage, EditMessageText,  # noqa: E402
                             SendMessage, SendPhoto)
from aiogram.types import Chat, Message  # noqa: E402

import bot as app  # noqa: E402


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (AnswerCallbackQuery, DeleteMessage)):
            return True
        if isinstance(method, (SendMessage, SendPhoto, EditMessageText)):
            chat_id = getattr(method, "chat_id", 1)
            return Message(message_id=len(self.calls), date=int(time.time()), chat=Chat(id=chat_id, type="private"),
                           text=getattr(method, "text", None))
        return True

    async def close(self): pass
    async def stream_content(self, *a, **k): yield b""

    def sent(self, to=None):
        return [m for m in self.calls if isinstance(m, SendMessage) and (to is None or m.chat_id == to)]


def msg(uid, text, n=[0]):
    n[0] += 1
    return {"update_id": n[0], "message": {"message_id": n[0], "date": int(time.time()),
            "chat": {"id": uid, "type": "private"}, "from": {"id": uid, "is_bot": False, "first_name": "T"},
            "text": text}}


def cbq(uid, data, n=[1000]):
    n[0] += 1
    return {"update_id": n[0], "callback_query": {"id": str(n[0]), "chat_instance": "x",
            "from": {"id": uid, "is_bot": False, "first_name": "T"}, "data": data,
            "message": {"message_id": 5, "date": int(time.time()), "chat": {"id": uid, "type": "private"},
                        "text": "x"}}}


def test_full_purchase_and_admin_flow():
    async def run():
        session = FakeSession()
        bot = Bot("123456:TEST", session=session)
        dp = Dispatcher()
        dp.include_router(app.router)
        feed = lambda u: dp.feed_raw_update(bot, u)

        cat = app.shop.add_category("Чай")
        pid = app.shop.add_product(cat, "Зелёный чай", 120, "Лёгкий")

        await feed(msg(1, "/start"))
        await feed(msg(1, "🛍 Каталог"))
        await feed(cbq(1, app.Cat(id=cat).pack()))
        await feed(cbq(1, app.Add(id=pid).pack()))
        await feed(cbq(1, app.Add(id=pid).pack()))
        assert app.shop.cart_total(1) == 240

        await feed(msg(1, "🧺 Корзина"))
        assert "Итого" in session.sent(1)[-1].text and "240" in session.sent(1)[-1].text
        await feed(cbq(1, app.Qty(id=pid, delta=-1).pack()))
        assert app.shop.cart_total(1) == 120
        await feed(cbq(1, "checkout"))
        await feed(msg(1, "Анна"))
        await feed(msg(1, "123"))  # плохой номер должен быть отклонён
        assert "Не похоже на номер" in session.sent(1)[-1].text
        await feed(msg(1, "+380501112233"))
        await feed(msg(1, "Київ, відділення 5"))
        await feed(msg(1, "пропустить"))

        assert app.shop.cart(1) == []
        orders = app.shop.orders()
        assert len(orders) == 1 and orders[0]["total"] == 120
        assert any("Новый заказ" in m.text for m in session.sent(999))  # администратор получил уведомление

        # админ меняет статус, покупатель получает сообщение
        await feed(cbq(999, app.Adm(act="status", id=orders[0]["id"], arg="paid").pack()))
        assert app.shop.orders()[0]["status"] == "paid"
        assert any("оплачен" in m.text for m in session.sent(1))

        # чужой не пускается в админку
        await feed(msg(1, "/admin"))
        assert session.sent(1)[-1].text == "Нет доступа."
        await bot.session.close()

    asyncio.run(run())
