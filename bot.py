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
from telegram.error import TelegramError, BadRequest

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
        self.has_started_bot: bool = False  # آیا ربات را استارت کرده؟
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
        self.verification_messages: Dict[int, int] = {}
        self.player_cards_messages: Dict[int, int] = {}  # پیام کارت‌های هر بازیکن
    
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
    
    def initialize_deck(self):
        """ایجاد و ترکیب کارت‌ها"""
        self.deck = []
        for suit in [Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS, Suit.SPADES]:
            for rank in RANKS.values():
                self.deck.append(Card(suit, rank))
        random.shuffle(self.deck)
    
    def deal_cards(self):
        """توزیع کارت‌ها"""
        cards_per_player = 13
        for i, player in enumerate(self.players):
            start = i * cards_per_player
            end = start + cards_per_player
            player.cards = self.deck[start:end]
            # مرتب کردن کارت‌ها
            player.cards.sort(key=lambda c: (c.suit.value, c.rank.value))
    
    def start_game(self):
        """شروع بازی"""
        if len(self.players) < 4:
            return False
        
        if not all(player.verified for player in self.players):
            return False
        
        self.initialize_deck()
        self.deal_cards()
        self.turn_order = [p.user_id for p in self.players]
        random.shuffle(self.turn_order)
        self.current_turn_index = 0
        self.state = "choosing_trump"
        self.trump_chooser_id = self.turn_order[0]
        return True
    
    def choose_trump(self, user_id: int, suit: Suit) -> bool:
        """انتخاب خال حکم"""
        if self.state != "choosing_trump" or user_id != self.trump_chooser_id:
            return False
        
        self.trump_suit = suit
        self.state = "playing"
        return True
    
    def create_cards_keyboard(self, player_id: int) -> Optional[InlineKeyboardMarkup]:
        """ایجاد کیبورد کارت‌های بازیکن"""
        player = self.get_player(player_id)
        if not player or not player.cards:
            return None
        
        # گروه‌بندی کارت‌ها بر اساس خال
        cards_by_suit = defaultdict(list)
        for i, card in enumerate(player.cards):
            cards_by_suit[card.suit].append((i, card))
        
        # ایجاد دکمه‌ها
        keyboard = []
        
        # اول خال دل
        if Suit.HEARTS in cards_by_suit:
            row = []
            for card_idx, card in cards_by_suit[Suit.HEARTS]:
                row.append(InlineKeyboardButton(
                    f"{card.rank.symbol}{card.suit.value}",
                    callback_data=f"play_{self.game_id}_{card_idx}"
                ))
            keyboard.append(row)
        
        # خال خشت
        if Suit.DIAMONDS in cards_by_suit:
            row = []
            for card_idx, card in cards_by_suit[Suit.DIAMONDS]:
                row.append(InlineKeyboardButton(
                    f"{card.rank.symbol}{card.suit.value}",
                    callback_data=f"play_{self.game_id}_{card_idx}"
                ))
            keyboard.append(row)
        
        # خال پیک
        if Suit.CLUBS in cards_by_suit:
            row = []
            for card_idx, card in cards_by_suit[Suit.CLUBS]:
                row.append(InlineKeyboardButton(
                    f"{card.rank.symbol}{card.suit.value}",
                    callback_data=f"play_{self.game_id}_{card_idx}"
                ))
            keyboard.append(row)
        
        # خال گیشنیز
        if Suit.SPADES in cards_by_suit:
            row = []
            for card_idx, card in cards_by_suit[Suit.SPADES]:
                row.append(InlineKeyboardButton(
                    f"{card.rank.symbol}{card.suit.value}",
                    callback_data=f"play_{self.game_id}_{card_idx}"
                ))
            keyboard.append(row)
        
        return InlineKeyboardMarkup(keyboard)
    
    def can_play_card(self, player: Player, card: Card, is_first_card: bool = False) -> bool:
        """بررسی قانونی بودن حرکت"""
        if not self.current_round.cards_played:
            return True
        
        first_card = list(self.current_round.cards_played.values())[0]
        leading_suit = first_card.suit
        
        if card.suit == leading_suit:
            return True
        
        has_leading_suit = any(c.suit == leading_suit for c in player.cards)
        
        if has_leading_suit:
            return False
        
        return True
    
    def play_card(self, user_id: int, card_index: int) -> Tuple[bool, Optional[Card], Optional[str]]:
        """بازی کردن یک کارت"""
        if self.state != "playing":
            return False, None, "بازی در حال اجرا نیست"
        
        current_player_id = self.turn_order[self.current_turn_index]
        if user_id != current_player_id:
            return False, None, "نوبت شما نیست"
        
        player = self.get_player(user_id)
        if not player or card_index >= len(player.cards):
            return False, None, "کارت نامعتبر"
        
        card = player.cards[card_index]
        
        is_first_card = len(self.current_round.cards_played) == 0
        if not self.can_play_card(player, card, is_first_card):
            valid_cards = [c for c in player.cards if self.can_play_card(player, c, is_first_card)]
            if valid_cards:
                return False, None, f"باید همخال بیاورید. کارت‌های مجاز: {', '.join(c.persian_name for c in valid_cards)}"
            else:
                return False, None, "خطا در بررسی کارت"
        
        player.cards.pop(card_index)
        
        if len(self.current_round.cards_played) == 0:
            self.current_round.starting_player_id = user_id
        
        self.current_round.cards_played[user_id] = card
        self.current_turn_index = (self.current_turn_index + 1) % len(self.players)
        
        if self.current_round.is_complete(len(self.players)):
            winner_id = self.get_round_winner()
            self.current_round.winner_id = winner_id
            
            winner = self.get_player(winner_id)
            if winner:
                winner.tricks_won += 1
            
            self.rounds.append(self.current_round)
            self.current_round = Round()
            
            winner_index = self.turn_order.index(winner_id)
            self.current_turn_index = winner_index
            
            if all(len(p.cards) == 0 for p in self.players):
                self.state = "finished"
                self.calculate_scores()
        
        return True, card, None
    
    def get_round_winner(self) -> Optional[int]:
        """پیدا کردن برنده دور"""
        if not self.current_round.cards_played:
            return None
        
        first_player_id = self.current_round.starting_player_id
        first_card = self.current_round.cards_played[first_player_id]
        leading_suit = first_card.suit
        
        winning_player_id = first_player_id
        winning_card = first_card
        
        for player_id, card in self.current_round.cards_played.items():
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
        """محاسبه امتیازها"""
        for player in self.players:
            player.score = player.tricks_won
    
    def get_game_info_text(self) -> str:
        """متن اطلاعات بازی"""
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
        self.user_started_bot: Dict[int, bool] = {}  # کاربرانی که ربات را استارت کرده‌اند
    
    def create_game(self, chat_id: int, creator: Player) -> Optional[Game]:
        """ایجاد بازی جدید - فقط در گروه"""
        if chat_id > 0:  # اگر چت خصوصی است
            return None
        
        game_id = f"hokm_{chat_id}_{int(datetime.now().timestamp())}"
        game = Game(game_id=game_id, chat_id=chat_id, creator_id=creator.user_id)
        
        creator.verified = True
        creator.is_channel_member = True
        creator.has_started_bot = True  # سازنده حتماً استارت کرده
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
    
    def mark_user_started(self, user_id: int):
        """علامت گذاری کاربر به عنوان استارت شده"""
        self.user_started_bot[user_id] = True
    
    def has_user_started_bot(self, user_id: int) -> bool:
        """آیا کاربر ربات را استارت کرده؟"""
        return self.user_started_bot.get(user_id, False)

game_manager = GameManager()

# ==================== تایید عضویت ====================

async def check_channel_membership(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Tuple[bool, str]:
    """بررسی عضویت کاربر در کانال"""
    try:
        channel = REQUIRED_CHANNEL.lstrip('@')
        
        chat_member = await context.bot.get_chat_member(
            chat_id=f"@{channel}",
            user_id=user_id
        )
        
        if chat_member.status in ['member', 'administrator', 'creator']:
            return True, "عضویت تایید شد"
        elif chat_member.status == 'restricted':
            if hasattr(chat_member, 'is_member') and chat_member.is_member:
                return True, "عضویت تایید شد"
        
        return False, "شما عضو کانال نیستید"
        
    except Exception as e:
        error_msg = str(e).lower()
        
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
        
        game.verification_messages[user_id] = message.message_id
        
        return message.message_id
        
    except Exception as e:
        logger.error(f"خطا در ارسال پیام تایید: {e}")
        return None

async def verify_player_membership(context: ContextTypes.DEFAULT_TYPE, user_id: int, game: Game) -> Tuple[bool, str]:
    """بررسی و تایید عضویت یک بازیکن"""
    try:
        is_member, message = await check_channel_membership(context, user_id)
        
        if is_member:
            game.update_verification_status(user_id, True)
            
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
            
            await update_game_message(context, game)
            
            return True, "✅ عضویت شما تایید شد! حالا می‌توانید بازی کنید."
        else:
            if user_id not in game.verification_messages:
                await send_verification_message(context, user_id, game)
            
            return False, f"❌ {message}\n\nلطفا به کانال {REQUIRED_CHANNEL} بپیوندید."
            
    except Exception as e:
        logger.error(f"خطا در تایید عضویت: {e}")
        return False, f"خطا در بررسی عضویت: {str(e)[:50]}"

async def update_game_message(context: ContextTypes.DEFAULT_TYPE, game: Game):
    """آپدیت پیام اصلی بازی"""
    try:
        if game.state == "waiting":
            keyboard = [
                [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
                [
                    InlineKeyboardButton("▶️ شروع بازی", callback_data=f"start_{game.game_id}"),
                    InlineKeyboardButton("❌ بستن بازی", callback_data=f"close_{game.game_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
        elif game.state == "choosing_trump":
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
        else:
            reply_markup = None
        
        await context.bot.edit_message_text(
            chat_id=game.chat_id,
            message_id=game.message_id,
            text=game.get_game_info_text(),
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"خطا در آپدیت پیام بازی: {e}")

async def send_game_link_to_user(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """ارسال لینک بازی به کاربر برای فوروارد کردن به گروه"""
    keyboard = [
        [InlineKeyboardButton("📋 نحوه اضافه کردن ربات به گروه", callback_data="how_to_add")],
        [InlineKeyboardButton("🎮 ایجاد بازی در گروه", callback_data="create_in_group")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=user_id,
        text="🎴 برای ایجاد بازی پاسور:\n\n"
             "۱. ربات را به گروه مورد نظر اضافه کنید\n"
             "۲. به ربات دسترسی ارسال پیام بدهید\n"
             "۳. سپس در گروه دستور /newgame را وارد کنید\n\n"
             "⚠️ بازی فقط در گروه قابل ایجاد است.",
        reply_markup=reply_markup
    )

# ==================== دستورات ربات ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور شروع"""
    user = update.effective_user
    chat_type = update.effective_chat.type
    
    # علامت گذاری کاربر به عنوان استارت شده
    game_manager.mark_user_started(user.id)
    
    if chat_type == "private":
        await send_game_link_to_user(context, user.id)
    else:
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
    """ایجاد بازی جدید - فقط در گروه"""
    chat_id = update.effective_chat.id
    user = update.effective_user
    
    # بررسی اینکه آیا در گروه است
    if chat_id > 0:  # چت خصوصی
        await update.message.reply_text(
            "❌ بازی فقط در گروه قابل ایجاد است!\n\n"
            "لطفا:\n"
            "۱. این ربات را به گروه مورد نظر اضافه کنید\n"
            "۲. سپس در گروه دستور /newgame را وارد کنید\n\n"
            "برای اطلاعات بیشتر /start را در پیوی ربات بزنید."
        )
        return
    
    # بررسی اینکه کاربر ربات را استارت کرده
    if not game_manager.has_user_started_bot(user.id):
        await update.message.reply_text(
            "❌ ابتدا باید ربات را استارت کنید!\n\n"
            "لطفا:\n"
            "۱. به پیوی ربات بروید (@konkorkhabarbot)\n"
            "۲. دستور /start را بزنید\n"
            "۳. سپس دوباره به گروه برگردید و /newgame را بزنید"
        )
        return
    
    player = Player(user.id, user.username, user.first_name)
    game = game_manager.create_game(chat_id, player)
    
    if not game:
        await update.message.reply_text("❌ خطا در ایجاد بازی!")
        return
    
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
    
    if data == "create_new_game":
        chat_id = query.message.chat.id
        
        if chat_id > 0:  # چت خصوصی
            await query.edit_message_text(
                text="❌ بازی فقط در گروه قابل ایجاد است!\n\n"
                     "لطفا ربات را به گروه اضافه کنید و در گروه دستور /newgame را بزنید."
            )
            return
        
        if not game_manager.has_user_started_bot(user.id):
            await query.answer(
                "❌ ابتدا باید ربات را استارت کنید!\n\n"
                "به پیوی ربات بروید و /start بزنید.",
                show_alert=True
            )
            return
        
        player = Player(user.id, user.username, user.first_name)
        game = game_manager.create_game(chat_id, player)
        
        if not game:
            await query.answer("❌ خطا در ایجاد بازی!", show_alert=True)
            return
        
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
        
        # بررسی اینکه کاربر ربات را استارت کرده
        if not game_manager.has_user_started_bot(user.id):
            await query.answer(
                "❌ ابتدا باید ربات را استارت کنید!\n\n"
                "لطفا:\n"
                "۱. به پیوی ربات بروید\n"
                "۲. دستور /start را بزنید\n"
                "۳. سپس دوباره امتحان کنید",
                show_alert=True
            )
            return
        
        player = Player(user.id, user.username, user.first_name)
        player.has_started_bot = True
        
        if game.add_player(player):
            game_manager.user_games[user.id] = game.game_id
            
            await update_game_message(context, game)
            
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
        
        success, message = await verify_player_membership(context, user.id, game)
        
        if success:
            await query.answer("✅ عضویت شما تایید شد!", show_alert=True)
            
            try:
                await query.edit_message_text(
                    text=f"✅ {user.first_name} عزیز، عضویت شما تایید شد!\n\n"
                         f"🎮 حالا می‌توانید در بازی شرکت کنید.\n"
                         f"🔢 کد بازی: {game.game_id[-6:]}\n\n"
                         f"📌 برای ادامه بازی به گروه برگردید.",
                    reply_markup=None
                )
            except:
                pass
        else:
            await query.answer("❌ هنوز عضو کانال نیستید!", show_alert=True)
            
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
                    text=f"❌ هنوز عضو کانال نیستید!\n\n"
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
        
        not_verified = [p for p in game.players if not p.verified]
        if not_verified:
            names = ", ".join([p.display_name for p in not_verified])
            await query.answer(f"❌ این بازیکنان تایید نشده‌اند: {names}", show_alert=True)
            return
        
        # شروع بازی
        if game.start_game():
            # آپدیت پیام بازی
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
                await query.edit_message_text(
                    text=game.get_game_info_text(),
                    reply_markup=reply_markup
                )
            except:
                pass
            
            await query.answer("✅ بازی شروع شد! اولین بازیکن باید خال حکم را انتخاب کند.", show_alert=True)
            
            # ارسال کارت‌ها به هر بازیکن
            for player in game.players:
                if player.cards:
                    keyboard = game.create_cards_keyboard(player.user_id)
                    if keyboard:
                        try:
                            message = await context.bot.send_message(
                                chat_id=player.user_id,
                                text=f"🎴 کارت‌های شما:\n\n" +
                                     "\n".join([f"{i+1}. {card.persian_name}" for i, card in enumerate(player.cards)]),
                                reply_markup=keyboard
                            )
                            game.player_cards_messages[player.user_id] = message.message_id
                        except:
                            pass
        else:
            await query.answer("❌ خطا در شروع بازی!", show_alert=True)
    
    elif data.startswith("trump_"):
        parts = data.split("_")
        if len(parts) >= 3:
            game_id = parts[1]
            suit_str = parts[2]
            game = game_manager.get_game(game_id)
            
            if not game:
                await query.answer("❌ بازی یافت نشد!", show_alert=True)
                return
            
            if user.id != game.trump_chooser_id:
                await query.answer("❌ شما نمی‌توانید حکم انتخاب کنید!", show_alert=True)
                return
            
            suit_map = {
                'hearts': Suit.HEARTS,
                'diamonds': Suit.DIAMONDS,
                'clubs': Suit.CLUBS,
                'spades': Suit.SPADES
            }
            
            if suit_str not in suit_map:
                await query.answer("❌ خال نامعتبر!", show_alert=True)
                return
            
            suit = suit_map[suit_str]
            
            if game.choose_trump(user.id, suit):
                await query.answer(f"✅ خال حکم انتخاب شد: {suit.value} {suit.persian_name}", show_alert=True)
                
                try:
                    await query.edit_message_text(
                        text=game.get_game_info_text(),
                        reply_markup=None
                    )
                except:
                    pass
                
                # شروع بازی با ارسال کارت‌ها به همه
                for player in game.players:
                    if player.cards:
                        keyboard = game.create_cards_keyboard(player.user_id)
                        if keyboard:
                            try:
                                message = await context.bot.send_message(
                                    chat_id=player.user_id,
                                    text=f"🎴 کارت‌های شما (خال حکم: {suit.value} {suit.persian_name}):\n\n" +
                                         "\n".join([f"{i+1}. {card.persian_name}" for i, card in enumerate(player.cards)]),
                                    reply_markup=keyboard
                                )
                                game.player_cards_messages[player.user_id] = message.message_id
                            except:
                                pass
            else:
                await query.answer("❌ خطا در انتخاب حکم!", show_alert=True)

# ==================== اجرای ربات ====================

def main():
    """تابع اصلی"""
    
    print("🤖 ربات پاسور Railway در حال راه‌اندازی...")
    print(f"📢 کانال اجباری: {REQUIRED_CHANNEL}")
    print("✅ سیستم تایید عضویت فعال")
    
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
