# requirements.txt
python-telegram-bot[job-queue]==20.7
python-dotenv==1.0.0
Pillow==10.0.0

import os
import random
import logging
import asyncio
from enum import Enum
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Set
from dataclasses import dataclass, field
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

# ==================== تنظیمات ====================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TOKEN")

if not TOKEN:
    try:
        from dotenv import load_dotenv
        load_dotenv()
        TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TOKEN")
    except ImportError:
        pass

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

# ==================== کلاس‌های اصلی ====================

class Suit(Enum):
    HEARTS = "♥️"      # دل
    DIAMONDS = "♦️"    # خشت
    CLUBS = "♣️"       # پیک
    SPADES = "♠️"      # گیشنیز
    
    @property
    def persian_name(self):
        names = {
            Suit.HEARTS: "دل",
            Suit.DIAMONDS: "خشت",
            Suit.CLUBS: "پیک",
            Suit.SPADES: "گیشنیز"
        }
        return names[self]

class Rank(Enum):
    TWO = ("2", 2)
    THREE = ("3", 3)
    FOUR = ("4", 4)
    FIVE = ("5", 5)
    SIX = ("6", 6)
    SEVEN = ("7", 7)
    EIGHT = ("8", 8)
    NINE = ("9", 9)
    TEN = ("10", 10)
    JACK = ("J", 11)
    QUEEN = ("Q", 12)
    KING = ("K", 13)
    ACE = ("A", 14)
    
    def __init__(self, symbol: str, rank_value: int):
        self._symbol = symbol
        self._rank_value = rank_value
    
    @property
    def symbol(self) -> str:
        return self._symbol
    
    @property
    def value(self) -> int:
        return self._rank_value

@dataclass
class Card:
    suit: Suit
    rank: Rank
    
    def __str__(self):
        return f"{self.suit.value}{self.rank.symbol}"
    
    @property
    def persian_name(self):
        rank_names = {
            Rank.ACE: "آس",
            Rank.KING: "شاه",
            Rank.QUEEN: "بیبی",
            Rank.JACK: "سرباز",
            Rank.TEN: "ده",
            Rank.NINE: "نه",
            Rank.EIGHT: "هشت",
            Rank.SEVEN: "هفت",
            Rank.SIX: "شش",
            Rank.FIVE: "پنج",
            Rank.FOUR: "چهار",
            Rank.THREE: "سه",
            Rank.TWO: "دو"
        }
        return f"{rank_names[self.rank]} {self.suit.persian_name}"

@dataclass
class Player:
    user_id: int
    username: str = ""
    first_name: str = ""
    cards: List[Card] = field(default_factory=list)
    score: int = 0
    tricks_won: int = 0
    is_ready: bool = False
    
    @property
    def display_name(self):
        if self.username:
            return f"@{self.username}"
        return self.first_name or f"User_{self.user_id}"

@dataclass
class Round:
    cards_played: Dict[int, Card] = field(default_factory=dict)
    starting_player_id: Optional[int] = None
    winner_id: Optional[int] = None
    
    def is_complete(self, players_count: int) -> bool:
        return len(self.cards_played) == players_count

@dataclass
class Game:
    game_id: str
    chat_id: int
    message_id: int = 0
    players: List[Player] = field(default_factory=list)
    deck: List[Card] = field(default_factory=list)
    current_round: Round = field(default_factory=Round)
    rounds: List[Round] = field(default_factory=list)
    turn_order: List[int] = field(default_factory=list)
    current_turn_index: int = 0
    trump_suit: Optional[Suit] = None
    trump_chooser_id: Optional[int] = None
    state: str = "waiting"  # waiting, choosing_trump, playing, finished
    created_at: datetime = field(default_factory=datetime.now)
    
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
        for suit in Suit:
            for rank in Rank:
                self.deck.append(Card(suit, rank))
        random.shuffle(self.deck)
    
    def deal_cards(self):
        cards_per_player = 13 if len(self.players) == 4 else (13 if len(self.players) == 3 else 13)
        for i, player in enumerate(self.players):
            start = i * cards_per_player
            end = start + cards_per_player
            player.cards = self.deck[start:end]
            player.cards.sort(key=lambda c: (c.suit.value, c.rank.value))
    
    def start_game(self):
        if len(self.players) < 2:
            return False
        
        self.initialize_deck()
        self.deal_cards()
        self.turn_order = [p.user_id for p in self.players]
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
    
    def play_card(self, user_id: int, card_index: int) -> Optional[Card]:
        if self.state != "playing":
            return None
        
        current_player_id = self.turn_order[self.current_turn_index]
        if user_id != current_player_id:
            return None
        
        player = self.get_player(user_id)
        if not player or card_index >= len(player.cards):
            return None
        
        card = player.cards.pop(card_index)
        
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
        
        return card
    
    def get_round_winner(self) -> Optional[int]:
        if not self.current_round.cards_played:
            return None
        
        first_player_id = self.current_round.starting_player_id
        first_card = self.current_round.cards_played[first_player_id]
        leading_suit = first_card.suit
        
        winning_player_id = first_player_id
        winning_card = first_card
        
        for player_id, card in self.current_round.cards_played.items():
            if card.suit == self.trump_suit and winning_card.suit != self.trump_suit:
                winning_player_id = player_id
                winning_card = card
            elif card.suit == self.trump_suit and winning_card.suit == self.trump_suit:
                if card.rank.value > winning_card.rank.value:
                    winning_player_id = player_id
                    winning_card = card
            elif card.suit == leading_suit and winning_card.suit == leading_suit:
                if card.rank.value > winning_card.rank.value:
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
    
    def get_game_info_text(self) -> str:
        text = "🎴 بازی پاسور (حکم)\n\n"
        
        if self.state == "waiting":
            text += f"⏳ در انتظار بازیکنان ({len(self.players)}/4)\n\n"
            text += "بازیکنان:\n"
            for player in self.players:
                text += f"• {player.display_name}\n"
            text += "\nبرای پیوستن روی دکمه زیر کلیک کنید."
        
        elif self.state == "choosing_trump":
            chooser = self.get_player(self.trump_chooser_id)
            text += f"👑 انتخاب خال حکم\n\n"
            text += f"بازیکنان:\n"
            for player in self.players:
                text += f"• {player.display_name} - {len(player.cards)} کارت\n"
            text += f"\n{chooser.display_name if chooser else '?'} باید خال حکم را انتخاب کند."
        
        elif self.state == "playing":
            current_player = self.get_player(self.turn_order[self.current_turn_index])
            text += f"🎮 دور: {len(self.rounds) + 1}/13\n"
            text += f"🃏 خال حکم: {self.trump_suit.value if self.trump_suit else '?'} {self.trump_suit.persian_name if self.trump_suit else ''}\n"
            text += f"🎯 نوبت: {current_player.display_name if current_player else '?'}\n\n"
            
            text += "📊 امتیازات:\n"
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
    
    def create_game(self, chat_id: int, player1: Player) -> Game:
        game_id = f"hokm_{chat_id}_{int(datetime.now().timestamp())}"
        game = Game(game_id=game_id, chat_id=chat_id)
        game.add_player(player1)
        self.games[game_id] = game
        self.user_games[player1.user_id] = game_id
        return game
    
    def get_game(self, game_id: str) -> Optional[Game]:
        return self.games.get(game_id)
    
    def get_chat_game(self, chat_id: int) -> Optional[Game]:
        for game in self.games.values():
            if game.chat_id == chat_id and game.state != "finished":
                return game
        return None
    
    def delete_game(self, game_id: str):
        game = self.games.get(game_id)
        if game:
            for player in game.players:
                self.user_games.pop(player.user_id, None)
            del self.games[game_id]
    
    def get_player_game(self, user_id: int) -> Optional[Game]:
        game_id = self.user_games.get(user_id)
        if game_id:
            return self.get_game(game_id)
        return None

game_manager = GameManager()

# ==================== دستورات ربات ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع ربات"""
    user = update.effective_user
    await update.message.reply_text(
        f"سلام {user.first_name}! 👋\n\n"
        "به ربات بازی پاسور (حکم) خوش آمدید! 🎴\n\n"
        "🎮 دستورات:\n"
        "/start - راهنمای بازی\n"
        "/newgame - ایجاد بازی جدید\n"
        "/join - پیوستن به بازی\n"
        "/startgame - شروع بازی\n"
        "/leave - ترک بازی\n"
        "/status - وضعیت بازی\n"
        "/rules - قوانین بازی\n"
        "/cancel - لغو بازی\n\n"
        "برای شروع یک بازی جدید در گروه از /newgame استفاده کنید."
    )

async def new_game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ایجاد بازی جدید"""
    chat_id = update.effective_chat.id
    
    existing_game = game_manager.get_chat_game(chat_id)
    if existing_game and existing_game.state != "finished":
        await update.message.reply_text("⚠️ یک بازی در حال اجرا در این گروه وجود دارد!")
        return
    
    user = update.effective_user
    player = Player(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name
    )
    
    game = game_manager.create_game(chat_id, player)
    
    keyboard = [
        [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
        [InlineKeyboardButton("▶️ شروع بازی", callback_data=f"start_{game.game_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = await update.message.reply_text(
        game.get_game_info_text(),
        reply_markup=reply_markup
    )
    
    game.message_id = message.message_id

async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پیوستن به بازی از طریق دستور"""
    chat_id = update.effective_chat.id
    game = game_manager.get_chat_game(chat_id)
    
    if not game:
        await update.message.reply_text("❌ هیچ بازی در انتظاری در این گروه وجود ندارد!")
        return
    
    if game.state != "waiting":
        await update.message.reply_text("❌ بازی در حال اجراست! نمی‌توانید الان بپیوندید.")
        return
    
    user = update.effective_user
    if any(p.user_id == user.id for p in game.players):
        await update.message.reply_text("✅ شما قبلاً در این بازی هستید!")
        return
    
    if len(game.players) >= 4:
        await update.message.reply_text("❌ بازی تکمیل است!")
        return
    
    player = Player(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name
    )
    
    if game.add_player(player):
        game_manager.user_games[user.id] = game.game_id
        
        keyboard = [
            [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
            [InlineKeyboardButton("▶️ شروع بازی", callback_data=f"start_{game.game_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=game.message_id,
                text=game.get_game_info_text(),
                reply_markup=reply_markup
            )
        except:
            pass
        
        await update.message.reply_text(f"✅ {user.first_name} به بازی پیوست!")
    else:
        await update.message.reply_text("❌ خطا در پیوستن به بازی!")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت کلیک‌ها"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    data = query.data
    
    if data.startswith("join_"):
        game_id = data[5:]
        game = game_manager.get_game(game_id)
        
        if not game:
            await query.edit_message_text("❌ بازی یافت نشد!")
            return
        
        if game.state != "waiting":
            await query.answer("بازی در حال اجراست!", show_alert=True)
            return
        
        if any(p.user_id == user.id for p in game.players):
            await query.answer("شما قبلاً در بازی هستید!", show_alert=True)
            return
        
        if len(game.players) >= 4:
            await query.answer("بازی تکمیل است!", show_alert=True)
            return
        
        player = Player(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name
        )
        
        if game.add_player(player):
            game_manager.user_games[user.id] = game.game_id
            
            keyboard = [
                [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
                [InlineKeyboardButton("▶️ شروع بازی", callback_data=f"start_{game.game_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                text=game.get_game_info_text(),
                reply_markup=reply_markup
            )
    
    elif data.startswith("start_"):
        game_id = data[6:]
        game = game_manager.get_game(game_id)
        
        if not game:
            await query.edit_message_text("❌ بازی یافت نشد!")
            return
        
        if len(game.players) < 2:
            await query.answer("حداقل ۲ بازیکن نیاز است!", show_alert=True)
            return
        
        if game.start_game():
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
            
            await query.edit_message_text(
                text=game.get_game_info_text(),
                reply_markup=reply_markup
            )
        else:
            await query.answer("خطا در شروع بازی!", show_alert=True)
    
    elif data.startswith("trump_"):
        parts = data.split("_")
        if len(parts) >= 3:
            game_id = parts[1]
            suit_name = parts[2]
            game = game_manager.get_game(game_id)
            
            if not game:
                return
            
            if game.state != "choosing_trump" or user.id != game.trump_chooser_id:
                await query.answer("شما نمی‌توانید خال حکم را انتخاب کنید!", show_alert=True)
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
                await update_game_display(update, context, game)
    
    elif data.startswith("card_"):
        parts = data.split("_")
        if len(parts) >= 3:
            game_id = parts[1]
            card_index = int(parts[2])
            game = game_manager.get_game(game_id)
            
            if not game:
                return
            
            played_card = game.play_card(user.id, card_index)
            if played_card:
                await update_game_display(update, context, game)
                await query.answer(f"کارت بازی شد: {played_card.persian_name}")
            else:
                await query.answer("حرکت نامعتبر!", show_alert=True)

async def update_game_display(update: Update, context: ContextTypes.DEFAULT_TYPE, game: Game):
    """به‌روزرسانی نمایش بازی"""
    if game.state == "playing":
        # نمایش کارت‌های بازیکن فعلی
        current_player = game.get_player(game.turn_order[game.current_turn_index])
        if current_player:
            await send_player_cards(context, game.chat_id, current_player.user_id, game)
    
    # به‌روزرسانی پیام اصلی بازی
    keyboard = get_game_keyboard(game)
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    
    try:
        await context.bot.edit_message_text(
            chat_id=game.chat_id,
            message_id=game.message_id,
            text=game.get_game_info_text(),
            reply_markup=reply_markup
        )
    except:
        pass

async def send_player_cards(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, game: Game):
    """ارسال کارت‌های بازیکن"""
    player = game.get_player(user_id)
    if not player:
        return
    
    cards_by_suit = defaultdict(list)
    for i, card in enumerate(player.cards):
        cards_by_suit[card.suit].append((i, card))
    
    keyboard = []
    for suit in Suit:
        row = []
        cards = cards_by_suit.get(suit, [])
        if cards:
            for card_index, card in cards:
                button_text = f"{suit.value}{card.rank.symbol}"
                callback_data = f"card_{game.game_id}_{card_index}"
                row.append(InlineKeyboardButton(button_text, callback_data=callback_data))
            if row:
                keyboard.append(row)
    
    if keyboard:
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🎴 کارت‌های شما (خال حکم: {game.trump_suit.value if game.trump_suit else '?'}):\n\n"
                     f"یک کارت برای بازی انتخاب کنید:",
                reply_markup=reply_markup
            )
        except:
            # اگر نتوانستیم پیام خصوصی بفرستیم
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ {player.display_name}، لطفا به ربات پیام خصوصی بدهید: @{context.bot.username}"
            )

def get_game_keyboard(game: Game):
    """دریافت کیبورد مناسب برای وضعیت بازی"""
    if game.state == "waiting":
        return [
            [InlineKeyboardButton("🎮 پیوستن به بازی", callback_data=f"join_{game.game_id}")],
            [InlineKeyboardButton("▶️ شروع بازی", callback_data=f"start_{game.game_id}")]
        ]
    elif game.state == "choosing_trump":
        return [
            [
                InlineKeyboardButton("♥️ دل", callback_data=f"trump_{game.game_id}_hearts"),
                InlineKeyboardButton("♦️ خشت", callback_data=f"trump_{game.game_id}_diamonds")
            ],
            [
                InlineKeyboardButton("♣️ پیک", callback_data=f"trump_{game.game_id}_clubs"),
                InlineKeyboardButton("♠️ گیشنیز", callback_data=f"trump_{game.game_id}_spades")
            ]
        ]
    elif game.state == "finished":
        return [
            [InlineKeyboardButton("🔄 بازی جدید", callback_data=f"new_{game.chat_id}")]
        ]
    return None

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش وضعیت بازی"""
    chat_id = update.effective_chat.id
    game = game_manager.get_chat_game(chat_id)
    
    if not game:
        await update.message.reply_text("📭 هیچ بازی فعالی در این گروه وجود ندارد.")
        return
    
    await update.message.reply_text(game.get_game_info_text())

async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قوانین بازی"""
    rules_text = (
        "📖 قوانین بازی پاسور (حکم):\n\n"
        "🎯 هدف بازی:\n"
        "بردیدن بیشترین تعداد دست (تریک) در ۱۳ دور\n\n"
        "👥 تعداد بازیکنان:\n"
        "۲ تا ۴ نفر (بهتره ۴ نفره باشه)\n\n"
        "🃏 نحوه بازی:\n"
        "۱. به هر بازیکن ۱۳ کارت داده می‌شود\n"
        "۲. اولین بازیکن خال حکم را انتخاب می‌کند\n"
        "۳. بازی با اولین بازیکن شروع می‌شود\n"
        "۴. هر بازیکن باید همخال بیاورد\n"
        "۵. اگر همخال نداشته باشد، هر کارتی می‌تواند بیاورد\n"
        "۶. برنده دست، کارت بالاتر خال حکم را می‌برد\n"
        "۷. برنده دست بعدی را شروع می‌کند\n\n"
        "🏆 امتیازدهی:\n"
        "• هر دست برده = ۱ امتیاز\n"
        "• بعد از ۱۳ دست، برنده کسی است که امتیاز بیشتری دارد\n\n"
        "💡 نکات:\n"
        "• خال حکم از همه خال‌ها قوی‌تر است\n"
        "• باید حتماً همخال آورد مگر اینکه نداشته باشید\n"
        "• آس بالا‌ترین کارت و ۲ پایین‌ترین کارت است"
    )
    
    await update.message.reply_text(rules_text)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لغو بازی"""
    chat_id = update.effective_chat.id
    game = game_manager.get_chat_game(chat_id)
    
    if not game:
        await update.message.reply_text("❌ هیچ بازی فعالی برای لغو وجود ندارد.")
        return
    
    game_manager.delete_game(game.game_id)
    await update.message.reply_text("✅ بازی لغو شد.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت خطاها"""
    logger.error(f"خطا: {context.error}")
    try:
        if update and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ خطایی رخ داد. لطفا دوباره تلاش کنید."
            )
    except:
        pass

# ==================== اجرای ربات ====================

def main():
    """تابع اصلی"""
    application = Application.builder().token(TOKEN).build()
    
    # اضافه کردن دستورات
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("newgame", new_game_command))
    application.add_handler(CommandHandler("join", join_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("rules", rules_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    
    # اضافه کردن handler برای callback
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    # اضافه کردن handler خطا
    application.add_error_handler(error_handler)
    
    print("🤖 ربات بازی پاسور در حال اجراست...")
    print(f"🔗 آدرس ربات: https://t.me/{application.bot.username}")
    
    # راه‌اندازی ربات
    application.run_polling()

if __name__ == "__main__":
    main()
