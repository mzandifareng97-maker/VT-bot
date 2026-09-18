import os
import json
import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from gradio_client import Client, handle_file

# --- Settings read from environment variables (set these on Render) ---
TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", "10000"))
# Render automatically provides RENDER_EXTERNAL_URL for Web Services.
WEBHOOK_BASE_URL = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("WEBHOOK_URL")

DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "30"))
COUNTER_FILE = "/tmp/vton_counter.json"

PHOTO1, PHOTO2 = range(2)


def _load_counter():
    today = datetime.date.today().isoformat()
    if os.path.exists(COUNTER_FILE):
        try:
            with open(COUNTER_FILE, "r") as f:
                data = json.load(f)
            if data.get("date") == today:
                return data
        except Exception:
            pass
    return {"date": today, "count": 0}


def _save_counter(data):
    with open(COUNTER_FILE, "w") as f:
        json.dump(data, f)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    counter = _load_counter()
    if counter["count"] >= DAILY_LIMIT:
        await update.message.reply_text("ظرفیت امروز پر شد، فردا دوباره امتحان کنید.")
        return ConversationHandler.END

    await update.message.reply_text("سلام! لطفا عکس مدل را ارسال کنید.")
    return PHOTO1


async def get_photo1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_file = await update.message.photo[-1].get_file()
    await photo_file.download_to_drive("person.jpg")
    await update.message.reply_text("عکس مدل دریافت شد. حالا عکس لباس را ارسال کنید.")
    return PHOTO2


async def get_photo2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_file = await update.message.photo[-1].get_file()
    await photo_file.download_to_drive("garment.jpg")

    await update.message.reply_text("عکس‌ها دریافت شدند. در حال پردازش پرو مجازی...")

    try:
        client = Client("yisol/IDM-VTON")
        result = client.predict(
            dict={"background": handle_file("person.jpg"), "layers": [], "composite": None},
            garm_img=handle_file("garment.jpg"),
            garment_des="clothing",
            is_checked=True,
            is_checked_crop=False,
            denoise_steps=30,
            seed=42,
            api_name="/tryon"
        )

        result_img_path = result[0] if isinstance(result, (list, tuple)) else result

        with open(result_img_path, 'rb') as photo:
            await update.message.reply_photo(photo=photo, caption="نتیجه پرو مجازی آماده شد!")

        counter = _load_counter()
        counter["count"] += 1
        _save_counter(counter)

    except Exception as e:
        await update.message.reply_text(
            "سرویس هوش مصنوعی الان در دسترس نیست یا خطا داد. لطفاً چند دقیقه دیگر دوباره امتحان کنید."
        )
        print(f"Error: {e}")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("عملیات لغو شد. برای شروع مجدد /start را بفرستید.")
    return ConversationHandler.END


if __name__ == '__main__':
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is not set!")
    if not WEBHOOK_BASE_URL:
        raise RuntimeError("WEBHOOK_URL (or RENDER_EXTERNAL_URL) environment variable is not set!")

    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            PHOTO1: [MessageHandler(filters.PHOTO, get_photo1)],
            PHOTO2: [MessageHandler(filters.PHOTO, get_photo2)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(conv_handler)
    print("Bot is running with webhook...")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_BASE_URL}/{TOKEN}"
    )
