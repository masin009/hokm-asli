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
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)
from telegram.error import TelegramError

# ==================== تنظیمات ====================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    print("❌ توکن یافت نشد!")
    exit(1)

REQUIRED_CHANNEL = "@konkorkhabar"

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
            Suit.CLUBS: "گیشنیز",
            Suit.SPADES: "پیک"
        }
        return names[self]

class Rank:
    def __init__(self, symbol: str, value: int, persian_name: str):
        self.symbol = symbol
        self.value = value
        self.persian_name = persian_name

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
        self.all_cards: List[Card] = []  # همه کارت‌ها
        self.current_cards: List[Card] = []  # کارت‌های فعلی دست
        self.tricks_won: int = 0
        self.verified: bool = False
        self.position: Optional[int] = None
        self.team: Optional[int] = None
        self.has_started_bot: bool = False
    
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
    
    def is_complete(self) -> bool:
        return len(self.cards_played) == 4

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
        self.verification_messages: Dict[int, int] = {}
        self.player_cards_messages: Dict[int, int] = {}
        self.join_requests: Dict[int, str] = {}
        self.first_round_dealt: bool = False  # آیا کارت‌های دور اول داده شده
    
    def add_player(self, player: Player) -> bool:
        if len(self.players) >= 4:
            return False
        if any(p.user_id == player.user_id for p in self.players):
            return False
        
        player.position = len(self.players)
        self.players.append(player)
        
        if len(self.players) == 4:
            self.assign_teams()
        
        return True
    
    def assign_teams(self):
        """تیم‌بندی: بازیکنان روبه‌رو هم یار هستند"""
        for i, player in enumerate(self.players):
            player.team = i % 2
    
    def get_teammate(self, player: Player) -> Optional[Player]:
        if player.team is None:
            return None
        for p in self.players:
            if p.team == player.team and p.user_id != player.user_id:
                return p
        return None
    
    def get_teams_info(self) -> str:
        if len(self.players) < 4:
            return ""
        
        text = "\n🤝 **تیم‌ها:**\n"
        team0 = [p for p in self.players if p.team == 0]
        team1 = [p for p in self.players if p.team == 1]
        
        if team0:
            text += f"• تیم ۱: {team0[0].display_name} و {team0[1].display_name}\n"
        if team1:
            text += f"• تیم ۲: {team1[0].display_name} و {team1[1].display_name}\n"
        
        return text
    
    def get_player(self, user_id: int) -> Optional[Player]:
        for player in self.players:
            if player.user_id == user_id:
                return player
        return None
    
    def initialize_deck(self):
        self.deck = []
        for suit in [Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS, Suit.SPADES]:
            for rank in RANKS.values():
                self.deck.append(Card(suit, rank))
        random.shuffle(self.deck)
    
    def deal_first_round(self):
        """دور اول: فقط ۵ کارت به هر بازیکن بده"""
        cards_per_player = 5
        for i, player in enumerate(self.players):
            start = i * cards_per_player
            end = start + cards_per_player
            player.all_cards = self.deck[start:end]  # ذخیره همه کارت‌ها
            player.current_cards = player.all_cards.copy()  # کارت‌های فعلی
            player.current_cards.sort(key=lambda c: (c.suit.value, c.rank.value))
        
        # 20 کارت اول داده شد، بقیه بعداً
        self.first_round_dealt = True
    
    def deal_remaining_cards(self):
        """بعد از انتخاب حکم: بقیه کارت‌ها رو بده"""
        cards_per_player = 13
        for i, player in enumerate(self.players):
            start = i * cards_per_player
            end = start + cards_per_player
            player.all_cards = self.deck[start:end]
            player.current_cards = player.all_cards.copy()
            player.current_cards.sort(key=lambda c: (c.suit.value, c.rank.value))
    
    def start_game(self):
        if len(self.players) < 4:
            return False
        
        if not all(p.verified for p in self.players):
            return False
        
        self.initialize_deck()
        self.deal_first_round()  # فقط 5 کارت بده
        
        # ترتیب نشستن
        self.turn_order = [p.user_id for p in self.players]
        random.shuffle(self.turn_order)
        self.current_turn_index = 0
        
        self.state = "choosing_trump"
        self.trump_chooser_id = self.turn_order[0]
        return True
    
    def choose_trump(self, user_id: int, suit: Suit) -> bool:
        if self.state != "choosing_trump" or user_id != self.trump_chooser_id:
            return False
        
        self.trump_suit = suit
        self.deal_remaining_cards()  # بقیه کارت‌ها رو بده
        self.state = "playing"
        
        # بازیکن انتخاب کننده حکم شروع کننده است
        self.turn_order = [p.user_id for p in self.players]
        chooser_index = self.turn_order.index(user_id)
        self.current_turn_index = chooser_index
        
        return True
    
    def create_cards_keyboard(self, player_id: int) -> Optional[InlineKeyboardMarkup]:
        player = self.get_player(player_id)
        if not player or not player.current_cards:
            return None
        
        keyboard = []
        row = []
        
        for i, card in enumerate(player.current_cards):
            row.append(InlineKeyboardButton(
                f"{card.rank.symbol}{card.suit.value}",
                callback_data=f"play_card_{self.game_id}_{i}"
            ))
            if len(row) == 4:
                keyboard.append(row)
                row = []
        
        if row:
            keyboard.append(row)
        
        return InlineKeyboardMarkup(keyboard) if keyboard else None
    
    def can_play_card(self, player: Player, card: Card) -> bool:
        if not self.current_round.cards_played:
            return True
        
        first_card = list(self.current_round.cards_played.values())[0]
        leading_suit = first_card.suit
        
        if card.suit == leading_suit:
            return True
        
        has_leading = any(c.suit == leading_suit for c in player.current_cards)
        return not has_leading
    
    def play_card(self, user_id: int, card_index: int) -> Tuple[bool, Optional[Card], Optional[str]]:
        if self.state != "playing":
            return False, None, "بازی در حال اجرا نیست"
        
        current_id = self.turn_order[self.current_turn_index]
        if user_id != current_id:
            return False, None, "نوبت شما نیست"
        
        player = self.get_player(user_id)
        if not player or card_index >= len(player.current_cards):
            return False, None, "کارت نامعتبر"
        
        card = player.current_cards.pop(card_index)
        
        if len(self.current_round.cards_played) == 0:
            self.current_round.starting_player_id = user_id
        
        self.current_round.cards_played[user_id] = card
        self.current_turn_index = (self.current_turn_index + 1) % 4
        
        if self.current_round.is_complete():
            winner_id = self.get_round_winner()
            self.current_round.winner_id = winner_id
            
            winner = self.get_player(winner_id)
            if winner:
                winner.tricks_won += 1
            
            self.rounds.append(self.current_round)
            self.current_round = Round()
            
            winner_index = self.turn_order.index(winner_id)
            self.current_turn_index = winner_index
            
            if all(len(p.current_cards) == 0 for p in self.players):
                self.state = "finished"
        
        return True, card, None
    
    def get_round_winner(self) -> Optional[int]:
        if not self.current_round.cards_played:
            return None
        
        first_id = self.current_round.starting_player_id
        first_card = self.current_round.cards_played[first_id]
        leading_suit = first_card.suit
        
        winner_id = first_id
        winner_card = first_card
        
        for pid, card in self.current_round.cards_played.items():
            if card.suit == self.trump_suit:
                if winner_card.suit != self.trump_suit:
                    winner_id = pid
                    winner_card = card
                elif card.value > winner_card.value:
                    winner_id = pid
                    winner_card = card
            elif card.suit == leading_suit and winner_card.suit == leading_suit:
                if card.value > winner_card.value:
                    winner_id = pid
                    winner_card = card
            elif card.suit == leading_suit and winner_card.suit != self.trump_suit:
                winner_id = pid
                winner_card = card
        
        return winner_id
    
    def get_game_info_text(self) -> str:
        text = f"🎴 بازی پاسور - کد: {self.game_id[-6:]}\n\n"
        
        if self.state == "waiting":
            text += f"⏳ انتظار ({len(self.players)}/4)\n\n👥 بازیکنان:\n"
            for i, p in enumerate(self.players, 1):
                status = "✅" if p.verified else "⏳"
                text += f"{i}. {p.display_name} {status}\n"
            
            if len(self.players) == 4:
                text += self.get_teams_info()
            
            text += f"\n📢 کانال: {REQUIRED_CHANNEL}"
        
        elif self.state == "choosing_trump":
            chooser = self.get_player(self.trump_chooser_id)
            text += "👑 **انتخاب حکم**\n\n"
            text += self.get_teams_info()
            text += f"\n🎯 انتخاب کننده: **{chooser.display_name if chooser else '?'}**\n"
            text += f"📊 دور: 1/13 (۵ کارت اولیه)\n\n"
            text += "👇 **روی یکی از دکمه‌ها کلیک کنید:**"
        
        elif self.state == "playing":
            current = self.get_player(self.turn_order[self.current_turn_index])
            text += f"🎮 دور: {len(self.rounds)+1}/13\n"
            text += f"🃏 حکم: {self.trump_suit.value} {self.trump_suit.persian_name}\n"
            text += f"🎯 نوبت: **{current.display_name if current else '?'}**\n\n"
            text += "📊 دست‌های برده:\n"
            for p in self.players:
                text += f"• {p.display_name}: {p.tricks_won}\n"
        
        return text
    
    def update_verification_status(self, user_id: int, verified: bool):
        player = self.get_player(user_id)
        if player:
            player.verified = verified
            return True
        return False

# ==================== مدیریت بازی‌ها ====================

class GameManager:
    def __init__(self):
        self.games: Dict[str, Game] = {}
        self.user_games: Dict[int, str] = {}
        self.user_started: Dict[int, bool] = {}
    
    def create_game(self, chat_id: int, creator: Player) -> Optional[Game]:
        if chat_id > 0:
            return None
        
        game_id = f"game_{chat_id}_{int(datetime.now().timestamp())}"
        game = Game(game_id, chat_id, creator.user_id)
        
        creator.verified = True
        game.add_player(creator)
        
        self.games[game_id] = game
        self.user_games[creator.user_id] = game_id
        return game
    
    def get_game(self, game_id: str) -> Optional[Game]:
        return self.games.get(game_id)
    
    def get_player_game(self, user_id: int) -> Optional[Game]:
        game_id = self.user_games.get(user_id)
        return self.get_game(game_id) if game_id else None
    
    def mark_started(self, user_id: int):
        self.user_started[user_id] = True
    
    def has_started(self, user_id: int) -> bool:
        return self.user_started.get(user_id, False)

game_manager = GameManager()

# ==================== تایید عضویت ====================

async def check_membership(context, user_id: int) -> Tuple[bool, str]:
    try:
        chat = await context.bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        if chat.status in ['member', 'administrator', 'creator']:
            return True, "✅ عضویت تایید شد"
        if chat.status == 'restricted' and hasattr(chat, 'is_member') and chat.is_member:
            return True, "✅ عضویت تایید شد"
        return False, "❌ عضو کانال نیستید"
    except:
        return False, "❌ خطا در بررسی"

async def verify_player(context, user_id: int, game: Game) -> Tuple[bool, str]:
    is_member, msg = await check_membership(context, user_id)
    if is_member:
        game.update_verification_status(user_id, True)
        await update_game_message(context, game)
        return True, "✅ عضویت تایید شد!"
    return False, msg

# ==================== دستورات ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    game_manager.mark_started(user.id)
    
    if update.effective_chat.id > 0:
        await update.message.reply_text(
            f"سلام {user.first_name}! 👋\n\n"
            "🎴 **ربات بازی پاسور (حکم)**\n\n"
            "📌 **برای بازی:**\n"
            "۱. ربات را به گروه اضافه کنید\n"
            "۲. در گروه /newgame بزنید\n\n"
            f"📢 کانال اجباری: {REQUIRED_CHANNEL}"
        )

async def newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    
    if chat_id > 0:
        keyboard = [[InlineKeyboardButton("➕ اضافه به گروه", url=f"https://t.me/{context.bot.username}?startgroup=new")]]
        await update.message.reply_text("❌ بازی فقط در گروه!", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    if not game_manager.has_started(user.id):
        await update.message.reply_text("❌ ابتدا در پیوی /start بزنید!")
        return
    
    player = Player(user.id, user.username, user.first_name)
    game = game_manager.create_game(chat_id, player)
    if not game:
        await update.message.reply_text("❌ خطا!")
        return
    
    keyboard = [
        [InlineKeyboardButton("🎮 پیوستن", callback_data=f"join_{game.game_id}")],
        [InlineKeyboardButton("▶️ شروع", callback_data=f"start_{game.game_id}"),
         InlineKeyboardButton("❌ بستن", callback_data=f"close_{game.game_id}")]
    ]
    
    msg = await update.message.reply_text(
        game.get_game_info_text(),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    game.message_id = msg.message_id

# ==================== مدیریت کلیک‌ها ====================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data
    
    # ========== پیوستن به بازی ==========
    if data.startswith("join_"):
        game_id = data[5:]
        game = game_manager.get_game(game_id)
        
        if not game:
            await query.answer("❌ بازی یافت نشد!", show_alert=True)
            return
        
        if game.state != "waiting":
            await query.answer("❌ بازی شروع شده!", show_alert=True)
            return
        
        if len(game.players) >= 4:
            await query.answer("❌ بازی تکمیل!", show_alert=True)
            return
        
        if any(p.user_id == user.id for p in game.players):
            await query.answer("⚠️ شما قبلاً پیوستید!", show_alert=True)
            return
        
        # ارسال پیام به پیوی
        try:
            keyboard = [[InlineKeyboardButton("✅ تایید و پیوستن", callback_data=f"verify_join_{game.game_id}")]]
            await context.bot.send_message(
                user.id,
                f"🎴 درخواست پیوستن به بازی\n\n🔢 کد: `{game.game_id[-6:]}`\n📢 کانال: {REQUIRED_CHANNEL}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            await query.answer("✅ به پیوی بروید!", show_alert=True)
        except:
            await query.answer("❌ ابتدا به ربات پیام دهید!", show_alert=True)
    
    # ========== تایید و پیوستن ==========
    elif data.startswith("verify_join_"):
        game_id = data[12:]
        game = game_manager.get_game(game_id)
        
        if not game:
            await query.answer("❌ بازی یافت نشد!", show_alert=True)
            return
        
        if len(game.players) >= 4:
            await query.answer("❌ بازی تکمیل!", show_alert=True)
            return
        
        is_member, _ = await check_membership(context, user.id)
        
        if is_member:
            player = Player(user.id, user.username, user.first_name)
            player.verified = True
            player.has_started_bot = True
            
            if game.add_player(player):
                game_manager.user_games[user.id] = game.game_id
                game_manager.mark_started(user.id)
                await update_game_message(context, game)
                await query.edit_message_text("✅ عضویت تایید! به گروه برگردید.")
                await query.answer("✅ به بازی اضافه شدید!", show_alert=True)
            else:
                await query.answer("❌ خطا!", show_alert=True)
        else:
            channel = REQUIRED_CHANNEL.lstrip('@')
            keyboard = [[
                InlineKeyboardButton("📢 جوین شو", url=f"https://t.me/{channel}"),
                InlineKeyboardButton("🔄 بررسی", callback_data=f"verify_join_{game.game_id}")
            ]]
            await query.edit_message_text(
                f"❌ عضو {REQUIRED_CHANNEL} نیستید!",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    # ========== شروع بازی ==========
    elif data.startswith("start_"):
        game_id = data[6:]
        game = game_manager.get_game(game_id)
        
        if not game:
            await query.answer("❌ بازی یافت نشد!", show_alert=True)
            return
        
        if user.id != game.creator_id:
            await query.answer("❌ فقط سازنده!", show_alert=True)
            return
        
        if len(game.players) < 4:
            await query.answer(f"❌ {len(game.players)}/4 نفر!", show_alert=True)
            return
        
        if not all(p.verified for p in game.players):
            await query.answer("❌ تایید نشده!", show_alert=True)
            return
        
        if game.start_game():
            await update_game_message(context, game)
            
            # ارسال ۵ کارت اول به هر بازیکن
            for player in game.players:
                if player.current_cards:
                    teammate = game.get_teammate(player)
                    team_text = f"\n🤝 یار: {teammate.display_name}" if teammate else ""
                    
                    cards = "\n".join([f"{i+1}. {c.persian_name}" for i, c in enumerate(player.current_cards)])
                    
                    await context.bot.send_message(
                        player.user_id,
                        f"🎴 **کارت‌های دور اول**{team_text}\n\n"
                        f"🃏 ۵ کارت اولیه\n\n{cards}\n\n"
                        f"⏳ منتظر انتخاب حکم..."
                    )
            
            chooser = game.get_player(game.trump_chooser_id)
            if chooser:
                await context.bot.send_message(
                    chooser.user_id,
                    f"👑 **شما انتخاب کننده حکم هستید!**\n\n"
                    f"لطفاً در گروه روی یکی از دکمه‌ها کلیک کنید:"
                )
            
            await query.answer("✅ بازی شروع شد!", show_alert=True)
    
    # ========== انتخاب حکم - مستقیم وصل شده ==========
    elif data.startswith("trump_select_"):
        # فرمت: trump_select_{game_id}_{suit}
        parts = data.split("_")
        game_id = parts[2]
        suit_str = parts[3]
        
        game = game_manager.get_game(game_id)
        if not game:
            await query.answer("❌ بازی یافت نشد!", show_alert=True)
            return
        
        if user.id != game.trump_chooser_id:
            await query.answer("❌ فقط انتخاب کننده حکم!", show_alert=True)
            return
        
        suit_map = {
            'hearts': Suit.HEARTS,
            'diamonds': Suit.DIAMONDS,
            'clubs': Suit.CLUBS,
            'spades': Suit.SPADES
        }
        
        suit = suit_map.get(suit_str)
        if not suit:
            await query.answer("❌ خال نامعتبر!", show_alert=True)
            return
        
        if game.choose_trump(user.id, suit):
            await query.answer(f"✅ حکم: {suit.value} {suit.persian_name}", show_alert=True)
            
            # حذف دکمه‌ها
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except:
                pass
            
            await update_game_message(context, game)
            
            # اطلاع به گروه
            await context.bot.send_message(
                game.chat_id,
                f"🎉 **حکم انتخاب شد!**\n\n"
                f"🃏 **{suit.value} {suit.persian_name}**\n"
                f"👑 انتخاب کننده: {user.first_name}\n\n"
                f"📌 بقیه کارت‌ها در حال ارسال..."
            )
            
            # ارسال بقیه کارت‌ها به همه
            for player in game.players:
                if player.current_cards:
                    keyboard = game.create_cards_keyboard(player.user_id)
                    teammate = game.get_teammate(player)
                    team_text = f"\n🤝 یار: {teammate.display_name}" if teammate else ""
                    
                    cards = "\n".join([f"{i+1}. {c.persian_name}" for i, c in enumerate(player.current_cards)])
                    
                    try:
                        if player.user_id in game.player_cards_messages:
                            await context.bot.delete_message(
                                player.user_id,
                                game.player_cards_messages[player.user_id]
                            )
                        
                        msg = await context.bot.send_message(
                            player.user_id,
                            f"🎴 **کارت‌های کامل**{team_text}\n\n"
                            f"🃏 حکم: {suit.value} {suit.persian_name}\n\n"
                            f"{cards}\n\n"
                            f"🎯 نوبت: {game.get_player(game.turn_order[game.current_turn_index]).display_name}",
                            reply_markup=keyboard
                        )
                        game.player_cards_messages[player.user_id] = msg.message_id
                    except:
                        pass
    
    # ========== بازی کردن کارت - مستقیم وصل شده ==========
    elif data.startswith("play_card_"):
        parts = data.split("_")
        game_id = parts[2]
        card_idx = int(parts[3])
        
        game = game_manager.get_game(game_id)
        if not game:
            await query.answer("❌ بازی یافت نشد!", show_alert=True)
            return
        
        success, card, error = game.play_card(user.id, card_idx)
        
        if success and card:
            await query.answer(f"✅ {card.persian_name}", show_alert=True)
            await update_game_message(context, game)
            
            # آپدیت کارت‌های بازیکن
            player = game.get_player(user.id)
            if player and player.current_cards:
                keyboard = game.create_cards_keyboard(user.id)
                if keyboard:
                    try:
                        if user.id in game.player_cards_messages:
                            await context.bot.delete_message(
                                user.id,
                                game.player_cards_messages[user.id]
                            )
                        
                        teammate = game.get_teammate(player)
                        team_text = f"\n🤝 یار: {teammate.display_name}" if teammate else ""
                        cards = "\n".join([f"{i+1}. {c.persian_name}" for i, c in enumerate(player.current_cards)])
                        
                        msg = await context.bot.send_message(
                            user.id,
                            f"🎴 **کارت‌های شما**{team_text}\n\n"
                            f"🃏 حکم: {game.trump_suit.value} {game.trump_suit.persian_name}\n\n"
                            f"{cards}\n\n"
                            f"🎯 نوبت: {game.get_player(game.turn_order[game.current_turn_index]).display_name}",
                            reply_markup=keyboard
                        )
                        game.player_cards_messages[user.id] = msg.message_id
                    except:
                        pass
        else:
            await query.answer(f"❌ {error}", show_alert=True)

async def update_game_message(context, game):
    if not game.message_id:
        return
    
    try:
        keyboard = None
        if game.state == "waiting":
            keyboard = [
                [InlineKeyboardButton("🎮 پیوستن", callback_data=f"join_{game.game_id}")],
                [InlineKeyboardButton("▶️ شروع", callback_data=f"start_{game.game_id}"),
                 InlineKeyboardButton("❌ بستن", callback_data=f"close_{game.game_id}")]
            ]
        elif game.state == "choosing_trump":
            # دکمه‌های مستقیم وصل شده
            keyboard = [
                [
                    InlineKeyboardButton("♥️ دل", callback_data=f"trump_select_{game.game_id}_hearts"),
                    InlineKeyboardButton("♦️ خشت", callback_data=f"trump_select_{game.game_id}_diamonds")
                ],
                [
                    InlineKeyboardButton("♣️ گیشنیز", callback_data=f"trump_select_{game.game_id}_clubs"),
                    InlineKeyboardButton("♠️ پیک", callback_data=f"trump_select_{game.game_id}_spades")
                ]
            ]
        
        await context.bot.edit_message_text(
            chat_id=game.chat_id,
            message_id=game.message_id,
            text=game.get_game_info_text(),
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
        )
    except:
        pass

# ==================== اجرا ====================

def main():
    print("🤖 ربات پاسور در حال راه‌اندازی...")
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newgame", newgame))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    print("✅ ربات آماده است!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
