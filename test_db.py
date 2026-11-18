import pytest
from db import Shop


@pytest.fixture
def shop():
    s = Shop()
    cat = s.add_category("Чай")
    s.p1 = s.add_product(cat, "Зелёный чай", 120, "Лёгкий")
    s.p2 = s.add_product(cat, "Чёрный чай", 90)
    s.cat = cat
    return s


def test_search_and_stock(shop):
    assert len(shop.products(query="зелён")) == 1  # регистр кириллицы не важен
    assert len(shop.products(query="ЗЕЛЁН")) == 1
    assert len(shop.products(query="кофе")) == 0
    shop.update_product(shop.p2, in_stock=0)
    assert [p["id"] for p in shop.products(category_id=shop.cat)] == [shop.p1]
    assert len(shop.products(only_in_stock=False)) == 2


def test_cart_flow(shop):
    shop.cart_add(1, shop.p1)
    shop.cart_add(1, shop.p1, 2)
    shop.cart_add(1, shop.p2)
    assert shop.cart_total(1) == 3 * 120 + 90
    shop.cart_set(1, shop.p1, 1)
    assert shop.cart_total(1) == 120 + 90
    shop.cart_set(1, shop.p2, 0)
    assert [r["id"] for r in shop.cart(1)] == [shop.p1]


def test_cannot_add_out_of_stock(shop):
    shop.update_product(shop.p1, in_stock=0)
    with pytest.raises(ValueError):
        shop.cart_add(1, shop.p1)


def test_order_clears_cart_and_keeps_snapshot(shop):
    shop.cart_add(7, shop.p1, 2)
    oid = shop.make_order(7, "Анна", "+380501112233", "Київ", receipt_id="file123")
    assert shop.cart(7) == []
    order, items = shop.order(oid)
    assert order["total"] == 240 and order["status"] == "new"
    shop.update_product(shop.p1, price=999)  # цена в старом заказе не меняется
    assert shop.order(oid)[1][0]["price"] == 120


def test_empty_cart_order_fails(shop):
    with pytest.raises(ValueError):
        shop.make_order(1, "x", "y")


def test_status_flow(shop):
    shop.cart_add(1, shop.p1)
    oid = shop.make_order(1, "A", "1")
    shop.set_status(oid, "paid")
    assert [o["id"] for o in shop.orders(status="paid")] == [oid]
    with pytest.raises(ValueError):
        shop.set_status(oid, "weird")


def test_category_with_products_not_deletable(shop):
    assert shop.delete_category(shop.cat) is False
    shop.delete_product(shop.p1); shop.delete_product(shop.p2)
    assert shop.delete_category(shop.cat) is True


def test_bad_price(shop):
    with pytest.raises(ValueError):
        shop.add_product(shop.cat, "Бесплатно", 0)
