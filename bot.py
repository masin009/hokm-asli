import os
import random
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext

# ==================== تنظیمات Railway ====================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TOKEN") or "8316915338:AAEo62io5KHBhq-MOMA-BRgSD9VleSDoRGc"

if not TOKEN:
    print("❌ توکن یافت نشد!")
    print("در Railway: Environment Variable با نام TELEGRAM_BOT_TOKEN ایجاد کن")
    exit(1)

print(f"✅ توکن خوانده شد")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== بازی ساده پاسور ====================

# ذخیره بازی‌های فعال
active_games = {}

class Player:
    def __init__(self, user_id, username, first_name):
        self.user_id = user_id
        self.username = username
        self.first_name = first_name
        self.cards = []
    
    @property
    def display_name(self):
        if self.username:
            return f"@{self.username}"
        return self.first_name or f"User_{self.user_id}"

def create_game(chat_id):
    """ایجاد بازی جدید"""
    game = {
        'chat_id': chat_id,
        'players': [],
        'status': 'waiting',  # waiting, playing, finished
        'trump': None,
        'message_id': None
    }
    active_games[chat_id] = game
    return game

def get_game(chat_id):
    """دریافت بازی فعال"""
    return active_games.get(chat_id)

# ==================== دستورات ربات ====================

def start_command(update: Update, context: CallbackContext):
    """دستور شروع"""
    user = update.effective_user
    update.message.reply_text(
        f"سلام {user.first_name}! 👋\n\n"
        "🎴 به ربات بازی پاسور خوش آمدید!\n\n"
        "📋 دستورات:\n"
        "/newgame - ایجاد بازی جدید\n"
        "/join - پیوستن به بازی\n"
        "/startgame - شروع بازی\n"
        "/rules - قوانین بازی\n"
        "/cancel - لغو بازی\n\n"
        "یک بازی ۴ نفره جذاب با دوستان! 🃏"
    )

def new_game_command(update: Update, context: CallbackContext):
    """ایجاد بازی جدید"""
    chat_id = update.effective_chat.id
    
    # بررسی بازی فعال
    existing_game = get_game(chat_id)
    if existing_game and existing_game['status'] != 'finished':
        update.message.reply_text("⚠️ یک بازی در حال اجرا در این گروه وجود دارد!")
        return
    
    user = update.effective_user
    
    # ایجاد بازی جدید
    game = create_game(chat_id)
    player = Player(user.id, user.username, user.first_name)
    game['players'].append(player)
    
    # ایجاد دکمه‌ها
    keyboard = [
        [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data="join_game")],
        [InlineKeyboardButton("▶️ شروع بازی", callback_data="start_game")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    players_list = "\n".join([f"• {p.display_name}" for p in game['players']])
    
    message = update.message.reply_text(
        f"🎴 بازی جدید پاسور ایجاد شد!\n\n"
        f"بازیکنان ({len(game['players'])}/۴):\n"
        f"{players_list}\n\n"
        f"برای پیوستن به بازی کلیک کنید:",
        reply_markup=reply_markup
    )
    
    game['message_id'] = message.message_id

def join_command(update: Update, context: CallbackContext):
    """پیوستن به بازی"""
    chat_id = update.effective_chat.id
    game = get_game(chat_id)
    
    if not game:
        update.message.reply_text("❌ هیچ بازی فعالی در این گروه وجود ندارد!")
        return
    
    if game['status'] != 'waiting':
        update.message.reply_text("❌ بازی در حال اجراست! نمی‌توانید الان بپیوندید.")
        return
    
    user = update.effective_user
    
    # بررسی حضور قبلی
    if any(p.user_id == user.id for p in game['players']):
        update.message.reply_text("✅ شما قبلاً در این بازی هستید!")
        return
    
    if len(game['players']) >= 4:
        update.message.reply_text("❌ بازی تکمیل است!")
        return
    
    # اضافه کردن بازیکن
    player = Player(user.id, user.username, user.first_name)
    game['players'].append(player)
    
    update.message.reply_text(f"✅ {user.first_name} به بازی پیوست!")
    
    # آپدیت پیام بازی
    players_list = "\n".join([f"• {p.display_name}" for p in game['players']])
    
    keyboard = [
        [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data="join_game")],
        [InlineKeyboardButton("▶️ شروع بازی", callback_data="start_game")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=game['message_id'],
            text=f"🎴 بازی پاسور\n\n"
                 f"بازیکنان ({len(game['players'])}/۴):\n"
                 f"{players_list}\n\n"
                 f"برای پیوستن به بازی کلیک کنید:",
            reply_markup=reply_markup
        )
    except:
        pass

def callback_handler(update: Update, context: CallbackContext):
    """مدیریت کلیک‌ها"""
    query = update.callback_query
    query.answer()
    
    chat_id = query.message.chat.id
    user = query.from_user
    
    if query.data == "join_game":
        game = get_game(chat_id)
        
        if not game:
            query.edit_message_text("❌ بازی یافت نشد!")
            return
        
        if game['status'] != 'waiting':
            query.answer("بازی در حال اجراست!", show_alert=True)
            return
        
        if any(p.user_id == user.id for p in game['players']):
            query.answer("شما قبلاً در بازی هستید!", show_alert=True)
            return
        
        if len(game['players']) >= 4:
            query.answer("بازی تکمیل است!", show_alert=True)
            return
        
        player = Player(user.id, user.username, user.first_name)
        game['players'].append(player)
        
        players_list = "\n".join([f"• {p.display_name}" for p in game['players']])
        
        keyboard = [
            [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data="join_game")],
            [InlineKeyboardButton("▶️ شروع بازی", callback_data="start_game")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            f"🎴 بازی پاسور\n\n"
            f"بازیکنان ({len(game['players'])}/۴):\n"
            f"{players_list}\n\n"
            f"برای پیوستن به بازی کلیک کنید:",
            reply_markup=reply_markup
        )
    
    elif query.data == "start_game":
        game = get_game(chat_id)
        
        if not game:
            query.edit_message_text("❌ بازی یافت نشد!")
            return
        
        if len(game['players']) < 2:
            query.answer("حداقل ۲ بازیکن نیاز است!", show_alert=True)
            return
        
        # شروع بازی
        game['status'] = 'playing'
        
        # انتخاب خال حکم تصادفی
        trumps = ["♥️ دل", "♦️ خشت", "♣️ پیک", "♠️ گیشنیز"]
        game['trump'] = random.choice(trumps)
        
        players_list = "\n".join([f"• {p.display_name}" for p in game['players']])
        
        query.edit_message_text(
            f"🎮 بازی شروع شد!\n\n"
            f"🃏 خال حکم: {game['trump']}\n\n"
            f"بازیکنان:\n"
            f"{players_list}\n\n"
            f"هر بازیکن ۱۳ کارت دریافت می‌کند.\n"
            f"بازی را {game['players'][0].display_name} شروع می‌کند."
        )
        
        # توزیع کارت‌های نمونه
        cards = ["آس", "شاه", "بیبی", "سرباز", "۱۰", "۹", "۸", "۷", "۶", "۵", "۴", "۳", "۲"]
        
        for player in game['players']:
            try:
                context.bot.send_message(
                    chat_id=player.user_id,
                    text=f"🎴 کارت‌های شما:\n\n"
                         f"خال حکم: {game['trump']}\n"
                         f"کارت‌ها: {', '.join(random.sample(cards, 5))}\n\n"
                         f"برای بازی در گروه کلیک کنید."
                )
            except:
                # اگر نتوانستیم پیام خصوصی بفرستیم
                query.message.reply_text(
                    f"⚠️ {player.display_name}، لطفا به ربات پیام خصوصی بدهید."
                )

def rules_command(update: Update, context: CallbackContext):
    """قوانین بازی"""
    rules_text = (
        "📖 قوانین بازی پاسور (حکم):\n\n"
        "🎯 هدف: بردیدن بیشترین تعداد دست\n\n"
        "👥 بازیکنان: ۴ نفر\n\n"
        "🃏 نحوه بازی:\n"
        "۱. هر بازیکن ۱۳ کارت می‌گیرد\n"
        "۲. یک خال به عنوان خال حکم انتخاب می‌شود\n"
        "۳. اولین بازیکن یک کارت بازی می‌کند\n"
        "۴. بقیه باید همخال بیاورند\n"
        "۵. اگر همخال ندارند، هر کارتی می‌توانند بیاورند\n"
        "۶. برنده دست، بالاترین کارت خال حکم را می‌برد\n"
        "۷. برنده دست بعدی را شروع می‌کند\n\n"
        "🏆 بازی بعد از ۱۳ دست تمام می‌شود."
    )
    
    update.message.reply_text(rules_text)

def cancel_command(update: Update, context: CallbackContext):
    """لغو بازی"""
    chat_id = update.effective_chat.id
    game = get_game(chat_id)
    
    if not game:
        update.message.reply_text("❌ هیچ بازی فعالی برای لغو وجود ندارد.")
        return
    
    # حذف بازی
    if chat_id in active_games:
        del active_games[chat_id]
    
    update.message.reply_text("✅ بازی لغو شد.")

def error_handler(update: Update, context: CallbackContext):
    """مدیریت خطا"""
    logger.error(f"خطا: {context.error}")

# ==================== اجرای ربات ====================

def main():
    """تابع اصلی"""
    
    print("🤖 ربات پاسور Railway در حال راه‌اندازی...")
    
    # ساخت Updater (نسخه 13.15)
    updater = Updater(TOKEN, use_context=True)
    dispatcher = updater.dispatcher
    
    # اضافه کردن دستورات
    dispatcher.add_handler(CommandHandler("start", start_command))
    dispatcher.add_handler(CommandHandler("newgame", new_game_command))
    dispatcher.add_handler(CommandHandler("join", join_command))
    dispatcher.add_handler(CommandHandler("rules", rules_command))
    dispatcher.add_handler(CommandHandler("cancel", cancel_command))
    
    # اضافه کردن handler برای callback
    dispatcher.add_handler(CallbackQueryHandler(callback_handler))
    
    # اضافه کردن handler خطا
    dispatcher.add_error_handler(error_handler)
    
    print("✅ ربات آماده است!")
    print("🎮 دستور /newgame را در یک گروه امتحان کنید")
    
    # شروع ربات
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
