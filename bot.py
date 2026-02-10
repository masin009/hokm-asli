import os
import random
import logging
import asyncio
from enum import Enum
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)
from telegram.error import TelegramError

# ==================== تنظیمات ====================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TOKEN")

if not TOKEN:
    try:
        from dotenv import load_dotenv
        load_dotenv()
        TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TOKEN")
    except:
        pass

if not TOKEN:
    print("❌ توکن یافت نشد!")
    print("در Railway: Environment Variable با نام TELEGRAM_BOT_TOKEN ایجاد کن")
    exit(1)

# کانال اجباری - با @
REQUIRED_CHANNEL = "@konkorkhabar"

print(f"✅ توکن خوانده شد")
print(f"📢 کانال اجباری: {REQUIRED_CHANNEL}")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== کلاس‌های بازی ====================

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

class Rank:
    def __init__(self, symbol: str, value: int, persian_name: str):
        self.symbol = symbol
        self.value = value
        self.persian_name = persian_name

# تعریف رتبه‌های کارت
RANKS = {
    '2': Rank('2', 2, 'دو'),
    '3': Rank('3', 3, 'سه'),
    '4': Rank('4', 4, 'چهار'),
    '5': Rank('5', 5, 'پنج'),
    '6': Rank('6', 6, 'شش'),
    '7': Rank('7', 7, 'هفت'),
    '8': Rank('8', 8, 'هشت'),
    '9': Rank('9', 9, 'نه'),
    '10': Rank('10', 10, 'ده'),
    'J': Rank('J', 11, 'سرباز'),
    'Q': Rank('Q', 12, 'بیبی'),
    'K': Rank('K', 13, 'شاه'),
    'A': Rank('A', 14, 'آس')
}

class Card:
    def __init__(self, suit: Suit, rank: Rank):
        self.suit = suit
        self.rank = rank
    
    def __str__(self):
        return f"{self.suit.value}{self.rank.symbol}"
    
    @property
    def persian_name(self):
        return f"{self.rank.persian_name} {self.suit.persian_name}"
    
    @property
    def value(self):
        return self.rank.value

class Player:
    def __init__(self, user_id: int, username: str = "", first_name: str = ""):
        self.user_id = user_id
        self.username = username
        self.first_name = first_name
        self.cards: List[Card] = []
        self.tricks_won: int = 0
        self.score: int = 0
        self.is_ready: bool = False
        self.is_channel_member: bool = False
        self.verified: bool = False
        self.last_checked: datetime = datetime.now()
    
    @property
    def display_name(self):
        if self.username:
            return f"@{self.username}"
        return self.first_name or f"User_{self.user_id}"
    
    def get_verification_status(self):
        """وضعیت تایید بازیکن"""
        if self.verified:
            return "✅ تایید شده"
        return "⏳ در انتظار تایید"

class Round:
    def __init__(self):
        self.cards_played: Dict[int, Card] = {}
        self.starting_player_id: Optional[int] = None
        self.winner_id: Optional[int] = None
    
    def is_complete(self, players_count: int) -> bool:
        return len(self.cards_played) == players_count

class Game:
    def __init__(self, game_id: str, chat_id: int, creator_id: int):
        self.game_id = game_id
        self.chat_id = chat_id
        self.creator_id = creator_id
        self.players: List[Player] = []
        self.deck: List[Card] = []
        self.current_round = Round()
        self.rounds: List[Round] = []
        self.turn_order: List[int] = []
        self.current_turn_index: int = 0
        self.trump_suit: Optional[Suit] = None
        self.trump_chooser_id: Optional[int] = None
        self.state: str = "waiting"
        self.message_id: Optional[int] = None
        self.created_at = datetime.now()
        self.verification_messages: Dict[int, int] = {}  # user_id -> message_id
    
    def add_player(self, player: Player) -> bool:
        if len(self.players) >= 4:
            return False
        if any(p.user_id == player.user_id for p in self.players):
            return False
        self.players.append(player)
        return True
    
    def remove_player(self, user_id: int) -> bool:
        for i, player in enumerate(self.players):
            if player.user_id == user_id:
                self.players.pop(i)
                return True
        return False
    
    def get_player(self, user_id: int) -> Optional[Player]:
        for player in self.players:
            if player.user_id == user_id:
                return player
        return None
    
    def get_game_info_text(self) -> str:
        text = f"🎴 بازی پاسور (حکم) - کد: {self.game_id[-6:]}\n\n"
        
        if self.state == "waiting":
            text += f"⏳ در انتظار بازیکنان ({len(self.players)}/4)\n\n"
            text += "👥 بازیکنان:\n"
            for i, player in enumerate(self.players, 1):
                status = player.get_verification_status()
                text += f"{i}. {player.display_name} - {status}\n"
            
            text += f"\n📢 برای بازی باید عضو کانال {REQUIRED_CHANNEL} باشید.\n"
            
            creator = self.get_player(self.creator_id)
            if creator:
                text += f"🎮 سازنده: {creator.display_name}"
        
        elif self.state == "choosing_trump":
            chooser = self.get_player(self.trump_chooser_id)
            text += "👑 انتخاب خال حکم\n\n"
            text += f"بازیکن انتخاب کننده: {chooser.display_name if chooser else '?'}\n"
            text += f"دست: {len(self.rounds) + 1}/13\n\n"
            text += "لطفا خال حکم را انتخاب کنید:"
        
        return text
    
    def update_verification_status(self, user_id: int, is_verified: bool):
        """آپدیت وضعیت تایید بازیکن"""
        player = self.get_player(user_id)
        if player:
            player.verified = is_verified
            player.is_channel_member = is_verified
            player.last_checked = datetime.now()
            return True
        return False

# ==================== مدیریت بازی‌ها ====================

class GameManager:
    def __init__(self):
        self.games: Dict[str, Game] = {}
        self.user_games: Dict[int, str] = {}
        self.chat_games: Dict[int, List[str]] = defaultdict(list)
    
    def create_game(self, chat_id: int, creator: Player) -> Game:
        game_id = f"hokm_{chat_id}_{int(datetime.now().timestamp())}"
        game = Game(game_id=game_id, chat_id=chat_id, creator_id=creator.user_id)
        
        # سازنده به صورت خودکار تایید می‌شود
        creator.verified = True
        creator.is_channel_member = True
        game.add_player(creator)
        
        self.games[game_id] = game
        self.user_games[creator.user_id] = game_id
        self.chat_games[chat_id].append(game_id)
        
        return game
    
    def get_game(self, game_id: str) -> Optional[Game]:
        return self.games.get(game_id)
    
    def get_player_game(self, user_id: int) -> Optional[Game]:
        game_id = self.user_games.get(user_id)
        if game_id:
            return self.get_game(game_id)
        return None
    
    def delete_game(self, game_id: str):
        game = self.games.get(game_id)
        if game:
            for player in game.players:
                self.user_games.pop(player.user_id, None)
            
            if game.chat_id in self.chat_games:
                if game_id in self.chat_games[game.chat_id]:
                    self.chat_games[game.chat_id].remove(game_id)
            
            del self.games[game_id]
            return True
        return False

game_manager = GameManager()

# ==================== تایید عضویت ====================

async def check_channel_membership(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Tuple[bool, str]:
    """بررسی عضویت کاربر در کانال"""
    try:
        logger.info(f"🔍 بررسی عضویت کاربر {user_id} در {REQUIRED_CHANNEL}")
        
        # حذف @ از ابتدای آیدی کانال
        channel = REQUIRED_CHANNEL.lstrip('@')
        
        chat_member = await context.bot.get_chat_member(
            chat_id=f"@{channel}",
            user_id=user_id
        )
        
        logger.info(f"وضعیت کاربر {user_id}: {chat_member.status}")
        
        # وضعیت‌های مجاز
        if chat_member.status in ['member', 'administrator', 'creator']:
            logger.info(f"✅ کاربر {user_id} عضو است")
            return True, "عضویت تایید شد"
        elif chat_member.status == 'restricted':
            # بررسی وضعیت restricted
            if hasattr(chat_member, 'is_member') and chat_member.is_member:
                logger.info(f"✅ کاربر {user_id} عضو است (restricted)")
                return True, "عضویت تایید شد"
        
        logger.info(f"❌ کاربر {user_id} عضو نیست. وضعیت: {chat_member.status}")
        return False, "شما عضو کانال نیستید"
        
    except Exception as e:
        error_msg = str(e).lower()
        logger.error(f"❌ خطا در بررسی عضویت کاربر {user_id}: {e}")
        
        if "user not found" in error_msg or "not a member" in error_msg:
            return False, "شما عضو کانال نیستید"
        elif "chat not found" in error_msg:
            return False, "کانال یافت نشد"
        elif "not enough rights" in error_msg:
            return False, "ربات دسترسی کافی به کانال ندارد"
        else:
            return False, f"خطا در بررسی: {str(e)[:50]}"

async def send_verification_message(context: ContextTypes.DEFAULT_TYPE, user_id: int, game: Game) -> Optional[int]:
    """ارسال پیام تایید عضویت به کاربر"""
    try:
        # حذف @ از ابتدای آیدی کانال برای لینک
        channel = REQUIRED_CHANNEL.lstrip('@')
        
        keyboard = [
            [
                InlineKeyboardButton("📢 جوین شو در کانال", url=f"https://t.me/{channel}"),
                InlineKeyboardButton("✅ بررسی عضویت من", callback_data=f"verify_check_{game.game_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = await context.bot.send_message(
            chat_id=user_id,
            text=f"🔐 تایید عضویت برای بازی پاسور\n\n"
                 f"📢 کانال اجباری: {REQUIRED_CHANNEL}\n"
                 f"🔢 کد بازی: {game.game_id[-6:]}\n\n"
                 f"📋 مراحل:\n"
                 f"۱. روی 'جوین شو در کانال' کلیک کنید\n"
                 f"۲. به کانال بپیوندید (Join)\n"
                 f"۳. روی 'بررسی عضویت من' کلیک کنید\n\n"
                 f"⚠️ بدون تایید عضویت نمی‌توانید بازی کنید.",
            reply_markup=reply_markup
        )
        
        # ذخیره پیام تایید
        game.verification_messages[user_id] = message.message_id
        logger.info(f"✅ پیام تایید برای کاربر {user_id} ارسال شد")
        
        return message.message_id
        
    except Exception as e:
        logger.error(f"❌ خطا در ارسال پیام تایید به کاربر {user_id}: {e}")
        return None

async def verify_player_membership(context: ContextTypes.DEFAULT_TYPE, user_id: int, game: Game) -> Tuple[bool, str]:
    """بررسی و تایید عضویت یک بازیکن"""
    try:
        is_member, message = await check_channel_membership(context, user_id)
        
        if is_member:
            # تایید بازیکن
            game.update_verification_status(user_id, True)
            
            # حذف پیام تایید اگر وجود دارد
            if user_id in game.verification_messages:
                try:
                    await context.bot.delete_message(
                        chat_id=user_id,
                        message_id=game.verification_messages[user_id]
                    )
                except:
                    pass
                finally:
                    game.verification_messages.pop(user_id, None)
            
            # آپدیت پیام اصلی بازی
            await update_game_message(context, game)
            
            return True, "✅ عضویت شما تایید شد! حالا می‌توانید بازی کنید."
        else:
            # اگر عضو نیست، دوباره پیام تایید بفرست
            if user_id not in game.verification_messages:
                await send_verification_message(context, user_id, game)
            
            return False, f"❌ {message}\n\nلطفا به کانال {REQUIRED_CHANNEL} بپیوندید."
            
    except Exception as e:
        logger.error(f"❌ خطا در تایید عضویت کاربر {user_id}: {e}")
        return False, f"خطا در بررسی عضویت: {str(e)[:50]}"

async def update_game_message(context: ContextTypes.DEFAULT_TYPE, game: Game):
    """آپدیت پیام اصلی بازی"""
    try:
        keyboard = [
            [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
            [
                InlineKeyboardButton("▶️ شروع بازی", callback_data=f"start_{game.game_id}"),
                InlineKeyboardButton("❌ بستن بازی", callback_data=f"close_{game.game_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.edit_message_text(
            chat_id=game.chat_id,
            message_id=game.message_id,
            text=game.get_game_info_text(),
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"❌ خطا در آپدیت پیام بازی: {e}")

# ==================== دستورات ربات ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور شروع"""
    user = update.effective_user
    await update.message.reply_text(
        f"سلام {user.first_name}! 👋\n\n"
        "🎴 به ربات بازی پاسور (حکم) خوش آمدید!\n\n"
        "📋 دستورات:\n"
        "/newgame - ایجاد بازی جدید\n"
        "/join - پیوستن به بازی\n"
        "/startgame - شروع بازی (فقط سازنده)\n"
        "/close - بستن بازی (فقط سازنده)\n"
        "/leave - ترک بازی\n"
        "/games - نمایش بازی‌های فعال\n"
        "/status - وضعیت بازی فعلی\n"
        "/verify - بررسی عضویت من\n"
        "/rules - قوانین بازی\n\n"
        f"📢 برای بازی باید عضو کانال {REQUIRED_CHANNEL} باشید."
    )

async def new_game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ایجاد بازی جدید"""
    chat_id = update.effective_chat.id
    user = update.effective_user
    
    player = Player(user.id, user.username, user.first_name)
    game = game_manager.create_game(chat_id, player)
    
    keyboard = [
        [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
        [
            InlineKeyboardButton("▶️ شروع بازی", callback_data=f"start_{game.game_id}"),
            InlineKeyboardButton("❌ بستن بازی", callback_data=f"close_{game.game_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = await update.message.reply_text(
        game.get_game_info_text(),
        reply_markup=reply_markup
    )
    
    game.message_id = message.message_id
    
    await update.message.reply_text(
        f"✅ بازی جدید ایجاد شد!\n"
        f"🎮 شما سازنده این بازی هستید.\n"
        f"🔢 کد بازی: {game.game_id[-6:]}\n\n"
        f"دیگران می‌توانند با کلیک روی 'پیوستن به بازی' به این بازی بپیوندند."
    )

async def verify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بررسی عضویت کاربر"""
    user = update.effective_user
    game = game_manager.get_player_game(user.id)
    
    if not game:
        await update.message.reply_text("❌ شما در هیچ بازی فعالی نیستید!")
        return
    
    success, message = await verify_player_membership(context, user.id, game)
    
    if success:
        await update.message.reply_text(message)
    else:
        await update.message.reply_text(message)

async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پیوستن به بازی"""
    chat_id = update.effective_chat.id
    games = game_manager.get_chat_games(chat_id)
    
    if not games:
        await update.message.reply_text("❌ هیچ بازی فعالی در این گروه وجود ندارد!")
        return
    
    text = "🎮 بازی‌های فعال در این گروه:\n\n"
    keyboard = []
    
    for game in games:
        if game.state == "waiting":
            text += f"🔢 کد: {game.game_id[-6:]}\n"
            text += f"👤 سازنده: {game.get_player(game.creator_id).display_name if game.get_player(game.creator_id) else '?'}\n"
            text += f"👥 بازیکنان: {len(game.players)}/4\n\n"
            
            keyboard.append([
                InlineKeyboardButton(
                    f"🎮 پیوستن به بازی {game.game_id[-6:]}",
                    callback_data=f"join_{game.game_id}"
                )
            ])
    
    if keyboard:
        keyboard.append([InlineKeyboardButton("🆕 ایجاد بازی جدید", callback_data="create_new_game")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text("❌ هیچ بازی در انتظاری وجود ندارد!")

# ==================== مدیریت کلیک‌ها ====================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت کلیک روی دکمه‌ها"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data
    
    logger.info(f"📱 کلیک دریافت شد: {data} از کاربر {user.id}")
    
    if data == "create_new_game":
        chat_id = query.message.chat.id
        player = Player(user.id, user.username, user.first_name)
        game = game_manager.create_game(chat_id, player)
        
        keyboard = [
            [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
            [
                InlineKeyboardButton("▶️ شروع بازی", callback_data=f"start_{game.game_id}"),
                InlineKeyboardButton("❌ بستن بازی", callback_data=f"close_{game.game_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(
                text=game.get_game_info_text(),
                reply_markup=reply_markup
            )
        except:
            await query.edit_message_text("❌ خطا در ایجاد بازی!")
            return
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ بازی جدید ایجاد شد!\n🎮 سازنده: {user.first_name}"
        )
    
    elif data.startswith("join_"):
        game_id = data[5:]
        game = game_manager.get_game(game_id)
        
        if not game:
            await query.answer("❌ بازی یافت نشد!", show_alert=True)
            return
        
        if game.state != "waiting":
            await query.answer("❌ بازی در حال اجراست!", show_alert=True)
            return
        
        if any(p.user_id == user.id for p in game.players):
            await query.answer("⚠️ شما قبلاً در بازی هستید!", show_alert=True)
            return
        
        if len(game.players) >= 4:
            await query.answer("❌ بازی تکمیل است!", show_alert=True)
            return
        
        player = Player(user.id, user.username, user.first_name)
        
        if game.add_player(player):
            game_manager.user_games[user.id] = game.game_id
            
            # آپدیت پیام بازی
            await update_game_message(context, game)
            
            # ارسال پیام تایید به کاربر
            await send_verification_message(context, user.id, game)
            
            await query.answer("✅ به بازی پیوستید! لطفا عضویت خود را تایید کنید.", show_alert=True)
        else:
            await query.answer("❌ خطا در پیوستن به بازی!", show_alert=True)
    
    elif data.startswith("verify_check_"):
        game_id = data[13:]
        game = game_manager.get_game(game_id)
        
        if not game:
            await query.answer("❌ بازی یافت نشد!", show_alert=True)
            return
        
        player = game.get_player(user.id)
        if not player:
            await query.answer("❌ شما در این بازی نیستید!", show_alert=True)
            return
        
        # بررسی عضویت
        success, message = await verify_player_membership(context, user.id, game)
        
        if success:
            # تایید شد
            await query.answer("✅ عضویت شما تایید شد!", show_alert=True)
            
            # آپدیت پیام به کاربر
            try:
                await query.edit_message_text(
                    text=f"✅ {user.first_name} عزیز، عضویت شما تایید شد!\n\n"
                         f"🎮 حالا می‌توانید در بازی شرکت کنید.\n"
                         f"🔢 کد بازی: {game.game_id[-6:]}",
                    reply_markup=None
                )
            except:
                pass
        else:
            # هنوز عضو نیست
            await query.answer("❌ هنوز عضو کانال نیستید!", show_alert=True)
            
            # دکمه‌های جدید
            channel = REQUIRED_CHANNEL.lstrip('@')
            keyboard = [
                [
                    InlineKeyboardButton("📢 جوین شو در کانال", url=f"https://t.me/{channel}"),
                    InlineKeyboardButton("✅ بررسی عضویت من", callback_data=f"verify_check_{game.game_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                await query.edit_message_text(
                    text=f"❌ {message}\n\n"
                         f"⚠️ لطفا:\n"
                         f"۱. به کانال {REQUIRED_CHANNEL} بپیوندید\n"
                         f"۲. سپس روی 'بررسی عضویت من' کلیک کنید",
                    reply_markup=reply_markup
                )
            except:
                pass
    
    elif data.startswith("start_"):
        game_id = data[6:]
        game = game_manager.get_game(game_id)
        
        if not game:
            await query.answer("❌ بازی یافت نشد!", show_alert=True)
            return
        
        if user.id != game.creator_id:
            await query.answer("❌ فقط سازنده می‌تواند بازی را شروع کند!", show_alert=True)
            return
        
        if game.state != "waiting":
            await query.answer("⚠️ بازی قبلاً شروع شده!", show_alert=True)
            return
        
        if len(game.players) < 4:
            await query.answer(f"❌ باید ۴ نفر باشند! فعلاً {len(game.players)} نفر هستند.", show_alert=True)
            return
        
        # بررسی تایید همه بازیکنان
        not_verified = [p for p in game.players if not p.verified]
        if not_verified:
            names = ", ".join([p.display_name for p in not_verified])
            await query.answer(f"❌ این بازیکنان تایید نشده‌اند: {names}", show_alert=True)
            return
        
        # اگر همه تایید شده‌اند، شروع بازی
        await query.answer("✅ بازی شروع شد!", show_alert=True)
        await query.edit_message_text(
            text=f"🎮 بازی شروع شد!\n\n"
                 f"🔢 کد بازی: {game.game_id[-6:]}\n"
                 f"👥 بازیکنان: {len(game.players)} نفر\n"
                 f"✅ همه بازیکنان تایید شده‌اند"
        )

# ==================== اجرای ربات ====================

def main():
    """تابع اصلی"""
    
    print("🤖 ربات پاسور Railway در حال راه‌اندازی...")
    print(f"📢 کانال اجباری: {REQUIRED_CHANNEL}")
    print("✅ سیستم تایید عضویت فعال")
    
    # ساخت application
    application = Application.builder() \
        .token(TOKEN) \
        .connect_timeout(30.0) \
        .read_timeout(30.0) \
        .write_timeout(30.0) \
        .pool_timeout(30.0) \
        .build()
    
    # اضافه کردن دستورات
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("newgame", new_game_command))
    application.add_handler(CommandHandler("verify", verify_command))
    application.add_handler(CommandHandler("join", join_command))
    
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    print("✅ ربات آماده است!")
    print("🎮 دستور /newgame را در یک گروه امتحان کنید")
    
    # تنظیمات polling
    try:
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            poll_interval=0.5,
            timeout=15,
            close_loop=False
        )
    except Exception as e:
        print(f"❌ خطا در اجرای ربات: {e}")

if __name__ == "__main__":
    main()
