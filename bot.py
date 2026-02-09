import os
import random
import logging
import asyncio
from enum import Enum
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    CallbackQueryHandler,
    CallbackContext,
    MessageHandler,
    Filters
)

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

# کانال اجباری
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
        self.verified: bool = False  # تایید شده برای بازی
        self.last_checked: datetime = datetime.now()
    
    @property
    def display_name(self):
        if self.username:
            return f"@{self.username}"
        return self.first_name or f"User_{self.user_id}"

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
        self.state: str = "waiting"  # waiting, choosing_trump, playing, finished
        self.message_id: Optional[int] = None
        self.created_at = datetime.now()
        self.player_cards_messages: Dict[int, int] = {}  # user_id -> message_id
        self.verification_messages: Dict[int, int] = {}  # user_id -> message_id (پیام تایید)
    
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
    
    def initialize_deck(self):
        self.deck = []
        for suit in [Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS, Suit.SPADES]:
            for rank in RANKS.values():
                self.deck.append(Card(suit, rank))
        random.shuffle(self.deck)
    
    def deal_cards(self):
        # توزیع ۱۳ کارت به هر بازیکن
        cards_per_player = 13
        for i, player in enumerate(self.players):
            start = i * cards_per_player
            end = start + cards_per_player
            player.cards = self.deck[start:end]
            # مرتب کردن کارت‌ها
            player.cards.sort(key=lambda c: (c.suit.value, c.rank.value))
    
    def start_game(self):
        if len(self.players) < 4:
            return False
        
        # بررسی تایید همه بازیکنان
        if not all(player.verified for player in self.players):
            return False
        
        self.initialize_deck()
        self.deal_cards()
        self.turn_order = [p.user_id for p in self.players]
        random.shuffle(self.turn_order)  # انتخاب رندوم شروع کننده
        self.current_turn_index = 0
        self.state = "choosing_trump"
        self.trump_chooser_id = self.turn_order[0]
        return True
    
    def choose_trump(self, user_id: int, suit: Suit) -> bool:
        if self.state != "choosing_trump" or user_id != self.trump_chooser_id:
            return False
        
        self.trump_suit = suit
        self.state = "playing"
        return True
    
    def can_play_card(self, player: Player, card: Card, is_first_card: bool = False) -> bool:
        """بررسی قانونی بودن حرکت"""
        if not self.current_round.cards_played:
            # اولین کارت دور
            return True
        
        first_card = list(self.current_round.cards_played.values())[0]
        leading_suit = first_card.suit
        
        # اگر بازیکن همخال دارد، باید همخال بیاورد
        if card.suit == leading_suit:
            return True
        
        # بررسی اینکه آیا بازیکن همخال دارد یا نه
        has_leading_suit = any(c.suit == leading_suit for c in player.cards)
        
        if has_leading_suit:
            # اگر همخال دارد اما همخال نمی‌آورد، غیرقانونی است
            return False
        
        # اگر همخال ندارد، می‌تواند هر کارتی بیاورد
        return True
    
    def play_card(self, user_id: int, card_index: int) -> Tuple[bool, Optional[Card], Optional[str]]:
        if self.state != "playing":
            return False, None, "بازی در حال اجرا نیست"
        
        current_player_id = self.turn_order[self.current_turn_index]
        if user_id != current_player_id:
            return False, None, "نوبت شما نیست"
        
        player = self.get_player(user_id)
        if not player or card_index >= len(player.cards):
            return False, None, "کارت نامعتبر"
        
        card = player.cards[card_index]
        
        # بررسی قانونی بودن حرکت
        is_first_card = len(self.current_round.cards_played) == 0
        if not self.can_play_card(player, card, is_first_card):
            # لیست کارت‌های قانونی
            valid_cards = [c for c in player.cards if self.can_play_card(player, c, is_first_card)]
            if valid_cards:
                return False, None, f"باید همخال بیاورید. کارت‌های مجاز: {', '.join(c.persian_name for c in valid_cards)}"
            else:
                return False, None, "خطا در بررسی کارت"
        
        # حذف کارت از دست بازیکن
        player.cards.pop(card_index)
        
        if len(self.current_round.cards_played) == 0:
            self.current_round.starting_player_id = user_id
        
        self.current_round.cards_played[user_id] = card
        
        # حرکت به بازیکن بعدی
        self.current_turn_index = (self.current_turn_index + 1) % len(self.players)
        
        # اگر دور کامل شد
        if self.current_round.is_complete(len(self.players)):
            winner_id = self.get_round_winner()
            self.current_round.winner_id = winner_id
            
            winner = self.get_player(winner_id)
            if winner:
                winner.tricks_won += 1
            
            # ذخیره دور و شروع دور جدید
            self.rounds.append(self.current_round)
            self.current_round = Round()
            
            # بازیکن برنده دور بعدی را شروع می‌کند
            winner_index = self.turn_order.index(winner_id)
            self.current_turn_index = winner_index
            
            # اگر بازی تمام شد (همه کارت‌ها بازی شدند)
            if all(len(p.cards) == 0 for p in self.players):
                self.state = "finished"
                self.calculate_scores()
        
        return True, card, None
    
    def get_round_winner(self) -> Optional[int]:
        if not self.current_round.cards_played:
            return None
        
        first_player_id = self.current_round.starting_player_id
        first_card = self.current_round.cards_played[first_player_id]
        leading_suit = first_card.suit
        
        winning_player_id = first_player_id
        winning_card = first_card
        
        for player_id, card in self.current_round.cards_played.items():
            # اولویت با خال حکم
            if card.suit == self.trump_suit:
                if winning_card.suit != self.trump_suit:
                    winning_player_id = player_id
                    winning_card = card
                elif card.value > winning_card.value:
                    winning_player_id = player_id
                    winning_card = card
            elif card.suit == leading_suit and winning_card.suit == leading_suit:
                if card.value > winning_card.value:
                    winning_player_id = player_id
                    winning_card = card
            elif card.suit == leading_suit and winning_card.suit != self.trump_suit:
                winning_player_id = player_id
                winning_card = card
        
        return winning_player_id
    
    def calculate_scores(self):
        for player in self.players:
            player.score = player.tricks_won
    
    def get_player(self, user_id: int) -> Optional[Player]:
        for player in self.players:
            if player.user_id == user_id:
                return player
        return None
    
    def get_player_index(self, user_id: int) -> Optional[int]:
        for i, player in enumerate(self.players):
            if player.user_id == user_id:
                return i
        return None
    
    def get_game_info_text(self) -> str:
        text = f"🎴 بازی پاسور (حکم) - کد: {self.game_id[-6:]}\n\n"
        
        if self.state == "waiting":
            text += f"⏳ در انتظار بازیکنان ({len(self.players)}/4)\n\n"
            text += "👥 بازیکنان:\n"
            for i, player in enumerate(self.players, 1):
                status = "✅ تایید شده" if player.verified else "⏳ در انتظار تایید"
                text += f"{i}. {player.display_name} - {status}\n"
            text += f"\n📢 برای بازی باید عضو کانال {REQUIRED_CHANNEL} باشید.\n"
            text += f"🎮 سازنده: {self.get_player(self.creator_id).display_name if self.get_player(self.creator_id) else '?'}"
        
        elif self.state == "choosing_trump":
            chooser = self.get_player(self.trump_chooser_id)
            text += "👑 انتخاب خال حکم\n\n"
            text += f"بازیکن انتخاب کننده: {chooser.display_name if chooser else '?'}\n"
            text += f"دست: {len(self.rounds) + 1}/13\n\n"
            text += "لطفا خال حکم را انتخاب کنید:"
        
        elif self.state == "playing":
            current_player = self.get_player(self.turn_order[self.current_turn_index])
            text += f"🎮 دور: {len(self.rounds) + 1}/13\n"
            text += f"🃏 خال حکم: {self.trump_suit.value if self.trump_suit else '?'} {self.trump_suit.persian_name if self.trump_suit else ''}\n"
            text += f"🎯 نوبت: {current_player.display_name if current_player else '?'}\n\n"
            
            text += "📊 دست‌های برده شده:\n"
            for player in self.players:
                text += f"• {player.display_name}: {player.tricks_won} دست\n"
            
            if self.current_round.cards_played:
                text += "\n🎴 کارت‌های این دور:\n"
                for player_id, card in self.current_round.cards_played.items():
                    player = self.get_player(player_id)
                    text += f"• {player.display_name if player else '?'}: {card.persian_name}\n"
        
        elif self.state == "finished":
            text += "🏆 بازی تمام شد!\n\n"
            text += "نتایج نهایی:\n"
            sorted_players = sorted(self.players, key=lambda p: p.tricks_won, reverse=True)
            for i, player in enumerate(sorted_players):
                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "🎯"
                text += f"{medal} {player.display_name}: {player.tricks_won} دست\n"
        
        return text

# ==================== مدیریت بازی‌ها ====================

class GameManager:
    def __init__(self):
        self.games: Dict[str, Game] = {}
        self.user_games: Dict[int, str] = {}  # user_id -> game_id
        self.chat_games: Dict[int, List[str]] = defaultdict(list)  # chat_id -> list of game_ids
        self.pending_verifications: Dict[int, str] = {}  # user_id -> game_id (کاربرانی که منتظر تایید هستند)
    
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
    
    def get_chat_games(self, chat_id: int) -> List[Game]:
        """دریافت تمام بازی‌های فعال یک چت"""
        game_ids = self.chat_games.get(chat_id, [])
        games = []
        for game_id in game_ids[:]:  # از کپی لیست استفاده می‌کنیم
            game = self.games.get(game_id)
            if game:
                games.append(game)
            else:
                # حذف بازی‌های حذف شده
                game_ids.remove(game_id)
        return games
    
    def get_player_game(self, user_id: int) -> Optional[Game]:
        game_id = self.user_games.get(user_id)
        if game_id:
            return self.get_game(game_id)
        return None
    
    def delete_game(self, game_id: str):
        """حذف بازی"""
        game = self.games.get(game_id)
        if game:
            # حذف از لیست بازیکنان
            for player in game.players:
                self.user_games.pop(player.user_id, None)
                self.pending_verifications.pop(player.user_id, None)
            
            # حذف از لیست بازی‌های چت
            if game.chat_id in self.chat_games:
                if game_id in self.chat_games[game.chat_id]:
                    self.chat_games[game.chat_id].remove(game_id)
            
            # حذف بازی
            del self.games[game_id]
            return True
        return False
    
    def add_pending_verification(self, user_id: int, game_id: str):
        """افزودن کاربر به لیست انتظار تایید"""
        self.pending_verifications[user_id] = game_id
    
    def remove_pending_verification(self, user_id: int):
        """حذف کاربر از لیست انتظار تایید"""
        self.pending_verifications.pop(user_id, None)

game_manager = GameManager()

# ==================== توابع کمکی ====================

def create_cards_keyboard(player: Player, game: Game) -> InlineKeyboardMarkup:
    """ایجاد کیبورد شیشه‌ای برای کارت‌های بازیکن"""
    # گروه‌بندی کارت‌ها بر اساس خال
    cards_by_suit = defaultdict(list)
    for i, card in enumerate(player.cards):
        cards_by_suit[card.suit].append((i, card))
    
    keyboard = []
    for suit in [Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS, Suit.SPADES]:
        row = []
        cards = cards_by_suit.get(suit, [])
        if cards:
            for card_index, card in cards:
                # رنگ‌بندی متفاوت برای خال حکم
                if suit == game.trump_suit:
                    emoji = "👑"
                else:
                    emoji = suit.value
                
                button_text = f"{emoji} {card.rank.symbol}"
                callback_data = f"play_{game.game_id}_{card_index}"
                row.append(InlineKeyboardButton(button_text, callback_data=callback_data))
            
            if row:
                keyboard.append(row)
    
    return InlineKeyboardMarkup(keyboard)

async def send_verification_message(context: CallbackContext, user_id: int, game: Game) -> Optional[int]:
    """ارسال پیام تایید عضویت به کاربر و بازگشت message_id"""
    try:
        keyboard = [
            [
                InlineKeyboardButton("📢 جوین شو در کانال", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"),
                InlineKeyboardButton("✅ بررسی عضویت من", callback_data=f"check_{game.game_id}_{user_id}")
            ],
            [
                InlineKeyboardButton("🔄 تازه سازی", callback_data=f"refresh_{game.game_id}_{user_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = await context.bot.send_message(
            chat_id=user_id,
            text=f"🔐 تایید عضویت برای بازی پاسور\n\n"
                 f"کانال اجباری: {REQUIRED_CHANNEL}\n"
                 f"کد بازی: {game.game_id[-6:]}\n\n"
                 f"📋 مراحل:\n"
                 f"۱. روی 'جوین شو در کانال' کلیک کنید\n"
                 f"۲. به کانال بپیوندید (Join)\n"
                 f"۳. روی 'بررسی عضویت من' کلیک کنید\n"
                 f"۴. اگر مشکل بود، 'تازه سازی' را بزنید\n\n"
                 f"⚠️ بدون تایید عضویت نمی‌توانید بازی کنید.\n"
                 f"🔄 سیستم به طور خودکار هر ۲ دقیقه بررسی می‌کند.",
            reply_markup=reply_markup
        )
        
        # ذخیره کاربر در لیست انتظار تایید
        game_manager.add_pending_verification(user_id, game.game_id)
        
        return message.message_id
    except Exception as e:
        logger.error(f"خطا در ارسال پیام تایید به کاربر {user_id}: {e}")
        
        # اگر کاربر با ربات چت نکرده باشد
        if "bot was blocked by the user" in str(e).lower() or "chat not found" in str(e).lower():
            logger.warning(f"کاربر {user_id} با ربات چت نکرده یا ربات را بلاک کرده")
            
            # سعی در ارسال پیام در گروه
            try:
                await context.bot.send_message(
                    chat_id=game.chat_id,
                    text=f"⚠️ {game.get_player(user_id).display_name if game.get_player(user_id) else 'کاربر'}، لطفا به ربات پیام خصوصی بدهید: @{context.bot.username}"
                )
            except:
                pass
        
        return None

async def check_channel_membership(context: CallbackContext, user_id: int) -> bool:
    """بررسی عضویت کاربر در کانال"""
    try:
        # بررسی عضویت کاربر
        chat_member = await context.bot.get_chat_member(
            chat_id=REQUIRED_CHANNEL,
            user_id=user_id
        )
        
        # وضعیت‌های مجاز
        allowed_statuses = ['member', 'administrator', 'creator', 'restricted']
        
        # اگر restricted است، بررسی کنیم آیا می‌تواند پیام ببیند یا نه
        if chat_member.status == 'restricted':
            is_member = chat_member.is_member
        else:
            is_member = chat_member.status in allowed_statuses
        
        logger.info(f"بررسی عضویت کاربر {user_id} در {REQUIRED_CHANNEL}: {chat_member.status} -> {is_member}")
        return is_member
    except Exception as e:
        logger.error(f"خطا در بررسی عضویت کاربر {user_id}: {e}")
        # اگر خطا خورد، بررسی کنیم شاید کانال اشتباه است
        if "Chat not found" in str(e):
            logger.error(f"کانال {REQUIRED_CHANNEL} یافت نشد!")
        elif "User not found" in str(e):
            logger.error(f"کاربر {user_id} در کانال یافت نشد!")
        elif "Not enough rights" in str(e):
            logger.error(f"ربات دسترسی کافی در کانال {REQUIRED_CHANNEL} ندارد!")
        return False

async def verify_player_membership(context: CallbackContext, user_id: int, game: Game) -> Tuple[bool, str]:
    """بررسی و تایید عضویت یک بازیکن - بازگشت وضعیت و پیام"""
    try:
        # بررسی عضویت
        is_member = await check_channel_membership(context, user_id)
        
        player = game.get_player(user_id)
        if not player:
            return False, "بازیکن یافت نشد"
        
        if is_member:
            player.verified = True
            player.is_channel_member = True
            player.last_checked = datetime.now()
            
            # حذف از لیست انتظار
            game_manager.remove_pending_verification(user_id)
            
            # حذف پیام تایید قبلی اگر وجود دارد
            if user_id in game.verification_messages:
                try:
                    await context.bot.delete_message(
                        chat_id=user_id,
                        message_id=game.verification_messages[user_id]
                    )
                except:
                    pass
                game.verification_messages.pop(user_id, None)
            
            logger.info(f"✅ عضویت کاربر {user_id} تایید شد")
            return True, "عضویت شما تایید شد! حالا می‌توانید بازی کنید."
        else:
            player.verified = False
            player.is_channel_member = False
            
            logger.info(f"❌ کاربر {user_id} عضو کانال نیست")
            
            # ارسال پیام جدید تایید
            message_id = await send_verification_message(context, user_id, game)
            if message_id:
                game.verification_messages[user_id] = message_id
            
            return False, f"شما عضو کانال {REQUIRED_CHANNEL} نیستید!\nلطفا ابتدا به کانال جوین شوید سپس دوباره بررسی کنید."
            
    except Exception as e:
        logger.error(f"خطا در تایید عضویت کاربر {user_id}: {e}")
        return False, f"خطا در بررسی عضویت: {str(e)}"

async def periodic_membership_check(context: CallbackContext):
    """بررسی دوره‌ی عضویت همه بازیکنان"""
    try:
        logger.info("🔍 شروع بررسی دوره‌ای عضویت بازیکنان...")
        
        for game_id, game in list(game_manager.games.items()):
            if game.state == "waiting":
                for player in game.players[:]:  # از کپی لیست استفاده می‌کنیم
                    # فقط بازیکنان تایید شده را بررسی کنیم
                    if player.verified:
                        # بررسی مجدد عضویت (اگر بیش از 5 دقیقه از آخرین بررسی گذشته)
                        time_diff = (datetime.now() - player.last_checked).total_seconds()
                        if time_diff > 300:  # هر 5 دقیقه
                            is_member = await check_channel_membership(context, player.user_id)
                            
                            if not is_member:
                                # اگر قبلاً تایید شده بود ولی الان عضو نیست
                                player.verified = False
                                player.is_channel_member = False
                                
                                # ارسال پیام تایید جدید
                                message_id = await send_verification_message(context, player.user_id, game)
                                if message_id:
                                    game.verification_messages[player.user_id] = message_id
                                
                                logger.info(f"⚠️ کاربر {player.user_id} از کانال خارج شده، نیاز به تایید مجدد")
                                
                                # آپدیت پیام بازی
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
                                except:
                                    pass
                            
                            player.last_checked = datetime.now()
    except Exception as e:
        logger.error(f"خطا در بررسی دوره‌ای عضویت: {e}")

# ==================== دستورات ربات ====================

def start_command(update: Update, context: CallbackContext):
    """دستور شروع"""
    user = update.effective_user
    update.message.reply_text(
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

def new_game_command(update: Update, context: CallbackContext):
    """ایجاد بازی جدید - هرکس می‌تواند بازی خودش را ایجاد کند"""
    chat_id = update.effective_chat.id
    user = update.effective_user
    
    # ایجاد بازیکن (سازنده)
    player = Player(user.id, user.username, user.first_name)
    
    # ایجاد بازی جدید
    game = game_manager.create_game(chat_id, player)
    
    # دکمه‌های بازی
    keyboard = [
        [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
        [
            InlineKeyboardButton("▶️ شروع بازی", callback_data=f"start_{game.game_id}"),
            InlineKeyboardButton("❌ بستن بازی", callback_data=f"close_{game.game_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = update.message.reply_text(
        game.get_game_info_text(),
        reply_markup=reply_markup
    )
    
    game.message_id = message.message_id
    
    update.message.reply_text(
        f"✅ بازی جدید ایجاد شد!\n"
        f"🎮 شما سازنده این بازی هستید.\n"
        f"🔢 کد بازی: {game.game_id[-6:]}\n\n"
        f"دیگران می‌توانند با کلیک روی 'پیوستن به بازی' به این بازی بپیوندند."
    )

def join_command(update: Update, context: CallbackContext):
    """پیوستن به بازی"""
    chat_id = update.effective_chat.id
    games = game_manager.get_chat_games(chat_id)
    
    if not games:
        update.message.reply_text(
            "❌ هیچ بازی فعالی در این گروه وجود ندارد!\n\n"
            "برای ایجاد بازی جدید از دستور /newgame استفاده کنید."
        )
        return
    
    # نمایش لیست بازی‌های فعال
    text = "🎮 بازی‌های فعال در این گروه:\n\n"
    for i, game in enumerate(games, 1):
        if game.state == "waiting":
            text += f"{i}. کد: {game.game_id[-6:]}\n"
            text += f"   سازنده: {game.get_player(game.creator_id).display_name if game.get_player(game.creator_id) else '?'}\n"
            text += f"   بازیکنان: {len(game.players)}/4\n"
            text += f"   برای پیوستن: /join_{game.game_id[-6:]}\n\n"
    
    text += "برای پیوستن به یک بازی، روی دکمه مربوطه کلیک کنید یا کد بازی را وارد کنید."
    
    # ایجاد دکمه‌ها برای بازی‌ها
    keyboard = []
    for game in games:
        if game.state == "waiting":
            keyboard.append([
                InlineKeyboardButton(
                    f"🎮 پیوستن به بازی {game.game_id[-6:]}",
                    callback_data=f"join_{game.game_id}"
                )
            ])
    
    if keyboard:
        keyboard.append([
            InlineKeyboardButton("🆕 ایجاد بازی جدید", callback_data="create_new_game")
        ])
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(text, reply_markup=reply_markup)
    else:
        update.message.reply_text(
            "❌ هیچ بازی در انتظاری وجود ندارد!\n\n"
            "برای ایجاد بازی جدید از /newgame استفاده کنید."
        )

def verify_command(update: Update, context: CallbackContext):
    """بررسی عضویت کاربر"""
    user = update.effective_user
    
    # بررسی اینکه کاربر در کدام بازی است
    game = game_manager.get_player_game(user.id)
    
    if not game:
        update.message.reply_text(
            "❌ شما در هیچ بازی فعالی نیستید!\n\n"
            "برای ایجاد بازی از /newgame استفاده کنید یا با /join به بازی بپیوندید."
        )
        return
    
    # بررسی عضویت
    async def check_and_update():
        success, message = await verify_player_membership(context, user.id, game)
        
        player = game.get_player(user.id)
        if player:
            if success:
                # آپدیت پیام بازی
                keyboard = [
                    [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
                    [
                        InlineKeyboardButton("▶️ شروع بازی", callback_data=f"start_{game.game_id}"),
                        InlineKeyboardButton("❌ بستن بازی", callback_data=f"close_{game.game_id}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                try:
                    await context.bot.edit_message_text(
                        chat_id=game.chat_id,
                        message_id=game.message_id,
                        text=game.get_game_info_text(),
                        reply_markup=reply_markup
                    )
                except:
                    pass
                
                update.message.reply_text(
                    f"✅ عضویت شما تایید شد!\n"
                    f"🎮 حالا می‌توانید در بازی شرکت کنید.\n"
                    f"🔢 کد بازی: {game.game_id[-6:]}"
                )
            else:
                update.message.reply_text(
                    f"❌ {message}\n\n"
                    f"کانال اجباری: {REQUIRED_CHANNEL}\n\n"
                    f"لطفا ابتدا به کانال زیر جوین شوید:\n"
                    f"{REQUIRED_CHANNEL}\n\n"
                    f"سپس دوباره /verify را بزنید."
                )
        else:
            update.message.reply_text("❌ شما در این بازی نیستید!")
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(check_and_update())

def startgame_command(update: Update, context: CallbackContext):
    """شروع بازی - فقط سازنده"""
    user = update.effective_user
    game = game_manager.get_player_game(user.id)
    
    if not game:
        update.message.reply_text(
            "❌ شما در هیچ بازی فعالی نیستید!\n\n"
            "برای ایجاد بازی از /newgame استفاده کنید یا با /join به بازی بپیوندید."
        )
        return
    
    # بررسی اینکه آیا کاربر سازنده بازی است
    if user.id != game.creator_id:
        update.message.reply_text("❌ فقط سازنده بازی می‌تواند بازی را شروع کند!")
        return
    
    if game.state != "waiting":
        update.message.reply_text("⚠️ بازی قبلاً شروع شده است!")
        return
    
    if len(game.players) < 4:
        update.message.reply_text(f"❌ فقط {len(game.players)}/4 بازیکن وجود دارد! باید ۴ نفر کامل باشند.")
        return
    
    # بررسی تایید همه بازیکنان
    not_verified_players = [p for p in game.players if not p.verified]
    
    if not_verified_players:
        # لیست بازیکنان تایید نشده
        not_verified_names = []
        for player in not_verified_players:
            not_verified_names.append(player.display_name)
            
            # اگر پیام تایید قبلاً ارسال نشده، ارسال کن
            if player.user_id not in game.verification_messages:
                async def send_verification():
                    message_id = await send_verification_message(context, player.user_id, game)
                    if message_id:
                        game.verification_messages[player.user_id] = message_id
                
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(send_verification())
        
        update.message.reply_text(
            f"⚠️ برخی بازیکنان عضویت خود را تایید نکرده‌اند!\n\n"
            f"بازیکنان زیر باید عضویت خود را تایید کنند:\n"
            f"{chr(10).join(['• ' + name for name in not_verified_names])}\n\n"
            f"راه‌های تایید:\n"
            f"۱. از دستور /verify استفاده کنند\n"
            f"۲. یا روی دکمه 'بررسی عضویت' در پیوی کلیک کنند\n\n"
            f"پس از تایید همه، دوباره /startgame را بزنید."
        )
        return
    
    # شروع بازی
    if game.start_game():
        # نمایش پیام انتخاب خال حکم
        chooser = game.get_player(game.trump_chooser_id)
        
        keyboard = [
            [
                InlineKeyboardButton("♥️ دل", callback_data=f"trump_{game.game_id}_hearts"),
                InlineKeyboardButton("♦️ خشت", callback_data=f"trump_{game.game_id}_diamonds")
            ],
            [
                InlineKeyboardButton("♣️ پیک", callback_data=f"trump_{game.game_id}_clubs"),
                InlineKeyboardButton("♠️ گیشنیز", callback_data=f"trump_{game.game_id}_spades")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            context.bot.edit_message_text(
                chat_id=game.chat_id,
                message_id=game.message_id,
                text=game.get_game_info_text(),
                reply_markup=reply_markup
            )
        except:
            pass
        
        # ارسال کارت‌ها به بازیکن انتخاب کننده خال حکم
        if chooser:
            try:
                cards_keyboard = create_cards_keyboard(chooser, game)
                message = context.bot.send_message(
                    chat_id=chooser.user_id,
                    text=f"🎴 کارت‌های شما:\n\n"
                         f"🃏 شما باید خال حکم را انتخاب کنید.\n"
                         f"خال حکم: (هنوز انتخاب نشده)\n\n"
                         f"کارت‌های خود را برای آماده شدن بررسی کنید.",
                    reply_markup=cards_keyboard
                )
                game.player_cards_messages[chooser.user_id] = message.message_id
            except:
                context.bot.send_message(
                    chat_id=game.chat_id,
                    text=f"⚠️ {chooser.display_name}، لطفا به ربات پیام خصوصی بدهید: @{context.bot.username}"
                )
        
        update.message.reply_text(
            f"✅ بازی شروع شد!\n"
            f"🎮 اولین بازیکن ({chooser.display_name if chooser else '?'}) باید خال حکم را انتخاب کند."
        )
    else:
        update.message.reply_text("❌ خطا در شروع بازی!")

def close_command(update: Update, context: CallbackContext):
    """بستن بازی - فقط سازنده"""
    user = update.effective_user
    game = game_manager.get_player_game(user.id)
    
    if not game:
        update.message.reply_text("❌ شما در هیچ بازی فعالی نیستید.")
        return
    
    # بررسی اینکه آیا کاربر سازنده بازی است
    if user.id != game.creator_id:
        update.message.reply_text("❌ فقط سازنده بازی می‌تواند بازی را ببندد!")
        return
    
    game_manager.delete_game(game.game_id)
    update.message.reply_text("🛑 بازی بسته شد.")

def leave_command(update: Update, context: CallbackContext):
    """ترک بازی"""
    user = update.effective_user
    game = game_manager.get_player_game(user.id)
    
    if not game:
        update.message.reply_text("❌ شما در هیچ بازی فعالی نیستید.")
        return
    
    # سازنده نمی‌تواند بازی را ترک کند (باید بازی را ببندد)
    if user.id == game.creator_id:
        update.message.reply_text("⚠️ شما سازنده بازی هستید. برای بستن بازی از /close استفاده کنید.")
        return
    
    if game.state != "waiting":
        update.message.reply_text("❌ بازی در حال اجراست! نمی‌توانید بازی را ترک کنید.")
        return
    
    if game.remove_player(user.id):
        game_manager.user_games.pop(user.id, None)
        game_manager.remove_pending_verification(user.id)
        
        # حذف پیام تایید
        if user.id in game.verification_messages:
            try:
                context.bot.delete_message(
                    chat_id=user.id,
                    message_id=game.verification_messages[user.id]
                )
            except:
                pass
            game.verification_messages.pop(user.id, None)
        
        # آپدیت پیام بازی
        keyboard = [
            [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
            [
                InlineKeyboardButton("▶️ شروع بازی", callback_data=f"start_{game.game_id}"),
                InlineKeyboardButton("❌ بستن بازی", callback_data=f"close_{game.game_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            context.bot.edit_message_text(
                chat_id=game.chat_id,
                message_id=game.message_id,
                text=game.get_game_info_text(),
                reply_markup=reply_markup
            )
        except:
            pass
        
        update.message.reply_text("✅ شما از بازی خارج شدید.")
    else:
        update.message.reply_text("❌ شما در این بازی نیستید!")

def games_command(update: Update, context: CallbackContext):
    """نمایش بازی‌های فعال"""
    chat_id = update.effective_chat.id
    games = game_manager.get_chat_games(chat_id)
    
    if not games:
        update.message.reply_text("📭 هیچ بازی فعالی در این گروه وجود ندارد.")
        return
    
    text = f"🎮 بازی‌های فعال در این گروه: {len(games)}\n\n"
    
    for i, game in enumerate(games, 1):
        status_map = {
            "waiting": "⏳ در انتظار",
            "choosing_trump": "👑 انتخاب خال",
            "playing": "🎮 در حال بازی",
            "finished": "🏆 تمام شده"
        }
        
        text += f"{i}. کد بازی: {game.game_id[-6:]}\n"
        text += f"   وضعیت: {status_map.get(game.state, game.state)}\n"
        text += f"   سازنده: {game.get_player(game.creator_id).display_name if game.get_player(game.creator_id) else '?'}\n"
        text += f"   بازیکنان: {len(game.players)}/4\n"
        
        if game.state == "waiting":
            text += f"   برای پیوستن: /join_{game.game_id[-6:]}\n"
        
        text += "\n"
    
    update.message.reply_text(text)

def status_command(update: Update, context: CallbackContext):
    """نمایش وضعیت بازی فعلی کاربر"""
    user = update.effective_user
    game = game_manager.get_player_game(user.id)
    
    if not game:
        update.message.reply_text(
            "📭 شما در هیچ بازی فعالی نیستید.\n\n"
            "برای ایجاد بازی از /newgame استفاده کنید یا با /join به بازی بپیوندید."
        )
        return
    
    text = game.get_game_info_text()
    text += f"\n\n🎮 سازنده بازی: {game.get_player(game.creator_id).display_name if game.get_player(game.creator_id) else '?'}"
    text += f"\n🔢 کد بازی: {game.game_id[-6:]}"
    
    update.message.reply_text(text)

def rules_command(update: Update, context: CallbackContext):
    """قوانین بازی"""
    rules_text = (
        "📖 قوانین کامل بازی پاسور (حکم):\n\n"
        "🎯 هدف بازی:\n"
        "بردیدن بیشترین تعداد دست (تریک) در ۱۳ دور\n\n"
        "👥 تعداد بازیکنان:\n"
        "۴ نفر (الزامی)\n\n"
        "🃏 مراحل بازی:\n"
        "۱. هر بازیکن ۱۳ کارت دریافت می‌کند\n"
        "۲. یک خال به عنوان خال حکم انتخاب می‌شود\n"
        "۳. اولین بازیکن (به صورت رندوم) یک کارت بازی می‌کند\n"
        "۴. بازیکنان بعدی باید همخال بیاورند\n"
        "۵. اگر همخال نداشته باشند، می‌توانند هر کارتی بگذارند\n"
        "۶. برنده دست، بالاترین کارت خال حکم را می‌برد\n"
        "۷. اگر خال حکم بازی نشده باشد، برنده بالاترین کارت خال اول است\n"
        "۸. برنده دست بعدی را شروع می‌کند\n\n"
        "📋 قوانین ویژه:\n"
        "• خال حکم از همه خال‌ها قوی‌تر است\n"
        "• باید حتماً همخال آورد (اگر داشته باشید)\n"
        "• آس (A) بالا‌ترین و ۲ پایین‌ترین کارت است\n"
        "• ترتیب قدرت: آس > شاه > بیبی > سرباز > ۱۰ > ... > ۲\n\n"
        "⚙️ مدیریت بازی:\n"
        "• هرکس می‌تواند با /newgame بازی جدید ایجاد کند\n"
        "• هر گروه می‌تواند چندین بازی همزمان داشته باشد\n"
        "• فقط سازنده می‌تواند بازی را شروع کند (/startgame)\n"
        "• فقط سازنده می‌تواند بازی را ببندد (/close)\n"
        f"• برای بازی باید عضو کانال {REQUIRED_CHANNEL} باشید\n"
        "• برای تایید عضویت از /verify استفاده کنید"
    )
    
    update.message.reply_text(rules_text)

def callback_handler(update: Update, context: CallbackContext):
    """مدیریت کلیک‌ها - نسخه sync شده"""
    query = update.callback_query
    query.answer()
    
    user = query.from_user
    data = query.data
    
    if data == "create_new_game":
        # ایجاد بازی جدید از طریق دکمه
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
            query.edit_message_text(
                text=game.get_game_info_text(),
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"خطا در ایجاد بازی: {e}")
            query.edit_message_text("❌ خطا در ایجاد بازی!")
            return
        
        try:
            context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ بازی جدید ایجاد شد!\n🎮 شما سازنده این بازی هستید.\n🔢 کد بازی: {game.game_id[-6:]}"
            )
        except:
            pass
    
    elif data.startswith("join_"):
        game_id = data[5:]
        game = game_manager.get_game(game_id)
        
        if not game:
            query.answer("بازی یافت نشد!", show_alert=True)
            return
        
        if game.state != "waiting":
            query.answer("بازی در حال اجراست!", show_alert=True)
            return
        
        if any(p.user_id == user.id for p in game.players):
            query.answer("شما قبلاً در بازی هستید!", show_alert=True)
            return
        
        if len(game.players) >= 4:
            query.answer("بازی تکمیل است!", show_alert=True)
            return
        
        player = Player(user.id, user.username, user.first_name)
        
        if game.add_player(player):
            game_manager.user_games[user.id] = game.game_id
            
            keyboard = [
                [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
                [
                    InlineKeyboardButton("▶️ شروع بازی", callback_data=f"start_{game.game_id}"),
                    InlineKeyboardButton("❌ بستن بازی", callback_data=f"close_{game.game_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                query.edit_message_text(
                    text=game.get_game_info_text(),
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"خطا در آپدیت پیام بازی: {e}")
            
            # ارسال پیام تایید عضویت به کاربر جدید
            try:
                # استفاده از asyncio برای توابع async
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                message_id = loop.run_until_complete(send_verification_message(context, user.id, game))
                if message_id:
                    game.verification_messages[user.id] = message_id
            except Exception as e:
                logger.error(f"خطا در ارسال پیام تایید: {e}")
                query.answer("خطا در ارسال پیام تایید!", show_alert=True)
                return
            
            query.answer("✅ به بازی پیوستید! لطفا عضویت خود را تایید کنید.", show_alert=True)
        else:
            query.answer("خطا در پیوستن به بازی!", show_alert=True)
    
    elif data.startswith("check_"):
        # بررسی عضویت کاربر
        parts = data.split("_")
        if len(parts) >= 3:
            game_id = parts[1]
            user_id = int(parts[2])
            game = game_manager.get_game(game_id)
            
            if not game:
                query.answer("بازی یافت نشد!", show_alert=True)
                return
            
            # بررسی اینکه آیا این کاربر همان کلیک کننده است
            if user.id != user_id:
                query.answer("این دکمه برای شما نیست!", show_alert=True)
                return
            
            player = game.get_player(user_id)
            if not player:
                query.answer("شما در این بازی نیستید!", show_alert=True)
                return
            
            # بررسی عضویت با استفاده از asyncio
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                success, message = loop.run_until_complete(verify_player_membership(context, user_id, game))
                
                if success:
                    # آپدیت پیام بازی در گروه
                    keyboard = [
                        [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
                        [
                            InlineKeyboardButton("▶️ شروع بازی", callback_data=f"start_{game.game_id}"),
                            InlineKeyboardButton("❌ بستن بازی", callback_data=f"close_{game.game_id}")
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    try:
                        context.bot.edit_message_text(
                            chat_id=game.chat_id,
                            message_id=game.message_id,
                            text=game.get_game_info_text(),
                            reply_markup=reply_markup
                        )
                    except Exception as e:
                        logger.error(f"خطا در آپدیت پیام گروه: {e}")
                    
                    query.answer("✅ عضویت شما تایید شد!", show_alert=True)
                    
                    # به روزرسانی پیام تایید
                    try:
                        query.edit_message_text(
                            text=f"✅ عضویت شما تایید شد!\n\n"
                                 f"کانال: {REQUIRED_CHANNEL}\n"
                                 f"کد بازی: {game.game_id[-6:]}\n\n"
                                 f"🎮 حالا می‌توانید در بازی شرکت کنید.",
                            reply_markup=None
                        )
                    except:
                        pass
                else:
                    # اگر عضو نیست، پیام جدید بفرست
                    keyboard = [
                        [
                            InlineKeyboardButton("📢 جوین شو در کانال", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"),
                            InlineKeyboardButton("✅ بررسی عضویت من", callback_data=f"check_{game.game_id}_{user_id}")
                        ],
                        [
                            InlineKeyboardButton("🔄 تازه سازی", callback_data=f"refresh_{game.game_id}_{user_id}")
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    try:
                        query.edit_message_text(
                            text=f"❌ {message}\n\n"
                                 f"کانال اجباری: {REQUIRED_CHANNEL}\n"
                                 f"کد بازی: {game.game_id[-6:]}\n\n"
                                 f"لطفا:\n"
                                 f"۱. روی دکمه 'جوین شو در کانال' کلیک کنید\n"
                                 f"۲. به کانال بپیوندید\n"
                                 f"۳. سپس روی 'بررسی عضویت من' کلیک کنید\n\n"
                                 f"⚠️ بدون تایید عضویت نمی‌توانید بازی کنید.",
                            reply_markup=reply_markup
                        )
                    except:
                        pass
                    
                    query.answer("❌ عضویت تایید نشد! لطفا به کانال بپیوندید.", show_alert=True)
                    
            except Exception as e:
                logger.error(f"خطا در بررسی عضویت: {e}")
                query.answer("❌ خطا در بررسی عضویت!", show_alert=True)
    
    elif data.startswith("refresh_"):
        parts = data.split("_")
        if len(parts) >= 3:
            game_id = parts[1]
            user_id = int(parts[2])
            game = game_manager.get_game(game_id)
            
            if not game:
                query.answer("بازی یافت نشد!", show_alert=True)
                return
            
            if user.id != user_id:
                query.answer("این دکمه برای شما نیست!", show_alert=True)
                return
            
            # فقط پیام را تازه کنیم
            keyboard = [
                [
                    InlineKeyboardButton("📢 جوین شو در کانال", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"),
                    InlineKeyboardButton("✅ بررسی عضویت من", callback_data=f"check_{game.game_id}_{user_id}")
                ],
                [
                    InlineKeyboardButton("🔄 تازه سازی", callback_data=f"refresh_{game.game_id}_{user_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                query.edit_message_text(
                    text=f"🔄 پیام تازه‌سازی شد!\n\n"
                         f"کانال اجباری: {REQUIRED_CHANNEL}\n"
                         f"کد بازی: {game.game_id[-6:]}\n\n"
                         f"لطفا عضویت خود را بررسی کنید:",
                    reply_markup=reply_markup
                )
            except:
                pass
            
            query.answer("پیام تازه‌سازی شد!", show_alert=False)
    
    elif data.startswith("start_"):
        game_id = data[6:]
        game = game_manager.get_game(game_id)
        
        if not game:
            query.answer("بازی یافت نشد!", show_alert=True)
            return
        
        # بررسی اینکه آیا کاربر سازنده بازی است
        if user.id != game.creator_id:
            query.answer("فقط سازنده بازی می‌تواند شروع کند!", show_alert=True)
            return
        
        # شروع بازی
        if game.start_game():
            # نمایش پیام انتخاب خال حکم
            chooser = game.get_player(game.trump_chooser_id)
            
            keyboard = [
                [
                    InlineKeyboardButton("♥️ دل", callback_data=f"trump_{game.game_id}_hearts"),
                    InlineKeyboardButton("♦️ خشت", callback_data=f"trump_{game.game_id}_diamonds")
                ],
                [
                    InlineKeyboardButton("♣️ پیک", callback_data=f"trump_{game.game_id}_clubs"),
                    InlineKeyboardButton("♠️ گیشنیز", callback_data=f"trump_{game.game_id}_spades")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                query.edit_message_text(
                    text=game.get_game_info_text(),
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"خطا در نمایش انتخاب خال: {e}")
                query.answer("خطا در شروع بازی!", show_alert=True)
                return
            
            # ارسال کارت‌ها به بازیکن انتخاب کننده خال حکم
            if chooser:
                try:
                    cards_keyboard = create_cards_keyboard(chooser, game)
                    message = context.bot.send_message(
                        chat_id=chooser.user_id,
                        text=f"🎴 کارت‌های شما:\n\n"
                             f"🃏 شما باید خال حکم را انتخاب کنید.\n"
                             f"کارت‌های خود را برای آماده شدن بررسی کنید.",
                        reply_markup=cards_keyboard
                    )
                    game.player_cards_messages[chooser.user_id] = message.message_id
                except Exception as e:
                    logger.error(f"خطا در ارسال کارت‌ها: {e}")
                    try:
                        context.bot.send_message(
                            chat_id=game.chat_id,
                            text=f"⚠️ {chooser.display_name}، لطفا به ربات پیام خصوصی بدهید: @{context.bot.username}"
                        )
                    except:
                        pass
        else:
            query.answer("خطا در شروع بازی!", show_alert=True)
    
    elif data.startswith("close_"):
        game_id = data[6:]
        game = game_manager.get_game(game_id)
        
        if not game:
            query.answer("بازی یافت نشد!", show_alert=True)
            return
        
        # بررسی اینکه آیا کاربر سازنده بازی است
        if user.id != game.creator_id:
            query.answer("فقط سازنده بازی می‌تواند بازی را ببندد!", show_alert=True)
            return
        
        game_manager.delete_game(game.game_id)
        try:
            query.edit_message_text("🛑 بازی بسته شد.")
        except:
            pass
    
    elif data.startswith("trump_"):
        parts = data.split("_")
        if len(parts) >= 3:
            game_id = parts[1]
            suit_name = parts[2]
            game = game_manager.get_game(game_id)
            
            if not game:
                return
            
            if game.state != "choosing_trump" or user.id != game.trump_chooser_id:
                query.answer("شما نمی‌توانید خال حکم را انتخاب کنید!", show_alert=True)
                return
            
            suit_map = {
                "hearts": Suit.HEARTS,
                "diamonds": Suit.DIAMONDS,
                "clubs": Suit.CLUBS,
                "spades": Suit.SPADES
            }
            
            suit = suit_map.get(suit_name)
            if not suit:
                query.answer("خال نامعتبر!", show_alert=True)
                return
            
            if game.choose_trump(user.id, suit):
                # آپدیت پیام اصلی
                try:
                    query.edit_message_text(
                        text=game.get_game_info_text(),
                        reply_markup=None
                    )
                except Exception as e:
                    logger.error(f"خطا در آپدیت پیام: {e}")
                
                # حذف پیام کارت‌های قبلی
                if user.id in game.player_cards_messages:
                    try:
                        context.bot.delete_message(
                            chat_id=user.id,
                            message_id=game.player_cards_messages[user.id]
                        )
                    except:
                        pass
                
                # ارسال کارت‌ها به بازیکن اول
                current_player = game.get_player(game.turn_order[game.current_turn_index])
                if current_player:
                    try:
                        cards_keyboard = create_cards_keyboard(current_player, game)
                        message = context.bot.send_message(
                            chat_id=current_player.user_id,
                            text=f"🎴 کارت‌های شما:\n\n"
                                 f"🃏 خال حکم: {game.trump_suit.value} {game.trump_suit.persian_name}\n"
                                 f"🎯 نوبت شماست! یک کارت انتخاب کنید:",
                            reply_markup=cards_keyboard
                        )
                        game.player_cards_messages[current_player.user_id] = message.message_id
                    except Exception as e:
                        logger.error(f"خطا در ارسال کارت‌ها: {e}")
                        try:
                            context.bot.send_message(
                                chat_id=game.chat_id,
                                text=f"⚠️ {current_player.display_name}، لطفا به ربات پیام خصوصی بدهید: @{context.bot.username}"
                            )
                        except:
                            pass
            else:
                query.answer("خطا در انتخاب خال!", show_alert=True)
    
    elif data.startswith("play_"):
        parts = data.split("_")
        if len(parts) >= 3:
            game_id = parts[1]
            card_index = int(parts[2])
            game = game_manager.get_game(game_id)
            
            if not game:
                query.answer("بازی یافت نشد!", show_alert=True)
                return
            
            # بازی کردن کارت
            success, card, error_message = game.play_card(user.id, card_index)
            
            if not success:
                query.answer(error_message or "حرکت نامعتبر!", show_alert=True)
                return
            
            # حذف پیام کارت‌های قبلی
            if user.id in game.player_cards_messages:
                try:
                    context.bot.delete_message(
                        chat_id=user.id,
                        message_id=game.player_cards_messages[user.id]
                    )
                except:
                    pass
            
            query.answer(f"کارت بازی شد: {card.persian_name}")
            
            # آپدیت پیام اصلی بازی
            try:
                context.bot.edit_message_text(
                    chat_id=game.chat_id,
                    message_id=game.message_id,
                    text=game.get_game_info_text(),
                    reply_markup=None
                )
            except Exception as e:
                logger.error(f"خطا در آپدیت پیام بازی: {e}")
            
            # اگر بازی تمام شد
            if game.state == "finished":
                # نمایش نتایج نهایی
                results_text = "🏆 بازی تمام شد!\n\nنتایج نهایی:\n\n"
                sorted_players = sorted(game.players, key=lambda p: p.tricks_won, reverse=True)
                for i, player in enumerate(sorted_players):
                    medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "🎯"
                    results_text += f"{medal} {player.display_name}: {player.tricks_won} دست\n"
                
                try:
                    context.bot.send_message(
                        chat_id=game.chat_id,
                        text=results_text
                    )
                except:
                    pass
                
                # حذف بازی
                game_manager.delete_game(game.game_id)
                return
            
            # اگر دور کامل شد
            if game.current_round.cards_played and len(game.current_round.cards_played) == 0:
                winner = game.get_player(game.rounds[-1].winner_id)
                if winner:
                    try:
                        context.bot.send_message(
                            chat_id=game.chat_id,
                            text=f"🎉 برنده این دست: {winner.display_name}"
                        )
                    except:
                        pass
            
            # ارسال کارت‌ها به بازیکن بعدی
            current_player = game.get_player(game.turn_order[game.current_turn_index])
            if current_player:
                try:
                    cards_keyboard = create_cards_keyboard(current_player, game)
                    message = context.bot.send_message(
                        chat_id=current_player.user_id,
                        text=f"🎴 کارت‌های شما:\n\n"
                             f"🃏 خال حکم: {game.trump_suit.value} {game.trump_suit.persian_name}\n"
                             f"🎯 نوبت شماست! یک کارت انتخاب کنید:",
                        reply_markup=cards_keyboard
                    )
                    game.player_cards_messages[current_player.user_id] = message.message_id
                except Exception as e:
                    logger.error(f"خطا در ارسال کارت‌ها: {e}")
                    try:
                        context.bot.send_message(
                            chat_id=game.chat_id,
                            text=f"⚠️ {current_player.display_name}، لطفا به ربات پیام خصوصی بدهید: @{context.bot.username}"
                        )
                    except:
                        pass

def error_handler(update: Update, context: CallbackContext):
    """مدیریت خطا"""
    logger.error(f"خطا: {context.error}")

def run_async_task(func, *args):
    """اجرای تابع async در sync"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(func(*args))
    finally:
        loop.close()

def periodic_membership_check_sync(context: CallbackContext):
    """نسخه sync برای بررسی دوره‌ای"""
    run_async_task(periodic_membership_check, context)

# ==================== اجرای ربات ====================

def main():
    """تابع اصلی"""
    
    print("🤖 ربات پاسور Railway در حال راه‌اندازی...")
    print(f"📢 کانال اجباری: {REQUIRED_CHANNEL}")
    print("✅ سیستم تایید عضویت اتوماتیک فعال")
    print("✅ سیستم بررسی دوره‌ای عضویت فعال (هر 5 دقیقه)")
    print("🎮 چندین بازی همزمان در یک گروه")
    print("⚡ سازنده: کسی که /newgame را می‌زند")
    
    # ساخت Updater
    updater = Updater(TOKEN, use_context=True)
    dispatcher = updater.dispatcher
    
    # اضافه کردن دستورات
    dispatcher.add_handler(CommandHandler("start", start_command))
    dispatcher.add_handler(CommandHandler("newgame", new_game_command))
    dispatcher.add_handler(CommandHandler("join", join_command))
    dispatcher.add_handler(CommandHandler("startgame", startgame_command))
    dispatcher.add_handler(CommandHandler("close", close_command))
    dispatcher.add_handler(CommandHandler("leave", leave_command))
    dispatcher.add_handler(CommandHandler("games", games_command))
    dispatcher.add_handler(CommandHandler("status", status_command))
    dispatcher.add_handler(CommandHandler("verify", verify_command))
    dispatcher.add_handler(CommandHandler("rules", rules_command))
    
    # هندلر برای join با کد بازی
    dispatcher.add_handler(MessageHandler(Filters.regex(r'^/join_[A-Za-z0-9]{6}$'), join_command))
    
    # اضافه کردن handler برای callback
    dispatcher.add_handler(CallbackQueryHandler(callback_handler))
    
    # اضافه کردن handler خطا
    dispatcher.add_error_handler(error_handler)
    
    # تنظیم Job برای بررسی دوره‌ای عضویت (هر 5 دقیقه)
    jq = updater.job_queue
    if jq:
        # بررسی اولیه 30 ثانیه بعد از شروع
        jq.run_once(periodic_membership_check_sync, when=30)
        # بررسی دوره‌ای هر 5 دقیقه
        jq.run_repeating(periodic_membership_check_sync, interval=300, first=60)
        print("✅ سیستم بررسی دوره‌ای عضویت فعال شد (هر 5 دقیقه)")
    
    print("✅ ربات آماده است!")
    print("🎮 دستور /newgame را در یک گروه امتحان کنید")
    
    # شروع ربات
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
