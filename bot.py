import os
import secrets
import time
import telebot
import requests
from flask import Flask, request, abort

# ดึงค่าจาก Environment Variables บน Render
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
EASYSLIP_ACCESS_TOKEN = os.environ.get('EASYSLIP_ACCESS_TOKEN')
WEBHOOK_URL = os.environ.get('WEBHOOK_URL')

WEBHOOK_SECRET_TOKEN = secrets.token_hex(32)

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False)
app = Flask(__name__)

# เก็บประวัติ TransRef ของสลิปที่เคยตรวจแล้ว เพื่อป้องกันการใช้สลิปซ้ำ (Duplicate Check)
used_slips_cache = set()

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

# ฟังก์ชันดักจับรูปภาพสลิปที่ส่งเข้ามาใน Telegram
@bot.message_handler(content_types=['photo'])
def handle_slip_image(message):
    checking_msg = bot.reply_to(message, "⏳ กำลังตรวจสอบสลิปโอนเงิน...")
    
    downloaded_file = None
    result = None
    
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # ตรวจสอบกับ EasySlip API พร้อม Retry
        result = verify_slip_with_easyslip(downloaded_file)

        if result.get("status") == 200 or result.get("status") == "success":
            data = result.get("data", {})
            
            # ตรวจสอบสลิปซ้ำจากรหัสอ้างอิง (TransRef)
            trans_ref = data.get('transRef') or data.get('ref1') or data.get('ref2')
            if trans_ref:
                if trans_ref in used_slips_cache:
                    reply_text = (
                        f"❌ *ตรวจสอบสลิปไม่สำเร็จ*\n\n"
                        f"• *สถานะสลิป:* แจ้งเตือน! สลิปนี้เคยถูกใช้งานไปแล้ว (สลิปซ้ำ)"
                    )
                    bot.delete_message(message.chat.id, checking_msg.message_id)
                    bot.reply_to(message, reply_text, parse_mode="Markdown")
                    return
                else:
                    # บันทึกรหัสอ้างอิงลงในแคช (จำกัดขนาดไม่ให้เมมเต็มเกินไป)
                    if len(used_slips_cache) > 1000:
                        used_slips_cache.clear()
                    used_slips_cache.add(trans_ref)

            # ดึงข้อมูลรายละเอียด
            amount = data.get('amount', {}).get('amount', data.get('amount', 0))
            sender = data.get('sender', {})
            receiver = data.get('receiver', {})
            
            sender_name = sender.get('account', {}).get('name', {}).get('th', sender.get('name', 'ไม่ระบุ'))
            receiver_name = receiver.get('account', {}).get('name', {}).get('th', receiver.get('name', 'ไม่ระบุ'))
            
            # ดึงเลขบัญชี
            sender_acc = sender.get('account', {}).get('bank', {}).get('account', sender.get('account', {}).get('number', 'ไม่ระบุ'))
            receiver_acc = receiver.get('account', {}).get('bank', {}).get('account', receiver.get('account', {}).get('number', 'ไม่ระบุ'))
            
            # วัน-เวลาที่โอน
            trans_date = data.get('date', data.get('transDate', 'ไม่ระบุ'))
            trans_time = data.get('transTime', '')

            # จัดเรียงลำดับใหม่ตามที่เจ้านายต้องการ
            reply_text = (
                f"✅ *ตรวจสอบสลิปสำเร็จ*\n\n"
                f"1. *สถานะสลิป:* สลิปสแกนติดผ่าน (สลิปจริง)\n"
                f"2. *เลขบัญชีต้นทาง:* {sender_acc}\n"
                f"3. *เลขบัญชีปลายทาง:* {receiver_acc}\n"
                f"4. *วันที่และเวลา:* {trans_date} {trans_time}\n"
                f"5. *ชื่อ-นามสกุล:* โอนจาก {sender_name} ถึง {receiver_name}\n"
                f"6. *จำนวนเงิน:* {amount} บาท"
            )
        else:
            error_message = result.get("message", "อ่านสลิปไม่ได้หรือข้อมูลไม่ถูกต้อง")
            reply_text = (
                f"❌ *ตรวจสอบสลิปไม่สำเร็จ*\n\n"
                f"• *สถานะสลิป:* สลิปปลอม / หรือ {error_message}"
            )
    except Exception:
        reply_text = "⚠️ เกิดข้อผิดพลาดภายในระบบ"
    
    try:
        bot.delete_message(message.chat.id, checking_msg.message_id)
    except Exception:
        pass
        
    bot.reply_to(message, reply_text, parse_mode="Markdown")
    
    # เคลียร์แรม
    if downloaded_file:
        del downloaded_file
    if result:
        del result

# Webhook Endpoint
@app.route('/webhook', methods=['POST'])
def webhook():
    tg_secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if tg_secret != WEBHOOK_SECRET_TOKEN:
        abort(403)
        
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        abort(400)

@app.route('/')
def index():
    return "Secure Slip Audit Bot is running!"

if __name__ == "__main__":
    if TELEGRAM_BOT_TOKEN and WEBHOOK_URL:
        bot.remove_webhook()
        bot.set_webhook(
            url=WEBHOOK_URL, 
            secret_token=WEBHOOK_SECRET_TOKEN,
            drop_pending_updates=True
        )
    
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
