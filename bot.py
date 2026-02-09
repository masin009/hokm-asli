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
        self.state: str = "waiting"  # waiting, checking_members, choosing_trump, playing, finished
        self.message_id: Optional[int] = None
        self.created_at = datetime.now()
        self.player_cards_messages: Dict[int, int] = {}  # user_id -> message_id
    
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
        
        # بررسی عضویت همه در کانال
        if not all(player.is_channel_member for player in self.players):
            return False
        
        self.initialize_deck()
        self.deal_cards()
        self.turn_order = [p.user_id for p in self.players]
        random.shuffle(self.turn_order)  # انتخاب رندوم شروع کننده
        self.current_turn_index = 0
        self.state = "choosing_trump"
        self.trump_chooser_id = self.turn_order[0]  # اولین نفری که نوبتش است خال حکم را انتخاب می‌کند
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
        text = "🎴 بازی پاسور (حکم)\n\n"
        
        if self.state == "waiting":
            text += f"⏳ در انتظار بازیکنان ({len(self.players)}/4)\n\n"
            text += "👥 بازیکنان:\n"
            for i, player in enumerate(self.players, 1):
                member_status = "✅" if player.is_channel_member else "❌"
                text += f"{i}. {player.display_name} {member_status}\n"
            text += f"\n📢 برای بازی باید عضو کانال {REQUIRED_CHANNEL} باشید.\n"
            text += "سازنده بازی: " + (self.get_player(self.creator_id).display_name if self.get_player(self.creator_id) else "?")
        
        elif self.state == "checking_members":
            text += "🔍 بررسی عضویت در کانال...\n\n"
            text += "👥 وضعیت بازیکنان:\n"
            for player in self.players:
                member_status = "✅ عضو است" if player.is_channel_member else "❌ عضو نیست"
                text += f"• {player.display_name}: {member_status}\n"
        
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
        self.user_games: Dict[int, str] = {}
        self.chat_games: Dict[int, str] = {}
    
    def create_game(self, chat_id: int, creator: Player) -> Game:
        game_id = f"hokm_{chat_id}_{int(datetime.now().timestamp())}"
        game = Game(game_id=game_id, chat_id=chat_id, creator_id=creator.user_id)
        game.add_player(creator)
        self.games[game_id] = game
        self.user_games[creator.user_id] = game_id
        self.chat_games[chat_id] = game_id
        return game
    
    def get_game(self, game_id: str) -> Optional[Game]:
        return self.games.get(game_id)
    
    def get_chat_game(self, chat_id: int) -> Optional[Game]:
        game_id = self.chat_games.get(chat_id)
        if game_id:
            return self.get_game(game_id)
        return None
    
    def delete_game(self, game_id: str):
        game = self.games.get(game_id)
        if game:
            for player in game.players:
                self.user_games.pop(player.user_id, None)
            self.chat_games.pop(game.chat_id, None)
            del self.games[game_id]
    
    def get_player_game(self, user_id: int) -> Optional[Game]:
        game_id = self.user_games.get(user_id)
        if game_id:
            return self.get_game(game_id)
        return None

game_manager = GameManager()

# ==================== توابع کمکی ====================

async def check_channel_membership(context: CallbackContext, user_id: int) -> bool:
    """بررسی عضویت کاربر در کانال"""
    try:
        chat_member = await context.bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        return chat_member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"خطا در بررسی عضویت: {e}")
        return False

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

# ==================== دستورات ربات ====================

def start_command(update: Update, context: CallbackContext):
    """دستور شروع"""
    user = update.effective_user
    update.message.reply_text(
        f"سلام {user.first_name}! 👋\n\n"
        "🎴 به ربات بازی پاسور (حکم) خوش آمدید!\n\n"
        "📋 دستورات:\n"
        "/newgame - ایجاد بازی جدید (فقط سازنده)\n"
        "/join - پیوستن به بازی\n"
        "/startgame - شروع بازی (فقط سازنده)\n"
        "/stop - توقف بازی (فقط سازنده)\n"
        "/leave - ترک بازی\n"
        "/status - وضعیت بازی\n"
        "/rules - قوانین بازی\n\n"
        f"📢 برای بازی باید عضو کانال {REQUIRED_CHANNEL} باشید."
    )

def new_game_command(update: Update, context: CallbackContext):
    """ایجاد بازی جدید - فقط سازنده"""
    chat_id = update.effective_chat.id
    
    # بررسی بازی فعال
    existing_game = game_manager.get_chat_game(chat_id)
    if existing_game and existing_game.state != "finished":
        update.message.reply_text("⚠️ یک بازی در حال اجرا در این گروه وجود دارد!")
        return
    
    user = update.effective_user
    
    # بررسی عضویت سازنده در کانال
    async def check_and_create():
        is_member = await check_channel_membership(context, user.id)
        if not is_member:
            update.message.reply_text(
                f"❌ برای ایجاد بازی باید عضو کانال {REQUIRED_CHANNEL} باشید.\n"
                f"لطفا ابتدا به کانال جوین شوید و سپس دوباره امتحان کنید."
            )
            return
        
        # ایجاد بازی جدید
        player = Player(user.id, user.username, user.first_name)
        player.is_channel_member = True
        game = game_manager.create_game(chat_id, player)
        
        keyboard = [
            [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
            [InlineKeyboardButton("✅ بررسی عضویت", callback_data=f"check_{game.game_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = update.message.reply_text(
            game.get_game_info_text(),
            reply_markup=reply_markup
        )
        
        game.message_id = message.message_id
    
    # اجرای غیرهمزمان
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(check_and_create())

def join_command(update: Update, context: CallbackContext):
    """پیوستن به بازی"""
    chat_id = update.effective_chat.id
    game = game_manager.get_chat_game(chat_id)
    
    if not game:
        update.message.reply_text("❌ هیچ بازی در انتظاری در این گروه وجود ندارد!")
        return
    
    if game.state != "waiting":
        update.message.reply_text("❌ بازی در حال اجراست! نمی‌توانید الان بپیوندید.")
        return
    
    user = update.effective_user
    
    # بررسی حضور قبلی
    if any(p.user_id == user.id for p in game.players):
        update.message.reply_text("✅ شما قبلاً در این بازی هستید!")
        return
    
    if len(game.players) >= 4:
        update.message.reply_text("❌ بازی تکمیل است!")
        return
    
    # بررسی عضویت در کانال
    async def check_and_join():
        is_member = await check_channel_membership(context, user.id)
        
        player = Player(user.id, user.username, user.first_name)
        player.is_channel_member = is_member
        
        if game.add_player(player):
            game_manager.user_games[user.id] = game.game_id
            
            keyboard = [
                [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
                [InlineKeyboardButton("✅ بررسی عضویت", callback_data=f"check_{game.game_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=game.message_id,
                    text=game.get_game_info_text(),
                    reply_markup=reply_markup
                )
            except:
                pass
            
            if is_member:
                update.message.reply_text(f"✅ {user.first_name} به بازی پیوست!")
            else:
                update.message.reply_text(
                    f"⚠️ {user.first_name} به بازی پیوست اما عضو کانال {REQUIRED_CHANNEL} نیست!\n"
                    f"لطفا به کانال جوین شوید تا بتوانید بازی کنید."
                )
        else:
            update.message.reply_text("❌ خطا در پیوستن به بازی!")
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(check_and_join())

def startgame_command(update: Update, context: CallbackContext):
    """شروع بازی - فقط سازنده"""
    chat_id = update.effective_chat.id
    game = game_manager.get_chat_game(chat_id)
    
    if not game:
        update.message.reply_text("❌ هیچ بازی فعالی در این گروه وجود ندارد!")
        return
    
    user = update.effective_user
    
    # بررسی اینکه آیا کاربر سازنده بازی است
    if user.id != game.creator_id:
        update.message.reply_text("❌ فقط سازنده بازی می‌تواند بازی را شروع کند!")
        return
    
    if game.state != "waiting":
        update.message.reply_text("⚠️ بازی قبلاً شروع شده است!")
        return
    
    if len(game.players) < 4:
        update.message.reply_text("❌ برای شروع بازی باید ۴ بازیکن وجود داشته باشد!")
        return
    
    # بررسی عضویت همه بازیکنان
    async def check_all_and_start():
        all_members = True
        for player in game.players:
            player.is_channel_member = await check_channel_membership(context, player.user_id)
            if not player.is_channel_member:
                all_members = False
        
        if not all_members:
            game.state = "checking_members"
            
            keyboard = [
                [InlineKeyboardButton("🔄 بررسی مجدد عضویت", callback_data=f"check_{game.game_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=game.message_id,
                    text=game.get_game_info_text(),
                    reply_markup=reply_markup
                )
            except:
                pass
            
            update.message.reply_text(
                f"⚠️ برخی بازیکنان عضو کانال {REQUIRED_CHANNEL} نیستند!\n"
                f"لطفا همه بازیکنان ابتدا به کانال جوین شوند."
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
                    chat_id=chat_id,
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
                             f"کارت‌های خود را برای آماده شدن بررسی کنید.",
                        reply_markup=cards_keyboard
                    )
                    game.player_cards_messages[chooser.user_id] = message.message_id
                except:
                    context.bot.send_message(
                        chat_id=chat_id,
                        text=f"⚠️ {chooser.display_name}، لطفا به ربات پیام خصوصی بدهید: @{context.bot.username}"
                    )
        else:
            update.message.reply_text("❌ خطا در شروع بازی!")
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(check_all_and_start())

def stop_command(update: Update, context: CallbackContext):
    """توقف بازی - فقط سازنده"""
    chat_id = update.effective_chat.id
    game = game_manager.get_chat_game(chat_id)
    
    if not game:
        update.message.reply_text("❌ هیچ بازی فعالی برای توقف وجود ندارد.")
        return
    
    user = update.effective_user
    
    # بررسی اینکه آیا کاربر سازنده بازی است
    if user.id != game.creator_id:
        update.message.reply_text("❌ فقط سازنده بازی می‌تواند بازی را متوقف کند!")
        return
    
    game_manager.delete_game(game.game_id)
    update.message.reply_text("🛑 بازی متوقف شد.")

def leave_command(update: Update, context: CallbackContext):
    """ترک بازی"""
    chat_id = update.effective_chat.id
    game = game_manager.get_chat_game(chat_id)
    
    if not game:
        update.message.reply_text("❌ شما در هیچ بازی فعالی نیستید.")
        return
    
    user = update.effective_user
    
    # سازنده نمی‌تواند بازی را ترک کند (باید بازی را متوقف کند)
    if user.id == game.creator_id:
        update.message.reply_text("⚠️ شما سازنده بازی هستید. برای حذف بازی از /stop استفاده کنید.")
        return
    
    if game.state != "waiting":
        update.message.reply_text("❌ بازی در حال اجراست! نمی‌توانید بازی را ترک کنید.")
        return
    
    if game.remove_player(user.id):
        game_manager.user_games.pop(user.id, None)
        
        # آپدیت پیام بازی
        keyboard = [
            [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
            [InlineKeyboardButton("✅ بررسی عضویت", callback_data=f"check_{game.game_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=game.message_id,
                text=game.get_game_info_text(),
                reply_markup=reply_markup
            )
        except:
            pass
        
        update.message.reply_text("✅ شما از بازی خارج شدید.")
    else:
        update.message.reply_text("❌ شما در این بازی نیستید!")

def status_command(update: Update, context: CallbackContext):
    """نمایش وضعیت بازی"""
    chat_id = update.effective_chat.id
    game = game_manager.get_chat_game(chat_id)
    
    if not game:
        update.message.reply_text("📭 هیچ بازی فعالی در این گروه وجود ندارد.")
        return
    
    update.message.reply_text(game.get_game_info_text())

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
        f"📢 شرط بازی: عضویت در کانال {REQUIRED_CHANNEL}"
    )
    
    update.message.reply_text(rules_text)

def callback_handler(update: Update, context: CallbackContext):
    """مدیریت کلیک‌ها"""
    query = update.callback_query
    query.answer()
    
    user = query.from_user
    data = query.data
    
    if data.startswith("join_"):
        game_id = data[5:]
        game = game_manager.get_game(game_id)
        
        if not game:
            query.edit_message_text("❌ بازی یافت نشد!")
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
        
        # بررسی عضویت در کانال
        async def check_and_join_async():
            is_member = await check_channel_membership(context, user.id)
            
            player = Player(user.id, user.username, user.first_name)
            player.is_channel_member = is_member
            
            if game.add_player(player):
                game_manager.user_games[user.id] = game.game_id
                
                keyboard = [
                    [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
                    [InlineKeyboardButton("✅ بررسی عضویت", callback_data=f"check_{game.game_id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                query.edit_message_text(
                    text=game.get_game_info_text(),
                    reply_markup=reply_markup
                )
                
                if not is_member:
                    query.message.reply_text(
                        f"⚠️ {user.first_name} به بازی پیوست اما عضو کانال {REQUIRED_CHANNEL} نیست!\n"
                        f"لطفا به کانال جوین شوید تا بتوانید بازی کنید."
                    )
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(check_and_join_async())
    
    elif data.startswith("check_"):
        game_id = data[6:]
        game = game_manager.get_game(game_id)
        
        if not game:
            return
        
        # بررسی عضویت همه بازیکنان
        async def check_all_async():
            all_members = True
            for player in game.players:
                player.is_channel_member = await check_channel_membership(context, player.user_id)
                if not player.is_channel_member:
                    all_members = False
            
            keyboard = [
                [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
                [InlineKeyboardButton("✅ بررسی عضویت", callback_data=f"check_{game.game_id}")]
            ]
            
            if game.state == "checking_members" and all_members:
                keyboard = [
                    [InlineKeyboardButton("▶️ شروع بازی", callback_data=f"start_{game.game_id}")]
                ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            query.edit_message_text(
                text=game.get_game_info_text(),
                reply_markup=reply_markup
            )
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(check_all_async())
    
    elif data.startswith("start_"):
        game_id = data[6:]
        game = game_manager.get_game(game_id)
        
        if not game:
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
            
            query.edit_message_text(
                text=game.get_game_info_text(),
                reply_markup=reply_markup
            )
            
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
                except:
                    query.message.reply_text(
                        f"⚠️ {chooser.display_name}، لطفا به ربات پیام خصوصی بدهید: @{context.bot.username}"
                    )
    
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
                return
            
            if game.choose_trump(user.id, suit):
                # آپدیت پیام اصلی
                query.edit_message_text(
                    text=game.get_game_info_text(),
                    reply_markup=None
                )
                
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
                    except:
                        query.message.reply_text(
                            f"⚠️ {current_player.display_name}، لطفا به ربات پیام خصوصی بدهید: @{context.bot.username}"
                        )
    
    elif data.startswith("play_"):
        parts = data.split("_")
        if len(parts) >= 3:
            game_id = parts[1]
            card_index = int(parts[2])
            game = game_manager.get_game(game_id)
            
            if not game:
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
            except:
                pass
            
            # اگر بازی تمام شد
            if game.state == "finished":
                # نمایش نتایج نهایی
                results_text = "🏆 بازی تمام شد!\n\nنتایج نهایی:\n\n"
                sorted_players = sorted(game.players, key=lambda p: p.tricks_won, reverse=True)
                for i, player in enumerate(sorted_players):
                    medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "🎯"
                    results_text += f"{medal} {player.display_name}: {player.tricks_won} دست\n"
                
                context.bot.send_message(
                    chat_id=game.chat_id,
                    text=results_text
                )
                
                # حذف بازی
                game_manager.delete_game(game.game_id)
                return
            
            # اگر دور کامل شد
            if game.current_round.cards_played and len(game.current_round.cards_played) == 0:
                winner = game.get_player(game.rounds[-1].winner_id)
                if winner:
                    context.bot.send_message(
                        chat_id=game.chat_id,
                        text=f"🎉 برنده این دست: {winner.display_name}"
                    )
            
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
                except:
                    context.bot.send_message(
                        chat_id=game.chat_id,
                        text=f"⚠️ {current_player.display_name}، لطفا به ربات پیام خصوصی بدهید: @{context.bot.username}"
                    )

def error_handler(update: Update, context: CallbackContext):
    """مدیریت خطا"""
    logger.error(f"خطا: {context.error}")

# ==================== اجرای ربات ====================

def main():
    """تابع اصلی"""
    
    print("🤖 ربات پاسور Railway در حال راه‌اندازی...")
    print(f"📢 کانال اجباری: {REQUIRED_CHANNEL}")
    
    # ساخت Updater
    updater = Updater(TOKEN, use_context=True)
    dispatcher = updater.dispatcher
    
    # اضافه کردن دستورات
    dispatcher.add_handler(CommandHandler("start", start_command))
    dispatcher.add_handler(CommandHandler("newgame", new_game_command))
    dispatcher.add_handler(CommandHandler("join", join_command))
    dispatcher.add_handler(CommandHandler("startgame", startgame_command))
    dispatcher.add_handler(CommandHandler("stop", stop_command))
    dispatcher.add_handler(CommandHandler("leave", leave_command))
    dispatcher.add_handler(CommandHandler("status", status_command))
    dispatcher.add_handler(CommandHandler("rules", rules_command))
    
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
