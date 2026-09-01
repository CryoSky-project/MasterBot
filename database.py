# ==============================================================================
# 1-BO'LIM: MA'LUMOTLAR BAZASINI INIZIALIZATSIYA QILISH VA POOL SOZLAMALARI
# ==============================================================================
import os
import asyncio
import asyncpg
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user_master:pass_master@localhost:5432/db_master")

pool: asyncpg.Pool = None

async def init_db():
    """PostgreSQL ulanish poolini ishga tushirish va agar mavjud bo'lmasa sxemalarni yaratish."""
    global pool
    if pool is None:
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    
    async with pool.acquire() as conn:
        # Users table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Tizim sozlamalari jadvali
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        
        # Boshlang'ich sozlamalarni kiritish
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

        # Mijozlar va Botlar jadvali
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

        # Bot xaridlari uchun buyurtmalar jadvali
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

async def get_db() -> asyncpg.Pool:
    """Faol ma'lumotlar bazasi pool obyektini olish."""
    global pool
    if pool is None:
        await init_db()
    return pool

# ==============================================================================
# 2-BO'LIM: TIZIM SOZLAMALARI VA KONFIGURATSIYALARI
# ==============================================================================
async def get_setting(key: str, default: str = "") -> str:
    """Kalit bo'yicha sozlama qiymatini olish."""
    p = await get_db()
    async with p.acquire() as conn:
        val = await conn.fetchval("SELECT value FROM settings WHERE key = $1;", key)
        return val if val is not None else default

async def set_setting(key: str, value: str):
    """Sozlama qiymatini saqlash yoki yangilash."""
    p = await get_db()
    async with p.acquire() as conn:
        await conn.execute("""
            INSERT INTO settings (key, value) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = $2;
        """, key, str(value))

# ==============================================================================
# 3-BO'LIM: FOYDALANUVCHI PROFILI OPERATSIYALARI
# ==============================================================================
async def add_user(user_id: int, username: str, full_name: str):
    """Bot foydalanuvchisi profilini ro'yxatdan o'tkazish yoki yangilash."""
    p = await get_db()
    async with p.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, username, full_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO UPDATE SET username = $2, full_name = $3;
        """, user_id, username, full_name)

async def get_all_users():
    """Barcha foydalanuvchilar ro'yxatini olish."""
    p = await get_db()
    async with p.acquire() as conn:
        return await conn.fetch("SELECT * FROM users ORDER BY joined_at DESC;")

# ==============================================================================
# 4-BO'LIM: MIJOZ BOTLARINI BOSHQARISH
# ==============================================================================
async def add_client_bot(client_id: int, bot_username: str, bot_token: str, server_folder: str, mode: str, monthly_price: float, last_payment_date: str, next_payment_date: str) -> int:
    """Faol ro'yxatga yangi mijoz boti yozuvini qo'shish."""
    p = await get_db()
    l_date = datetime.strptime(last_payment_date, "%Y-%m-%d").date()
    n_date = datetime.strptime(next_payment_date, "%Y-%m-%d").date()
    
    async with p.acquire() as conn:
        return await conn.fetchval("""
            INSERT INTO master_clients (client_id, bot_username, bot_token, server_folder, mode, monthly_price, last_payment_date, next_payment_date, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'active')
            RETURNING id;
        """, client_id, bot_username, bot_token, server_folder, mode, monthly_price, l_date, n_date)

async def get_all_clients():
    """Barcha sozlangan mijoz botlarini olish."""
    p = await get_db()
    async with p.acquire() as conn:
        return await conn.fetch("SELECT * FROM master_clients ORDER BY id ASC;")

async def get_client_by_id(client_record_id: int):
    """Yozuv id si bo'yicha bot metama'lumotlarini olish."""
    p = await get_db()
    async with p.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM master_clients WHERE id = $1;", client_record_id)

async def update_client_field(client_record_id: int, field_name: str, value):
    """Mijoz boti yozuvining maydon qiymatlarini dinamik ravishda yangilash."""
    p = await get_db()
    valid_fields = ['client_id', 'bot_username', 'bot_token', 'server_folder', 'mode', 'monthly_price', 'last_payment_date', 'next_payment_date', 'status']
    if field_name not in valid_fields:
        return
    
    async with p.acquire() as conn:
        if 'date' in field_name and isinstance(value, str):
            value = datetime.strptime(value, "%Y-%m-%d").date()
        elif field_name == 'client_id':
            value = int(value)
        elif field_name == 'monthly_price':
            value = float(value)
            
        await conn.execute(f"UPDATE master_clients SET {field_name} = $1 WHERE id = $2;", value, client_record_id)

async def delete_client_bot(client_record_id: int):
    """Mijoz boti yozuvini o'chirish."""
    p = await get_db()
    async with p.acquire() as conn:
        await conn.execute("DELETE FROM master_clients WHERE id = $1;", client_record_id)

async def get_client_bots_by_user(client_id: int):
    """Muayyan Telegram foydalanuvchi ID siga tegishli mijoz botlarini olish."""
    p = await get_db()
    async with p.acquire() as conn:
        return await conn.fetch("SELECT * FROM master_clients WHERE client_id = $1 ORDER BY next_payment_date ASC;", client_id)

async def search_clients(query: str):
    """master_clients ichidan bot_username yoki client_id bo'yicha mijozlarni qidirish."""
    p = await get_db()
    async with p.acquire() as conn:
        if query.isdigit():
            cid = int(query)
            return await conn.fetch(
                "SELECT * FROM master_clients WHERE client_id = $1 OR bot_username ILIKE $2 ORDER BY id ASC;",
                cid, f"%{query}%"
            )
        else:
            return await conn.fetch(
                "SELECT * FROM master_clients WHERE bot_username ILIKE $1 ORDER BY id ASC;",
                f"%{query}%"
            )

# ==============================================================================
# 5-BO'LIM: XARID BUYURTMALARINI BOSHQARISH
# ==============================================================================
async def create_order(user_id: int, bot_username: str, bot_token: str, mode: str, total_price: float, receipt_file_id: str = None) -> int:
    """Yangi bot xarid qilish buyurtmasini yaratish."""
    p = await get_db()
    async with p.acquire() as conn:
        return await conn.fetchval("""
            INSERT INTO orders (user_id, bot_username, bot_token, mode, total_price, receipt_file_id, status)
            VALUES ($1, $2, $3, $4, $5, $6, 'pending')
            RETURNING id;
        """, user_id, bot_username, bot_token, mode, total_price, receipt_file_id)

async def get_order_by_id(order_id: int):
    """Id bo'yicha buyurtma ma'lumotlarini olish."""
    p = await get_db()
    async with p.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM orders WHERE id = $1;", order_id)

async def update_order_status(order_id: int, status: str):
    """Xarid buyurtmasining holatini yangilash."""
    p = await get_db()
    async with p.acquire() as conn:
        await conn.execute("UPDATE orders SET status = $1 WHERE id = $2;", status, order_id)
