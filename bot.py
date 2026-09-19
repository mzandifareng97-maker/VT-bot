import os
import json
import asyncio
import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from gradio_client import Client, handle_file

# --- Settings read from environment variables (set these on Render) ---
TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", "10000"))
WEBHOOK_BASE_URL = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("WEBHOOK_URL")

DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "30"))
COUNTER_FILE = "/tmp/vton_counter.json"

# How long (in seconds) uploaded/result photos stay on the server before being deleted.
FILE_RETENTION_SECONDS = 30

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


async def _delete_files_later(paths, delay=FILE_RETENTION_SECONDS):
    """Wait `delay` seconds, then delete each file in `paths` if it still exists."""
    await asyncio.sleep(delay)
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception as e:
            print(f"Cleanup error for {p}: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    counter = _load_counter()
    if counter["count"] >= DAILY_LIMIT:
        await update.message.reply_text("ظرفیت امروز پر شد، فردا دوباره امتحان کنید.")
        return ConversationHandler.END

    await update.message.reply_text("سلام! 👋 به اتاق پرو مجازی زاروس خوش اومدید. لطفاً اول عکس خودتون رو ارسال کنید. 📸")
    return PHOTO1


async def get_photo1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Unique filename per user so two people using the bot at the same time
    # never overwrite each other's photos.
    person_path = f"/tmp/person_{user_id}.jpg"

    photo_file = await update.message.photo[-1].get_file()
    await photo_file.download_to_drive(person_path)

    # Remember this user's file path for the next step.
    context.user_data["person_path"] = person_path

    await update.message.reply_text("عکستون دریافت شد ✅ حالا عکس لباس مدنظرتون رو ارسال کنید تا جادوی زاروس رو ببینید. 🪄👕")
    return PHOTO2


async def get_photo2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    garment_path = f"/tmp/garment_{user_id}.jpg"
    person_path = context.user_data.get("person_path")

    if not person_path or not os.path.exists(person_path):
        await update.message.reply_text("مشکلی پیش اومد، لطفاً دوباره با /start شروع کنید.")
        return ConversationHandler.END

    photo_file = await update.message.photo[-1].get_file()
    await photo_file.download_to_drive(garment_path)

    await update.message.reply_text("عکس‌ها دریافت شدن، شما در حال پوشیدن لباس هستید... ⏳✨")

    result_img_path = None
    try:
        client = Client("yisol/IDM-VTON")
        result = client.predict(
            dict={"background": handle_file(person_path), "layers": [], "composite": None},
            garm_img=handle_file(garment_path),
            garment_des="clothing",
            is_checked=True,
            is_checked_crop=False,
            denoise_steps=30,
            seed=42,
            api_name="/tryon"
        )

        result_img_path = result[0] if isinstance(result, (list, tuple)) else result

        with open(result_img_path, 'rb') as photo:
            await update.message.reply_photo(photo=photo, caption="از اتاق پرو اومدید بیرون! می\u200cتونید خودتون رو توی آینه\u200cی زاروس ببینید. 🪞😍😍😍")

        await update.message.reply_text(
            f"تصاویر شما تا {FILE_RETENTION_SECONDS} ثانیه دیگر از سرور حذف می‌شوند."
        )

        counter = _load_counter()
        counter["count"] += 1
        _save_counter(counter)

    except Exception as e:
        await update.message.reply_text(
            "اتاق‌های پرو همه پر هستن، لطفا چند دقیقه صبر کنید.\nممنون از شکیبایی شما. 🙏"
        )
        print(f"Error: {e}")

    finally:
        # Whether it succeeded or failed, clean up this user's files after the delay.
        files_to_clean = [person_path, garment_path]
        if result_img_path:
            files_to_clean.append(result_img_path)
        asyncio.create_task(_delete_files_later(files_to_clean))
        context.user_data.pop("person_path", None)

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
