import os
import secrets
import telebot
import requests
from flask import Flask, request, abort

# ดึงค่าจาก Environment Variables บน Render
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
EASYSLIP_ACCESS_TOKEN = os.environ.get('EASYSLIP_ACCESS_TOKEN')
WEBHOOK_URL = os.environ.get('WEBHOOK_URL')

WEBHOOK_SECRET_TOKEN = secrets.token_hex(32)

# กำหนด threaded=False เพื่อความเสถียรบน Webhook
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False)
app = Flask(__name__)

# ฟังก์ชันตรวจสอบสลิปกับ EasySlip API
def verify_slip_with_easyslip(image_bytes):
    url = "https://api.easyslip.com/v1/verify" # ใช้ Endpoint มาตรฐานของ EasySlip
    headers = {"Authorization": f"Bearer {EASYSLIP_ACCESS_TOKEN}"}
    files = {"file": ("slip.jpg", image_bytes, "image/jpeg")}
    try:
        response = requests.post(url, headers=headers, files=files, timeout=10)
        return response.json()
    except Exception:
        return {"status": "error", "message": "Connection error"}
    finally:
        # เคลียร์หน่วยความจำหลังใช้งานเสร็จทันที
        del image_bytes
        if 'files' in locals(): del files

# ฟังก์ชันดักจับรูปภาพสลิปที่ส่งเข้ามาในแชท/กลุ่ม
@bot.message_handler(content_types=['photo'])
def handle_slip_image(message):
    checking_msg = bot.reply_to(message, "⏳ กำลังตรวจสอบสลิปโอนเงิน...")
    
    downloaded_file = None
    result = None
    
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        result = verify_slip_with_easyslip(downloaded_file)

        # EasySlip API ส่งสถานะสำเร็จเป็น 200 หรือ "success"
        if result.get("status") == 200 or result.get("status") == "success":
            data = result.get("data", {})
            sender_name = data.get('sender', {}).get('account', {}).get('name', {}).get('th', data.get('sender', {}).get('name', 'ไม่ระบุ'))
            receiver_name = data.get('receiver', {}).get('account', {}).get('name', {}).get('th', data.get('receiver', {}).get('name', 'ไม่ระบุ'))
            amount = data.get('amount', {}).get('amount', data.get('amount', 0))
            
            reply_text = (
                f"✅ *ตรวจสอบสลิปสำเร็จ*\n\n"
                f"• *ยอดเงินที่โอน:* {amount} บาท\n"
                f"• *ผู้โอน:* {sender_name}\n"
                f"• *ผู้รับโอน:* {receiver_name}\n"
                f"• *สถานะ:* สลิปแท้ถูกต้องตามระบบ"
            )
        else:
            error_message = result.get("message", "อ่านสลิปไม่ได้หรือข้อมูลไม่ถูกต้อง")
            reply_text = (
                f"❌ *ตรวจสอบสลิปไม่สำเร็จ*\n\n"
                f"• *เหตุผล:* {error_message}"
            )
    except Exception:
        reply_text = "⚠️ เกิดข้อผิดพลาดภายในระบบ"
    
    try:
        bot.delete_message(message.chat.id, checking_msg.message_id)
    except Exception:
        pass
        
    bot.reply_to(message, reply_text, parse_mode="Markdown")
    
    if downloaded_file:
        del downloaded_file
    if result:
        del result

# ระบบ Webhook สำหรับรับข้อมูลจาก Telegram
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

# หน้าแรกสำหรับให้ Render คอย Ping เช็กสถานะเว็บ
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
