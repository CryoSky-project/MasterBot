# ==============================================================================
# 1-BO'LIM: MA'LUMOTLAR BAZASINI INIZIALIZATSIYA QILISH (POSTGRESQL & SQLITE)
# ==============================================================================
# Ushbu modul ma'lumotlar bazasini boshqarish va Postgres/SQLite ulanishlarini ta'minlaydi.
# Barcha sharhlar o'zbek tilida yozilgan.

import os
import asyncio
import asyncpg
import aiosqlite
from datetime import datetime, date
from dotenv import load_dotenv

load_dotenv()

# Global ma'lumotlar bazasi menejeri obyekti
class DatabaseManager:
    def __init__(self):
        self.is_sqlite = False
        self.pg_pool = None
        self.sqlite_conn = None
        self.db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db")

    async def connect(self):
        """Ma'lumotlar bazasiga ulanish. Agar PostgreSQL bo'lmasa, SQLite-ga o'tadi."""
        db_url = os.getenv("DATABASE_URL")
        
        # Agar DATABASE_URL bo'lmasa yoki u postgresql bilan boshlanmasa, SQLite-ga o'tish
        if not db_url or not db_url.startswith("postgresql"):
            print("DATABASE_URL topilmadi yoki noto'g'ri. SQLite-ga o'tilmoqda...")
            self.is_sqlite = True
            self.sqlite_conn = await aiosqlite.connect(self.db_path)
            self.sqlite_conn.row_factory = aiosqlite.Row
            return
            
        try:
            # PostgreSQL-ga ulanishga urinib ko'rish
            self.pg_pool = await asyncpg.create_pool(db_url, min_size=1, max_size=10, statement_cache_size=0)
            # Ulanishni tekshirish
            async with self.pg_pool.acquire() as conn:
                await conn.execute("SELECT 1")
            self.is_sqlite = False
            print("PostgreSQL ma'lumotlar bazasiga muvaffaqiyatli ulandi!")
        except Exception as e:
            print(f"PostgreSQL-ga ulanishda xatolik: {e}. SQLite ulanishiga o'tilmoqda...")
            self.is_sqlite = True
            self.sqlite_conn = await aiosqlite.connect(self.db_path)
            self.sqlite_conn.row_factory = aiosqlite.Row

    async def init_tables(self):
        """Jadvallarni yaratish va boshlang'ich sozlamalarni kiritish."""
        if self.is_sqlite:
            # SQLite jadvallarini yaratish
            await self.sqlite_conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    lang TEXT DEFAULT 'uz',
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await self.sqlite_conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            # Boshlang'ich sozlamalar
            await self.sqlite_conn.execute("""
                INSERT OR IGNORE INTO settings (key, value) VALUES
                ('polling_price', '20000'),
                ('webhook_price', '25000'),
                ('bot_sale_price', '60000'),
                ('card_number', '8600 0000 0000 0000'),
                ('provider_token', ''),
                ('auto_create_bot', 'false');
            """)
            await self.sqlite_conn.execute("""
                CREATE TABLE IF NOT EXISTS master_clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id INTEGER NOT NULL,
                    bot_username TEXT NOT NULL,
                    bot_token TEXT NOT NULL,
                    server_folder TEXT NOT NULL,
                    mode TEXT DEFAULT 'polling',
                    monthly_price REAL DEFAULT 15000,
                    last_payment_date DATE NOT NULL,
                    next_payment_date DATE NOT NULL,
                    status TEXT DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await self.sqlite_conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    bot_username TEXT NOT NULL,
                    bot_token TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    total_price REAL NOT NULL,
                    receipt_file_id TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await self.sqlite_conn.commit()
        else:
            # PostgreSQL jadvallarini yaratish
            async with self.pg_pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT PRIMARY KEY,
                        username TEXT,
                        full_name TEXT,
                        lang TEXT DEFAULT 'uz',
                        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                """)
                await conn.execute("""
                    INSERT INTO settings (key, value) VALUES
                    ('polling_price', '20000'),
                    ('webhook_price', '25000'),
                    ('bot_sale_price', '60000'),
                    ('card_number', '8600 0000 0000 0000'),
                    ('provider_token', ''),
                    ('auto_create_bot', 'false')
                    ON CONFLICT (key) DO NOTHING;
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS master_clients (
                        id SERIAL PRIMARY KEY,
                        client_id BIGINT NOT NULL,
                        bot_username TEXT NOT NULL,
                        bot_token TEXT NOT NULL,
                        server_folder TEXT NOT NULL,
                        mode TEXT DEFAULT 'polling',
                        monthly_price NUMERIC DEFAULT 15000,
                        last_payment_date DATE NOT NULL,
                        next_payment_date DATE NOT NULL,
                        status TEXT DEFAULT 'active',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS orders (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        bot_username TEXT NOT NULL,
                        bot_token TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        total_price NUMERIC NOT NULL,
                        receipt_file_id TEXT,
                        status TEXT DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)

db_manager = DatabaseManager()

async def init_db():
    await db_manager.connect()
    await db_manager.init_tables()

async def get_db() -> DatabaseManager:
    if db_manager.is_sqlite and db_manager.sqlite_conn is None:
        await init_db()
    elif not db_manager.is_sqlite and db_manager.pg_pool is None:
        await init_db()
    return db_manager

def sqlite_row_to_dict(row, date_fields=None):
    """SQLite qatorini oddiy dict-ga o'tkazish va sanalarni to'g'ri formatlash."""
    if row is None:
        return None
    d = dict(row)
    if date_fields:
        for f in date_fields:
            if f in d and d[f]:
                if isinstance(d[f], str):
                    try:
                        d[f] = datetime.strptime(d[f].split()[0], "%Y-%m-%d").date()
                    except Exception:
                        pass
    return d

# ==============================================================================
# 2-BO'LIM: TIZIM SOZLAMALARI VA KONFIGURATSIYALARI
# ==============================================================================
async def get_setting(key: str, default: str = "") -> str:
    db = await get_db()
    if db.is_sqlite:
        async with db.sqlite_conn.execute("SELECT value FROM settings WHERE key = ?;", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row is not None else default
    else:
        async with db.pg_pool.acquire() as conn:
            val = await conn.fetchval("SELECT value FROM settings WHERE key = $1;", key)
            return val if val is not None else default

async def set_setting(key: str, value: str):
    db = await get_db()
    if db.is_sqlite:
        await db.sqlite_conn.execute("""
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value;
        """, (key, str(value)))
        await db.sqlite_conn.commit()
    else:
        async with db.pg_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO settings (key, value) VALUES ($1, $2)
                ON CONFLICT (key) DO UPDATE SET value = $2;
            """, key, str(value))

# ==============================================================================
# 3-BO'LIM: FOYDALANUVCHI PROFILI OPERATSIYALARI
# ==============================================================================
async def add_user(user_id: int, username: str, full_name: str):
    db = await get_db()
    if db.is_sqlite:
        await db.sqlite_conn.execute("""
            INSERT INTO users (user_id, username, full_name)
            VALUES (?, ?, ?)
            ON CONFLICT (user_id) DO UPDATE SET username = excluded.username, full_name = excluded.full_name;
        """, (user_id, username, full_name))
        await db.sqlite_conn.commit()
    else:
        async with db.pg_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO users (user_id, username, full_name)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id) DO UPDATE SET username = $2, full_name = $3;
            """, user_id, username, full_name)

async def get_all_users():
    db = await get_db()
    if db.is_sqlite:
        async with db.sqlite_conn.execute("SELECT * FROM users ORDER BY joined_at DESC;") as cursor:
            rows = await cursor.fetchall()
            return [sqlite_row_to_dict(r) for r in rows]
    else:
        async with db.pg_pool.acquire() as conn:
            return await conn.fetch("SELECT * FROM users ORDER BY joined_at DESC;")

async def get_user_by_id(user_id: int):
    """Foydalanuvchini ID bo'yicha olish."""
    db = await get_db()
    if db.is_sqlite:
        async with db.sqlite_conn.execute("SELECT * FROM users WHERE user_id = ?;", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return sqlite_row_to_dict(row)
    else:
        async with db.pg_pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1;", user_id)

# ==============================================================================
# 4-BO'LIM: MIJOZ BOTLARINI BOSHQARISH
# ==============================================================================
async def add_client_bot(client_id: int, bot_username: str, bot_token: str, server_folder: str, mode: str, monthly_price: float, last_payment_date: str, next_payment_date: str) -> int:
    db = await get_db()
    l_date = datetime.strptime(last_payment_date, "%Y-%m-%d").date()
    n_date = datetime.strptime(next_payment_date, "%Y-%m-%d").date()
    
    if db.is_sqlite:
        cursor = await db.sqlite_conn.execute("""
            INSERT INTO master_clients (client_id, bot_username, bot_token, server_folder, mode, monthly_price, last_payment_date, next_payment_date, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active');
        """, (client_id, bot_username, bot_token, server_folder, mode, monthly_price, l_date.strftime("%Y-%m-%d"), n_date.strftime("%Y-%m-%d")))
        last_id = cursor.lastrowid
        await db.sqlite_conn.commit()
        return last_id
    else:
        async with db.pg_pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO master_clients (client_id, bot_username, bot_token, server_folder, mode, monthly_price, last_payment_date, next_payment_date, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'active')
                RETURNING id;
            """, client_id, bot_username, bot_token, server_folder, mode, monthly_price, l_date, n_date)

async def get_all_clients():
    db = await get_db()
    date_fields = ['last_payment_date', 'next_payment_date']
    if db.is_sqlite:
        async with db.sqlite_conn.execute("""
            SELECT m.*, u.full_name as user_full_name, u.username as user_username
            FROM master_clients m
            LEFT JOIN users u ON m.client_id = u.user_id
            ORDER BY m.id ASC;
        """) as cursor:
            rows = await cursor.fetchall()
            return [sqlite_row_to_dict(r, date_fields) for r in rows]
    else:
        async with db.pg_pool.acquire() as conn:
            return await conn.fetch("""
                SELECT m.*, u.full_name as user_full_name, u.username as user_username
                FROM master_clients m
                LEFT JOIN users u ON m.client_id = u.user_id
                ORDER BY m.id ASC;
            """)

async def get_client_by_id(client_record_id: int):
    db = await get_db()
    date_fields = ['last_payment_date', 'next_payment_date']
    if db.is_sqlite:
        async with db.sqlite_conn.execute("""
            SELECT m.*, u.full_name as user_full_name, u.username as user_username
            FROM master_clients m
            LEFT JOIN users u ON m.client_id = u.user_id
            WHERE m.id = ?;
        """, (client_record_id,)) as cursor:
            row = await cursor.fetchone()
            return sqlite_row_to_dict(row, date_fields)
    else:
        async with db.pg_pool.acquire() as conn:
            return await conn.fetchrow("""
                SELECT m.*, u.full_name as user_full_name, u.username as user_username
                FROM master_clients m
                LEFT JOIN users u ON m.client_id = u.user_id
                WHERE m.id = $1;
            """, client_record_id)

async def update_client_field(client_record_id: int, field_name: str, value):
    db = await get_db()
    valid_fields = ['client_id', 'bot_username', 'bot_token', 'server_folder', 'mode', 'monthly_price', 'last_payment_date', 'next_payment_date', 'status']
    if field_name not in valid_fields:
        return
        
    # Qiymat turlarini moslashtirish
    if 'date' in field_name and isinstance(value, str):
        parsed_value = datetime.strptime(value, "%Y-%m-%d").date()
    elif field_name == 'client_id':
        parsed_value = int(value)
    elif field_name == 'monthly_price':
        parsed_value = float(value)
    else:
        parsed_value = value
        
    if db.is_sqlite:
        # SQLite uchun sanalarni string ko'rinishida saqlash
        if isinstance(parsed_value, (date, datetime)):
            val_to_save = parsed_value.strftime("%Y-%m-%d")
        else:
            val_to_save = parsed_value
            
        await db.sqlite_conn.execute(f"UPDATE master_clients SET {field_name} = ? WHERE id = ?;", (val_to_save, client_record_id))
        await db.sqlite_conn.commit()
    else:
        async with db.pg_pool.acquire() as conn:
            await conn.execute(f"UPDATE master_clients SET {field_name} = $1 WHERE id = $2;", parsed_value, client_record_id)

async def delete_client_bot(client_record_id: int):
    db = await get_db()
    if db.is_sqlite:
        await db.sqlite_conn.execute("DELETE FROM master_clients WHERE id = ?;", (client_record_id,))
        await db.sqlite_conn.commit()
    else:
        async with db.pg_pool.acquire() as conn:
            await conn.execute("DELETE FROM master_clients WHERE id = $1;", client_record_id)

async def get_client_bots_by_user(client_id: int):
    db = await get_db()
    date_fields = ['last_payment_date', 'next_payment_date']
    if db.is_sqlite:
        async with db.sqlite_conn.execute("SELECT * FROM master_clients WHERE client_id = ? ORDER BY next_payment_date ASC;", (client_id,)) as cursor:
            rows = await cursor.fetchall()
            return [sqlite_row_to_dict(r, date_fields) for r in rows]
    else:
        async with db.pg_pool.acquire() as conn:
            return await conn.fetch("SELECT * FROM master_clients WHERE client_id = $1 ORDER BY next_payment_date ASC;", client_id)

async def search_clients(query: str):
    db = await get_db()
    date_fields = ['last_payment_date', 'next_payment_date']
    if db.is_sqlite:
        if query.isdigit():
            cid = int(query)
            async with db.sqlite_conn.execute("""
                SELECT m.*, u.full_name as user_full_name, u.username as user_username
                FROM master_clients m
                LEFT JOIN users u ON m.client_id = u.user_id
                WHERE m.client_id = ? OR m.bot_username LIKE ? OR u.full_name LIKE ? OR u.username LIKE ?
                ORDER BY m.id ASC;
            """, (cid, f"%{query}%", f"%{query}%", f"%{query}%")) as cursor:
                rows = await cursor.fetchall()
                return [sqlite_row_to_dict(r, date_fields) for r in rows]
        else:
            async with db.sqlite_conn.execute("""
                SELECT m.*, u.full_name as user_full_name, u.username as user_username
                FROM master_clients m
                LEFT JOIN users u ON m.client_id = u.user_id
                WHERE m.bot_username LIKE ? OR u.full_name LIKE ? OR u.username LIKE ?
                ORDER BY m.id ASC;
            """, (f"%{query}%", f"%{query}%", f"%{query}%")) as cursor:
                rows = await cursor.fetchall()
                return [sqlite_row_to_dict(r, date_fields) for r in rows]
    else:
        async with db.pg_pool.acquire() as conn:
            if query.isdigit():
                cid = int(query)
                return await conn.fetch("""
                    SELECT m.*, u.full_name as user_full_name, u.username as user_username
                    FROM master_clients m
                    LEFT JOIN users u ON m.client_id = u.user_id
                    WHERE m.client_id = $1 OR m.bot_username ILIKE $2 OR u.full_name ILIKE $2 OR u.username ILIKE $2
                    ORDER BY m.id ASC;
                """, cid, f"%{query}%")
            else:
                return await conn.fetch("""
                    SELECT m.*, u.full_name as user_full_name, u.username as user_username
                    FROM master_clients m
                    LEFT JOIN users u ON m.client_id = u.user_id
                    WHERE m.bot_username ILIKE $1 OR u.full_name ILIKE $1 OR u.username ILIKE $1
                    ORDER BY m.id ASC;
                """, f"%{query}%")

# ==============================================================================
# 5-BO'LIM: XARID BUYURTMALARINI BOSHQARISH
# ==============================================================================
async def create_order(user_id: int, bot_username: str, bot_token: str, mode: str, total_price: float, receipt_file_id: str = None) -> int:
    db = await get_db()
    if db.is_sqlite:
        cursor = await db.sqlite_conn.execute("""
            INSERT INTO orders (user_id, bot_username, bot_token, mode, total_price, receipt_file_id, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending');
        """, (user_id, bot_username, bot_token, mode, total_price, receipt_file_id))
        last_id = cursor.lastrowid
        await db.sqlite_conn.commit()
        return last_id
    else:
        async with db.pg_pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO orders (user_id, bot_username, bot_token, mode, total_price, receipt_file_id, status)
                VALUES ($1, $2, $3, $4, $5, $6, 'pending')
                RETURNING id;
            """, user_id, bot_username, bot_token, mode, total_price, receipt_file_id)

async def get_order_by_id(order_id: int):
    db = await get_db()
    if db.is_sqlite:
        async with db.sqlite_conn.execute("SELECT * FROM orders WHERE id = ?;", (order_id,)) as cursor:
            row = await cursor.fetchone()
            return sqlite_row_to_dict(row)
    else:
        async with db.pg_pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM orders WHERE id = $1;", order_id)

async def update_order_status(order_id: int, status: str):
    db = await get_db()
    if db.is_sqlite:
        await db.sqlite_conn.execute("UPDATE orders SET status = ? WHERE id = ?;", (status, order_id))
        await db.sqlite_conn.commit()
    else:
        async with db.pg_pool.acquire() as conn:
            await conn.execute("UPDATE orders SET status = $1 WHERE id = $2;", status, order_id)

async def get_user_lang(user_id: int) -> str:
    """Foydalanuvchining til sozlamasini olish (default: uz) (o'zbekcha sharh)"""
    db = await get_db()
    if db.is_sqlite:
        async with db.sqlite_conn.execute("SELECT lang FROM users WHERE user_id = ?;", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if (row is not None and row[0]) else "uz"
    else:
        async with db.pg_pool.acquire() as conn:
            val = await conn.fetchval("SELECT lang FROM users WHERE user_id = $1;", user_id)
            return val if val else "uz"

async def set_user_lang(user_id: int, lang: str):
    """Foydalanuvchining til sozlamasini saqlash (o'zbekcha sharh)"""
    db = await get_db()
    if db.is_sqlite:
        await db.sqlite_conn.execute("UPDATE users SET lang = ? WHERE user_id = ?;", (lang, user_id))
        await db.sqlite_conn.commit()
    else:
        async with db.pg_pool.acquire() as conn:
            await conn.execute("UPDATE users SET lang = $1 WHERE user_id = $2;", lang, user_id)
