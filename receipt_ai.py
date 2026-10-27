import os
import io
import re
import json
import base64
import hashlib
import logging
from datetime import datetime
import aiohttp

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

def compute_image_hash(image_bytes: bytes) -> str:
    """Tasvir baytlarining SHA-256 xeshini hisoblash."""
    return hashlib.sha256(image_bytes).hexdigest()

async def call_gemini_vision(image_bytes: bytes, prompt: str) -> str:
    """Gemini Flash Vision API ga aiohttp orqali to'g'ridan-to'g'ri so'rov yuborish."""
    b64_str = base64.b64encode(image_bytes).decode('utf-8')
    
    models = [GEMINI_MODEL, "gemini-3.5-flash", "gemini-3.1-flash-lite"]
    seen = set()
    unique_models = []
    for m in models:
        if m and m not in seen:
            seen.add(m)
            unique_models.append(m)

    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": b64_str}},
                {"text": prompt}
            ]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1024
        }
    }

    last_error = None
    for model_name in unique_models:
        clean_model = model_name.replace("models/", "")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={GEMINI_API_KEY}"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            text_parts = [p.get("text", "") for p in parts if "text" in p]
                            return "".join(text_parts).strip()
                    else:
                        err_text = await resp.text()
                        logging.warning(f"Gemini API ({clean_model}) returned {resp.status}: {err_text}")
                        last_error = f"HTTP {resp.status}: {err_text}"
        except Exception as e:
            logging.error(f"Error calling Gemini model {clean_model}: {e}")
            last_error = str(e)
            
    raise RuntimeError(f"Gemini API xatoligi: {last_error}")

async def verify_receipt_with_ai(image_bytes: bytes, expected_amount: float) -> dict:
    """
    To'lov chekini Gemini Vision orqali o'ta yuqori darajada tahlil qilish.
    Natija:
    {
        "status": "approved" | "older_24h" | "rejected",
        "reason": str,
        "detected_amount": float,
        "is_older_than_24h": bool,
        "is_fake": bool,
        "bank_or_service": str
    }
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    prompt = f"""
Siz professional bank va to'lov tizimlari cheklarini tekshiruvchi avtonom ekspertizasisiz.
Vazifangiz - taqdim etilgan rasmning haqiqiy to'lov cheki ekanligini, soxtalik/montaj alomatlari yo'qligini, summasini va vaqtini o'ta yuqori aniqlikda tekshirish.

Hozirgi sana va vaqt (server vaqti): {now_str}
Kutilayotgan to'lov summasi: {int(expected_amount)} (so'm, UZS, tenge, KZT, rubl, va h.k.)

TEKSHIRUV TALABLARI VA QOIDALARI:
1. Haqiqiylik va rasm turi:
   - Ushbu rasm haqiqiy bank/to'lov tizimi (Kaspi, Halyk, Click, Payme, Uzum, Apelsin, Sberbank, Oson, bank mobil ilovasi, kassa/terminal, OFD fiskal cheki) chekimi?
   - Agar to'lov cheki bo'lmasa (tabiat, mem, mashina, shaxsiy rasm, boshqa hujjat) -> status="rejected", reason="Yuborilgan rasm to'lov cheki emas."

2. Soxtalik, montaj va feyk tekshiruvi:
   - Shriftlar o'zgarganmi (Photoshop, Pixel manipulyatsiyasi, Telegram feyk-chek botlari generatori)?
   - Raqamlar yoki matn atrofida xiralashish, noaniq piksellar, boshqa shrift turi bormi?
   - Agar montaj yoki soxtalik alomati bo'lsa -> is_fake=true, status="rejected", reason="To'lov chekida montaj yoki soxtalashtirish alomatlari aniqlandi."

3. Vaqt chegarasi (24 soatlik cheklov):
   - Chekdagi to'lov amalga oshirilgan sana va vaqtni aniqlang.
   - Agar to'lov hozirgi vaqtdan 24 soatdan oldin o'tkazilgan bo'lsa -> is_older_than_24h=true, status="older_24h", reason="To'lov cheki 24 soatdan oshib ketgan (eski chek)."

4. Summa mosligi:
   - Chekdagi to'lov/o'tkazma summasini aniqlang.
   - Agar to'langan summa kutilayotgan summadan kam bo'lsa -> status="rejected", reason="Chekdagi to'lov summasi buyurtma summasiga yetmaydi."

5. Agar chek haqiqiy, montajsiz, summasi to'liq va 24 soat ichida bo'lsa -> status="approved", reason="Chek to'liq tasdiqlandi."

FAQAT QUYIDAGI JSON FORMATIDA JAVOB BERING, HECH QANDAY ORTIQCHA MATN QO'SHMANG:
{{
  "is_receipt": true,
  "bank_or_service": "Kaspi / Click / Payme / ...",
  "detected_amount": {int(expected_amount)},
  "detected_datetime": "YYYY-MM-DD HH:MM",
  "is_older_than_24h": false,
  "is_fake": false,
  "status": "approved",
  "reason": "Qisqa tushuntirish"
}}
"""

    try:
        raw_res = await call_gemini_vision(image_bytes, prompt)
        clean_json = raw_res.strip()
        clean_json = re.sub(r'^```json\s*', '', clean_json, flags=re.MULTILINE)
        clean_json = re.sub(r'^```\s*', '', clean_json, flags=re.MULTILINE)
        clean_json = re.sub(r'```$', '', clean_json, flags=re.MULTILINE).strip()
        
        parsed = json.loads(clean_json)
        
        status = parsed.get("status", "rejected").lower()
        if status not in ["approved", "older_24h", "rejected"]:
            status = "rejected"
            
        return {
            "status": status,
            "reason": parsed.get("reason", "Chek tekshiruvdan o'tmadi"),
            "detected_amount": parsed.get("detected_amount", 0),
            "is_older_than_24h": bool(parsed.get("is_older_than_24h", False)),
            "is_fake": bool(parsed.get("is_fake", False)),
            "bank_or_service": parsed.get("bank_or_service", "Noma'lum")
        }
    except Exception as e:
        logging.error(f"Receipt AI parsing failed: {e}")
        return {
            "status": "rejected",
            "reason": f"AI tekshiruvida xatolik yuz berdi: {e}",
            "detected_amount": 0,
            "is_older_than_24h": False,
            "is_fake": False,
            "bank_or_service": "Xatolik"
        }
