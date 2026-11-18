"""Слой данных магазина: категории, товары, корзина, заказы (SQLite)."""
import sqlite3
from contextlib import closing

STATUSES = ("new", "paid", "shipped", "done", "cancelled")

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories(
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS products(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    name TEXT NOT NULL, description TEXT DEFAULT '', price REAL NOT NULL,
    photo_id TEXT, in_stock INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS cart(
    user_id INTEGER NOT NULL, product_id INTEGER NOT NULL REFERENCES products(id),
    qty INTEGER NOT NULL CHECK(qty > 0), PRIMARY KEY(user_id, product_id));
CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    name TEXT, phone TEXT, address TEXT, receipt_id TEXT,
    total REAL NOT NULL, status TEXT NOT NULL DEFAULT 'new',
    created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS order_items(
    order_id INTEGER NOT NULL REFERENCES orders(id), product_id INTEGER,
    name TEXT NOT NULL, price REAL NOT NULL, qty INTEGER NOT NULL);
"""


class Shop:
    def __init__(self, path=":memory:"):
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        # SQLite сам не понимает регистр кириллицы, поэтому поиск делаем через свою функцию
        self.con.create_function("lower_u", 1, lambda s: s.lower() if s else "")
        self.con.executescript(SCHEMA)

    # --- каталог -----------------------------------------------------------
    def add_category(self, name):
        with self.con:
            self.con.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (name.strip(),))
        return self.con.execute("SELECT id FROM categories WHERE name=?", (name.strip(),)).fetchone()["id"]

    def categories(self):
        return self.con.execute("SELECT * FROM categories ORDER BY name").fetchall()

    def delete_category(self, cat_id):
        """Нельзя удалить категорию, пока в ней есть товары."""
        n = self.con.execute("SELECT COUNT(*) c FROM products WHERE category_id=?", (cat_id,)).fetchone()["c"]
        if n:
            return False
        with self.con:
            self.con.execute("DELETE FROM categories WHERE id=?", (cat_id,))
        return True

    def add_product(self, category_id, name, price, description="", photo_id=None):
        if price <= 0:
            raise ValueError("Цена должна быть больше нуля")
        with self.con:
            cur = self.con.execute(
                "INSERT INTO products(category_id,name,description,price,photo_id) VALUES(?,?,?,?,?)",
                (category_id, name.strip(), description, price, photo_id))
        return cur.lastrowid

    def update_product(self, pid, **fields):
        allowed = {"name", "description", "price", "photo_id", "in_stock", "category_id"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"Нельзя менять поля: {bad}")
        if "price" in fields and fields["price"] <= 0:
            raise ValueError("Цена должна быть больше нуля")
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.con:
            self.con.execute(f"UPDATE products SET {sets} WHERE id=?", (*fields.values(), pid))

    def delete_product(self, pid):
        with self.con:
            self.con.execute("DELETE FROM cart WHERE product_id=?", (pid,))
            self.con.execute("DELETE FROM products WHERE id=?", (pid,))

    def product(self, pid):
        return self.con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()

    def products(self, category_id=None, query=None, only_in_stock=True):
        sql, args = "SELECT * FROM products WHERE 1=1", []
        if category_id is not None:
            sql += " AND category_id=?"; args.append(category_id)
        if query:
            sql += " AND (lower_u(name) LIKE ? OR lower_u(description) LIKE ?)"
            q = f"%{query.strip().lower()}%"
            args += [q, q]
        if only_in_stock:
            sql += " AND in_stock=1"
        return self.con.execute(sql + " ORDER BY name", args).fetchall()

    # --- корзина -----------------------------------------------------------
    def cart_add(self, user_id, pid, qty=1):
        p = self.product(pid)
        if not p or not p["in_stock"]:
            raise ValueError("Товара нет в наличии")
        with self.con:
            self.con.execute(
                "INSERT INTO cart(user_id,product_id,qty) VALUES(?,?,?) "
                "ON CONFLICT(user_id,product_id) DO UPDATE SET qty=qty+excluded.qty",
                (user_id, pid, qty))

    def cart_set(self, user_id, pid, qty):
        with self.con:
            if qty <= 0:
                self.con.execute("DELETE FROM cart WHERE user_id=? AND product_id=?", (user_id, pid))
            else:
                self.con.execute("UPDATE cart SET qty=? WHERE user_id=? AND product_id=?", (qty, user_id, pid))

    def cart(self, user_id):
        return self.con.execute(
            "SELECT p.id, p.name, p.price, c.qty, p.price*c.qty AS sum "
            "FROM cart c JOIN products p ON p.id=c.product_id WHERE c.user_id=? ORDER BY p.name",
            (user_id,)).fetchall()

    def cart_total(self, user_id):
        return sum(r["sum"] for r in self.cart(user_id))

    # --- заказы ------------------------------------------------------------
    def make_order(self, user_id, name, phone, address="", receipt_id=None):
        items = self.cart(user_id)
        if not items:
            raise ValueError("Корзина пуста")
        total = sum(r["sum"] for r in items)
        with self.con:
            cur = self.con.execute(
                "INSERT INTO orders(user_id,name,phone,address,receipt_id,total) VALUES(?,?,?,?,?,?)",
                (user_id, name, phone, address, receipt_id, total))
            oid = cur.lastrowid
            self.con.executemany(
                "INSERT INTO order_items(order_id,product_id,name,price,qty) VALUES(?,?,?,?,?)",
                [(oid, r["id"], r["name"], r["price"], r["qty"]) for r in items])
            self.con.execute("DELETE FROM cart WHERE user_id=?", (user_id,))
        return oid

    def order(self, oid):
        o = self.con.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        items = self.con.execute("SELECT * FROM order_items WHERE order_id=?", (oid,)).fetchall()
        return o, items

    def orders(self, status=None, user_id=None):
        sql, args = "SELECT * FROM orders WHERE 1=1", []
        if status:
            sql += " AND status=?"; args.append(status)
        if user_id:
            sql += " AND user_id=?"; args.append(user_id)
        return self.con.execute(sql + " ORDER BY id DESC", args).fetchall()

    def set_status(self, oid, status):
        if status not in STATUSES:
            raise ValueError(f"Неизвестный статус: {status}")
        with self.con:
            self.con.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
