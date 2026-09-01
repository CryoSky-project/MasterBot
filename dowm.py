# ==============================================================================
# TELEGRAM VIDEO QUALITY ANALYZER & HIGH-COMPRESSION BOT (dowm.py)
# ==============================================================================
import os
import json
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv

# Third-party Telegram Bot library imports
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Load environment variables
load_dotenv()

# Logger settings configuration
logging.basicConfig(level=logging.INFO)

# Retrieve token from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "8714157634:AAF6oBKitMgPZadMiG1JCELD9CFM8zMu1tY")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Directories for temp files
DOWNLOAD_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(DOWNLOAD_DIR, "temp_videos")
os.makedirs(TEMP_DIR, exist_ok=True)

# Path to FFmpeg binaries on VPS
FFMPEG_PATH = "/root/bin/ffmpeg"
FFPROBE_PATH = "/root/bin/ffprobe"

# ==============================================================================
# FSM STATE DEFINITIONS
# ==============================================================================
class CompressState(StatesGroup):
    waiting_for_quality = State()

# ==============================================================================
# VIDEO UTILITY METHODS (FFMPEG & FFPROBE)
# ==============================================================================
async def analyze_video(file_path: str) -> dict:
    """Run ffprobe asynchronously and return video metadata."""
    cmd = [
        FFPROBE_PATH,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await process.communicate()
    
    try:
        data = json.loads(stdout.decode('utf-8', errors='ignore'))
        
        # Parse stream details
        video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
        
        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))
        duration = float(data.get("format", {}).get("duration", 0.0))
        size = int(data.get("format", {}).get("size", 0))
        codec = video_stream.get("codec_name", "unknown")
        
        return {
            "width": width,
            "height": height,
            "duration": duration,
            "size": size,
            "codec": codec,
            "valid": True
        }
    except Exception as e:
        logging.error(f"Error parsing video info: {e}")
        return {"valid": False}

async def compress_video(input_path: str, output_path: str, target_height: int) -> bool:
    """Run FFmpeg asynchronously to compress and scale video with high compression settings."""
    # Scale filter (ensure dimensions are divisible by 2 for H.264)
    vf_filter = f"scale=-2:{target_height}"
    
    cmd = [
        FFMPEG_PATH,
        "-y",
        "-i", input_path,
        "-vcodec", "libx264",
        "-crf", "28",           # CRF 28 offers high compression ratio with decent quality
        "-preset", "veryfast",  # Speed up rendering process
        "-vf", vf_filter,
        "-acodec", "aac",
        "-ab", "64k",           # Audio compression to save additional space
        output_path
    ]
    
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await process.communicate()
    return process.returncode == 0

# ==============================================================================
# BOT EVENT HANDLERS
# ==============================================================================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    """Greeting message for start command."""
    welcome = (
        "🎬 <b>Salom! Men Video Compress Botман!</b>\n\n"
        "Menga istalgan video yuboring, men uning o'lchamlarini, davomiyligini "
        "tahlil qilib, uni eng minimal hajmga (MB) keltirib beraman!\n\n"
        "👇 Boshlash uchun video yuboring:"
    )
    await message.answer(welcome, parse_mode="HTML")

@dp.message(F.video)
async def process_video_message(message: types.Message, state: FSMContext):
    """Handle incoming video files, download, and show details with quality options."""
    video = message.video
    
    # 20MB bot limit check for downloading
    if video.file_size > 20 * 1024 * 1024:
        await message.answer(
            "⚠️ <b>Telegram bot cheklovi:</b>\n"
            "Bot API orqali faqat 20 MB gacha bo'lgan videolarni yuklab olish mumkin.\n"
            f"Siz yuborgan video hajmi: <b>{video.file_size / (1024*1024):.2f} MB</b>.",
            parse_mode="HTML"
        )
        return
        
    status_msg = await message.answer("⏳ <b>Video serverga yuklanmoqda...</b>", parse_mode="HTML")
    
    # Generate unique temp paths
    file_id = video.file_id
    input_filename = f"in_{message.message_id}_{file_id[:8]}.mp4"
    input_path = os.path.join(TEMP_DIR, input_filename)
    
    await bot.download(video, destination=input_path)
    
    await status_msg.edit_text("🔍 <b>Video sifati va formati tahlil qilinmoqda...</b>", parse_mode="HTML")
    
    info = await analyze_video(input_path)
    if not info["valid"]:
        await status_msg.edit_text("❌ <b>Xatolik:</b> Video formatini aniqlab bo'lmadi.")
        if os.path.exists(input_path):
            os.remove(input_path)
        return

    # Store file paths in state
    await state.update_data(input_path=input_path, original_size=info["size"])
    await state.set_state(CompressState.waiting_for_quality)
    
    # Construct details card
    original_mb = info["size"] / (1024 * 1024)
    duration_str = str(timedelta(seconds=int(info["duration"])))
    
    details_text = (
        f"🎬 <b>Video muvaffaqiyatli yuklandi!</b>\n\n"
        f"📏 <b>O'lchami (Resolution):</b> {info['width']}x{info['height']}\n"
        f"⏳ <b>Davomiyligi (Duration):</b> {duration_str}\n"
        f"📦 <b>Hajmi (File Size):</b> {original_mb:.2f} MB\n"
        f"🔑 <b>Format/Codec:</b> {info['codec'].upper()}\n\n"
        f"👇 <i>Сығымдалатын видео сапасын таңдаңыз (неғұрлым төмен болса, МБ соғұрлым аз болады):</i>"
    )
    
    # Generate keyboard options dynamically
    builder = InlineKeyboardBuilder()
    builder.button(text="🎬 1080p (FHD)", callback_data="quality:1080")
    builder.button(text="🎬 720p (HD)", callback_data="quality:720")
    builder.button(text="🎬 480p (SD)", callback_data="quality:480")
    builder.button(text="🎬 360p (Low)", callback_data="quality:360")
    builder.adjust(2, 2)
    
    await status_msg.delete()
    await message.reply(details_text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.callback_query(CompressState.waiting_for_quality, F.data.startswith("quality:"))
async def process_quality_choice(call: types.CallbackQuery, state: FSMContext):
    """Start compression using chosen target height quality."""
    await call.answer()
    target_height = int(call.data.split(":")[1])
    
    data = await state.get_data()
    input_path = data["input_path"]
    original_size = data["original_size"]
    
    status_msg = await call.message.answer(
        f"⚡️ <b>Video {target_height}p formatiga o'tkazilib, minimal hajmga (MB) sıғымдалмауда...</b>\n"
        f"<i>(Bu jarayon bir necha soniyadan bir necha daqiqagacha davom etishi mumkin)</i>",
        parse_mode="HTML"
    )
    
    # Generate output path
    output_filename = f"out_{call.message.message_id}_{target_height}p.mp4"
    output_path = os.path.join(TEMP_DIR, output_filename)
    
    start_time = datetime.now()
    
    success = await compress_video(input_path, output_path, target_height)
    
    if not success:
        await status_msg.edit_text("❌ <b>Xatolik:</b> FFmpeg сығымдау процесі сәтсіз аяқталды.")
        await state.clear()
        if os.path.exists(input_path): os.remove(input_path)
        return
        
    duration = (datetime.now() - start_time).seconds
    compressed_size = os.path.getsize(output_path)
    
    # 50MB check for Telegram uploads
    if compressed_size > 50 * 1024 * 1024:
        await status_msg.edit_text(
            f"❌ <b>Xatolik:</b> Сығымдалған файл өлшемі Telegram шектеуінен (50 MB) асып кетті: "
            f"<b>{compressed_size / (1024*1024):.2f} MB</b>.\n"
            "Кішірек сапаны таңдап көріңіз.",
            parse_mode="HTML"
        )
        await state.clear()
        if os.path.exists(input_path): os.remove(input_path)
        if os.path.exists(output_path): os.remove(output_path)
        return
        
    await status_msg.edit_text("📤 <b>Сығымдалған видео жүктелуде...</b>", parse_mode="HTML")
    
    orig_mb = original_size / (1024 * 1024)
    comp_mb = compressed_size / (1024 * 1024)
    saved_percent = ((original_size - compressed_size) / original_size) * 100
    
    caption_text = (
        f"✅ <b>Видео сәтті сығымдалды!</b>\n\n"
        f"📦 <b>Бастапқы өлшем:</b> {orig_mb:.2f} MB\n"
        f"📉 <b>Жаңа өлшем:</b> {comp_mb:.2f} MB\n"
        f"🔥 <b>Үнемделді:</b> {saved_percent:.1f}%\n"
        f"⏱ <b>Уақыт:</b> {duration} секунд"
    )
    
    # Send compressed video back to user
    await bot.send_video(
        chat_id=call.from_user.id,
        video=types.FSInputFile(output_path),
        caption=caption_text,
        parse_mode="HTML"
    )
    
    await status_msg.delete()
    await state.clear()
    
    # Clean up temp files
    if os.path.exists(input_path):
        os.remove(input_path)
    if os.path.exists(output_path):
        os.remove(output_path)

@dp.message()
async def process_fallback_message(message: types.Message):
    """Answer fallback messages."""
    await message.answer("Menga видео файл жіберіңіз, мен оны сығымдап беремін!")

# ==============================================================================
# MAIN ASYNC POLLING INITIALIZER
# ==============================================================================
async def main():
    print("🤖 Video Compressor Bot is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
