import os
import random
import logging
import asyncio
from enum import Enum
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)

# ==================== تنظیمات Railway ====================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TOKEN")
PORT = int(os.environ.get("PORT", 8080))
RAILWAY_STATIC_URL = os.environ.get("RAILWAY_STATIC_URL", "")
WEBHOOK_URL = f"{RAILWAY_STATIC_URL}/{TOKEN}" if RAILWAY_STATIC_URL else ""

if not TOKEN:
    print("❌ توکن یافت نشد!")
    print("در Railway: Environment Variable با نام TELEGRAM_BOT_TOKEN ایجاد کن")
    exit(1)

print(f"✅ توکن خوانده شد")
print(f"🔧 پورت: {PORT}")
if WEBHOOK_URL:
    print(f"🌐 Webhook URL: {WEBHOOK_URL}")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== Healthcheck برای Railway ====================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # سر و صدای لاگ رو کم می‌کنه
        pass

def start_healthcheck_server():
    """سرور Healthcheck رو راه‌اندازی کن"""
    server = HTTPServer(('0.0.0.0', PORT), HealthCheckHandler)
    print(f"🩺 Healthcheck server started on port {PORT}")
    
    # در یک ترد جداگانه اجرا بشه
    import threading
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    return server

# ==================== بازی ساده پاسور ====================

class Suit(Enum):
    HEARTS = "♥️"
    DIAMONDS = "♦️"
    CLUBS = "♣️"
    SPADES = "♠️"
    
    @property
    def persian_name(self):
        names = {
            Suit.HEARTS: "دل",
            Suit.DIAMONDS: "خشت",
            Suit.CLUBS: "پیک",
            Suit.SPADES: "گیشنیز"
        }
        return names[self]

class Card:
    def __init__(self, suit: Suit, rank: str, value: int):
        self.suit = suit
        self.rank = rank
        self.value = value
    
    def __str__(self):
        return f"{self.suit.value}{self.rank}"
    
    @property
    def persian_name(self):
        rank_names = {
            "A": "آس", "K": "شاه", "Q": "بیبی", "J": "سرباز",
            "10": "ده", "9": "نه", "8": "هشت", "7": "هفت",
            "6": "شش", "5": "پنج", "4": "چهار", "3": "سه", "2": "دو"
        }
        return f"{rank_names.get(self.rank, self.rank)} {self.suit.persian_name}"

class Player:
    def __init__(self, user_id: int, username: str = "", first_name: str = ""):
        self.user_id = user_id
        self.username = username
        self.first_name = first_name
        self.cards = []
        self.score = 0
    
    @property
    def display_name(self):
        if self.username:
            return f"@{self.username}"
        return self.first_name or f"User_{self.user_id}"

class Game:
    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.players = []
        self.deck = []
        self.trump_suit = None
        self.current_player_index = 0
        self.status = "waiting"  # waiting, playing, finished
        self.message_id = None
    
    def add_player(self, player: Player):
        if len(self.players) < 4 and not any(p.user_id == player.user_id for p in self.players):
            self.players.append(player)
            return True
        return False
    
    def create_deck(self):
        ranks = [
            ("2", 2), ("3", 3), ("4", 4), ("5", 5), ("6", 6),
            ("7", 7), ("8", 8), ("9", 9), ("10", 10),
            ("J", 11), ("Q", 12), ("K", 13), ("A", 14)
        ]
        
        self.deck = []
        for suit in [Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS, Suit.SPADES]:
            for rank, value in ranks:
                self.deck.append(Card(suit, rank, value))
        
        random.shuffle(self.deck)
    
    def deal_cards(self):
        if len(self.players) == 0:
            return
        
        cards_per_player = 5  # برای بازی سریع
        for i, player in enumerate(self.players):
            start = i * cards_per_player
            end = start + cards_per_player
            player.cards = self.deck[start:end]
    
    def start_game(self):
        if len(self.players) < 2:
            return False
        
        self.create_deck()
        self.deal_cards()
        self.trump_suit = random.choice([Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS, Suit.SPADES])
        self.status = "playing"
        return True

# مدیریت بازی‌ها
games = {}

# ==================== دستورات ربات ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎴 به ربات پاسور خوش آمدید!\n\n"
        "دستورات:\n"
        "/newgame - بازی جدید\n"
        "/join - پیوستن\n"
        "/startgame - شروع بازی\n"
        "/rules - قوانین\n\n"
        "یک بازی ۴ نفره جذاب!"
    )

async def new_game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if chat_id in games:
        await update.message.reply_text("⚠️ یک بازی در این گروه فعال است!")
        return
    
    user = update.effective_user
    player = Player(user.id, user.username, user.first_name)
    
    game = Game(chat_id)
    game.add_player(player)
    games[chat_id] = game
    
    keyboard = [
        [InlineKeyboardButton("🎮 پیوستن", callback_data="join_game")],
        [InlineKeyboardButton("▶️ شروع", callback_data="start_game")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = await update.message.reply_text(
        f"🎴 بازی جدید ساخته شد!\n\n"
        f"بازیکنان (۱/۴):\n"
        f"• {player.display_name}\n\n"
        f"برای پیوستن کلیک کنید:",
        reply_markup=reply_markup
    )
    
    game.message_id = message.message_id

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat.id
    user = query.from_user
    
    if query.data == "join_game":
        if chat_id not in games:
            await query.edit_message_text("❌ بازی یافت نشد!")
            return
        
        game = games[chat_id]
        
        if len(game.players) >= 4:
            await query.answer("بازی پر است!", show_alert=True)
            return
        
        if any(p.user_id == user.id for p in game.players):
            await query.answer("شما قبلاً عضو هستید!", show_alert=True)
            return
        
        player = Player(user.id, user.username, user.first_name)
        game.add_player(player)
        
        players_text = "\n".join([f"• {p.display_name}" for p in game.players])
        
        keyboard = [
            [InlineKeyboardButton("🎮 پیوستن", callback_data="join_game")],
            [InlineKeyboardButton("▶️ شروع", callback_data="start_game")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🎴 بازی پاسور\n\n"
            f"بازیکنان ({len(game.players)}/۴):\n"
            f"{players_text}\n\n"
            f"برای پیوستن کلیک کنید:",
            reply_markup=reply_markup
        )
    
    elif query.data == "start_game":
        if chat_id not in games:
            await query.edit_message_text("❌ بازی یافت نشد!")
            return
        
        game = games[chat_id]
        
        if len(game.players) < 2:
            await query.answer("حداقل ۲ بازیکن نیاز است!", show_alert=True)
            return
        
        if game.start_game():
            players_cards = "\n".join([
                f"• {p.display_name}: {len(p.cards)} کارت" 
                for p in game.players
            ])
            
            await query.edit_message_text(
                f"🎮 بازی شروع شد!\n\n"
                f"خال حکم: {game.trump_suit.value} {game.trump_suit.persian_name}\n\n"
                f"{players_cards}\n\n"
                f"نوبت: {game.players[0].display_name}"
            )
        else:
            await query.answer("خطا در شروع بازی!", show_alert=True)

async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 قوانین ساده پاسور:\n\n"
        "۱. بازی ۴ نفره\n"
        "۲. هرکس ۵ کارت می‌گیرد\n"
        "۳. یک خال حکم انتخاب می‌شود\n"
        "۴. باید همخال بازی کرد\n"
        "۵. برنده دست بعدی را شروع می‌کند\n"
        "۶. امتیاز بر اساس دست‌های برده\n\n"
        "🎯 برای شروع: /newgame"
    )

# ==================== اجرای ربات ====================

async def post_init(application: Application):
    """تنظیم webhook بعد از راه‌اندازی"""
    if WEBHOOK_URL:
        await application.bot.set_webhook(WEBHOOK_URL)
        logger.info(f"Webhook set to: {WEBHOOK_URL}")

def main():
    """تابع اصلی"""
    
    # شروع Healthcheck server
    start_healthcheck_server()
    
    # ساخت application ربات
    application = Application.builder().token(TOKEN).post_init(post_init).build()
    
    # اضافه کردن دستورات
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("newgame", new_game_command))
    application.add_handler(CommandHandler("rules", rules_command))
    
    # اضافه کردن handler برای callback
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    print("🤖 ربات پاسور Railway در حال اجراست...")
    
    if WEBHOOK_URL:
        # حالت Webhook برای Railway
        print("🌐 حالت Webhook فعال است")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=WEBHOOK_URL,
            secret_token='HOKM_BOT_SECRET'
        )
    else:
        # حالت Polling برای لوکال
        print("🔄 حالت Polling فعال است")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
