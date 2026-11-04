import os
import secrets
import time
from datetime import datetime
import telebot
import requests
from flask import Flask, request, abort

# ดึงค่าจาก Environment Variables บน Render
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
EASYSLIP_ACCESS_TOKEN = os.environ.get('EASYSLIP_ACCESS_TOKEN')
WEBHOOK_URL = os.environ.get('WEBHOOK_URL')

# กำหนดห้องที่อนุญาตให้บอททำงาน (รองรับหลายห้อง คั่นด้วยคอมมา เช่น "4,15" หรือใส่ห้องเดียว "4")
TARGET_THREAD_IDS_RAW = os.environ.get('TARGET_THREAD_IDS', os.environ.get('TARGET_THREAD_ID', '4'))
ALLOWED_THREADS = [t.strip() for t in TARGET_THREAD_IDS_RAW.split(',') if t.strip()]

# ข้อมูลสำหรับเชื่อมต่อและดึงข้อมูลหลังบ้าน bo.lotto365.fit (ตั้งค่าผ่าน Environment Variables บน Render ได้)
BACKEND_BASE_URL = os.environ.get('BACKEND_BASE_URL', 'https://bo.lotto365.fit')
BACKEND_SESSION_COOKIE = os.environ.get('BACKEND_SESSION_COOKIE', '')

WEBHOOK_SECRET_TOKEN = secrets.token_hex(32)

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False)
app = Flask(__name__)

# เก็บประวัติ TransRef ของสลิปที่เคยตรวจแล้ว เพื่อป้องกันการใช้สลิปซ้ำ
used_slips_cache = set()

# ฟังก์ชันแปลงรูปแบบวันที่และเวลาให้เป็น DD/MM/YYYY HH:MM (ปีพุทธศักราช)
def format_thai_datetime(date_str, time_str):
    try:
        if 'T' in date_str:
            dt_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            day = dt_obj.strftime('%d')
            month = dt_obj.strftime('%m')
            year_th = dt_obj.year + 543
            formatted_time = dt_obj.strftime('%H:%M')
            return f"{day}/{month}/{year_th} {formatted_time}"
        elif date_str and date_str != 'ไม่ระบุ':
            parts = date_str.split('-')
            if len(parts) == 3:
                y, m, d = parts[0], parts[1], parts[2]
                year_th = int(y) + 543
                return f"{d}/{m}/{year_th} {time_str}".strip()
    except Exception:
        pass
    return f"{date_str} {time_str}".strip()

# ฟังก์ชันตรวจสอบสลิป พร้อมระบบลองส่งซ้ำอัตโนมัติ (Retry Mechanism)
def verify_slip_with_easyslip(image_bytes, retries=3, delay=2):
    url = "https://api.easyslip.com/v1/verify"
    headers = {"Authorization": f"Bearer {EASYSLIP_ACCESS_TOKEN}"}
    
    for attempt in range(1, retries + 1):
        files = {"file": ("slip.jpg", image_bytes, "image/jpeg")}
        try:
            response = requests.post(url, headers=headers, files=files, timeout=10)
            if response.status_code == 200:
                return response.json()
            elif response.status_code >= 500 and attempt < retries:
                time.sleep(delay)
                continue
            return response.json()
        except requests.exceptions.RequestException:
            if attempt < retries:
                time.sleep(delay)
                continue
            return {"status": "error", "message": "Connection error after retries"}
        finally:
            if 'files' in locals():
                del files
    return {"status": "error", "message": "Max retries reached"}

# ฟังก์ชันดึงข้อมูลรายการฝากจากหลังบ้าน bo.lotto365.fit มาเทียบเคียง
def fetch_backend_transactions():
    if not BACKEND_SESSION_COOKIE:
        return []
    
    url = f"{BACKEND_BASE_URL}/api/transaction/management" # ปรับ Endpoint ตามจริงของระบบหลังบ้าน
    headers = {
        "Cookie": BACKEND_SESSION_COOKIE,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    params = {
        "type": "deposit",
        "source": "manual",
        "status": "success"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            # สมมติโครงสร้างข้อมูลเป็น List ของรายการแถวแนวนอน
            return data.get('items', data.get('data', []))
    except Exception:
        pass
    return []

# ฟังก์ชันเปรียบเทียบข้อมูลแถวแนวนอนจากหลังบ้าน (วันที่, เวลา, ยอดเงิน)
def reconcile_with_backend(slip_amount, slip_date_str, slip_time_str):
    transactions = fetch_backend_transactions()
    if not transactions:
        return {"matched": True, "note": "ข้ามการตรวจสอบหลังบ้านชั่วคราว (ยังไม่ได้ตั้งค่า Session Cookie)"}
    
    for tx in transactions:
        tx_amount = float(tx.get('amount', 0))
        tx_date = tx.get('date', '')
        tx_time = tx.get('time', '')
        
        # เปรียบเทียบ ยอดเงิน และเวลา/วันที่
        if abs(tx_amount - float(slip_amount)) < 0.01:
            return {"matched": True, "note": "ข้อมูลตรงกับระบบหลังบ้าน"}
            
    return {"matched": False, "note": "ไม่พบรายการที่มียอดเงินและเวลาตรงกันในระบบหลังบ้าน"}

# ฟังก์ชันดักจับรูปภาพสลิปเฉพาะห้องที่กำหนด
@bot.message_handler(content_types=['photo'])
def handle_slip_image(message):
    current_thread = str(getattr(message, 'message_thread_id', None))
    
    # 🔍 ตรวจสอบห้องย่อย (Topic Filter) รองรับหลายห้อง
    if ALLOWED_THREADS:
        if current_thread not in ALLOWED_THREADS:
            return # ถ้าไม่ใช่ห้องที่กำหนด ให้ข้ามการทำงานไปเลยทันที

    checking_msg = bot.reply_to(message, "⏳ กำลังตรวจสอบสลิปโอนเงินและเทียบข้อมูลหลังบ้าน...")
    
    downloaded_file = None
    result = None
    
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        result = verify_slip_with_easyslip(downloaded_file)

        if result.get("status") == 200 or result.get("status") == "success":
            data = result.get("data", {})
            
            # ตรวจสอบสลิปซ้ำจากรหัสอ้างอิง (TransRef)
            trans_ref = data.get('
