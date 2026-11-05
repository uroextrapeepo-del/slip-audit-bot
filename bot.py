import os
import secrets
import time
import re
from datetime import datetime
import telebot
import requests
import pandas as pd
from flask import Flask, request, abort

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
EASYSLIP_ACCESS_TOKEN = os.environ.get('EASYSLIP_ACCESS_TOKEN')
WEBHOOK_URL = os.environ.get('WEBHOOK_URL')
GOOGLE_SHEET_ID = os.environ.get('GOOGLE_SHEET_ID')

WEBHOOK_SECRET_TOKEN = secrets.token_hex(32)

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False)
app = Flask(__name__)

used_slips_cache = set()
latest_photos_cache = {}

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

# ฟังก์ชันดึง CSV มาตรฐานจาก Google Sheet
def fetch_google_sheet_data():
    if not GOOGLE_SHEET_ID:
        return None
    try:
        # ใช้ลิงก์ export format=csv โดยตรงเพื่อดึงข้อมูลหน้าแรกของไฟล์
        url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/export?format=csv&t={time.time()}"
        df = pd.read_csv(url)
        print(f"DEBUG: Successfully fetched CSV, total rows: {len(df)}")
        return df
    except Exception as e:
        print(f"Error fetching sheet: {e}")
        return None

def match_sheet_row(df, slip_amount, slip_time_hm):
    if df is None or df.empty:
        return None, "ไม่สามารถดึงข้อมูลจาก Google Sheet ได้"
    
    try:
        clean_slip_amount = round(float(slip_amount), 2)
    except Exception:
        clean_slip_amount = 0.0

    checked_rows_preview = []

    for idx, row in df.iterrows():
        try:
            # คอลัมน์ I (จำนวนเงิน - Index 8) และ คอลัมน์ B (เวลา - Index 1)
            raw_amount_str = str(row.iloc[8]).replace(',', '').strip() if len(row) > 8 else "0"
            col_b_text = str(row.iloc[1]) if len(row) > 1 else ""
            
            clean_amount_val = re.sub(r'[^\d.]', '', raw_amount_str)
            sheet_amount = round(float(clean_amount_val), 2) if clean_amount_val else 0.0
            
            all_times = re.findall(r'(\d{2}:\d{2})', col_b_text)
            
            if len(checked_rows_preview) < 3:
                checked_rows_preview.append(f"แถว {idx+2}: ยอด {sheet_amount} | เวลา {all_times}")

            amount_matched = abs(sheet_amount - clean_slip_amount) <= 0.01
            time_matched = slip_time_hm in all_times

            if amount_matched and time_matched:
                row_number = idx + 2 
                matched_data = {
                    "row_num": row_number,
                    "user_id": str(row.iloc[2]) if len(row) > 2 else "-",           # คอลัมน์ C: ไอดี
                    "username": str(row.iloc[3]) if len(row) > 3 else "-",          # คอลัมน์ D: ชื่อสมาชิก
                    "acc_name": str(row.iloc[4]) if len(row) > 4 else "-",          # คอลัมน์ E: ชื่อบัญชีในระบบ
                    "amount": raw_amount_str,                                        # คอลัมน์ I: จำนวนเงิน
                    "type": str(row.iloc[7]) if len(row) > 7 else "-",              # คอลัมน์ H: ประเภท
                    "status": str(row.iloc[9]) if len(row) > 9 else "-",            # คอลัมน์ J: สถานะ
                    "creator": str(row.iloc[13]) if len(row) > 13 else "-",         # คอลัมน์ N: ผู้สร้างรายการ
                    "approver": str(row.iloc[14]) if len(row) > 14 else "-"         # คอลัมน์ O: ผู้ตรวจสอบ
                }
                return matched_data, "ปกติ"
        except Exception:
            continue
            
    return None, f"ไม่พบ (ตัวอย่างข้อมูลที่อ่าน: {', '.join(checked_rows_preview)})"

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

@bot.message_handler(content_types=['photo'])
def handle_incoming_photo(message):
    chat_id = message.chat.id
    file_id = message.photo[-1].file_id
    latest_photos_cache[chat_id] = file_id
    bot.reply_to(message, "📥 บันทึกรูปสลิปเรียบร้อย! พิมพ์ /ตรวจ เพื่อเช็กข้อมูลได้เลยครับ")

@bot.message_handler(func=lambda message: message.text and message.text.strip().startswith('/ตรวจ'))
def handle_check_command(message):
    chat_id = message.chat.id

    if chat_id not in latest_photos_cache:
        bot.reply_to(message, "⚠️ ยังไม่พบรูปสลิปในแชทนี้ กรุณาส่งรูปสลิปมาก่อน แล้วค่อยพิมพ์ /ตรวจ ครับ")
        return

    file_id = latest_photos_cache[chat_id]
    checking_msg = bot.reply_to(message, "⏳ กำลังตรวจสอบสลิปและค้นหาข้อมูลใน Google Sheet...")
    
    downloaded_file = None
    result = None
    
    try:
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        result = verify_slip_with_easyslip(downloaded_file)

        if result.get("status") == 200 or result.get("status") == "success":
            data = result.get("data", {})
            
            trans_ref = data.get('transRef') or data.get('ref1') or data.get('ref2')
            if trans_ref:
                if trans_ref in used_slips_cache:
                    reply_text = "❌ *ตรวจสอบสลิปไม่สำเร็จ*\n\n• *สถานะสลิป:* แจ้งเตือน! สลิปนี้เคยถูกใช้งานไปแล้ว (สลิปซ้ำ)"
                    bot.delete_message(message.chat.id, checking_msg.message_id)
                    bot.reply_to(message, reply_text, parse_mode="Markdown")
                    return
                else:
                    if len(used_slips_cache) > 1000:
                        used_slips_cache.clear()
                    used_slips_cache.add(trans_ref)

            amount = data.get('amount', {}).get('amount', data.get('amount', 0))
            sender = data.get('sender', {})
            receiver = data.get('receiver', {})
            
            sender_name = sender.get('account', {}).get('name', {}).get('th', sender.get('name', 'ไม่ระบุ')) if isinstance(sender.get('account', {}).get('name'), dict) else sender.get('name', 'ไม่ระบุ')
            receiver_name = receiver.get('account', {}).get('name', {}).get('th', receiver.get('name', 'ไม่ระบุ')) if isinstance(receiver.get('account', {}).get('name'), dict) else receiver.get('name', 'ไม่ระบุ')
            
            sender_acc = sender.get('account', {}).get('bank', {}).get('account', sender.get('account', {}).get('number', 'ไม่ระบุ'))
            receiver_acc = receiver.get('account', {}).get('bank', {}).get('account', receiver.get('account', {}).get('number', 'ไม่ระบุ'))
            
            sender_bank_raw = sender.get('bank', 'ไม่ระบุ')
            sender_bank = sender_bank_raw.get('name', 'ไม่ระบุ') if isinstance(sender_bank_raw, dict) else sender_bank_raw

            receiver_bank_raw = receiver.get('bank', 'ไม่ระบุ')
            receiver_bank = receiver_bank_raw.get('name', 'ไม่ระบุ') if isinstance(receiver_bank_raw, dict) else receiver_bank_raw
            
            raw_date = data.get('date', data.get('transDate', 'ไม่ระบุ'))
            raw_time = data.get('transTime', '')
            formatted_datetime = format_thai_datetime(raw_date, raw_time)

            slip_time_hm = raw_time[:5] if len(raw_time) >= 5 else ""

            df_sheet = fetch_google_sheet_data()
            sheet_match, audit_status = match_sheet_row(df_sheet, amount, slip_time_hm)

            if audit_status == "ปกติ" and sheet_match:
                status_icon = "🟢 *ปกติ*"
                sheet_info_text = (
                    f"📌 *ข้อมูลที่พบในตารางระบบ (แถวที่ {sheet_match['row_num']}):*\n"
                    f"• *ไอดีสมาชิก:* {sheet_match['user_id']}\n"
                    f"• *ชื่อสมาชิก:* {sheet_match['username']}\n"
                    f"• *ชื่อบัญชีในระบบ:* {sheet_match['acc_name']}\n"
                    f"• *จำนวนเงิน:* {sheet_match['amount']} บาท\n"
                    f"• *สถานะในระบบ:* {sheet_match['status']}\n"
                    f"• *ผู้สร้างรายการ:* {sheet_match['creator']}\n"
                    f"• *ผู้ตรวจสอบ (ช่อง O):* {sheet_match['approver']}"
                )
            else:
                status_icon = "🔴 *กรุณาตรวจสอบ*"
                sheet_info_text = f"⚠️ *ไม่พบรายการที่ตรงกัน* ({audit_status})"

            reply_text = (
                f"✅ *ผลการตรวจสอบสลิป*\n\n"
                f"1. *สถานะสลิป:* สลิปจริง (ตรวจสอบผ่าน EasySlip)\n"
                f"2. *วันที่และเวลา:* {formatted_datetime}\n"
                f"3. *ชื่อบัญชีต้นทาง:* {sender_name}\n"
                f"4. *เลขบัญชีต้นทาง:* {sender_acc}\n"
                f"5. *ชื่อธนาคารต้นทาง:* {sender_bank}\n"
                f"6. *ชื่อบัญชีปลายทาง:* {receiver_name}\n"
                f"7. *เลขบัญชีปลายทาง:* {receiver_acc}\n"
                f"8. *ชื่อธนาคารปลายทาง:* {receiver_bank}\n"
                f"9. *จำนวนเงิน:* {amount} บาท\n\n"
                f"📊 *ผลการเปรียบเทียบตารางหลังบ้าน:* {status_icon}\n"
                f"{sheet_info_text}"
            )
        else:
            error_message = result.get("message", "อ่านสลิปไม่ได้หรือข้อมูลไม่ถูกต้อง")
            reply_text = f"❌ *ตรวจสอบสลิปไม่สำเร็จ*\n\n• *สถานะสลิป:* สลิปลอม / หรือ {error_message}"
    except Exception as e:
        reply_text = f"⚠️ เกิดข้อผิดพลาดภายในระบบ: {str(e)}"
    
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
    return "Secure Slip Audit Bot (Direct Export CSV) is running!"

if __name__ == "__main__":
    if TELEGRAM_BOT_TOKEN and WEBHOOK_URL:
        bot.remove_webhook()
        bot.set_webhook(
            url=WEBHOOK_URL, 
            secret_token=WEBHOOK_SECRET_TOKEN,
            drop_pending_updates=True
        )
    
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
