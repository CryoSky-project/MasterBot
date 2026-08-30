# ==============================================================================
# 1-BO'LIM: KUTUBXONALARNI IMPORT QILISH VA BOSH KOD
# ==============================================================================
import os
import re
import asyncio
import logging
import html as py_html
from datetime import datetime, timedelta
from dotenv import load_dotenv
import paramiko
import aiohttp
from aiohttp import web

# Telegram Bot uchun tashqi kutubxona importlari (aiogram v3)
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Ma'lumotlar bazasi operatsiyalari importi
from database import (
    init_db, add_user, get_all_users, add_client_bot, get_all_clients,
    get_client_by_id, update_client_field, delete_client_bot,
    get_client_bots_by_user, get_setting, set_setting, create_order,
    get_order_by_id, update_order_status, search_clients
)

# Muhit o'zgaruvchilarini yuklash
# Muhit o'zgaruvchilarini yuklash
load_dotenv()

async def run_vps_command(cmd: str) -> tuple[int, str, str]:
    """
    VPS serverida SSH orqali buyruqni bajarish (o'zbekcha sharh).
    Render-da ishlayotganda ushbu funksiya VPS-dagi client botlarni boshqaradi.
    """
    vps_host = os.getenv("VPS_HOST", "157.173.110.5")
    vps_port = int(os.getenv("VPS_PORT", "22"))
    vps_user = os.getenv("VPS_USER", "root")
    vps_pass = os.getenv("VPS_PASSWORD", "NY0XMsJMJRd1043Tn252")
    
    loop = asyncio.get_event_loop()
    def _run():
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(vps_host, port=vps_port, username=vps_user, password=vps_pass, timeout=10)
            stdin, stdout, stderr = ssh.exec_command(cmd)
            stdout_str = stdout.read().decode("utf-8")
            stderr_str = stderr.read().decode("utf-8")
            exit_status = stdout.channel.recv_exit_status()
            return exit_status, stdout_str, stderr_str
        except Exception as e:
            return -1, "", str(e)
        finally:
            ssh.close()
            
    return await loop.run_in_executor(None, _run)

# Logger sozlamalarini konfiguratsiya qilish
logging.basicConfig(level=logging.INFO)

# ==============================================================================
# 2-BO'LIM: SOZLAMALAR VA GLOBAL O'ZGARUVCHILAR
# ==============================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8714157634:AAF6oBKitMgPZadMiG1JCELD9CFM8zMu1tY")
admin_env = os.getenv("ADMIN_IDS", "8551089366")
if not admin_env or not admin_env.strip():
    admin_env = "8551089366"
ADMIN_IDS = [int(i.strip()) for i in admin_env.split(",") if i.strip()]
ADMINS = ADMIN_IDS

# Bot va Dispatcher obyektlarini yaratish
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ==============================================================================
# 3-BO'LIM: O'ZGARMASLAR VA VALIDATSIYA
# ==============================================================================
MENU_BUTTONS = [
    "👤 Mijozlar paneli",
    "💳 Karta va Rejimlar",
    "📊 Statistika",
    "➕ Mijoz qo'shish",
    "📋 Mijozlar ro'yxati",
    "✏️ Mijoz tahrirlash",
    "🗑 Mijoz o'chirish",
    "🔍 Mijoz qidirish",
    "⚡️ Polling narxini o'zgartirish",
    "🌐 Webhook narxini o'zgartirish",
    "🤖 Bot narxini o'zgartirish",
    "💳 Karta raqamini o'zgartirish",
    "💳 Telegram to'lov tokenini o'zgartirish",
    "⬅️ Orqaga",
    "❌ Bekor qilish",
    "🛒 Bot sotib olish",
    "👤 Mening botlarim va to'lovlarim",
    "📞 Admin bilan bog'lanish",
    "📢 Reklama tarqatish",
    "ℹ️ Bot haqida malumot"
]

def is_menu_button_or_command(text: str) -> bool:
    """Matn biror buyruq prefiksi yoki menyu tugmasiga mos kelishini tekshirish."""
    if not text:
        return False
    if text.startswith("/") or text in MENU_BUTTONS:
        return True
    return False

# ==============================================================================
# 4-BO'LIM: YORDAMCHI FUNKSIYALAR VA API TEKSHIRUVLARI
# ==============================================================================
def parse_flexible_date(date_str: str) -> str:
    """Moslashuvchan sana matnlarini (masalan, YYYYMMDD, YYYY.MM.DD) YYYY-MM-DD formatiga o'tkazish."""
    date_str = date_str.strip()
    
    if re.match(r'^\d{8}$', date_str):
        yyyy = date_str[:4]
        mm = date_str[4:6]
        dd = date_str[6:8]
        date_str = f"{yyyy}-{mm}-{dd}"
        
    cleaned = re.sub(r'[\.\/\s]+', '-', date_str)
    
    # Agar faqat kun va oy kiritilgan bo'lsa (masalan "20-07"), 2026 yilni avtomat qo'shamiz
    if re.match(r'^\d{1,2}-\d{1,2}$', cleaned):
        cleaned = f"{cleaned}-2026"
        
    # 4-xonali va 2-xonali yil formatlarini qo'llab-quvvatlash
    formats = (
        "%Y-%m-%d", "%d-%m-%Y", "%Y-%d-%m",
        "%y-%m-%d", "%d-%m-%y", "%y-%d-%m"
    )
    
    for fmt in formats:
        try:
            dt = datetime.strptime(cleaned, fmt)
            if dt.year < 100:
                dt = dt.replace(year=2000 + dt.year)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
            
    raise ValueError(f"Sana formati noto'g'ri: {date_str}")

async def validate_bot_data(client_id: int, bot_username: str, bot_token: str, server_folder: str, last_payment: str, next_payment: str):
    """Telegram Bot Tokenini, server yo'lini va to'lov sanalarini asinxron tekshirish."""
    issues = []
    actual_username = bot_username

    try:
        from aiogram import Bot
        temp_bot = Bot(token=bot_token)
        try:
            res = await temp_bot.get_me()
            actual_username = f"@{res.username}"
        except Exception as e:
            issues.append(f"❌ <b>Bot API Token noto'g'ri:</b> Telegram API ({str(e)})")
        finally:
            await temp_bot.session.close()
    except Exception as e:
        issues.append(f"⚠️ <b>Bot API Token tekshirishda xatolik:</b> {e}")

    folder_path = server_folder if server_folder.startswith("/") else os.path.join("/root", server_folder)
    # VPS-da SSH orqali papka mavjudligini tekshirish
    exit_status, stdout_str, stderr_str = await run_vps_command(f'[ -d "{folder_path}" ] && echo "yes" || echo "no"')
    if stdout_str.strip() != "yes":
        issues.append(f"⚠️ <b>VPS serverda papka topilmadi:</b> <code>{folder_path}</code>")

    try:
        d1 = datetime.strptime(last_payment, "%Y-%m-%d").date()
        d2 = datetime.strptime(next_payment, "%Y-%m-%d").date()
        if d2 <= d1:
            issues.append(f"⚠️ <b>Keyingi to'lov sanasi ({next_payment}) oxirgi to'lov sanasidan ({last_payment}) kichik yoki teng!</b>")
    except Exception:
        issues.append("❌ <b>Sana formatini tahlil qilishda xatolik!</b>")

    return issues, actual_username

# ==============================================================================
# 5-BO'LIM: FSM HOLATLARI (FINITE STATE MACHINE)
# ==============================================================================
class AddClientState(StatesGroup):
    waiting_for_client_id = State()
    waiting_for_bot_username = State()
    waiting_for_bot_token = State()
    waiting_for_server_folder = State()
    waiting_for_mode = State()
    waiting_for_last_payment = State()
    waiting_for_next_payment = State()
    waiting_for_validation_confirm = State()

class EditClientState(StatesGroup):
    waiting_for_record_id = State()
    waiting_for_new_value = State()

class DeleteClientState(StatesGroup):
    waiting_for_record_id = State()

class SettingState(StatesGroup):
    waiting_for_polling_price = State()
    waiting_for_webhook_price = State()
    waiting_for_bot_sale_price = State()
    waiting_for_card_number = State()
    waiting_for_provider_token = State()

class BuyBotState(StatesGroup):
    waiting_for_bot_type = State()
    waiting_for_boshqa_desc = State()
    waiting_for_mode_selection = State()
    waiting_for_pay_method = State()
    waiting_for_receipt = State()
    waiting_for_receipt_time = State()
    waiting_for_bot_username = State()
    waiting_for_bot_token = State()

class AdminOrderActionState(StatesGroup):
    waiting_for_folder_name = State()
    waiting_for_reject_reason = State()

class BroadcastState(StatesGroup):
    waiting_for_message = State()

class SearchClientState(StatesGroup):
    waiting_for_query = State()

class BotMgmtState(StatesGroup):
    in_bot_panel = State()
    waiting_for_search_query = State()

class AdminMenuState(StatesGroup):
    in_clients_panel = State()
    in_modes_panel = State()
    in_user_panel = State()

# ==============================================================================
# 6-BO'LIM: TUGMA YASOVCHILAR
# ==============================================================================
def get_admin_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="👤 Mijozlar paneli")
    builder.button(text="🤖 Botlarni boshqarish")
    builder.button(text="📊 Statistika")
    builder.button(text="📢 Reklama tarqatish")
    builder.button(text="👤 User paneli")
    builder.adjust(2, 2, 1)
    return builder.as_markup(resize_keyboard=True)

def get_bot_mgmt_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="🔍 Bot izlash")
    builder.button(text="📋 Mijoz botlar ro'yxati")
    builder.button(text="⬅️ Orqaga")
    builder.adjust(2, 1)
    return builder.as_markup(resize_keyboard=True)

def get_clients_panel_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="➕ Mijoz qo'shish")
    builder.button(text="📋 Mijozlar ro'yxati")
    builder.button(text="✏️ Mijoz tahrirlash")
    builder.button(text="🗑 Mijoz o'chirish")
    builder.button(text="💳 Karta va Rejimlar")
    builder.button(text="🔍 Mijoz qidirish")
    builder.button(text="⬅️ Orqaga")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup(resize_keyboard=True)

def get_modes_settings_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="⚡️ Polling narxini o'zgartirish")
    builder.button(text="🌐 Webhook narxini o'zgartirish")
    builder.button(text="🤖 Bot narxini o'zgartirish")
    builder.button(text="💳 Karta raqamini o'zgartirish")
    builder.button(text="💳 Telegram to'lov tokenini o'zgartirish")
    builder.button(text="🤖 Auto Bot Yaratish")
    builder.button(text="⬅️ Orqaga")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup(resize_keyboard=True)

def get_cancel_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="❌ Bekor qilish")
    return builder.as_markup(resize_keyboard=True)

def get_client_user_keyboard(is_admin: bool = False):
    builder = ReplyKeyboardBuilder()
    builder.button(text="🛒 Bot sotib olish")
    builder.button(text="👤 Mening botlarim va to'lovlarim")
    builder.button(text="📞 Admin bilan bog'lanish")
    builder.button(text="ℹ️ Bot haqida malumot")
    if is_admin:
        builder.button(text="👑 Admin paneli")
        builder.adjust(2, 2, 1)
    else:
        builder.adjust(2, 2)
    return builder.as_markup(resize_keyboard=True)

# ==============================================================================
# 7-BO'LIM: NAVIGATSIYA VA ASOSIY HANDLERLAR
# ==============================================================================
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username or ""
    full_name = message.from_user.full_name or ""
    
    await add_user(user_id, username, full_name)
    
    if user_id in ADMINS:
        admin_text = (
            f"👑 <b>Xush kelibsiz, Admin {py_html.escape(full_name)}!</b>\n\n"
            f"🤖 <b>SKY MASTER BOT - Boshqaruv Paneli</b>\n\n"
            f"Quyidagi bo'limlardan birini tanlang:"
        )
        await message.answer(admin_text, parse_mode="HTML", reply_markup=get_admin_main_keyboard())
    else:
        client_text = (
            f"👋 <b>Assalomu alaykum, {py_html.escape(full_name)}!</b>\n\n"
            f"🤖 <b>SKY MASTER BOT</b> tizimiga xush kelibsiz!\n\n"
            f"Siz bu yerda tayyor bot sotib olishingiz yoki o'z botingizning to'lov muddatlarini kuzatishingiz mumkin."
        )
        await message.answer(client_text, parse_mode="HTML", reply_markup=get_client_user_keyboard())

@dp.message(F.text == "❌ Bekor qilish")
async def cancel_any_action(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    user_id = message.from_user.id
    
    if user_id in ADMINS:
        if current_state and ("SettingState" in current_state):
            await modes_panel_handler(message, state)
        elif current_state and any(s in current_state for s in ["AddClientState", "EditClientState", "DeleteClientState", "SearchClientState"]):
            await clients_panel_handler(message, state)
        elif current_state and ("BuyBotState" in current_state):
            await admin_switch_to_user_panel(message, state)
        elif current_state and ("BroadcastState" in current_state):
            await state.clear()
            await message.answer("👑 <b>Asosiy boshqaruv paneli:</b>", parse_mode="HTML", reply_markup=get_admin_main_keyboard())
        elif current_state and ("AdminOrderActionState" in current_state):
            await state.clear()
            await message.answer("👑 <b>Asosiy boshqaruv paneli:</b>", parse_mode="HTML", reply_markup=get_admin_main_keyboard())
        else:
            await state.clear()
            await clients_panel_handler(message, state)
    else:
        await state.clear()
        await cmd_start(message, state)

@dp.message(F.text == "⬅️ Orqaga")
async def back_to_previous_menu(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    user_id = message.from_user.id
    
    if user_id in ADMINS:
        if current_state == AdminMenuState.in_modes_panel.state:
            await clients_panel_handler(message, state)
        elif current_state == AdminMenuState.in_clients_panel.state:
            await state.clear()
            await message.answer("👑 <b>Asosiy boshqaruv paneli:</b>", parse_mode="HTML", reply_markup=get_admin_main_keyboard())
        elif current_state == AdminMenuState.in_user_panel.state:
            await state.clear()
            await message.answer("👑 <b>Asosiy boshqaruv paneli:</b>", parse_mode="HTML", reply_markup=get_admin_main_keyboard())
        elif current_state and ("SettingState" in current_state):
            await modes_panel_handler(message, state)
        elif current_state and any(s in current_state for s in ["AddClientState", "EditClientState", "DeleteClientState", "SearchClientState"]):
            await clients_panel_handler(message, state)
        elif current_state and ("BuyBotState" in current_state):
            await admin_switch_to_user_panel(message, state)
        elif current_state and ("BroadcastState" in current_state):
            await state.clear()
            await message.answer("👑 <b>Asosiy boshqaruv paneli:</b>", parse_mode="HTML", reply_markup=get_admin_main_keyboard())
        elif current_state and ("AdminOrderActionState" in current_state):
            await state.clear()
            await message.answer("👑 <b>Asosiy boshqaruv paneli:</b>", parse_mode="HTML", reply_markup=get_admin_main_keyboard())
        elif current_state and ("BotMgmtState" in current_state):
            if current_state == BotMgmtState.waiting_for_search_query.state:
                await state.set_state(BotMgmtState.in_bot_panel)
                await message.answer("🤖 <b>Botlarni boshqarish paneli:</b>", parse_mode="HTML", reply_markup=get_bot_mgmt_keyboard())
            else:
                await state.clear()
                await message.answer("👑 <b>Asosiy boshqaruv paneli:</b>", parse_mode="HTML", reply_markup=get_admin_main_keyboard())
        else:
            await state.clear()
            await message.answer("👑 <b>Asosiy boshqaruv paneli:</b>", parse_mode="HTML", reply_markup=get_admin_main_keyboard())
    else:
        await state.clear()
        await cmd_start(message, state)

@dp.message(F.text == "👤 User paneli")
async def admin_switch_to_user_panel(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return
    await state.set_state(AdminMenuState.in_user_panel)
    user_text = (
        f"👤 <b>Foydalanuvchi paneli (User Panel)</b>\n\n"
        f"Siz hozir foydalanuvchi rejimidasiz. Barcha funksiyalarni tekshirishingiz mumkin.\n"
        f"Qaytish uchun pastdagi <b>👑 Admin paneli</b> tugmasini bosing."
    )
    await message.answer(user_text, parse_mode="HTML", reply_markup=get_client_user_keyboard(is_admin=True))

@dp.message(F.text == "👑 Admin paneli")
async def admin_switch_to_admin_panel(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return
    await state.clear()
    admin_text = (
        f"👑 <b>SKY MASTER BOT - Boshqaruv Paneli</b>\n\n"
        f"Quyidagi bo'limlardan birini tanlang:"
    )
    await message.answer(admin_text, parse_mode="HTML", reply_markup=get_admin_main_keyboard())

# ==============================================================================
# 8-BO'LIM: XABAR TARQATISH TIZIMI
# ==============================================================================
@dp.message(F.text == "📢 Reklama tarqatish")
async def start_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return
    await state.set_state(BroadcastState.waiting_for_message)
    await message.answer(
        "📢 <b>Barcha foydalanuvchilarga yubormoqchi bo'lgan xabaringizni yuboring:</b>\n\n"
        "<i>Bu xabar matn, rasm, video, audio yoki hujjat bo'lika bo'lishi mumkin. "
        "Barcha foydalanuvchilarga aynan o'zi yuboriladi.</i>",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(BroadcastState.waiting_for_message)
async def process_broadcast_message(message: types.Message, state: FSMContext):
    if is_menu_button_or_command(message.text):
        await state.clear()
        return
        
    await state.clear()
    status_msg = await message.answer("⏳ <b>Xabar yuborilmoqda...</b>", parse_mode="HTML")
    
    users = await get_all_users()
    success = 0
    failed = 0
    
    for u in users:
        user_id = u['user_id']
        try:
            await message.copy_to(chat_id=user_id)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
            
    await status_msg.delete()
    await message.answer(
        f"✅ <b>Xabar tarqatish yakunlandi!</b>\n\n"
        f"🟢 Muvaffaqiyatli: <b>{success}</b> ta foydalanuvchiga\n"
        f"🔴 Muvaffaqiyatsiz (bloklangan/o'chirilgan): <b>{failed}</b> ta foydalanuvchiga",
        parse_mode="HTML",
        reply_markup=get_admin_main_keyboard()
    )

# ==============================================================================
# 9-BO'LIM: FOYDALANUVCHI MA'LUMOTLARI VA YO'RIQNOMALAR
# ==============================================================================

# ==============================================================================
# 9-BO'LIM: FOYDALANUVCHI MA'LUMOTLARI VA YO'RIQNOMALAR
# ==============================================================================
@dp.message(F.text == "ℹ️ Bot haqida malumot")
async def client_guide_info(message: types.Message):
    """Foydalanuvchiga botdan foydalanish qoidalari va yo'riqnomalarini ko'rsatish."""
    guide_text = (
        f"ℹ️ <b>SKY MASTER BOT - MA'LUMOT VA QO'LLANMA</b>\n\n"
        f"Ushbu bot orqali siz o'z Telegram botlaringizni to'lov muddatlarini nazorat qilishingiz, "
        f"yangi tayyor bot sotib olishingiz va to'lovlarni amalga oshirishingiz mumkin.\n\n"
        f"⚠️ <b>Bot sotib olish qoidalari:</b>\n"
        f"1️⃣ <b>Bot sotib olish</b> tugmasini bosing.\n"
        f"2️⃣ Botingiz uchun ish rejimini tanlang (Polling yoki Webhook).\n"
        f"3️⃣ Telegram orqali yoki Karta raqamiga to'lov qiling.\n"
        f"4️⃣ @BotFather-dan olingan API Token-ni botga yuboring.\n"
        f"5️⃣ Admin botingizni tasdiqlagach, botingiz 1 oylik muddatga ishga tushiriladi.\n\n"
        f"📲 Qandaydir savollar yoki muammolar yuzaga kelsa, <b>Admin bilan bog'lanish</b> bo'limi orqali bizga xabar yuboring."
    )
    await message.answer(guide_text, parse_mode="HTML")

# ==============================================================================
# 10-BO'LIM: ADMIN SOZLAMALARI VA PANEL NAVIGATSIYASI
# ==============================================================================
@dp.message(F.text == "👤 Mijozlar paneli")
async def clients_panel_handler(message: types.Message, state: FSMContext):
    """Adminlarga mijozlarni boshqarish panelini ko'rsatish."""
    if message.from_user.id not in ADMINS:
        return
    await state.clear()
    await state.set_state(AdminMenuState.in_clients_panel)
    await message.answer("👤 <b>Mijozlarni boshqarish paneli:</b>", parse_mode="HTML", reply_markup=get_clients_panel_keyboard())

@dp.message(F.text == "💳 Karta va Rejimlar")
async def modes_panel_handler(message: types.Message, state: FSMContext):
    """Adminlarga joriy tizim narxlari konfiguratsiyasini va karta ma'lumotlarini ko'rsatish."""
    if message.from_user.id not in ADMINS:
        return
    await state.clear()
    await state.set_state(AdminMenuState.in_modes_panel)
    poll_price = await get_setting("polling_price", "20000")
    web_price = await get_setting("webhook_price", "25000")
    bot_sale_p = await get_setting("bot_sale_price", "60000")
    card_num = await get_setting("card_number", "8600 0000 0000 0000")
    prov_tok = await get_setting("provider_token", "")
    auto_create = await get_setting("auto_create_bot", "false")
    
    prov_tok_status = "⚠️ Sozlanmagan (Telegram to'lovlari ishlamaydi)" if not prov_tok else "🟢 Sozlangan (Click/Payme)"
    auto_create_status = "🟢 Yoqilgan (Faol)" if auto_create == "true" else "🔴 O'chirilgan"
    
    text = (
        f"💳 <b>Karta va Rejimlar Narxlari:</b>\n\n"
        f"🤖 <b>Bot sotuv narxi:</b> {int(float(bot_sale_p)):,} som\n"
        f"⚡️ <b>Polling rejimi narxi:</b> {int(float(poll_price)):,} som / oy\n"
        f"🌐 <b>Webhook rejimi narxi:</b> {int(float(web_price)):,} som / oy\n"
        f"💳 <b>Karta raqami:</b> <code>{card_num}</code>\n"
        f"🔑 <b>Telegram to'lov tokeni:</b> {prov_tok_status}\n"
        f"🤖 <b>Auto Bot Yaratish:</b> {auto_create_status}\n\n"
        f"O'zgartirish uchun quyidagi tugmalardan birini bosing:"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=get_modes_settings_keyboard())

@dp.message(F.text == "🤖 Auto Bot Yaratish")
async def toggle_auto_create_bot(message: types.Message, state: FSMContext):
    """Avtomatik bot yaratish tizimini yoqish/o'chirish."""
    if message.from_user.id not in ADMINS:
        return
    await state.clear()
    current = await get_setting("auto_create_bot", "false")
    new_val = "false" if current == "true" else "true"
    await set_setting("auto_create_bot", new_val)
    
    status_text = "yoqildi (Faol)" if new_val == "true" else "o'chirildi"
    await message.answer(f"✅ <b>Avtomatik bot yaratish tizimi {status_text}!</b>", parse_mode="HTML")
    await modes_panel_handler(message, state)

@dp.message(F.text == "⚡️ Polling narxini o'zgartirish")
async def set_poll_price_start(message: types.Message, state: FSMContext):
    """Polling rejimi narxini yangilash jarayonini boshlash."""
    if message.from_user.id not in ADMINS:
        return
    await state.set_state(SettingState.waiting_for_polling_price)
    await message.answer("⚡️ <b>Polling rejimi uchun yangi oylik narxni kiriting (somda):</b>\n\n<i>(Misol: 15000)</i>", parse_mode="HTML", reply_markup=get_cancel_keyboard())

@dp.message(SettingState.waiting_for_polling_price)
async def process_poll_price(message: types.Message, state: FSMContext):
    """Yangi polling narxini sozlamalarga saqlash."""
    text = message.text.strip()
    if is_menu_button_or_command(text):
        await state.clear()
        return
        
    try:
        val = int(text)
        await set_setting("polling_price", str(val))
        await state.clear()
        await message.answer(f"✅ Polling narxi <b>{val:,} som</b> deb yangilandi!", parse_mode="HTML", reply_markup=get_modes_settings_keyboard())
    except ValueError:
        await message.answer("❌ Noto'g'ri narx. Faqat son kiriting (Masalan: 15000):")

@dp.message(F.text == "🌐 Webhook narxini o'zgartirish")
async def set_web_price_start(message: types.Message, state: FSMContext):
    """Webhook rejimi narxini yangilash jarayonini boshlash."""
    if message.from_user.id not in ADMINS:
        return
    await state.set_state(SettingState.waiting_for_webhook_price)
    await message.answer("🌐 <b>Webhook rejimi uchun yangi oylik narxni kiriting (somda):</b>\n\n<i>(Misol: 20000)</i>", parse_mode="HTML", reply_markup=get_cancel_keyboard())

@dp.message(SettingState.waiting_for_webhook_price)
async def process_web_price(message: types.Message, state: FSMContext):
    """Yangi webhook narxini sozlamalarga saqlash."""
    text = message.text.strip()
    if is_menu_button_or_command(text):
        await state.clear()
        return
 
    try:
        val = int(text)
        await set_setting("webhook_price", str(val))
        await state.clear()
        await message.answer(f"✅ Webhook narxi <b>{val:,} som</b> deb yangilandi!", parse_mode="HTML", reply_markup=get_modes_settings_keyboard())
    except ValueError:
        await message.answer("❌ Noto'g'ri narx. Faqat son kiriting (Masalan: 20000):")

@dp.message(F.text == "🤖 Bot narxini o'zgartirish")
async def set_bot_sale_price_start(message: types.Message, state: FSMContext):
    """Bot sotuv narxini yangilash jarayonini boshlash."""
    if message.from_user.id not in ADMINS:
        return
    await state.set_state(SettingState.waiting_for_bot_sale_price)
    await message.answer("🤖 <b>Bot sotuv narxini kiriting (somda):</b>\n\n<i>(Misol: 60000)</i>", parse_mode="HTML", reply_markup=get_cancel_keyboard())

@dp.message(SettingState.waiting_for_bot_sale_price)
async def process_bot_sale_price(message: types.Message, state: FSMContext):
    """Yangi bot sotuv narxini sozlamalarga saqlash."""
    text = message.text.strip()
    if is_menu_button_or_command(text):
        await state.clear()
        return
 
    try:
        val = int(text)
        await set_setting("bot_sale_price", str(val))
        await state.clear()
        await message.answer(f"✅ Bot sotuv narxi <b>{val:,} som</b> deb yangilandi!", parse_mode="HTML", reply_markup=get_modes_settings_keyboard())
    except ValueError:
        await message.answer("❌ Noto'g'ri narx. Faqat son kiriting (Masalan: 60000):")

@dp.message(F.text == "💳 Karta raqamini o'zgartirish")
async def set_card_start(message: types.Message, state: FSMContext):
    """Karta raqamini yangilash jarayonini boshlash."""
    if message.from_user.id not in ADMINS:
        return
    await state.set_state(SettingState.waiting_for_card_number)
    await message.answer("💳 <b>Yangi karta raqamini kiriting:</b>\n\n<i>(Misol: 8600 1234 5678 9012)</i>", parse_mode="HTML", reply_markup=get_cancel_keyboard())

@dp.message(SettingState.waiting_for_card_number)
async def process_card(message: types.Message, state: FSMContext):
    """Yangi karta ma'lumotlarini saqlash."""
    text = message.text.strip()
    if is_menu_button_or_command(text):
        await state.clear()
        return
 
    await set_setting("card_number", text)
    await state.clear()
    await message.answer(f"✅ Karta raqami <code>{text}</code> deb yangilandi!", parse_mode="HTML", reply_markup=get_modes_settings_keyboard())

@dp.message(F.text == "💳 Telegram to'lov tokenini o'zgartirish")
async def set_provider_token_start(message: types.Message, state: FSMContext):
    """Telegram Payments provayder tokenini yangilash jarayonini boshlash."""
    if message.from_user.id not in ADMINS:
        return
    await state.set_state(SettingState.waiting_for_provider_token)
    await message.answer(
        "🔑 <b>Telegram Payments uchun Provider Token-ni kiriting:</b>\n\n"
        "<i>(BotFather -> Bot Settings -> Payments -> Click yoki Payme orqali olingan token)\n"
        "Misol: <code>390234567:TEST:98452</code></i>",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(SettingState.waiting_for_provider_token)
async def process_provider_token(message: types.Message, state: FSMContext):
    """Yangi provayder tokeni ma'lumotlarini saqlash."""
    text = message.text.strip()
    if is_menu_button_or_command(text):
        await state.clear()
        return
 
    await set_setting("provider_token", text)
    await state.clear()
    await message.answer("✅ Telegram to'lov tokeni muvaffaqiyatli yangilandi!", parse_mode="HTML", reply_markup=get_modes_settings_keyboard())

# ==============================================================================
# 11-BO'LIM: FOYDALANUVCHILAR UCHUN BOT DO'KONI VA BUYURTMA BERISH
# ==============================================================================
def parse_receipt_time(time_str: str):
    time_str = time_str.strip()
    match = re.match(r'^(\d{1,2})[\:\-\.\s](\d{2})$', time_str)
    if not match:
        match = re.match(r'^(\d{4})$', time_str)
        if match:
            h = int(time_str[:2])
            m = int(time_str[2:])
            if 0 <= h <= 23 and 0 <= m <= 59:
                return h, m
        return None
    h = int(match.group(1))
    m = int(match.group(2))
    if 0 <= h <= 23 and 0 <= m <= 59:
        return h, m
    return None

@dp.message(F.text == "🛒 Bot sotib olish")
async def start_buy_bot_shop(message: types.Message, state: FSMContext):
    """Mijoz botlari uchun sotib olish jarayonini boshlash."""
    await state.clear()
    await state.set_state(BuyBotState.waiting_for_bot_type)
    
    text = (
        f"❓ <b>Sizga qanday bot turi kerak?</b>\n\n"
        f"O'zingizga kerakli bo'lgan bot turini tanlang:"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="👾 Anime bot", callback_data="buy_type:anime")
    builder.button(text="❓ Boshqa", callback_data="buy_type:boshqa")
    builder.adjust(2)
    
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.callback_query(BuyBotState.waiting_for_bot_type, F.data.startswith("buy_type:"))
async def process_bot_type_callback(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    choice = call.data.split(":")[1]
    
    if choice == "boshqa":
        await state.set_state(BuyBotState.waiting_for_boshqa_desc)
        await call.message.edit_text(
            "📝 <b>O'zingizga kerak bo'lgan botni batafsil tavsiflab (tushuntirib) bering:</b>",
            parse_mode="HTML"
        )
        await call.message.answer("Bekor qilish uchun tugmani bosing:", reply_markup=get_cancel_keyboard())
    else:
        bot_sale_p = await get_setting("bot_sale_price", "60000")
        text = (
            f"🤖 <b>ANIME BOT</b>\n\n"
            f"Hozirgi kunda eng yaxshi va yangi versiyadagi Anime Botimiz narxi:\n"
            f"💰 <b>{int(float(bot_sale_p)):,} som</b> <i>(1 martalik sozlash)</i>"
        )
        
        builder = InlineKeyboardBuilder()
        builder.button(text="➡️ Keyingi", callback_data="anime_next")
        builder.button(text="❌ Bekor qilish", callback_data="buy_cancel")
        builder.adjust(2)
        
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.message(BuyBotState.waiting_for_boshqa_desc)
async def process_boshqa_description(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if is_menu_button_or_command(text):
        await state.clear()
        return

    await state.clear()
    
    admin_msg = (
        f"🔔 <b>YANGI BOT BUYURTMASI (Boshqa turdagi bot)</b>\n\n"
        f"👤 Foydalanuvchi: {message.from_user.full_name} (ID: <code>{message.from_user.id}</code>, @{message.from_user.username or 'yoqo'})\n"
        f"📝 <b>Bot tavsifi:</b>\n{text}"
    )
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, text=admin_msg, parse_mode="HTML")
        except Exception:
            pass
            
    await message.answer(
        "✅ <b>So'rovingiz qabul qilindi!</b>\n\nSizning so'rovingiz adminga yuborildi. Tez orada adminlarimiz siz bilan bog'lanishadi.",
        parse_mode="HTML",
        reply_markup=get_client_user_keyboard(is_admin=(message.from_user.id in ADMINS))
    )

@dp.callback_query(F.data == "anime_next")
async def anime_bot_next_callback(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(BuyBotState.waiting_for_mode_selection)
    
    poll_p = await get_setting("polling_price", "20000")
    web_p = await get_setting("webhook_price", "25000")
    
    text = (
        f"⚙️ <b>SERVER UCHUN OYLIK TO'LOV</b>\n\n"
        f"Bot serverda 24/7 uzluksiz ishlashi uchun oylik to'lov talab qilinadi. Quyidagi 2 xil to'lov rejimidan birini tanlang:\n\n"
        f"⚡️ <b>Polling rejimi:</b> {int(float(poll_p)):,} som / oy\n"
        f"🌐 <b>Webhook rejimi:</b> {int(float(web_p)):,} som / oy"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text=f"⚡️ Polling ({int(float(poll_p)):,} som/oy)", callback_data="buy_mode:polling")
    builder.button(text=f"🌐 Webhook ({int(float(web_p)):,} som/oy)", callback_data="buy_mode:webhook")
    builder.button(text="❓ Oylik to'lov nima?", callback_data="explain_monthly")
    builder.button(text="❓ Webhook va Polling nima?", callback_data="explain_modes")
    builder.button(text="❌ Bekor qilish", callback_data="buy_cancel")
    builder.adjust(2, 2, 1)
    
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "explain_monthly")
async def explain_monthly_callback(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    poll_p = await get_setting("polling_price", "20000")
    web_p = await get_setting("webhook_price", "25000")
    
    info_text = (
        f"ℹ️ <b>Oylik to'lov nima?</b>\n\n"
        f"Oylik to'lov - bu botning serverda 24/7 rejimida uzluksiz ishlashini ta'minlash uchun to'lovdir. "
        f"Bunga botning internet sarfi, ma'lumotlar bazasi (database) xizmati, uptime 24/7 faol holatda saqlanishi va boshqa texnik xarajatlar kiradi.\n\n"
        f"⚠️ <i>Ushbu to'lov biz uchun emas, balki server provayderiga to'lanadigan xarajatlardir.</i>"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text=f"⚡️ Polling ({int(float(poll_p)):,} som/oy)", callback_data="buy_mode:polling")
    builder.button(text=f"🌐 Webhook ({int(float(web_p)):,} som/oy)", callback_data="buy_mode:webhook")
    builder.button(text="❓ Webhook va Polling nima?", callback_data="explain_modes")
    builder.button(text="❌ Bekor qilish", callback_data="buy_cancel")
    builder.adjust(2, 1, 1)
    
    await call.message.edit_text(info_text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "explain_modes")
async def explain_modes_callback(call: types.CallbackQuery):
    await call.answer()
    poll_p = await get_setting("polling_price", "20000")
    web_p = await get_setting("webhook_price", "25000")
    
    info_text = (
        f"❓ <b>Webhook va Polling nima?</b>\n\n"
        f"Bular serverdagi botning ishlash rejimlaridir:\n\n"
        f"⚡️ <b>Polling rejimi:</b> Webhook'ka qaraganda sekinroq ishlaydi (ba'zan tez, ba'zan biroz kechikib). Buning sababi, kimdir botga yozganda bot uni darhol ko'rmaydi, avval bot Telegram serveridan: \"Menga yangi xabar keldimi?\" deb so'raydi, keyin Telegram xabarni beradi.\n\n"
        f"🌐 <b>Webhook rejimi:</b> Polling'ga qaraganda o'ta tez ishlaydi. Botga xabar yozilishi bilan Telegram o'zi botga xabarni yo'naltiradi: \"Senga xabar keldi\" deb yuboradi (bot o'zi so'rab o'tirmaydi). Farqi shunda, shuning uchun Webhook rejimida bot tezroq javob beradi."
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text=f"⚡️ Polling ({int(float(poll_p)):,} som/oy)", callback_data="buy_mode:polling")
    builder.button(text=f"🌐 Webhook ({int(float(web_p)):,} som/oy)", callback_data="buy_mode:webhook")
    builder.button(text="❓ Oylik to'lov nima?", callback_data="explain_monthly")
    builder.button(text="❌ Bekor qilish", callback_data="buy_cancel")
    builder.adjust(2, 1, 1)
    
    await call.message.edit_text(info_text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "buy_cancel")
async def buy_cancel_callback(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    await call.message.delete()
    await call.message.answer(
        "❌ Xarid qilish bekor qilindi.", 
        reply_markup=get_client_user_keyboard(is_admin=(call.from_user.id in ADMINS))
    )

@dp.callback_query(BuyBotState.waiting_for_mode_selection, F.data.startswith("buy_mode:"))
async def process_user_buy_mode(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    mode = call.data.split(":")[1]
    
    bot_sale_p = float(await get_setting("bot_sale_price", "60000"))
    poll_p = float(await get_setting("polling_price", "20000"))
    web_p = float(await get_setting("webhook_price", "25000"))
    
    monthly = poll_p if mode == "polling" else web_p
    total = bot_sale_p + monthly
    
    await state.update_data(buy_mode=mode, total_price=total, monthly_price=monthly)
    await state.set_state(BuyBotState.waiting_for_bot_token)
    
    await call.message.delete()
    await call.message.answer(
        f"📝 <b>Bot API Tokeningizni yuboring:</b>\n\n"
        f"<i>(Tokenni olish uchun @BotFather botidan yangi bot yarating va u yerdagi tokenni yuboring. Misol: 812345678:AAEgX...)</i>",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(BuyBotState.waiting_for_bot_token)
async def process_buy_bot_token(message: types.Message, state: FSMContext):
    """Buyurtma berishda bot tokenini tekshirish va to'lov usulini ko'rsatish."""
    token = message.text.strip()
    if is_menu_button_or_command(token):
        await state.clear()
        return

    wait_msg = await message.answer("🔍 <b>API Token Telegram serverlaridan tekshirilmoqda...</b>", parse_mode="HTML")
    try:
        from aiogram import Bot
        temp_bot = Bot(token=token)
        res = await temp_bot.get_me()
        actual_username = f"@{res.username}"
        await temp_bot.session.close()
        
        await wait_msg.delete()
        await message.answer(f"✅ <b>Ulanish muvaffaqiyatli!</b>\n🤖 Bot: <b>{actual_username}</b> topildi.", parse_mode="HTML")
        
        await state.update_data(bot_token=token, actual_username=actual_username)
        await state.set_state(BuyBotState.waiting_for_pay_method)
        
        data = await state.get_data()
        mode = data['buy_mode']
        total = data['total_price']
        monthly = data['monthly_price']
        
        bot_sale_p = float(await get_setting("bot_sale_price", "60000"))
        
        text = (
            f"⚙️ <b>Tanlangan rejim:</b> {mode.upper()}\n"
            f"🤖 <b>Bot Username:</b> {actual_username}\n"
            f"💰 <b>Umumiy to'lov summasi:</b> <b>{int(total):,} som</b>\n"
            f"<i>(Bot narxi: {int(bot_sale_p):,} som + 1-oylik to'lov: {int(monthly):,} som)</i>\n\n"
            f"Iltimos, to'lov usulini tanlang:"
        )
        
        builder = InlineKeyboardBuilder()
        builder.button(text="💳 Telegram orqali tezkor to'lov (Click/Payme)", callback_data="buy_pay:telegram")
        builder.button(text="👤 Karta orqali (Rasm/Chek yuborish)", callback_data="buy_pay:card")
        builder.adjust(1, 1)
        
        await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())
    except Exception as e:
        await wait_msg.delete()
        await message.answer(f"❌ <b>API Token yaroqsiz!</b> BotFather bergan tokenni to'g'ri yuboring:\n\n<i>(Xatolik: {e})</i>", parse_mode="HTML")

@dp.callback_query(BuyBotState.waiting_for_pay_method, F.data.startswith("buy_pay:"))
async def process_buy_payment_method_callback(call: types.CallbackQuery, state: FSMContext):
    """Bot sotib olish uchun tanlangan to'lov yo'nalishini bajarish."""
    await call.answer()
    method = call.data.split(":")[1]
    data = await state.get_data()
    
    mode = data['buy_mode']
    total = data['total_price']
    monthly = data['monthly_price']
    actual_username = data['actual_username']
    token = data['bot_token']
    
    bot_sale_p = float(await get_setting("bot_sale_price", "60000"))
    user_id = call.from_user.id
    
    if method == "telegram":
        prov_token = await get_setting("provider_token", "")
        if not prov_token:
            await call.message.edit_text(
                "⚠️ <b>Telegram orqali tezkor to'lov hozircha faollashtirilmagan!</b>\n\n"
                "Iltimos, pastdagi Karta orqali to'lov tugmasini bosing:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardBuilder().button(text="👤 Karta orqali to'lov", callback_data="buy_pay:card").as_markup()
            )
            return
            
        await call.message.delete()
        prices = [
            types.LabeledPrice(label="Bot sozlash", amount=int(bot_sale_p) * 100),
            types.LabeledPrice(label=f"1-oylik {mode.upper()}", amount=int(monthly) * 100)
        ]
        
        await bot.send_invoice(
            chat_id=call.from_user.id,
            title="Sky Bot Xarid qilish",
            description=f"Bot xarid qilish - {mode.upper()} rejimi",
            payload=f"buy_bot:{mode}:{int(total)}",
            provider_token=prov_token,
            currency="UZS",
            prices=prices,
            start_parameter="buy-bot-invoice"
        )
        
    elif method == "card":
        card_num = await get_setting("card_number", "8600 0000 0000 0000")
        
        # Create pending order in database
        order_id = await create_order(user_id, actual_username, token, mode, total, None)
        await state.update_data(buy_order_id=order_id)
        await state.set_state(BuyBotState.waiting_for_receipt)
        
        pay_text = (
            f"💳 <b>KARTA ORQALI TO'LOV (BOT XARID QILISH)</b>\n\n"
            f"🤖 <b>Bot Username:</b> {actual_username}\n"
            f"⚙️ <b>Tanlangan rejim:</b> {mode.upper()}\n"
            f"💰 <b>Umumiy to'lov summasi:</b> <b>{int(total):,} som</b>\n"
            f"<i>(Bot narxi: {int(bot_sale_p):,} som + 1-oylik to'lov: {int(monthly):,} som)</i>\n\n"
            f"💳 <b>Karta raqami:</b>\n"
            f"<code>{card_num}</code>\n\n"
            f"📲 Click / Payme / Uzum ilovalari orqali yuqoridagi kartaga to'lov qiling.\n\n"
            f"📸 To'lovni amalga oshirgach, to'lov <b>CHEKINI (skrinshot yoki rasmini)</b> rasm yoki hujjat ko'rinishida shu yerga yuboring:"
        )
        
        builder = InlineKeyboardBuilder()
        builder.button(text="📲 Click App", url="https://click.uz")
        builder.button(text="📲 Payme App", url="https://payme.uz")
        builder.button(text="📋 Kartani nusxalash", callback_data=f"copy_card:{card_num}")
        builder.adjust(2, 1)
        
        await call.message.delete()
        await call.message.answer(pay_text, parse_mode="HTML", reply_markup=get_cancel_keyboard())
        await call.message.answer("To'lov ilovalari:", reply_markup=builder.as_markup())

# ==============================================================================
# 12-BO'LIM: TO'LOVNI TEKSHIRISH VA MUVAFFQQIYATLI TO'LOV HANDLERLARI
# ==============================================================================
@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    """Telegram to'lovlari uchun pre-checkout so'rovlariga javob berish."""
    await pre_checkout_query.answer(ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message, state: FSMContext):
    """Yangi botlar va uzaytirishlar uchun muvaffaqiyatli Telegram to'lovlarini qayta ishlash."""
    payload = message.successful_payment.invoice_payload
    parts = payload.split(":")
    
    if parts[0] == "buy_bot":
        mode = parts[1]
        total = float(parts[2])
        
        data = await state.get_data()
        token = data.get("bot_token")
        actual_username = data.get("actual_username")
        
        if not token or not actual_username:
            token = "unknown"
            actual_username = "@unknown"
            
        order_id = await create_order(
            message.from_user.id,
            actual_username,
            token,
            mode,
            total,
            "telegram_native_payment"
        )
        await update_order_status(order_id, "approved")
        await state.clear()
        
        auto_create = await get_setting("auto_create_bot", "false")
        if auto_create == "true":
            today_str = datetime.now().strftime("%Y-%m-%d")
            next_str = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
            clean_un = re.sub(r'[^a-zA-Z0-9]', '', actual_username).lower()
            folder_name = f"sky-{clean_un}"
            
            await add_client_bot(
                message.from_user.id, 
                actual_username, 
                token, 
                folder_name, 
                mode, 
                total - float(await get_setting("bot_sale_price", "60000")), 
                today_str, 
                next_str
            )
            
            user_msg = (
                f"✅ <b>To'lovingiz qabul qilindi va avtomatik tasdiqlandi!</b>\n\n"
                f"🤖 <b>Botni avtomatik yaratish tizimi ishga tushdi!</b>\n"
                f"Botingiz yasalmoqda, iltimos 10-20 daqiqa kuting. Tayyor bo'lsa xabar beramiz."
            )
            await message.answer(user_msg, parse_mode="HTML", reply_markup=get_client_user_keyboard(is_admin=(message.from_user.id in ADMINS)))
            
            admin_msg = (
                f"🔔 <b>AUTO TASDIQLASH (Bot Yaratildi - №#{order_id})</b>\n\n"
                f"👤 Xaridor ID: <code>{message.from_user.id}</code> (@{message.from_user.username or 'yoq'})\n"
                f"🤖 Bot Username: {actual_username}\n"
                f"⚙️ Rejimi: {mode.upper()}\n"
                f"💰 To'lov summasi: {int(total):,} som\n"
                f"📁 Server papkasi: <code>{folder_name}</code>\n\n"
                f"✅ Tizim to'lovni avtomatik tasdiqladi (Telegram Native Invoice) va bot avtomatik tarzda yaratildi."
            )
            admin_builder = None
        else:
            user_msg = (
                f"✅ <b>To'lovingiz muvaffaqiyatli qabul qilindi!</b>\n\n"
                f"🤖 <b>Botni avtomatik yaratish tizimi o'chirilgan.</b>\n"
                f"So'rov adminga jo'natildi. Admin botni yasab, ishga tushirgach xabar beradi."
            )
            await message.answer(user_msg, parse_mode="HTML", reply_markup=get_client_user_keyboard(is_admin=(message.from_user.id in ADMINS)))
            
            admin_builder = InlineKeyboardBuilder()
            admin_builder.button(text="✅ Server papkasini kiritish", callback_data=f"aord_approve:{order_id}")
            admin_builder.adjust(1)
            
            admin_msg = (
                f"🔔 <b>TEZKOR TO'LOV TASDIQLANDI (Server papkasi kutilmoqda - №#{order_id})</b>\n\n"
                f"👤 Xaridor ID: <code>{message.from_user.id}</code> (@{message.from_user.username or 'yoq'})\n"
                f"🤖 Bot Username: {actual_username}\n"
                f"⚙️ Rejimi: {mode.upper()}\n"
                f"💰 To'lov summasi: {int(total):,} som\n\n"
                f"Botni ishga tushirish uchun server papkasini kiriting:"
            )
            
        for admin_id in ADMINS:
            try:
                await bot.send_message(admin_id, text=admin_msg, parse_mode="HTML", reply_markup=admin_builder.as_markup() if admin_builder else None)
            except Exception:
                pass
    elif parts[0] == "renew_invoice":
        bot_id = int(parts[1])
        months = int(parts[2])
        total = float(parts[3])
        
        client = await get_client_by_id(bot_id)
        if not client:
            await message.answer("❌ Xatolik: Bot topilmadi!")
            return
            
        order_id = await create_order(
            message.from_user.id,
            client['bot_username'],
            client['bot_token'],
            f"renewal:{months}",
            total,
            "telegram_native_payment"
        )
        
        await message.answer(
            f"🎉 <b>Botni uzaytirish to'lovi muvaffaqiyatli amalga oshirildi!</b>\n\n"
            f"🆔 Buyurtma №: #{order_id}\n"
            f"🤖 Bot: <b>{client['bot_username']}</b>\n"
            f"⏳ Muddat: <b>{months} oy</b>\n\n"
            f"✅ Buyurtma adminga yuborildi. Tez orada bot faollashtiriladi!",
            parse_mode="HTML",
            reply_markup=get_client_user_keyboard(is_admin=(message.from_user.id in ADMINS))
        )
        
        # Adminlarni xabardor qilish
        admin_builder = InlineKeyboardBuilder()
        admin_builder.button(text="✅ Uzaytirishni Tasdiqlash", callback_data=f"aord_approve:{order_id}")
        admin_builder.button(text="❌ Rad etish", callback_data=f"aord_reject:{order_id}")
        admin_builder.adjust(1, 1)
        
        admin_msg = (
            f"🔔 <b>YANGI UZAYTIRISH BUYURTMASI (№#{order_id})</b>\n\n"
            f"👤 Xaridor ID: <code>{message.from_user.id}</code>\n"
            f"🤖 Bot Username: {client['bot_username']}\n"
            f"⚙️ Rejimi: UZAYTIRISH ({months} oy)\n"
            f"💰 To'lov summasi: {int(total):,} som\n"
            f"💳 (To'lov Telegram native invoice orqali avtomatik amalga oshirilgan)\n\n"
            f"Faollashtirish uchun quyidagi tugmani bosing:"
        )
        for admin_id in ADMINS:
            try:
                await bot.send_message(admin_id, text=admin_msg, parse_mode="HTML", reply_markup=admin_builder.as_markup())
            except Exception:
                pass

# ==============================================================================
# 13-BO'LIM: TO'LOV CHEKLARINI QO'LDA TEKSHIRISH
# ==============================================================================
@dp.message(BuyBotState.waiting_for_receipt, F.photo | F.document)
async def process_user_receipt(message: types.Message, state: FSMContext):
    """Yangi bot buyurtmalari va uzaytirishlar uchun karta to'lov cheklarini qayta ishlash."""
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    
    data = await state.get_data()
    renewing_bot_id = data.get("renewing_bot_id")
    
    if renewing_bot_id:
        months = data["renewing_months"]
        total = data["renewing_total"]
        bot_un = data["renewing_bot_username"]
        bot_token = data["renewing_bot_token"]
        
        order_id = await create_order(
            message.from_user.id,
            bot_un,
            bot_token,
            f"renewal:{months}",
            total,
            file_id
        )
        await state.clear()
        
        user_msg = (
            f"🎉 <b>Uzaytirish to'lov chekingiz qabul qilindi!</b>\n\n"
            f"🆔 Buyurtma №: #{order_id}\n"
            f"🤖 Bot: <b>{bot_un}</b>\n"
            f"⏳ Muddat: <b>{months} oy</b>\n"
            f"💰 Summa: <b>{int(total):,} som</b>\n\n"
            f"✅ Chek adminga tekshirishga yuborildi. Admin tasdiqlashi bilan botingiz muddati uzaytiriladi!"
        )
        await message.answer(user_msg, parse_mode="HTML", reply_markup=get_client_user_keyboard(is_admin=(message.from_user.id in ADMINS)))
        
        # Adminlarni xabardor qilish
        admin_builder = InlineKeyboardBuilder()
        admin_builder.button(text="✅ Uzaytirishni Tasdiqlash", callback_data=f"aord_approve:{order_id}")
        admin_builder.button(text="❌ Rad etish", callback_data=f"aord_reject:{order_id}")
        admin_builder.adjust(1, 1)
        
        admin_msg = (
            f"🔔 <b>YANGI UZAYTIRISH BUYURTMASI (№#{order_id})</b>\n\n"
            f"👤 Xaridor ID: <code>{message.from_user.id}</code> (@{message.from_user.username or 'yoq'})\n"
            f"🤖 Bot Username: {bot_un}\n"
            f"⚙️ Rejimi: UZAYTIRISH ({months} oy)\n"
            f"💰 To'lov summasi: {int(total):,} som\n\n"
            f"To'lov chekini tekshirib, quyidagi tugmalar orqali tasdiqlang:"
        )
        for admin_id in ADMINS:
            try:
                await bot.send_photo(admin_id, photo=file_id, caption=admin_msg, parse_mode="HTML", reply_markup=admin_builder.as_markup())
            except Exception:
                try:
                    await bot.send_message(admin_id, text=admin_msg + "\n⚠️ (Chek rasmini yuborib bo'lmadi)", parse_mode="HTML", reply_markup=admin_builder.as_markup())
                except Exception:
                    pass
        return

    # Purchase flow
    order_id = data.get("buy_order_id")
    actual_username = data.get("actual_username")
    token = data.get("bot_token")
    mode = data.get("buy_mode")
    total = data.get("total_price")
    user_id = message.from_user.id
    
    from database import get_db
    p = await get_db()
    async with p.acquire() as conn:
        await conn.execute("UPDATE orders SET receipt_file_id = $1 WHERE id = $2;", file_id, order_id)
        
    await state.clear()
    
    user_msg = (
        f"⏳ <b>To'lov chekingiz qabul qilindi va tekshirishga yuborildi.</b>\n\n"
        f"Mablag'lar kelib tushganligi va chekning haqiqiyligini tasdiqlash uchun har bir to'lov admin tomonidan qo'lda tekshiriladi. "
        f"Tekshiruv yakunlangach, sizga xabar beriladi."
    )
    await message.answer(user_msg, parse_mode="HTML", reply_markup=get_client_user_keyboard(is_admin=(user_id in ADMINS)))
    
    admin_builder = InlineKeyboardBuilder()
    admin_builder.button(text="✅ Tasdiqlash & Faollashtirish", callback_data=f"aord_approve:{order_id}")
    admin_builder.button(text="❌ Rad etish", callback_data=f"aord_reject:{order_id}")
    admin_builder.adjust(1, 1)
    
    admin_msg = (
        f"🔔 <b>YANGI BOT BUYURTMASI (№#{order_id})</b>\n\n"
        f"👤 Xaridor ID: <code>{user_id}</code> (@{message.from_user.username or 'yoq'})\n"
        f"🤖 Bot Username: {actual_username}\n"
        f"🔑 Bot Token: <code>{token}</code>\n"
        f"⚙️ Rejimi: {mode.upper()}\n"
        f"💰 To'lov summasi: {int(total):,} som\n\n"
        f"Ushbu buyurtmani tasdiqlash yoki rad etish uchun tugmalarni bosing:"
    )
    for admin_id in ADMINS:
        try:
            await bot.send_photo(admin_id, photo=file_id, caption=admin_msg, parse_mode="HTML", reply_markup=admin_builder.as_markup())
        except Exception:
            try:
                await bot.send_message(admin_id, text=admin_msg + "\n⚠️ (Chek rasmini yuborib bo'lmadi)", parse_mode="HTML", reply_markup=admin_builder.as_markup())
            except Exception:
                pass

@dp.message(BuyBotState.waiting_for_receipt)
async def process_user_receipt_text_skip(message: types.Message, state: FSMContext):
    """Qo'lda chek yuborishda rasm yoki fayl yuborishni talab qilish."""
    text = message.text.strip()
    if is_menu_button_or_command(text):
        await state.clear()
        return
    await message.answer("📸 Iltimos, to'lov chekini rasm yoki fayl ko'rinishida yuboring yoki bekor qilish uchun '❌ Bekor qilish' tugmasini bosing:")
# ==============================================================================
# 14-BO'LIM: ADMIN TOMONIDAN BUYURTMALARNI BOSHQARISH VA TASDIQLASH
# ==============================================================================

@dp.callback_query(F.data.startswith("aord_approve:"))
async def admin_approve_order(call: types.CallbackQuery, state: FSMContext):
    """Bot xaridlarini yoki uzaytirishlarini tasdiqlash va ma'lumotlar bazasini yangilash."""
    await call.answer()
    order_id = int(call.data.split(":")[1])
    order = await get_order_by_id(order_id)
    
    if not order:
        await call.message.reply("❌ Buyurtma topilmadi!")
        return
        
    mode_str = order['mode']
    if mode_str.startswith("renewal:"):
        months = int(mode_str.split(":")[1])
        await update_order_status(order_id, "approved")
        
        # Agar kerak bo'lsa get_db import qilinadi (u allaqachon database.py ichida)
        from database import get_db
        p = await get_db()
        async with p.acquire() as conn:
            client = await conn.fetchrow(
                "SELECT * FROM master_clients WHERE client_id = $1 AND bot_username = $2 ORDER BY id DESC LIMIT 1;",
                order['user_id'], order['bot_username']
            )
            
        if not client:
            await call.message.reply("❌ Xatolik: Ushbu mijoz boti topilmadi!")
            return
            
        today = datetime.now().date()
        current_next = client['next_payment_date']
        
        if current_next < today:
            new_next = today + timedelta(days=months * 30)
        else:
            new_next = current_next + timedelta(days=months * 30)
            
        new_next_str = new_next.strftime("%Y-%m-%d")
        today_str = today.strftime("%Y-%m-%d")
        
        await update_client_field(client['id'], 'next_payment_date', new_next_str)
        await update_client_field(client['id'], 'last_payment_date', today_str)
        await update_client_field(client['id'], 'status', 'active')
        
        await call.message.reply(
            f"✅ <b>Mijoz boti muvaffaqiyatli uzaytirildi!</b>\n\n"
            f"🤖 Bot: {client['bot_username']}\n"
            f"⏳ Yangi keyingi to'lov sanasi: <b>{new_next_str}</b>",
            parse_mode="HTML"
        )
        
        # Foydalanuvchini xabardor qilish
        try:
            user_msg = (
                f"🎉 <b>XUSHXABAR! Botingiz uzaytirilishi tasdiqlandi!</b>\n\n"
                f"🤖 Bot: <b>{client['bot_username']}</b>\n"
                f"⏳ Keyingi to'lov sanasi: <b>{new_next_str}</b>\n\n"
                f"Tizimdan foydalanganingiz uchun rahmat!"
            )
            await bot.send_message(order['user_id'], text=user_msg, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Failed to notify user {order['user_id']}: {e}")
        return

    # Check if auto_create_bot setting is enabled
    auto_create = await get_setting("auto_create_bot", "false")
    if auto_create == "true":
        await update_order_status(order_id, "approved")
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        next_str = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        
        poll_p = float(await get_setting("polling_price", "20000"))
        web_p = float(await get_setting("webhook_price", "25000"))
        m_price = poll_p if order['mode'] == "polling" else web_p
        
        clean_un = re.sub(r'[^a-zA-Z0-9]', '', order['bot_username']).lower()
        folder_name = f"sky-{clean_un}"
        
        rec_id = await add_client_bot(
            order['user_id'], order['bot_username'], order['bot_token'],
            folder_name, order['mode'], m_price, today_str, next_str
        )
        
        await call.message.reply(
            f"✅ <b>Buyurtma muvaffaqiyatli tasdiqlandi (Avtomatik yaratish faol)!</b>\n\n"
            f"🤖 Bot: {order['bot_username']}\n"
            f"📁 Server papkasi: <code>{folder_name}</code>",
            parse_mode="HTML"
        )
        
        # Foydalanuvchini xabardor qilish
        try:
            user_msg = (
                f"🎉 <b>XUSHXABAR! Buyurtmangiz tasdiqlandi!</b>\n\n"
                f"🤖 Botingiz: <b>{order['bot_username']}</b> muvaffaqiyatli ishga tushirildi.\n"
                f"⏳ Keyingi to'lov sanasi: <b>{next_str}</b>\n\n"
                f"🤖 <b>Botni avtomatik yaratish tizimi ishga tushdi!</b>\n"
                f"Botingiz yasalmoqda, iltimos 10-20 daqiqa kuting. Tayyor bo'lsa xabar beramiz."
            )
            await bot.send_message(order['user_id'], text=user_msg, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Failed to notify user {order['user_id']}: {e}")
        return

    await state.update_data(approve_order_id=order_id)
    await state.set_state(AdminOrderActionState.waiting_for_folder_name)
    await call.message.reply(
        f"📁 <b>Buyurtma №#{order_id} ({order['bot_username']}) tasdiqlanmoqda.</b>\n\n"
        f"Iltimos, serverdagi fayl papkasi nomini yuboring (Masalan: <code>sky-uzb</code>):",
        parse_mode="HTML"
    )

@dp.message(AdminOrderActionState.waiting_for_folder_name)
async def admin_save_order_folder(message: types.Message, state: FSMContext):
    """Tasdiqlashdan so'ng mijoz boti ma'lumotlarini master_clients ro'yxatiga saqlash."""
    folder_name = message.text.strip()
    if is_menu_button_or_command(folder_name):
        await state.clear()
        return

    data = await state.get_data()
    order_id = data.get("approve_order_id")
    order = await get_order_by_id(order_id)
    
    if not order:
        await message.answer("❌ Xatolik: Buyurtma topilmadi.")
        await state.clear()
        return
        
    await update_order_status(order_id, "approved")
    
    # master_clients ma'lumotlar bazasiga saqlash
    today_str = datetime.now().strftime("%Y-%m-%d")
    next_str = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    
    poll_p = float(await get_setting("polling_price", "20000"))
    web_p = float(await get_setting("webhook_price", "25000"))
    m_price = poll_p if order['mode'] == "polling" else web_p
    
    rec_id = await add_client_bot(
        order['user_id'], order['bot_username'], order['bot_token'],
        folder_name, order['mode'], m_price, today_str, next_str
    )
    
    await state.clear()
    await message.answer(f"✅ <b>Mijoz muvaffaqiyatli faollashtirildi! (Ro'yxat ID: #{rec_id})</b>", parse_mode="HTML")
    
    # Foydalanuvchini xabardor qilish
    try:
        user_msg = (
            f"🎉 <b>XUSHXABAR! Buyurtmangiz tasdiqlandi!</b>\n\n"
            f"🤖 Botingiz: <b>{order['bot_username']}</b> muvaffaqiyatli ishga tushirildi.\n"
            f"⏳ Keyingi to'lov sanasi: <b>{next_str}</b>\n\n"
            f"🤖 <b>Botni avtomatik yaratish tizimi o'chirilgan.</b>\n"
            f"Admin botni yasab, ishga tushirdi. Boshqarish uchun /start buyrug'ini yuboring."
        )
        await bot.send_message(order['user_id'], text=user_msg, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Failed to notify user {order['user_id']}: {e}")

# ==============================================================================
# 15-BO'LIM: ADMIN TOMONIDAN BUYURTMALARNI RAD ETISH JARAYONI
# ==============================================================================
@dp.callback_query(F.data.startswith("aord_reject:"))
async def admin_reject_order(call: types.CallbackQuery, state: FSMContext):
    """Mijoz botini xarid qilish yoki uzaytirish buyurtmasini rad etish jarayonini boshlash."""
    await call.answer()
    order_id = int(call.data.split(":")[1])
    
    await state.update_data(reject_order_id=order_id)
    await state.set_state(AdminOrderActionState.waiting_for_reject_reason)
    await call.message.reply(
        f"❌ <b>Buyurtma №#{order_id} ni rad etish sababini yuboring:</b>\n"
        f"<i>(Masalan: To'lov cheki yaroqsiz)</i>",
        parse_mode="HTML"
    )

@dp.message(AdminOrderActionState.waiting_for_reject_reason)
async def admin_save_reject_reason(message: types.Message, state: FSMContext):
    """Rad etish sababini qayta ishlash, buyurtma holatini yangilash va foydalanuvchini xabardor qilish."""
    reason = message.text.strip()
    if is_menu_button_or_command(reason):
        await state.clear()
        return

    data = await state.get_data()
    order_id = data.get("reject_order_id")
    order = await get_order_by_id(order_id)
    
    if not order:
        await message.answer("❌ Buyurtma topilmadi.")
        await state.clear()
        return
        
    await update_order_status(order_id, "rejected")
    await state.clear()
    
    await message.answer("❌ Buyurtma rad etildi va foydalanuvchiga xabar yuborildi.")
    
    # Foydalanuvchini xabardor qilish
    try:
        user_msg = (
            f"❌ <b>Buyurtmangiz rad etildi.</b>\n\n"
            f"🤖 Bot: <b>{order['bot_username']}</b>\n"
            f"⚠️ Sababi: <b>{reason}</b>\n\n"
            f"Savollar bo'lsa, adminga murojaat qiling."
        )
        await bot.send_message(order['user_id'], text=user_msg, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Failed to notify user {order['user_id']}: {e}")

# ==============================================================================
# 16-BO'LIM: ADMIN TOMONIDAN MIJOZ QO'SHISH JARAYONI
# ==============================================================================
@dp.message(F.text == "➕ Mijoz qo'shish")
async def start_add_client(message: types.Message, state: FSMContext):
    """Qo'lda mijoz qo'shish jarayonini boshlash."""
    if message.from_user.id not in ADMINS:
        return
    await state.set_state(AddClientState.waiting_for_client_id)
    await message.answer(
        "📝 <b>1-Qadam: Mijozning Telegram ID-sini kiriting:</b>\n\n"
        "<i>(Misol: 8551089366)</i>",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(AddClientState.waiting_for_client_id)
async def process_client_id(message: types.Message, state: FSMContext):
    """Mijoz user id-sini saqlash va bot usernamesini so'rash."""
    text = message.text.strip()
    if is_menu_button_or_command(text):
        await state.clear()
        return

    try:
        cid = int(text)
        await state.update_data(client_id=cid)
        await state.set_state(AddClientState.waiting_for_bot_username)
        await message.answer(
            "📝 <b>2-Qadam: Botning Username-ini kiriting:</b>\n\n"
            "<i>(Misol: @Anime_Uz_Bot)</i>",
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard()
        )
    except ValueError:
        await message.answer("❌ Noto'g'ri ID. Faqat son kiriting (Masalan: 8551089366):")

@dp.message(AddClientState.waiting_for_bot_username)
async def process_bot_username(message: types.Message, state: FSMContext):
    """Bot usernamesini saqlash va API tokenni so'rash."""
    text = message.text.strip()
    if is_menu_button_or_command(text):
        await state.clear()
        return

    bot_un = text
    if not bot_un.startswith("@"):
        bot_un = "@" + bot_un
    await state.update_data(bot_username=bot_un)
    await state.set_state(AddClientState.waiting_for_bot_token)
    await message.answer(
        "📝 <b>3-Qadam: Botning API Token-ini kiriting:</b>\n\n"
        "<i>(Misol: 812345678:AAEgX...)</i>",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(AddClientState.waiting_for_bot_token)
async def process_bot_token(message: types.Message, state: FSMContext):
    """API tokenni saqlash va serverdagi papka nomini so'rash."""
    text = message.text.strip()
    if is_menu_button_or_command(text):
        await state.clear()
        return

    token = text
    await state.update_data(bot_token=token)
    await state.set_state(AddClientState.waiting_for_server_folder)
    await message.answer(
        "📝 <b>4-Qadam: Serverdagi papka nomini kiriting:</b>\n\n"
        "<i>(Misol: sky-uzb)</i>",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(AddClientState.waiting_for_server_folder)
async def process_server_folder(message: types.Message, state: FSMContext):
    """Server papka yo'lini saqlash va ish rejimini tanlashni so'rash."""
    text = message.text.strip()
    if is_menu_button_or_command(text):
        await state.clear()
        return

    folder = text
    await state.update_data(server_folder=folder)
    await state.set_state(AddClientState.waiting_for_mode)
    
    poll_p = await get_setting("polling_price", "15000")
    web_p = await get_setting("webhook_price", "20000")
    
    builder = InlineKeyboardBuilder()
    builder.button(text=f"⚡️ Polling ({int(float(poll_p)):,} som)", callback_data="set_mode:polling")
    builder.button(text=f"🌐 Webhook ({int(float(web_p)):,} som)", callback_data="set_mode:webhook")
    builder.adjust(2)
    
    await message.answer(
        "📝 <b>5-Qadam: Bot ishlash rejimini tanlang:</b>",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("set_mode:"))
async def process_mode_choice(call: types.CallbackQuery, state: FSMContext):
    """Rejim sozlamasini saqlash va oxirgi to'lov sanasini tugmalar orqali so'rash."""
    await call.answer()
    mode = call.data.split(":")[1]
    poll_p = await get_setting("polling_price", "15000")
    web_p = await get_setting("webhook_price", "20000")
    
    m_price = float(poll_p) if mode == "polling" else float(web_p)
    await state.update_data(mode=mode, monthly_price=m_price)
    
    await state.set_state(AddClientState.waiting_for_last_payment)
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Bugun (Bugungi sana)", callback_data="add_date:today")
    builder.button(text="📅 Kecha (Kechagi sana)", callback_data="add_date:yesterday")
    builder.adjust(1)
    
    await call.message.edit_text(
        f"✅ Rejim: <b>{mode.upper()}</b> ({int(m_price):,} som)\n\n"
        f"📝 <b>6-Qadam: Oxirgi to'lov qilingan sanani kiriting yoki tanlang:</b>\n\n"
        f"<i>(Quyidagi tugmalardan tanlashingiz yoki matn ko'rinishida yuborishingiz mumkin. Misol: <code>{today_str}</code>)</i>",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("add_date:"))
async def process_add_date_callback(call: types.CallbackQuery, state: FSMContext):
    """Bugun/Kecha bosilishini qayta ishlash, to'lov sanasini saqlash va keyingi to'lov muddatini so'rash."""
    await call.answer()
    choice = call.data.split(":")[1]
    
    if choice == "today":
        date_val = datetime.now().strftime("%Y-%m-%d")
    else:
        date_val = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        
    await state.update_data(last_payment_date=date_val)
    await state.set_state(AddClientState.waiting_for_next_payment)
    
    dt_l = datetime.strptime(date_val, "%Y-%m-%d")
    next_suggest = (dt_l + timedelta(days=30)).strftime("%Y-%m-%d")
    
    builder = InlineKeyboardBuilder()
    builder.button(text="⏳ 1 oy (30 kundan keyin)", callback_data="add_duration:30")
    builder.button(text="⏳ 3 oy (90 kundan keyin)", callback_data="add_duration:90")
    builder.button(text="⏳ 6 oy (180 kundan keyin)", callback_data="add_duration:180")
    builder.button(text="⏳ 12 oy (365 kundan keyin)", callback_data="add_duration:365")
    builder.adjust(2, 2)
    
    await call.message.edit_text(
        f"✅ Oxirgi to'lov sanasi: <b>{date_val}</b>\n\n"
        f"📝 <b>7-Qadam: Keyingi to'lov sanasini tanlang yoki kiriting:</b>\n\n"
        f"<i>(Qadamli muddatni tanlang yoki qo'lda yozib yuboring. Misol: <code>{next_suggest}</code>)</i>",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )

@dp.message(AddClientState.waiting_for_last_payment)
async def process_last_payment(message: types.Message, state: FSMContext):
    """Qo'lda yozilgan oxirgi to'lov sanasini qayta ishlash va keyingi to'lov variantlarini ko'rsatish."""
    date_input = message.text.strip()
    if is_menu_button_or_command(date_input):
        await state.clear()
        return

    try:
        parsed_l_date = parse_flexible_date(date_input)
        await state.update_data(last_payment_date=parsed_l_date)
        await state.set_state(AddClientState.waiting_for_next_payment)
        
        dt_l = datetime.strptime(parsed_l_date, "%Y-%m-%d")
        next_suggest = (dt_l + timedelta(days=30)).strftime("%Y-%m-%d")
        
        builder = InlineKeyboardBuilder()
        builder.button(text="⏳ 1 oy (30 kundan keyin)", callback_data="add_duration:30")
        builder.button(text="⏳ 3 oy (90 kundan keyin)", callback_data="add_duration:90")
        builder.button(text="⏳ 6 oy (180 kundan keyin)", callback_data="add_duration:180")
        builder.button(text="⏳ 12 oy (365 kundan keyin)", callback_data="add_duration:365")
        builder.adjust(2, 2)
        
        await message.answer(
            f"✅ Oxirgi to'lov sanasi: <b>{parsed_l_date}</b>\n\n"
            f"📝 <b>7-Qadam: Keyingi to'lov sanasini tanlang yoki kiriting:</b>\n\n"
            f"<i>(Misol: <code>{next_suggest}</code>)</i>",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
    except ValueError:
        await message.answer("❌ <b>Noto'g'ri sana formati!</b> (Misol: 2026-07-20)")

@dp.callback_query(F.data.startswith("add_duration:"))
async def process_add_duration_callback(call: types.CallbackQuery, state: FSMContext):
    """Muddat bosilishini qayta ishlash, keyingi to'lov sanasini hisoblash va mijoz ma'lumotlarini tekshirish."""
    await call.answer()
    days = int(call.data.split(":")[1])
    data = await state.get_data()
    
    l_date_str = data['last_payment_date']
    dt_l = datetime.strptime(l_date_str, "%Y-%m-%d")
    n_date_str = (dt_l + timedelta(days=days)).strftime("%Y-%m-%d")
    
    await state.update_data(next_payment_date=n_date_str)
    await proceed_to_client_validation(call.message, state, n_date_str)

@dp.message(AddClientState.waiting_for_next_payment)
async def process_next_payment(message: types.Message, state: FSMContext):
    """Qo'lda yozilgan keyingi to'lov sanasini qayta ishlash va tekshirishni boshlash."""
    next_date_input = message.text.strip()
    if is_menu_button_or_command(next_date_input):
        await state.clear()
        return

    try:
        parsed_n_date = parse_flexible_date(next_date_input)
        await state.update_data(next_payment_date=parsed_n_date)
        await proceed_to_client_validation(message, state, parsed_n_date)
    except ValueError:
        await message.answer("❌ <b>Noto'g'ri sana formati!</b> (Misol: 2026-07-20)")

async def proceed_to_client_validation(message_or_call_msg, state: FSMContext, parsed_n_date: str):
    """Tekshiruvlarni bajarish va admin uchun hisobotni ko'rsatish yordamchi metodi."""
    data = await state.get_data()
    cid = data['client_id']
    b_un = data['bot_username']
    b_token = data['bot_token']
    s_folder = data['server_folder']
    mode = data['mode']
    m_price = data['monthly_price']
    l_date = data['last_payment_date']
    
    wait_msg = await message_or_call_msg.answer("⏳ <b>Kiritilgan ma'lumotlar va bot sozlamalari tekshirilmoqda...</b>", parse_mode="HTML")
    await asyncio.sleep(0.5)
    
    issues, actual_username = await validate_bot_data(cid, b_un, b_token, s_folder, l_date, parsed_n_date)
    
    await state.update_data(final_username=actual_username)
    await state.set_state(AddClientState.waiting_for_validation_confirm)
    
    report_text = f"📋 <b>TEKSHIRUV HISOBOTI (Mijoz ID #{cid}):</b>\n\n"
    report_text += f"👤 <b>Mijoz ID:</b> <code>{cid}</code>\n"
    report_text += f"🤖 <b>Bot Username:</b> {actual_username}\n"
    report_text += f"📁 <b>Server papkasi:</b> <code>{s_folder}</code>\n"
    report_text += f"⚙️ <b>Rejim:</b> {mode.upper()} ({int(m_price):,} som)\n"
    report_text += f"📅 <b>Oxirgi to'lov:</b> {l_date}\n"
    report_text += f"⏳ <b>Keyingi to'lov:</b> {parsed_n_date}\n\n"
    
    if issues:
        report_text += "⚠️ <b>Aniqlangan kamchiliklar / Ogohlantirishlar:</b>\n"
        for issue in issues:
            report_text += f"• {issue}\n"
        report_text += "\nMa'lumotlarni baribir saqlamoqchimisiz?"
    else:
        report_text += "✅ <b>Barcha ma'lumotlar va bot tokeni muvaffaqiyatli tekshiruvdan o'tdi!</b>"
        
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Saqlash", callback_data="save_client_final")
    builder.button(text="❌ Bekor qilish", callback_data="cancel_client_final")
    builder.adjust(2)
    
    await wait_msg.delete()
    
    if isinstance(message_or_call_msg, types.Message) and getattr(message_or_call_msg, 'from_user', None) and message_or_call_msg.from_user.is_bot:
        try:
            await message_or_call_msg.edit_text(report_text, parse_mode="HTML", reply_markup=builder.as_markup())
            return
        except Exception:
            pass
    await message_or_call_msg.answer(report_text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "save_client_final")
async def save_client_final_callback(call: types.CallbackQuery, state: FSMContext):
    """Tekshirilgan mijoz botini ma'lumotlar bazasiga saqlash."""
    await call.answer()
    data = await state.get_data()
    
    cid = data['client_id']
    b_un = data.get('final_username', data['bot_username'])
    b_token = data['bot_token']
    s_folder = data['server_folder']
    mode = data['mode']
    m_price = data['monthly_price']
    l_date = data['last_payment_date']
    n_date = data['next_payment_date']
    
    rec_id = await add_client_bot(cid, b_un, b_token, s_folder, mode, m_price, l_date, n_date)
    await state.clear()
    
    success_text = (
        f"🎉 <b>Mijoz va Bot bazaga saqlandi!</b>\n\n"
        f"🆔 <b>Ro'yxat ID: #{rec_id}</b>\n"
        f"👤 <b>Mijoz ID:</b> <code>{cid}</code>\n"
        f"🤖 <b>Bot:</b> {b_un}\n"
        f"📁 <b>Server:</b> <code>{s_folder}</code>\n"
        f"⚙️ <b>Rejimi:</b> {mode.upper()}\n"
        f"💰 <b>Oylik narxi:</b> {int(m_price):,} som\n"
        f"📅 <b>Oxirgi to'lov:</b> {l_date}\n"
        f"⏳ <b>Keyingi to'lov:</b> <b>{n_date}</b>"
    )
    await call.message.edit_text(success_text, parse_mode="HTML")
    await call.message.answer("👤 <b>Mijozlar paneli:</b>", reply_markup=get_clients_panel_keyboard())

@dp.callback_query(F.data == "cancel_client_final")
async def cancel_client_final_callback(call: types.CallbackQuery, state: FSMContext):
    """Mijozni saqlash jarayonini bekor qilish va boshqaruv paneliga qaytish."""
    await call.answer()
    await state.clear()
    await call.message.edit_text("❌ <b>Saqlash bekor qilindi.</b>", parse_mode="HTML")
    await call.message.answer("👤 <b>Mijozlar paneli:</b>", reply_markup=get_clients_panel_keyboard())

# ==============================================================================
# 17-BO'LIM: ADMIN TOMONIDAN MIJOZLAR RO'YXATINI KO'RISH VA QIDIRISH
# ==============================================================================
@dp.message(F.text == "📋 Mijozlar ro'yxati")
async def list_clients(message: types.Message):
    """Barcha sozlangan mijoz botlari ro'yxatini olish va ko'rsatish."""
    if message.from_user.id not in ADMINS:
        return
    clients = await get_all_clients()
    if not clients:
        await message.answer("📭 Hozircha mijozlar ro'yxati bo'sh.", reply_markup=get_clients_panel_keyboard())
        return

    text = f"📋 <b>Barcha Mijozlar va Botlar Ro'yxati ({len(clients)} ta):</b>\n\n"
    today = datetime.now().date()
    
    for c in clients:
        n_date = c['next_payment_date']
        rem_days = (n_date - today).days
        status_str = "🟢 Faol" if rem_days > 0 else "🔴 To'lov vaqti kelgan!"
        
        text += (
            f"🆔 <b>Ro'yxat ID: #{c['id']}</b> | {status_str}\n"
            f"👤 <b>Mijoz ID:</b> <code>{c['client_id']}</code>\n"
            f"🤖 <b>Bot:</b> {c['bot_username']}\n"
            f"📁 <b>Server:</b> <code>{c['server_folder']}</code>\n"
            f"⚙️ <b>Rejimi:</b> {c['mode'].upper()}\n"
            f"💰 <b>Oylik narxi:</b> {int(c['monthly_price']):,} som\n"
            f"📅 <b>Oxirgi to'lov:</b> {c['last_payment_date']}\n"
            f"⏳ <b>Keyingi to'lov:</b> <b>{n_date}</b> (<i>{rem_days} kun qoldi</i>)\n"
            f"────────────────────\n"
        )
    await message.answer(text, parse_mode="HTML", reply_markup=get_clients_panel_keyboard())

@dp.message(F.text == "🔍 Mijoz qidirish")
async def start_search_client(message: types.Message, state: FSMContext):
    """Mijoz botini qidirish jarayonini boshlash."""
    if message.from_user.id not in ADMINS:
        return
    await state.set_state(SearchClientState.waiting_for_query)
    await message.answer(
        "🔍 <b>Mijoz qidirish:</b>\n\n"
        "Qidirilayotgan mijozning <b>Telegram ID-sini</b> yoki <b>Bot Username-ini</b> yuboring:\n"
        "<i>(Masalan: 8551089366 yoki @Anime_Uz_Bot)</i>",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(SearchClientState.waiting_for_query)
async def process_search_client(message: types.Message, state: FSMContext):
    """Ma'lumotlar bazasidan qidirish va natijalarni ko'rsatish."""
    query = message.text.strip()
    if is_menu_button_or_command(query):
        await state.clear()
        return

    results = await search_clients(query)
    
    if not results:
        await message.answer(
            f"❌ <b>'{query}' bo'yicha hech qanday mijoz topilmadi!</b>\n\n"
            f"Qaytadan boshqa ID yoki Username yuboring yoki bekor qilish uchun <b>❌ Bekor qilish</b> tugmasini bosing:",
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard()
        )
        return

    await state.clear()

    text = f"🔍 <b>Qidiruv natijalari ({len(results)} ta match):</b>\n\n"
    today = datetime.now().date()
    
    for c in results:
        n_date = c['next_payment_date']
        rem_days = (n_date - today).days
        status_str = "🟢 Faol" if rem_days > 0 else "🔴 To'lov vaqti kelgan!"
        
        text += (
            f"🆔 <b>Ro'yxat ID: #{c['id']}</b> | {status_str}\n"
            f"👤 <b>Mijoz ID:</b> <code>{c['client_id']}</code>\n"
            f"🤖 <b>Bot:</b> {c['bot_username']}\n"
            f"📁 <b>Server:</b> <code>{c['server_folder']}</code>\n"
            f"⚙️ <b>Rejimi:</b> {c['mode'].upper()}\n"
            f"💰 <b>Oylik narxi:</b> {int(c['monthly_price']):,} som\n"
            f"📅 <b>Oxirgi to'lov:</b> {c['last_payment_date']}\n"
            f"⏳ <b>Keyingi to'lov:</b> <b>{n_date}</b> (<i>{rem_days} kun qoldi</i>)\n"
            f"────────────────────\n"
        )
    await message.answer(text, parse_mode="HTML", reply_markup=get_clients_panel_keyboard())

# ==============================================================================
# 18-BO'LIM: ADMIN TOMONIDAN MIJOZNING O'CHIRILISHI VA TAHRIRLANISHI
# ==============================================================================
@dp.message(F.text == "🗑 Mijoz o'chirish")
async def start_delete_client(message: types.Message, state: FSMContext):
    """Qo'lda mijozni o'chirish jarayonini boshlash."""
    if message.from_user.id not in ADMINS:
        return
    await state.set_state(DeleteClientState.waiting_for_record_id)
    await message.answer(
        "🗑 <b>O'chirmoqchi bo'lgan botning Ro'yxat ID raqamini kiriting:</b>\n\n<i>(Masalan: 1)</i>",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(DeleteClientState.waiting_for_record_id)
async def confirm_delete_client(message: types.Message, state: FSMContext):
    """Mijoz botini o'chirishdan oldin tasdiqlashni so'rash."""
    text = message.text.strip()
    if is_menu_button_or_command(text):
        await state.clear()
        return

    try:
        rec_id = int(text)
        client = await get_client_by_id(rec_id)
        if not client:
            await message.answer("❌ Bunday Ro'yxat ID ga ega bot topilmadi. Qaytadan kiriting:")
            return
        
        await state.clear()
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Ha, o'chirish", callback_data=f"del_client_confirm:{rec_id}")
        builder.button(text="❌ Yo'q, bekor qilish", callback_data="del_client_cancel")
        builder.adjust(2)
        
        confirm_text = (
            f"⚠️ <b>Rostdan ham Ro'yxat ID #{rec_id} ({client['bot_username']}) botini o'chirmoqchimisiz?</b>\n\n"
            f"👤 Mijoz ID: <code>{client['client_id']}</code>\n"
            f"📁 Server: <code>{client['server_folder']}</code>"
        )
        await message.answer(confirm_text, parse_mode="HTML", reply_markup=builder.as_markup())
    except ValueError:
        await message.answer("❌ Faqat son kiriting:")

@dp.callback_query(F.data.startswith("del_client_confirm:"))
async def execute_delete_client(call: types.CallbackQuery):
    """Mijoz botini ma'lumotlar bazasidan o'chirish."""
    await call.answer()
    rec_id = int(call.data.split(":")[1])
    await delete_client_bot(rec_id)
    await call.message.edit_text(f"🗑 <b>Ro'yxat ID #{rec_id} boti muvaffaqiyatli o'chirildi!</b>", parse_mode="HTML")

@dp.callback_query(F.data == "del_client_cancel")
async def cancel_delete_client(call: types.CallbackQuery):
    """Mijozni o'chirish jarayonini bekor qilish."""
    await call.answer()
    await call.message.edit_text("❌ <b>O'chirish bekor qilindi.</b>", parse_mode="HTML")

@dp.message(F.text == "✏️ Mijoz tahrirlash")
async def start_edit_client(message: types.Message, state: FSMContext):
    """Mijozni tahrirlash jarayonini boshlash."""
    if message.from_user.id not in ADMINS:
        return
    await state.set_state(EditClientState.waiting_for_record_id)
    await message.answer(
        "✏️ <b>Tahrirlamoqchi bo'lgan botning Ro'yxat ID raqamini kiriting:</b>\n\n<i>(Masalan: 1)</i>",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(EditClientState.waiting_for_record_id)
async def select_field_to_edit(message: types.Message, state: FSMContext):
    """Tanlangan mijoz boti yozuvi uchun tahrirlash maydonlarini ko'rsatish."""
    text = message.text.strip()
    if is_menu_button_or_command(text):
        await state.clear()
        return

    try:
        rec_id = int(text)
        client = await get_client_by_id(rec_id)
        if not client:
            await message.answer("❌ Bunday Ro'yxat ID ga ega bot topilmadi. Qaytadan kiriting:")
            return
        
        await state.update_data(edit_rec_id=rec_id)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="👤 ID o'zgartirish", callback_data="efield:client_id")
        builder.button(text="🤖 Username o'zgartirish", callback_data="efield:bot_username")
        builder.button(text="🔑 Token o'zgartirish", callback_data="efield:bot_token")
        builder.button(text="📁 Papka nomini o'zgartirish", callback_data="efield:server_folder")
        builder.button(text="⚙️ Rejimni o'zgartirish", callback_data="efield:mode")
        builder.button(text="💰 Narxini o'zgartirish", callback_data="efield:monthly_price")
        builder.button(text="📅 Oxirgi to'lov sanasi", callback_data="efield:last_payment_date")
        builder.button(text="⏳ Keyingi to'lov sanasi", callback_data="efield:next_payment_date")
        builder.adjust(2)
        
        info_text = (
            f"✏️ <b>Ro'yxat ID #{rec_id} ({client['bot_username']}) ma'lumotlarini tahrirlash:</b>\n\n"
            f"👤 Mijoz ID: <code>{client['client_id']}</code>\n"
            f"📁 Papka: <code>{client['server_folder']}</code>\n"
            f"⚙️ Rejimi: {client['mode'].upper()}\n"
            f"💰 Narxi: {int(client['monthly_price']):,} som\n"
            f"📅 Oxirgi to'lov: {client['last_payment_date']}\n"
            f"⏳ Keyingi to'lov: {client['next_payment_date']}\n\n"
            f"Qaysi maydonni o'zgartirmoqchisiz?"
        )
        await message.answer(info_text, parse_mode="HTML", reply_markup=builder.as_markup())
    except ValueError:
        await message.answer("❌ Faqat son kiriting:")

@dp.callback_query(F.data.startswith("efield:"))
async def prompt_edit_value(call: types.CallbackQuery, state: FSMContext):
    """Tahrirlanayotgan maydon uchun yangi qiymat kiritishni so'rash."""
    await call.answer()
    field = call.data.split(":")[1]
    await state.update_data(edit_field=field)
    
    if field == "mode":
        builder = InlineKeyboardBuilder()
        builder.button(text="⚡️ Polling", callback_data="set_edit_mode:polling")
        builder.button(text="🌐 Webhook", callback_data="set_edit_mode:webhook")
        builder.adjust(2)
        await call.message.edit_text("⚙️ <b>Yangi rejimni tanlang:</b>", parse_mode="HTML", reply_markup=builder.as_markup())
        return
        
    await state.set_state(EditClientState.waiting_for_new_value)
    
    field_names = {
        "client_id": "yangi Mijoz Telegram ID-sini",
        "bot_username": "yangi Bot Username-ini (@...)",
        "bot_token": "yangi Bot API Token-ini",
        "server_folder": "yangi Server papkasi nomini",
        "monthly_price": "yangi Oylik narxni (somda)",
        "last_payment_date": "yangi Oxirgi to'lov sanasini (Misol: 2026-07-20)",
        "next_payment_date": "yangi Keyingi to'lov sanasini (Misol: 2026-08-20)"
    }
    
    await call.message.edit_text(
        f"📝 <b>Iltimos, {field_names.get(field, field)} kiriting:</b>",
        parse_mode="HTML"
    )
    await call.message.answer("Yangi qiymatni kiriting:", reply_markup=get_cancel_keyboard())

@dp.callback_query(F.data.startswith("set_edit_mode:"))
async def process_edit_mode_choice(call: types.CallbackQuery, state: FSMContext):
    """Mijoz boti ish rejimini yangilash va unga mos narxni belgilash."""
    await call.answer()
    mode = call.data.split(":")[1]
    data = await state.get_data()
    rec_id = data.get("edit_rec_id")
    
    poll_p = await get_setting("polling_price", "15000")
    web_p = await get_setting("webhook_price", "20000")
    m_price = float(poll_p) if mode == "polling" else float(web_p)
    
    await update_client_field(rec_id, "mode", mode)
    await update_client_field(rec_id, "monthly_price", m_price)
    
    await state.clear()
    await call.message.edit_text(
        f"✅ <b>Rejim {mode.upper()} ga va narx {int(m_price):,} som ga yangilandi!</b>",
        parse_mode="HTML"
    )
    await call.message.answer("👤 <b>Mijozlar paneli:</b>", reply_markup=get_clients_panel_keyboard())

@dp.message(EditClientState.waiting_for_new_value)
async def apply_edit_value(message: types.Message, state: FSMContext):
    """Qo'lda yozilgan yangi maydon qiymatini saqlash va kerak bo'lsa qayta hisoblash."""
    text_val = message.text.strip()
    if is_menu_button_or_command(text_val):
        await state.clear()
        return

    data = await state.get_data()
    rec_id = data.get("edit_rec_id")
    field = data.get("edit_field")
    
    try:
        if field == "client_id":
            val = int(text_val)
        elif field == "monthly_price":
            val = float(text_val)
        elif 'date' in field:
            val = parse_flexible_date(text_val)
        else:
            val = text_val
        
        await update_client_field(rec_id, field, val)
        
        if field == "last_payment_date":
            new_next = (datetime.strptime(val, "%Y-%m-%d") + timedelta(days=30)).strftime("%Y-%m-%d")
            await update_client_field(rec_id, "next_payment_date", new_next)
            
        await state.clear()
        await message.answer(
            f"✅ <b>Ro'yxat ID #{rec_id} bot ma'lumotlari muvaffaqiyatli yangilandi!</b>",
            parse_mode="HTML",
            reply_markup=get_clients_panel_keyboard()
        )
    except Exception as e:
        await message.answer(f"❌ Noto'g'ri qiymat kiritildi ({e}). Qaytadan kiriting:")

# ==============================================================================
# 18b-BO'LIM: ADMIN TOMONIDAN MIJOZ BOTLARINI TIZIM ORQALI BOSHQARISH
# ==============================================================================
@dp.message(F.text == "🤖 Botlarni boshqarish")
async def start_bot_management_panel(message: types.Message, state: FSMContext):
    """Admin botlarni boshqarish paneli."""
    if message.from_user.id not in ADMINS:
        return
    await state.set_state(BotMgmtState.in_bot_panel)
    await message.answer(
        "🤖 <b>Botlarni boshqarish paneli (systemctl service):</b>\n\n"
        "Ushbu bo'lim orqali siz mijoz botlarini qidirishingiz, ularning holatini "
        "ko'rishingiz (ishlayaptimi yoki yo'q) va serverdagi systemd xizmatini "
        "boshqarishingiz (Start/Stop/Restart) mumkin.",
        parse_mode="HTML",
        reply_markup=get_bot_mgmt_keyboard()
    )

@dp.message(BotMgmtState.in_bot_panel, F.text == "🔍 Bot izlash")
async def prompt_bot_search(message: types.Message, state: FSMContext):
    """Bot qidirish so'rovini yuborishni so'rash."""
    if message.from_user.id not in ADMINS:
        return
    await state.set_state(BotMgmtState.waiting_for_search_query)
    await message.answer(
        "🔍 <b>Botni qidirish uchun uning ma'lumotlarini yuboring:</b>\n\n"
        "Tizimda botni topish uchun quyidagilardan birini yuborishingiz mumkin:\n"
        "• Bot <b>Username-i</b> (masalan: @Yangi_Bot)\n"
        "• Mijoz Telegram <b>ID-si</b> (masalan: 123456789)\n"
        "• Bot <b>API Tokeni</b>\n"
        "• Tizimdagi <b>Ro'yxat ID-si</b> (masalan: 1)\n\n"
        "<i>Eslatma: Faqat ID yuborish orqali qidirishingiz tavsiya etiladi.</i>",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(BotMgmtState.in_bot_panel, F.text == "📋 Mijoz botlar ro'yxati")
async def list_bot_management(message: types.Message):
    """Barcha mijoz botlarini boshqarish tugmalari bilan ko'rsatish."""
    if message.from_user.id not in ADMINS:
        return
    clients = await get_all_clients()
    if not clients:
        await message.answer("📭 Hozircha mijoz botlar ro'yxati bo'sh.", reply_markup=get_bot_mgmt_keyboard())
        return
        
    text = f"📋 <b>Barcha Mijoz Botlari Ro'yxati ({len(clients)} ta):</b>\n\n"
    builder = InlineKeyboardBuilder()
    for c in clients:
        text += f"🆔 #{c['id']} | 🤖 {c['bot_username']} (Folder: {c['server_folder']})\n"
        builder.button(text=f"🤖 #{c['id']} ({c['bot_username']})", callback_data=f"manage_bot:{c['id']}")
    
    builder.adjust(2)
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.message(BotMgmtState.waiting_for_search_query)
async def process_bot_search_query(message: types.Message, state: FSMContext):
    """Kiritilgan matn bo'yicha botlarni qidirish va natijalarni inline klaviatura bilan ko'rsatish."""
    query = message.text.strip()
    if is_menu_button_or_command(query):
        await state.set_state(BotMgmtState.in_bot_panel)
        await message.answer("🤖 <b>Botlarni boshqarish paneli:</b>", reply_markup=get_bot_mgmt_keyboard())
        return
        
    from database import get_db
    p = await get_db()
    async with p.acquire() as conn:
        if query.isdigit():
            cid = int(query)
            clients = await conn.fetch(
                "SELECT * FROM master_clients WHERE id = $1 OR client_id = $1 OR bot_username ILIKE $2 ORDER BY id ASC;",
                cid, f"%{query}%"
            )
        else:
            clients = await conn.fetch(
                "SELECT * FROM master_clients WHERE bot_username ILIKE $1 OR bot_token = $2 ORDER BY id ASC;",
                f"%{query}%", query
            )
            
    if not clients:
        await message.answer("❌ <b>Ushbu ma'lumot bo'yicha hech qanday bot topilmadi!</b>\n\nQaytadan boshqa ID yoki username yuborib ko'ring:")
        return
        
    await state.set_state(BotMgmtState.in_bot_panel)
    text = f"🔍 <b>Qidiruv natijalari ({len(clients)} ta bot topildi):</b>\n\n"
    builder = InlineKeyboardBuilder()
    for c in clients:
        text += f"🆔 #{c['id']} | 🤖 {c['bot_username']} (Mijoz ID: {c['client_id']})\n"
        builder.button(text=f"🤖 #{c['id']} ({c['bot_username']})", callback_data=f"manage_bot:{c['id']}")
        
    builder.adjust(2)
    await message.answer(text, parse_mode="HTML", reply_markup=get_bot_mgmt_keyboard())

@dp.callback_query(F.data.startswith("manage_bot:"))
async def manage_bot_details_callback(call: types.CallbackQuery):
    """Tanlangan bot uchun boshqaruv paneli va holatini ko'rsatish."""
    await call.answer()
    bot_id = int(call.data.split(":")[1])
    client = await get_client_by_id(bot_id)
    if not client:
        await call.message.reply("❌ Xatolik: Bot topilmadi!")
        return
        
    await show_bot_detail_view(call.message, client)

async def show_bot_detail_view(message: types.Message, client):
    """Bot tafsilotlarini va boshqaruv (Start/Stop/Restart) tugmalarini ko'rsatish."""
    folder = client['server_folder']
    service_name = folder if folder.startswith("sky-") else f"sky-{folder}"
    
    # VPS-da SSH orqali xizmat holatini tekshirish
    exit_status, stdout_str, stderr_str = await run_vps_command(f"systemctl is-active {service_name}")
    status = stdout_str.strip()
    
    if status == "active":
        status_emoji = "🟢 Faol (Ishlamoqda)"
        action_btn_text = "🔴 Botni to'xtatish (Stop)"
        action_callback = f"bot_act:stop:{client['id']}"
    else:
        status_emoji = f"🔴 O'chirilgan (Ishlamayapti - {status})"
        action_btn_text = "🟢 Botni yoqish (Start)"
        action_callback = f"bot_act:start:{client['id']}"
        
    text = (
        f"🤖 <b>BOT MA'LUMOTLARI VA BOSHQARUVI:</b>\n\n"
        f"🆔 <b>Ro'yxat ID:</b> #{client['id']}\n"
        f"👤 <b>Mijoz ID:</b> <code>{client['client_id']}</code>\n"
        f"🤖 <b>Bot Username:</b> {client['bot_username']}\n"
        f"⚙️ <b>Ish rejimi:</b> {client['mode'].upper()}\n"
        f"📁 <b>Server papkasi:</b> <code>{client['server_folder']}</code>\n"
        f"⚙️ <b>Systemd Servisi:</b> <code>{service_name}.service</code>\n"
        f"📊 <b>Hozirgi holati:</b> <b>{status_emoji}</b>\n\n"
        f"<i>Siz ushbu botning serverdagi xizmatini (systemd service) boshqarishingiz mumkin:</i>"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text=action_btn_text, callback_data=action_callback)
    builder.button(text="🔄 Qayta ishga tushirish (Restart)", callback_data=f"bot_act:restart:{client['id']}")
    builder.button(text="📋 Ro'yxatga qaytish", callback_data="bot_back_to_list")
    builder.adjust(1, 1, 1)
    
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    except Exception:
        await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "bot_back_to_list")
async def bot_back_to_list_callback(call: types.CallbackQuery):
    """Boshqaruv panelidan yana botlar ro'yxatiga qaytish."""
    await call.answer()
    clients = await get_all_clients()
    if not clients:
        await call.message.edit_text("📭 Hozircha mijoz botlar ro'yxati bo'sh.")
        return
        
    text = f"📋 <b>Barcha Mijoz Botlari Ro'yxati ({len(clients)} ta):</b>\n\n"
    builder = InlineKeyboardBuilder()
    for c in clients:
        text += f"🆔 #{c['id']} | 🤖 {c['bot_username']} (Folder: {c['server_folder']})\n"
        builder.button(text=f"🤖 #{c['id']} ({c['bot_username']})", callback_data=f"manage_bot:{c['id']}")
    
    builder.adjust(2)
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("bot_act:"))
async def process_bot_action_callback(call: types.CallbackQuery):
    """Start, Stop yoki Restart buyruqlarini serverda bajarish."""
    parts = call.data.split(":")
    action = parts[1]
    client_id = int(parts[2])
    
    client = await get_client_by_id(client_id)
    if not client:
        await call.answer("❌ Bot topilmadi!", show_alert=True)
        return
        
    folder = client['server_folder']
    service_name = folder if folder.startswith("sky-") else f"sky-{folder}"
    
    # VPS-da SSH orqali xizmatni boshqarish
    if action == "start":
        exit_code, stdout_str, stderr_str = await run_vps_command(f"systemctl start {service_name}")
        if exit_code == 0:
            await call.answer(f"✅ {service_name} ishga tushirildi!", show_alert=True)
        else:
            await call.answer(f"❌ Xatolik yuz berdi (Exit code: {exit_code})", show_alert=True)
    elif action == "stop":
        exit_code, stdout_str, stderr_str = await run_vps_command(f"systemctl stop {service_name}")
        if exit_code == 0:
            await call.answer(f"🛑 {service_name} to'xtatildi!", show_alert=True)
        else:
            await call.answer(f"❌ Xatolik yuz berdi (Exit code: {exit_code})", show_alert=True)
    elif action == "restart":
        exit_code, stdout_str, stderr_str = await run_vps_command(f"systemctl restart {service_name}")
        if exit_code == 0:
            await call.answer(f"🔄 {service_name} qayta ishga tushirildi!", show_alert=True)
        else:
            await call.answer(f"❌ Xatolik yuz berdi (Exit code: {exit_code})", show_alert=True)
            
    await show_bot_detail_view(call.message, client)

# ==============================================================================
# 19-BO'LIM: FOYDALANUVCHI KABINETI VA UZAYTIRISHLAR
# ==============================================================================
@dp.message(F.text == "👤 Mening botlarim va to'lovlarim")
async def client_my_bots(message: types.Message):
    """Foydalanuvchiga tegishli barcha botlarni, ularning statistikasi va uzaytirish tugmalari bilan ko'rsatish."""
    user_id = message.from_user.id
    bots = await get_client_bots_by_user(user_id)
    
    if not bots:
        await message.answer("📭 Sizda ro'yxatdan o'tgan botlar topilmadi.")
        return

    await message.answer("👤 <b>Sizning Botlaringiz va To'lov Muddatlari:</b>", parse_mode="HTML")
    today = datetime.now().date()
    
    for b in bots:
        n_date = b['next_payment_date']
        rem_days = (n_date - today).days
        st = "🟢 Faol" if rem_days > 0 else "🔴 To'lov vaqti kelgan!"
        
        bot_desc = (
            f"🤖 <b>Bot:</b> {b['bot_username']}\n"
            f"📊 <b>Holati:</b> {st}\n"
            f"⚙️ <b>Rejimi:</b> {b['mode'].upper()}\n"
            f"💰 <b>Oylik to'lov:</b> {int(b['monthly_price']):,} som\n"
            f"📅 <b>Oxirgi to'lov:</b> {b['last_payment_date']}\n"
            f"⏳ <b>Keyingi to'lov:</b> <b>{n_date}</b>\n"
            f"⌛ <b>Qolgan vaqt:</b> <b>{max(0, rem_days)} kun</b>"
        )
        
        builder = InlineKeyboardBuilder()
        builder.button(text="💳 Uzaytirish (Renewal)", callback_data=f"renew_bot:{b['id']}")
        
        await message.answer(bot_desc, parse_mode="HTML", reply_markup=builder.as_markup())

# --- UZAYTIRISH UCHUN CALLBACK HANDLERLAR ---

@dp.callback_query(F.data.startswith("renew_bot:"))
async def process_renew_bot_callback(call: types.CallbackQuery, state: FSMContext):
    """Botni uzaytirish jarayonini boshlash va muddat tanlashni so'rash."""
    await call.answer()
    bot_id = int(call.data.split(":")[1])
    client = await get_client_by_id(bot_id)
    
    if not client:
        await call.message.reply("❌ Bot topilmadi!")
        return
        
    text = (
        f"💳 <b>Botni uzaytirish (Prolongation):</b>\n\n"
        f"🤖 Bot: <b>{client['bot_username']}</b>\n"
        f"💰 Oylik narxi: {int(client['monthly_price']):,} som\n\n"
        f"Iltimos, botni necha oyga uzaytirmoqchi ekanligingizni tanlang:"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="⏳ 1 oy", callback_data=f"ren_dur:{bot_id}:1")
    builder.button(text="⏳ 3 oy", callback_data=f"ren_dur:{bot_id}:3")
    builder.button(text="⏳ 6 oy", callback_data=f"ren_dur:{bot_id}:6")
    builder.button(text="⏳ 12 oy", callback_data=f"ren_dur:{bot_id}:12")
    builder.adjust(2, 2)
    
    await call.message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("ren_dur:"))
async def process_renew_duration_callback(call: types.CallbackQuery, state: FSMContext):
    """Uzaytirish muddatini saqlash, narxni hisoblash va to'lov usulini so'rash."""
    await call.answer()
    parts = call.data.split(":")
    bot_id = int(parts[1])
    months = int(parts[2])
    
    client = await get_client_by_id(bot_id)
    if not client:
        await call.message.reply("❌ Bot topilmadi!")
        return
        
    total_price = float(client['monthly_price']) * months
    
    await state.update_data(
        renewing_bot_id=bot_id,
        renewing_months=months,
        renewing_total=total_price,
        renewing_bot_username=client['bot_username'],
        renewing_bot_token=client['bot_token']
    )
    
    text = (
        f"⚙️ <b>Uzaytirish muddati:</b> {months} oy\n"
        f"💰 <b>Umumiy to'lov:</b> <b>{int(total_price):,} som</b>\n\n"
        f"To'lov usulini tanlang:"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Telegram orqali tezkor to'lov (Click/Payme)", callback_data=f"ren_pay:{bot_id}:{months}:telegram")
    builder.button(text="👤 Karta orqali (Chek yuborish)", callback_data=f"ren_pay:{bot_id}:{months}:card")
    builder.adjust(1, 1)
    
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("ren_pay:"))
async def process_renew_payment_method_callback(call: types.CallbackQuery, state: FSMContext):
    """Uzaytirish uchun tanlangan to'lov yo'nalishini bajarish."""
    await call.answer()
    parts = call.data.split(":")
    bot_id = int(parts[1])
    months = int(parts[2])
    method = parts[3]
    
    client = await get_client_by_id(bot_id)
    if not client:
        await call.message.reply("❌ Bot topilmadi!")
        return
        
    total_price = float(client['monthly_price']) * months
    
    if method == "telegram":
        prov_token = await get_setting("provider_token", "")
        if not prov_token:
            await call.message.edit_text(
                "⚠️ <b>Telegram orqali tezkor to'lov faollashtirilmagan!</b>\n\n"
                "Iltimos, pastdagi Karta orqali to'lov tugmasini bosing:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardBuilder().button(text="👤 Karta orqali to'lov", callback_data=f"ren_pay:{bot_id}:{months}:card").as_markup()
            )
            return
            
        await call.message.delete()
        prices = [
            types.LabeledPrice(label=f"Uzaytirish {months} oy - {client['bot_username']}", amount=int(total_price) * 100)
        ]
        
        await bot.send_invoice(
            chat_id=call.from_user.id,
            title="Bot muddatini uzaytirish",
            description=f"Bot: {client['bot_username']} - {months} oylik to'lov",
            payload=f"renew_invoice:{bot_id}:{months}:{int(total_price)}",
            provider_token=prov_token,
            currency="UZS",
            prices=prices,
            start_parameter="renew-bot-invoice"
        )
        
    elif method == "card":
        card_num = await get_setting("card_number", "8600 0000 0000 0000")
        await state.update_data(
            renewing_bot_id=bot_id,
            renewing_months=months,
            renewing_total=total_price,
            renewing_bot_username=client['bot_username'],
            renewing_bot_token=client['bot_token']
        )
        await state.set_state(BuyBotState.waiting_for_receipt)
        
        pay_text = (
            f"💳 <b>KARTA ORQALI TO'LOV (BOTNI UZAYTIRISH)</b>\n\n"
            f"🤖 <b>Bot:</b> {client['bot_username']}\n"
            f"⏳ <b>Muddat:</b> {months} oy\n"
            f"💰 <b>Umumiy to'lov:</b> <b>{int(total_price):,} som</b>\n\n"
            f"💳 <b>Karta raqami:</b>\n"
            f"<code>{card_num}</code>\n\n"
            f"📲 Click / Payme / Uzum ilovalari orqali to'lov qiling.\n\n"
            f"📸 To'lovdan so'ng <b>CHEKINI (skrinshot)</b> rasm yoki hujjat ko'rinishida shu yerga yuboring:"
        )
        
        builder = InlineKeyboardBuilder()
        builder.button(text="📲 Click App", url="https://click.uz")
        builder.button(text="📲 Payme App", url="https://payme.uz")
        builder.button(text="📋 Kartani nusxalash", callback_data=f"copy_card:{card_num}")
        builder.adjust(2, 1)
        
        await call.message.delete()
        await call.message.answer(pay_text, parse_mode="HTML", reply_markup=get_cancel_keyboard())
        await call.message.answer("To'lov ilovalari:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("copy_card:"))
async def process_copy_card_callback(call: types.CallbackQuery):
    """Karta raqamini matndan nusxalash mumkinligi haqida xabar berish."""
    card = call.data.split(":")[1]
    await call.answer(text=f"Kartani bosing, avtomatik nusxalanadi: {card}", show_alert=True)

# ==============================================================================
# 20-BO'LIM: STATISTIKA VA ALOQA MA'LUMOTLARI
# ==============================================================================
@dp.message(F.text == "📞 Admin bilan bog'lanish")
async def client_contact(message: types.Message):
    """To'lov karta ma'lumotlari va admin yordam kontaktlarini ko'rsatish."""
    card_num = await get_setting("card_number", "8600 0000 0000 0000")
    await message.answer(
        f"📞 <b>To'lovlar va botni uzaytirish bo'yicha admin bilan bog'lanish:</b>\n\n"
        f"💳 <b>To'lov uchun karta:</b> <code>{card_num}</code>\n"
        f"💬 Admin: @MasterAdminSupport\n"
        f"⏰ Ish vaqti: 24/7 online",
        parse_mode="HTML"
    )

@dp.message(F.text == "📊 Statistika")
async def admin_stats(message: types.Message):
    """Admin uchun umumiy tizim ko'rsatkichlari va to'lov statistikasini ko'rsatish."""
    if message.from_user.id not in ADMINS:
        return
    clients = await get_all_clients()
    today = datetime.now().date()
    active_count = sum(1 for c in clients if c['next_payment_date'] > today)
    
    poll_count = sum(1 for c in clients if c['mode'] == 'polling')
    web_count = sum(1 for c in clients if c['mode'] == 'webhook')
    total_rev = sum(c['monthly_price'] for c in clients)
    
    text = (
        f"📊 <b>Master Bot Statistikasi:</b>\n\n"
        f"🤖 Barcha biriktirilgan botlar: <b>{len(clients)} ta</b>\n"
        f"🟢 Faol (To'lov qilingan): <b>{active_count} ta</b>\n"
        f"🔴 To'lov vaqti kelgan: <b>{len(clients) - active_count} ta</b>\n\n"
        f"⚡️ Polling botlar: <b>{poll_count} ta</b>\n"
        f"🌐 Webhook botlar: <b>{web_count} ta</b>\n"
        f"💰 Oylik umumiy daromad: <b>{int(total_rev):,} som</b>"
    )
    await message.answer(text, parse_mode="HTML")

# ==============================================================================
# 21-BO'LIM: TO'LOV MUDDATLARINI AVTOMATIK TEKSHIRUVCHI VA ASOSIY ISHGA TUSHIROVCHI
# ==============================================================================
scheduler = AsyncIOScheduler()

async def check_payments_and_notify():
    """To'lov muddati tugashini har kuni tekshirish va mijoz/adminlarni xabardor qilish."""
    logging.info("Checking billing payment dates and notifying users...")
    try:
        clients = await get_all_clients()
        today = datetime.now().date()
        
        for c in clients:
            n_date = c['next_payment_date']
            rem_days = (n_date - today).days
            client_id = c['client_id']
            bot_un = c['bot_username']
            status = c.get('status', 'active')
            
            msg = None
            if rem_days == 3 and status == 'active':
                msg = (
                    f"⚠️ <b>Botingizning oylik to'lov muddati tugashiga 3 kun qoldi!</b>\n\n"
                    f"🤖 <b>Bot:</b> {bot_un}\n"
                    f"📅 <b>To'lov sanasi:</b> {n_date}\n"
                    f"💰 <b>Oylik to'lov:</b> {int(c['monthly_price']):,} som\n\n"
                    f"🔄 Botni uzaytirish uchun <b>Mening botlarim va to'lovlarim</b> bo'limiga kiring."
                )
            elif rem_days == 1 and status == 'active':
                msg = (
                    f"⏰ <b>Botingizning oylik to'lov muddati tugashiga 1 kun qoldi!</b>\n\n"
                    f"🤖 <b>Bot:</b> {bot_un}\n"
                    f"📅 <b>To'lov sanasi:</b> {n_date}\n"
                    f"💰 <b>Oylik to'lov:</b> {int(c['monthly_price']):,} som\n\n"
                    f"🚨 Ertaga botingiz faoliyati to'xtatilishi mumkin! Iltimos, uzaytirish uchun o'z vaqtida to'lov qiling."
                )
            elif rem_days == 0 and status == 'active':
                msg = (
                    f"🚨 <b>Botingizning oylik to'lov muddati BUGUN tugaydi!</b>\n\n"
                    f"🤖 <b>Bot:</b> {bot_un}\n"
                    f"📅 <b>To'lov sanasi:</b> {n_date}\n"
                    f"💰 <b>Oylik to'lov:</b> {int(c['monthly_price']):,} som\n\n"
                    f"Iltimos, bot faoliyati to'xtab qolmasligi uchun bugun to'lov qiling!"
                )
            elif rem_days < 0 and status == 'active':
                await update_client_field(c['id'], 'status', 'expired')
                msg = (
                    f"🔴 <b>Botingizning oylik to'lov muddati tugadi va bot to'xtatildi!</b>\n\n"
                    f"🤖 <b>Bot:</b> {bot_un}\n"
                    f"📅 <b>Muddati:</b> {n_date}\n\n"
                    f"🔄 Botni qayta faollashtirish uchun admin bilan bog'laning yoki to'lov qiling."
                )
                for admin_id in ADMINS:
                    try:
                        await bot.send_message(
                            admin_id,
                            text=f"🚨 <b>Mijoz boti to'lov muddati o'tib ketgani uchun to'xtatildi!</b>\n\n"
                                 f"🆔 ID: #{c['id']}\n"
                                 f"👤 Mijoz ID: <code>{client_id}</code>\n"
                                 f"🤖 Bot: {bot_un}\n"
                                 f"📅 Muddati: {n_date}",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logging.error(f"Failed to notify admin of expired bot: {e}")

            if msg:
                try:
                    await bot.send_message(client_id, text=msg, parse_mode="HTML")
                except Exception as e:
                    logging.error(f"Failed to send notification to client {client_id}: {e}")
    except Exception as e:
        logging.error(f"Error in check_payments_and_notify: {e}")

from aiogram.webhook.aiohttp_impl import SimpleRequestHandler, setup_application

app = web.Application()

async def self_ping_loop(url: str):
    """Bot o'chib qolmasligi uchun har 10 daqiqada o'ziga HTTP request yuboradi (o'zbekcha sharh)."""
    await asyncio.sleep(15)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                print(f"[Self-Ping] Request yuborilmoqda: {url}")
                async with session.get(url, timeout=10) as resp:
                    print(f"[Self-Ping] Status: {resp.status}")
            except Exception as e:
                print(f"[Self-Ping] Xatolik: {e}")
            await asyncio.sleep(600)

async def index_handler(request):
    """Veb-saytga kirganda bot holatini ko'rsatuvchi sahifa (o'zbekcha sharh)."""
    bot_me = None
    status_text = "Noma'lum"
    status_class = "unknown"
    try:
        bot_me = await bot.get_me()
        status_text = f"Boti faol va ishlamoqda: @{bot_me.username}"
        status_class = "running"
    except Exception as e:
        status_text = f"Bot o'chirilgan yoki ulanib bo'lmadi: {e}"
        status_class = "stopped"
        
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Sky Master Bot Status</title>
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background-color: #f0f2f5;
                margin: 0;
                padding: 0;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
            }}
            .card {{
                background: white;
                padding: 40px;
                border-radius: 16px;
                box-shadow: 0 10px 25px rgba(0,0,0,0.05);
                text-align: center;
                max-width: 450px;
                width: 90%;
            }}
            h1 {{
                color: #1a1a1a;
                font-size: 26px;
                margin-top: 0;
                margin-bottom: 20px;
            }}
            .status {{
                display: inline-block;
                padding: 12px 24px;
                border-radius: 30px;
                font-weight: bold;
                font-size: 16px;
                color: white;
                letter-spacing: 0.5px;
            }}
            .running {{
                background-color: #10b981;
            }}
            .stopped {{
                background-color: #ef4444;
            }}
            .unknown {{
                background-color: #f59e0b;
            }}
            p {{
                color: #6b7280;
                margin-top: 20px;
                font-size: 14px;
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>Sky Master Bot Status</h1>
            <div class="status {status_class}">{status_text}</div>
            <p>Oxirgi yangilangan vaqt: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type="text/html")

app.router.add_get("/", index_handler)

webhook_requests_handler = SimpleRequestHandler(
    dispatcher=dp,
    bot=bot,
)
webhook_requests_handler.register(app, path="/webhook")
setup_application(app, dp, bot=bot)

async def on_startup(app):
    await init_db()
    
    # Billing tekshiruvini kunlik 09:00 ga rejalashtirish
    scheduler.add_job(check_payments_and_notify, 'cron', hour=9, minute=0)
    scheduler.start()
    
    webhook_url = os.getenv("WEBHOOK_URL", "")
    if webhook_url:
        try:
            print(f"Webhook o'rnatilmoqda: {webhook_url}")
            await bot.set_webhook(webhook_url)
            print("Webhook muvaffaqiyatli o'rnatildi!")
            
            # O'ziga self-ping yuborishni boshlash (Render uyqu rejimidan himoya)
            base_url = webhook_url.split("/webhook")[0]
            asyncio.create_task(self_ping_loop(base_url))
        except Exception as e:
            print(f"Webhook o'rnatishda xatolik: {e}. Polling rejimiga o'tilmoqda...")
            await bot.delete_webhook()
            asyncio.create_task(run_polling())
    else:
        print("WEBHOOK_URL topilmadi. Polling rejimida boshlanmoqda...")
        await bot.delete_webhook()
        asyncio.create_task(run_polling())

async def run_polling():
    try:
        print("🚀 Polling ishga tushdi...")
        await dp.start_polling(bot, polling_timeout=10)
    except Exception as e:
        print(f"Polling xatosi: {e}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.on_startup.append(on_startup)
    print(f"🚀 Web server {port} portida ishga tushmoqda...")
    web.run_app(app, host="0.0.0.0", port=port)
