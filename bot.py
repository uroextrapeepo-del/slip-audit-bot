import os
import telebot
import requests
from flask import Flask, request

# ดึงค่า Token จาก Environment Variables บน Render (หรือใส่สำรองไว้ได้)
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', "9020738068:AAE9Cq8sbzfkNr3XsXocVWEjL5howqVHBCE")
EASYSLIP_ACCESS_TOKEN = os.environ.get('EASYSLIP_ACCESS_TOKEN', "xxxx")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
app = Flask(__name__)

# EasySlip API
def verify_slip_with_easyslip(image_bytes):
    url = "https://api.easyslip.com/v1/verify"
    headers = {"Authorization": f"Bearer {EASYSLIP_ACCESS_TOKEN}"}
    files = {"file": ("slip.jpg", image_bytes, "image/jpeg")}
    try:
        response = requests.post(url, headers=headers, files=files)
        return response.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ดักจับรูปภาพสลิป
@bot.message_handler(content_types=['photo'])
def handle_slip_image(message):
    checking_msg = bot.reply_to(message, "⏳ กำลังตรวจสอบสลิปโอนเงิน...")
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        result = verify_slip_with_easyslip(downloaded_file)

        if result.get("status") == 200:
            data = result.get("data", {})
            sender_name = data.get('sender', {}).get('account', {}).get('name', {}).get('th', 'ไม่ระบุ')
            receiver_name = data.get('receiver', {}).get('account', {}).get('name', {}).get('th', 'ไม่ระบุ')
            amount = data.get('amount', {}).get('amount', 0)
            
            reply_text = (
                f"✅ *ตรวจสอบสลิปสำเร็จ*\n"
                f"👤 ผู้โอน: {sender_name}\n"
                f"💰 ยอดเงิน: {amount} บาท\n"
                f"🏦 เข้าบัญชี: {receiver_name}"
            )
        else:
            reply_text = f"❌ *สลิปไม่ถูกต้อง:* {result.get('message', 'อ่านสลิปไม่ได้')}"
    except Exception as e:
        reply_text = f"⚠️ เกิดข้อผิดพลาด: {str(e)}"

    bot.delete_message(message.chat.id, checking_msg.message_id)
    bot.reply_to(message, reply_text, parse_mode="Markdown")

# Web Server สำหรับให้ Render Ping (กันเว็บดับ)
@app.route('/')
def index():
    return "Slip Audit Bot is running!"

if __name__ == "__main__":
    import threading
    threading.Thread(target=bot.infinity_polling, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))