import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ==================== تنظیمات ====================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    print("❌ لطفا در Railway، Environment Variable به نام TELEGRAM_BOT_TOKEN ایجاد کنید")
    exit(1)

REQUIRED_CHANNEL = "@konkorkhabar"  # کانال شما

# لاگ‌گیری
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== دستورات اصلی ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور شروع"""
    user = update.effective_user
    await update.message.reply_text(
        f"سلام {user.first_name}! 👋\n\n"
        "🎴 به ربات بازی پاسور خوش آمدید!\n\n"
        "دستورات:\n"
        "/newgame - ایجاد بازی جدید\n"
        "/verify - بررسی عضویت من\n\n"
        f"📢 برای بازی باید عضو کانال {REQUIRED_CHANNEL} باشید."
    )

async def newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ایجاد بازی جدید"""
    chat_id = update.effective_chat.id
    user = update.effective_user
    
    # ایجاد دکمه‌ها
    keyboard = [
        [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data="join_1")],
        [InlineKeyboardButton("✅ بررسی عضویت", callback_data="verify_member")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🎴 بازی جدید ایجاد شد!\n"
        f"سازنده: {user.first_name}\n"
        f"کانال لازم: {REQUIRED_CHANNEL}\n\n"
        f"برای بازی باید عضو کانال باشید.",
        reply_markup=reply_markup
    )

async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بررسی عضویت"""
    user = update.effective_user
    
    # دکمه‌ها
    keyboard = [
        [
            InlineKeyboardButton("📢 جوین شو در کانال", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"),
            InlineKeyboardButton("✅ بررسی عضویت من", callback_data="check_membership")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🔐 بررسی عضویت در کانال:\n{REQUIRED_CHANNEL}\n\n"
        f"۱. روی دکمه بالا کلیک کنید\n"
        f"۲. به کانال بپیوندید\n"
        f"۳. سپس عضویت خود را بررسی کنید",
        reply_markup=reply_markup
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت کلیک روی دکمه‌ها"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "verify_member":
        keyboard = [
            [
                InlineKeyboardButton("📢 جوین شو در کانال", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"),
                InlineKeyboardButton("✅ بررسی عضویت من", callback_data="check_membership")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text="لطفا عضویت خود را بررسی کنید:",
            reply_markup=reply_markup
        )
    
    elif data == "check_membership":
        user = query.from_user
        
        try:
            # بررسی عضویت
            chat_member = await context.bot.get_chat_member(
                chat_id=REQUIRED_CHANNEL,
                user_id=user.id
            )
            
            if chat_member.status in ['member', 'administrator', 'creator']:
                await query.edit_message_text(
                    text=f"✅ {user.first_name} عزیز، عضویت شما تایید شد!\n\n"
                         f"🎮 حالا می‌توانید بازی کنید."
                )
            else:
                await query.edit_message_text(
                    text=f"❌ شما عضو کانال {REQUIRED_CHANNEL} نیستید!\n\n"
                         f"لطفا ابتدا به کانال بپیوندید."
                )
        
        except Exception as e:
            logger.error(f"خطا در بررسی عضویت: {e}")
            await query.edit_message_text(
                text="❌ خطا در بررسی عضویت. لطفا دوباره تلاش کنید."
            )
    
    elif data == "join_1":
        await query.answer("✅ به بازی پیوستید! لطفا ابتدا عضویت خود را تایید کنید.", show_alert=True)

# ==================== راه‌اندازی اصلی ====================

def main():
    """تابع اصلی - نسخه بسیار ساده"""
    print("=" * 50)
    print("🤖 ربات پاسور در حال راه‌اندازی...")
    print(f"📢 کانال اجباری: {REQUIRED_CHANNEL}")
    print("=" * 50)
    
    try:
        # ساخت application
        application = Application.builder().token(TOKEN).build()
        
        # اضافه کردن دستورات
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("newgame", newgame))
        application.add_handler(CommandHandler("verify", verify))
        application.add_handler(CallbackQueryHandler(callback_handler))
        
        print("✅ ربات آماده است!")
        print("💡 دستور /start را در تلگرام امتحان کنید")
        print("=" * 50)
        
        # راه‌اندازی
        application.run_polling(
            drop_pending_updates=True,
            poll_interval=0.5,
            timeout=10
        )
    
    except Exception as e:
        print(f"❌ خطای بحرانی: {e}")
        print("🔧 راه‌حل‌ها:")
        print("1. در Railway، Environment Variable TELEGRAM_BOT_TOKEN را تنظیم کنید")
        print("2. مطمئن شوید ربات ADMIN کانال @konkorkhabar است")
        print("3. در Railway Settings > Scaling، replicas را روی 1 تنظیم کنید")

if __name__ == "__main__":
    main()
