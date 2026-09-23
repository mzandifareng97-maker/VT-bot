import os
import gc
import json
import shutil
import time
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from gradio_client import Client, handle_file
from PIL import Image

# --- Settings read from environment variables (set these on Render) ---
TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", "10000"))
WEBHOOK_BASE_URL = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("WEBHOOK_URL")

DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "30"))
COUNTER_FILE = "/tmp/vton_counter.json"

MAX_IMAGE_DIMENSION = 768

WELCOME, PHOTO1, PHOTO2, GARMENT_TYPE = range(4)


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


def _resize_image_in_place(path, max_dim=MAX_IMAGE_DIMENSION):
    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            width, height = img.size
            if max(width, height) > max_dim:
                scale = max_dim / float(max(width, height))
                new_size = (int(width * scale), int(height * scale))
                img = img.resize(new_size, Image.LANCZOS)
            img.save(path, "JPEG", quality=85)
    except Exception as e:
        print(f"Resize error for {path}: {e}")


def _cleanup_files(paths):
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception as e:
            print(f"Cleanup error for {p}: {e}")


def _run_tryon(person_path, garment_path, category, max_attempts=3):
    """
    category: "upper" or "lower".
    Returns the local file path of the generated result image.

    Retries a few times with a short backoff, since most failures from the
    free Hugging Face Spaces are transient (the Space waking up from sleep,
    or the shared queue being briefly full) rather than real errors.
    """
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            if category == "lower":
                # yisol/IDM-VTON is only trained for upper-body garments, so for
                # pants/shorts we use franciszzj/Leffa instead, which has a
                # dedicated DressCode-trained model for lower-body garments.
                client = Client("franciszzj/Leffa")
                result = client.predict(
                    handle_file(person_path),
                    handle_file(garment_path),
                    False,          # ref_acceleration
                    30,             # inference steps
                    2.5,            # guidance scale
                    42,             # seed
                    "dress_code",   # vt_model_type
                    "lower_body",   # vt_garment_type
                    False,          # vt_repaint
                    api_name="/leffa_predict_vt"
                )
            else:
                client = Client("yisol/IDM-VTON")
                result = client.predict(
                    dict={"background": handle_file(person_path), "layers": [], "composite": None},
                    garm_img=handle_file(garment_path),
                    garment_des="clothing",
                    is_checked=True,
                    is_checked_crop=True,
                    denoise_steps=30,
                    seed=42,
                    api_name="/tryon"
                )
            return result[0] if isinstance(result, (list, tuple)) else result
        except Exception as e:
            last_error = e
            print(f"_run_tryon attempt {attempt}/{max_attempts} failed: {e}")
            if attempt < max_attempts:
                time.sleep(attempt * 5)  # 5s, then 10s before the next try

    raise last_error


def _restart_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 دوباره امتحان کن", callback_data="restart")]
    ])


def _result_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👖 هم بالاتنه هم پایین‌تنه رو ببین", callback_data="add_more")],
        [InlineKeyboardButton("🔄 شروع از اول", callback_data="restart")]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    counter = _load_counter()
    if counter["count"] >= DAILY_LIMIT:
        await update.message.reply_text("ظرفیت امروز پر شد، فردا دوباره امتحان کنید.")
        return ConversationHandler.END

    _cleanup_files([context.user_data.pop("last_result_path", None)])

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 شروع پرو مجازی", callback_data="begin_tryon")]
    ])
    await update.message.reply_text(
        "سلام! 👋 به اتاق پرو مجازی زاروس خوش اومدید.\nبرای شروع، دکمه‌ی زیر رو بزنید. ✨",
        reply_markup=keyboard
    )
    return WELCOME


async def begin_tryon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("لطفاً اول عکس خودتون رو ارسال کنید. 📸")
    return PHOTO1


async def restart_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    counter = _load_counter()
    if counter["count"] >= DAILY_LIMIT:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="ظرفیت امروز پر شد، فردا دوباره امتحان کنید.")
        return ConversationHandler.END

    _cleanup_files([context.user_data.pop("last_result_path", None)])

    await context.bot.send_message(chat_id=update.effective_chat.id, text="لطفاً اول عکس خودتون رو ارسال کنید. 📸")
    return PHOTO1


async def add_more_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    counter = _load_counter()
    if counter["count"] >= DAILY_LIMIT:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="ظرفیت امروز پر شد، فردا دوباره امتحان کنید.")
        return ConversationHandler.END

    last_result_path = context.user_data.get("last_result_path")
    if not last_result_path or not os.path.exists(last_result_path):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="این ست دیگه در دسترس نیست، لطفاً دوباره با /start شروع کنید.")
        return ConversationHandler.END

    # Use the previous result as the new "person photo" for the next garment.
    context.user_data["person_path"] = last_result_path

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="عالیه! حالا عکس لباس بعدی رو ارسال کنید تا ست‌تون کامل بشه. 👕👖"
    )
    return PHOTO2


async def get_photo1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    person_path = f"/tmp/person_{user_id}.jpg"

    photo_file = await update.message.photo[-1].get_file()
    await photo_file.download_to_drive(person_path)
    _resize_image_in_place(person_path)

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
    _resize_image_in_place(garment_path)

    context.user_data["garment_path"] = garment_path

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👕 بالاتنه", callback_data="upper"),
            InlineKeyboardButton("👖 پایین‌تنه", callback_data="lower"),
        ]
    ])
    await update.message.reply_text(
        "این لباس بالاتنه است یا پایین‌تنه؟ 🤔",
        reply_markup=keyboard
    )
    return GARMENT_TYPE


async def get_garment_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    garment_des = "pants" if query.data == "lower" else "clothing"
    category = query.data  # "upper" or "lower"

    person_path = context.user_data.get("person_path")
    garment_path = context.user_data.get("garment_path")

    if not person_path or not garment_path or not os.path.exists(person_path) or not os.path.exists(garment_path):
        await query.edit_message_text("مشکلی پیش اومد، لطفاً دوباره با /start شروع کنید.")
        return ConversationHandler.END

    await query.edit_message_text("عکس‌ها دریافت شدن، شما در حال پوشیدن لباس هستید... ⏳✨")

    result_img_path = None
    persistent_result_path = None
    try:
        result_img_path = _run_tryon(person_path, garment_path, category)

        persistent_result_path = f"/tmp/result_{user_id}.jpg"
        shutil.copy(result_img_path, persistent_result_path)
        context.user_data["last_result_path"] = persistent_result_path

        with open(result_img_path, 'rb') as photo:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=photo,
                caption="از اتاق پرو اومدید بیرون! می\u200cتونید خودتون رو توی آینه\u200cی زاروس ببینید. 🪞😍😍😍\n\nاگه لباس بالا رو پرو کردید، پایین‌تنه هم می\u200cخواید ببینید؟ (یا برعکس) دکمه‌ی پایین رو بزنید 👇",
                reply_markup=_result_keyboard()
            )

        counter = _load_counter()
        counter["count"] += 1
        _save_counter(counter)

    except Exception as e:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="اتاق‌های پرو تا لحظاتی دیگه خالی می‌شن، لطفاً صبر کنید و دوباره امتحان کنید.\nممنون از شکیبایی شما. 🙏",
            reply_markup=_restart_keyboard()
        )
        print(f"Error: {e}")

    finally:
        files_to_clean = [garment_path, result_img_path]
        if person_path != persistent_result_path:
            files_to_clean.append(person_path)
        _cleanup_files(files_to_clean)
        context.user_data.pop("person_path", None)
        context.user_data.pop("garment_path", None)
        gc.collect()

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "عملیات لغو شد. برای شروع مجدد دکمه‌ی زیر رو بزنید. 👇",
        reply_markup=_restart_keyboard()
    )
    return ConversationHandler.END


if __name__ == '__main__':
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is not set!")
    if not WEBHOOK_BASE_URL:
        raise RuntimeError("WEBHOOK_URL (or RENDER_EXTERNAL_URL) environment variable is not set!")

    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CallbackQueryHandler(restart_flow, pattern="^restart$"),
            CallbackQueryHandler(add_more_flow, pattern="^add_more$"),
        ],
        states={
            WELCOME: [CallbackQueryHandler(begin_tryon, pattern="^begin_tryon$")],
            PHOTO1: [MessageHandler(filters.PHOTO, get_photo1)],
            PHOTO2: [MessageHandler(filters.PHOTO, get_photo2)],
            GARMENT_TYPE: [CallbackQueryHandler(get_garment_type, pattern="^(upper|lower)$")],
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
