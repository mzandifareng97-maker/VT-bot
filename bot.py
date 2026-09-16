import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from gradio_client import Client, handle_file

# Put your Telegram Bot Token below
TOKEN = os.environ.get("BOT_TOKEN")

PHOTO1, PHOTO2 = range(2)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            
    except Exception as e:
        await update.message.reply_text(f"خطا در پردازش: {e}")
        
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("عملیات لغو شد. برای شروع مجدد /start را بفرستید.")
    return ConversationHandler.END

if __name__ == '__main__':
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
    print("Bot is running...")
    app.run_polling()