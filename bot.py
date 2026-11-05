import os
import secrets
import telebot
from flask import Flask, request, abort

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
WEBHOOK_URL = os.environ.get('WEBHOOK_URL')
WEBHOOK_SECRET_TOKEN = secrets.token_hex(32)

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False)
app = Flask(__name__)

# ดักจับทุกข้อความและรูปภาพ เพื่อโชว์เลขไอดีห้อง
@bot.message_handler(content_types=['photo', 'text', 'document'])
def show_thread_id(message):
    current_thread = getattr(message, 'message_thread_id', None)
    chat_name = message.chat.title or "แชทกลุ่ม"
    
    # พิมพ์ออกที่ Logs ของ Render
    print(f"📌 [FOUND ID] Group: {chat_name} | Thread ID = {current_thread}")
    
    # ส่งข้อความเด้งตอบกลับในห้องแชท Telegram ทันที เพื่อให้เห็นจะๆ
    bot.reply_to(message, f"📌 Thread ID ของห้องนี้คือ: `{current_thread}`", parse_mode="Markdown")

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
    return "ID Detector Bot is running!"

if __name__ == "__main__":
    if TELEGRAM_BOT_TOKEN and WEBHOOK_URL:
        bot.remove_webhook()
        bot.set_webhook(
            url=WEBHOOK_URL, 
            secret_token=WEBHOOK_SECRET_TOKEN,
            drop_pending_updates=True
        )
    
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
