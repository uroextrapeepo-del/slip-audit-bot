import os
import secrets
import telebot
import requests
from flask import Flask, request, abort

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
EASYSLIP_ACCESS_TOKEN = os.environ.get('EASYSLIP_ACCESS_TOKEN')
WEBHOOK_URL = os.environ.get('WEBHOOK_URL')

WEBHOOK_SECRET_TOKEN = secrets.token_hex(32)

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False)
app = Flask(__name__)

def verify_slip_with_easyslip(image_bytes):
    url = "https://api.easyslip.com/v1/verify"
    headers = {"Authorization": f"Bearer {EASYSLIP_ACCESS_TOKEN}"}
    files = {"file": ("slip.jpg", image_bytes, "image/jpeg")}
    try:
        response = requests.post(url, headers=headers, files=files, timeout=10)
        return response.json()
    except Exception:
        return {"status": "error", "message": "Connection error"}
    finally:
        del image_bytes
        if 'files' in locals(): del files

@bot.message_handler(content_types=['photo'])
def handle_slip_image(message):
    checking_msg = bot.reply_to(message, "⏳ กำลังตรวจสอบสลิปโอนเงิน...")
    
    downloaded_file = None
    result = None
    
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        result = verify_slip_with_easyslip(downloaded_file)

        if result.get("status") == 200 or result.get("status") == "success":
            data = result.get("data", {})
            
            # ดึงข้อมูลรายละเอียดครบถ้วน
            amount = data.get('amount', {}).get('amount', data.get('amount', 0))
            sender = data.get('sender', {})
            receiver = data.get('receiver', {})
            
            sender_name = sender.get('account', {}).get('name', {}).get('th', sender.get('name', 'ไม่ระบุ'))
            receiver_name = receiver.get('account', {}).get('name', {}).get('th', receiver.get('name', 'ไม่ระบุ'))
            
            sender_bank = sender.get('bank', 'ไม่ระบุ')
            receiver_bank = receiver.get('bank', 'ไม่ระบุ')
            
            trans_date = data.get('transDate', 'ไม่ระบุ')
            trans_time = data.get('transTime', '')

            reply_text = (
                f"✅ *ตรวจสอบสลิปสำเร็จ*\n\n"
                f"• *ยอดเงินที่โอน (Amount):* {amount} บาท\n"
                f"• *ชื่อผู้โอน (Sender Name):* {sender_name}\n"
                f"• *ชื่อผู้รับโอน (Receiver Name):* {receiver_name}\n"
                f"• *ธนาคารต้นทาง (Sending Bank):* {sender_bank}\n"
                f"• *ธนาคารปลายทาง (Receiving Bank):* {receiver_bank}\n"
                f"• *วัน-เวลาที่โอน (Timestamp):* {trans_date} {trans_time}\n"
                f"• *สถานะสลิป:* สลิปจริง"
            )
        else:
            error_message = result.get("message", "อ่านสลิปไม่ได้")
            reply_text = (
                f"❌ *ตรวจสอบสลิปไม่สำเร็จ*\n\n"
                f"• *สถานะสลิป:* สลิปปลอม / สลิปซ้ำ หรือ {error_message}"
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
