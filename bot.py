import os
import json
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
    ContextTypes,
    MessageHandler,
    filters
)

# ==================== تنظیمات اولیه ====================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TOKEN")
if not TOKEN:
    print("❌ توکن یافت نشد! متغیر محیطی TELEGRAM_BOT_TOKEN را تنظیم کنید.")
    exit(1)

REQUIRED_CHANNEL = "@konkorkhabar"
BOT_USERNAME = None

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
        return {
            Suit.HEARTS: "دل",
            Suit.DIAMONDS: "خشت",
            Suit.CLUBS: "گیشنیز",
            Suit.SPADES: "پیک"
        }[self]

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
        return f"{self.rank.symbol}{self.suit.value}"

    def __eq__(self, other):
        if not isinstance(other, Card):
            return False
        return self.suit == other.suit and self.rank.symbol == other.rank.symbol

    def __hash__(self):
        return hash((self.suit.value, self.rank.symbol))

    @property
    def persian_name(self):
        return f"{self.rank.persian_name} {self.suit.persian_name}"

    @property
    def value(self):
        return self.rank.value

class Player:
    def __init__(self, user_id: int, full_name: str):
        self.user_id = user_id
        self.full_name = full_name
        self.cards: List[Card] = []
        self.first_five: List[Card] = []
        self.tricks_won: int = 0
        self.verified: bool = False
        self.position: Optional[int] = None
        self.team: Optional[int] = None

    @property
    def display_name(self):
        return self.full_name

class Round:
    def __init__(self):
        self.cards_played: Dict[int, Card] = {}
        self.starting_player_id: Optional[int] = None
        self.winner_id: Optional[int] = None

    def is_complete(self) -> bool:
        return len(self.cards_played) == 4

class Game:
    def __init__(self, game_id: str, creator_id: int):
        self.game_id = game_id
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
        self.created_at = datetime.now()
        self.player_chat_ids: Dict[int, int] = {}
        self.winner_team: Optional[int] = None
        self.first_round_dealt: bool = False
        self.team0_rounds: int = 0
        self.team1_rounds: int = 0
        self.hand_number: int = 1

    def add_player(self, player: Player) -> bool:
        if len(self.players) >= 4:
            return False
        if any(p.user_id == player.user_id for p in self.players):
            return False
        player.position = len(self.players)
        self.players.append(player)
        if len(self.players) == 4:
            self._assign_teams()
        return True

    def remove_player(self, user_id: int):
        self.players = [p for p in self.players if p.user_id != user_id]
        for i, p in enumerate(self.players):
            p.position = i

    def _assign_teams(self):
        for i, p in enumerate(self.players):
            p.team = i % 2

    def get_teammate(self, player: Player) -> Optional[Player]:
        if player.team is None:
            return None
        for p in self.players:
            if p.team == player.team and p.user_id != player.user_id:
                return p
        return None

    def get_player(self, user_id: int) -> Optional[Player]:
        for p in self.players:
            if p.user_id == user_id:
                return p
        return None

    def initialize_deck(self):
        self.deck = []
        for suit in [Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS, Suit.SPADES]:
            for rank in RANKS.values():
                self.deck.append(Card(suit, rank))
        random.shuffle(self.deck)

    def deal_first_round(self):
        for i, p in enumerate(self.players):
            start = i * 5
            end = start + 5
            p.first_five = self.deck[start:end].copy()
            p.cards = p.first_five.copy()
            p.cards.sort(key=lambda c: (c.suit.value, -c.rank.value))
        self.first_round_dealt = True

    def deal_remaining_cards(self):
        for i, p in enumerate(self.players):
            start = 20 + (i * 8)
            end = start + 8
            remaining_cards = self.deck[start:end].copy()
            p.cards = p.first_five.copy() + remaining_cards
            p.cards.sort(key=lambda c: (c.suit.value, -c.rank.value))

    def start_game(self) -> bool:
        if len(self.players) != 4:
            return False
        if not all(p.verified for p in self.players):
            return False
        self.initialize_deck()
        self.deal_first_round()
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
        self.deal_remaining_cards()
        self.state = "playing"
        self.turn_order = [p.user_id for p in self.players]
        chooser_index = self.turn_order.index(user_id)
        self.current_turn_index = chooser_index
        return True

    def can_play_card(self, player: Player, card: Card) -> bool:
        if not self.current_round.cards_played:
            return True
        first_card = list(self.current_round.cards_played.values())[0]
        leading_suit = first_card.suit
        if card.suit == leading_suit:
            return True
        has_leading = any(c.suit == leading_suit for c in player.cards)
        return not has_leading

    def reset_for_next_hand(self):
        """ریست کردن برای دست بعدی"""
        for p in self.players:
            p.cards = []
            p.first_five = []
            p.tricks_won = 0
        self.current_round = Round()
        self.rounds = []
        self.turn_order = []
        self.current_turn_index = 0
        self.trump_suit = None
        self.trump_chooser_id = None
        self.state = "choosing_trump"
        self.first_round_dealt = False
        self.deck = []
        self.initialize_deck()
        self.deal_first_round()
        self.turn_order = [p.user_id for p in self.players]
        random.shuffle(self.turn_order)
        self.current_turn_index = 0
        self.trump_chooser_id = self.turn_order[0]
        self.hand_number += 1

    def play_card(self, user_id: int, card_index: int) -> Tuple[bool, Optional[Card], Optional[str]]:
        if self.state != "playing":
            return False, None, "❌ بازی در حال اجرا نیست"
        if user_id != self.turn_order[self.current_turn_index]:
            return False, None, "❌ نوبت شما نیست"
        
        player = self.get_player(user_id)
        if not player or card_index >= len(player.cards):
            return False, None, "❌ کارت نامعتبر"
        
        card = player.cards[card_index]

        if not self.can_play_card(player, card):
            valid_cards = [c for c in player.cards if self.can_play_card(player, c)]
            if valid_cards:
                suits = set()
                for c in valid_cards:
                    suits.add(c.suit.persian_name)
                return False, None, f"❌ باید هم‌خال بازی کنید. خال مجاز: {', '.join(suits)}"
            else:
                return False, None, "❌ خطا در بررسی کارت"

        player.cards.pop(card_index)

        if len(self.current_round.cards_played) == 0:
            self.current_round.starting_player_id = user_id

        self.current_round.cards_played[user_id] = card
        self.current_turn_index = (self.current_turn_index + 1) % 4

        if self.current_round.is_complete():
            winner_id = self._get_round_winner()
            self.current_round.winner_id = winner_id
            winner = self.get_player(winner_id)
            if winner:
                winner.tricks_won += 1
                
                team0_score = sum(p.tricks_won for p in self.players if p.team == 0)
                team1_score = sum(p.tricks_won for p in self.players if p.team == 1)
                
                # اگر تیمی به ۷ امتیاز رسید
                if team0_score >= 7:
                    self.team0_rounds += 1
                    self.state = "hand_finished"
                elif team1_score >= 7:
                    self.team1_rounds += 1
                    self.state = "hand_finished"
                else:
                    self.rounds.append(self.current_round)
                    self.current_round = Round()
                    winner_index = self.turn_order.index(winner_id)
                    self.current_turn_index = winner_index
        return True, card, None

    def _get_round_winner(self) -> Optional[int]:
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

    def get_status_text(self) -> str:
        text = f"🎮 بازی پاسور - کد: {self.game_id[-6:]}\n\n"
        
        if self.state == "waiting":
            text += f"⏳ در انتظار بازیکنان ({len(self.players)}/4)\n\n👥 بازیکنان:\n"
            for p in self.players:
                status = "✅" if p.verified else "⏳"
                text += f"• {p.display_name} {status}\n"
            if len(self.players) == 4:
                text += self._teams_info()
                
        elif self.state == "choosing_trump":
            chooser = self.get_player(self.trump_chooser_id)
            text += "👑 انتخاب حکم\n\n"
            text += self._teams_info()
            text += f"\n🎯 انتخاب کننده: {chooser.display_name if chooser else '?'}\n"
            text += f"📊 دست: {self.hand_number} از ۷\n"
            text += f"🏆 امتیازات کلی:\n"
            
            team0 = [p for p in self.players if p.team == 0]
            team1 = [p for p in self.players if p.team == 1]
            team0_names = " و ".join(p.display_name for p in team0)
            team1_names = " و ".join(p.display_name for p in team1)
            
            text += f"• {team0_names}: {self.team0_rounds} دست\n"
            text += f"• {team1_names}: {self.team1_rounds} دست\n"
            text += f"🎯 اولین تیم با ۷ دست = برنده نهایی\n\n"
            text += "📍 لطفاً در پیوی ربات حکم را انتخاب کنید..."
            
        elif self.state == "playing":
            current = self.get_player(self.turn_order[self.current_turn_index])
            text += f"🎮 دست: {self.hand_number} از ۷\n"
            text += f"🃏 حکم این دست: {self.trump_suit.value} {self.trump_suit.persian_name}\n"
            text += f"🎯 نوبت: {current.display_name if current else '?'}\n\n"
            
            team0 = [p for p in self.players if p.team == 0]
            team1 = [p for p in self.players if p.team == 1]
            team0_names = " و ".join(p.display_name for p in team0)
            team1_names = " و ".join(p.display_name for p in team1)
            team0_score = sum(p.tricks_won for p in self.players if p.team == 0)
            team1_score = sum(p.tricks_won for p in self.players if p.team == 1)
            
            text += f"📊 امتیاز این دست:\n"
            text += f"• {team0_names}: {team0_score} امتیاز\n"
            text += f"• {team1_names}: {team1_score} امتیاز\n"
            text += f"🎯 اولین تیم با ۷ امتیاز = برنده این دست\n\n"
            text += f"🏆 امتیازات کلی:\n"
            text += f"• {team0_names}: {self.team0_rounds} دست\n"
            text += f"• {team1_names}: {self.team1_rounds} دست\n"
            text += f"🎯 اولین تیم با ۷ دست = برنده نهایی\n"
            
            if self.current_round.cards_played:
                text += "\n🎴 کارت‌های این دور:\n"
                for pid, card in self.current_round.cards_played.items():
                    player = self.get_player(pid)
                    text += f"• {player.display_name if player else '?'}: {card}\n"
                    
        elif self.state == "finished":
            team0 = [p for p in self.players if p.team == 0]
            team1 = [p for p in self.players if p.team == 1]
            team0_names = " و ".join(p.display_name for p in team0)
            team1_names = " و ".join(p.display_name for p in team1)
            
            text += "🏆 **بازی تمام شد!**\n\n"
            text += f"📊 نتیجه نهایی:\n"
            text += f"• {team0_names}: {self.team0_rounds} دست\n"
            text += f"• {team1_names}: {self.team1_rounds} دست\n\n"
            
            if self.team0_rounds >= 7:
                text += f"🏅 تیم {team0_names} با ۷ دست برنده نهایی بازی شد!\n🎉"
            elif self.team1_rounds >= 7:
                text += f"🏅 تیم {team1_names} با ۷ دست برنده نهایی بازی شد!\n🎉"
                
        return text

    def _teams_info(self) -> str:
        if len(self.players) < 4:
            return ""
        team0 = [p for p in self.players if p.team == 0]
        team1 = [p for p in self.players if p.team == 1]
        text = "🤝 تیم‌ها:\n"
        if team0:
            text += f"• تیم ۱: {team0[0].display_name} و {team0[1].display_name}\n"
        if team1:
            text += f"• تیم ۲: {team1[0].display_name} و {team1[1].display_name}\n"
        return text

# ==================== مدیریت بازی‌ها ====================
class GameManager:
    def __init__(self):
        self.games: Dict[str, Game] = {}
        self.user_game: Dict[int, str] = {}

    def create_game(self, creator_id: int) -> Game:
        game_id = f"game_{creator_id}_{int(datetime.now().timestamp())}"
        game = Game(game_id, creator_id)
        self.games[game_id] = game
        return game

    def get_game(self, game_id: str) -> Optional[Game]:
        """دریافت بازی با game_id - بازی تا وقتی تمام نشده یا بسته نشده وجود دارد"""
        return self.games.get(game_id)

    def get_user_game(self, user_id: int) -> Optional[Game]:
        gid = self.user_game.get(user_id)
        return self.games.get(gid) if gid else None

    def set_user_game(self, user_id: int, game_id: str):
        self.user_game[user_id] = game_id

    def remove_user_game(self, user_id: int):
        if user_id in self.user_game:
            del self.user_game[user_id]

    def delete_game(self, game_id: str):
        if game_id in self.games:
            del self.games[game_id]

game_manager = GameManager()

# ==================== بررسی عضویت ====================
async def check_membership(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Tuple[bool, str]:
    try:
        channel = REQUIRED_CHANNEL.lstrip('@')
        chat = await context.bot.get_chat_member(f"@{channel}", user_id)
        if chat.status in ['member', 'administrator', 'creator']:
            return True, "✅ عضویت تایید شد"
        if chat.status == 'restricted' and hasattr(chat, 'is_member') and chat.is_member:
            return True, "✅ عضویت تایید شد"
        return False, "❌ شما عضو کانال نیستید"
    except Exception as e:
        return False, f"❌ خطا در بررسی عضویت"

# ==================== توابع کمکی ====================
def format_cards(cards: List[Card]) -> str:
    if not cards:
        return "بدون کارت"
    by_suit = defaultdict(list)
    for card in cards:
        by_suit[card.suit].append(card)
    lines = []
    for suit in [Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS, Suit.SPADES]:
        if suit in by_suit:
            suit_cards = by_suit[suit]
            suit_cards.sort(key=lambda c: -c.rank.value)
            line = f"\n{suit.persian_name}: " + " ".join(f"{c.rank.symbol}{c.suit.value}" for c in suit_cards)
            lines.append(line)
    return "".join(lines)

def make_cards_keyboard(game_id: str, cards: List[Card]) -> Optional[InlineKeyboardMarkup]:
    if not cards:
        return None
    keyboard = []
    row = []
    for i, card in enumerate(cards):
        row.append(InlineKeyboardButton(
            f"{card.rank.symbol}{card.suit.value}",
            callback_data=f"play:{game_id}:{i}"
        ))
        if len(row) == 4:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard) if keyboard else None

def get_user_full_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    elif user.first_name and user.last_name:
        return f"{user.first_name} {user.last_name}"
    elif user.first_name:
        return user.first_name
    else:
        return f"کاربر {user.id}"

# ==================== دستورات خصوصی ====================
async def private_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id < 0:
        return

    user = update.effective_user
    args = context.args

    global BOT_USERNAME
    if not BOT_USERNAME:
        me = await context.bot.get_me()
        BOT_USERNAME = me.username

    if args and args[0].startswith("join_"):
        game_id = args[0][5:]
        game = game_manager.get_game(game_id)
        if not game:
            await update.message.reply_text(
                "❌ این بازی وجود ندارد یا قبلاً به اتمام رسیده است.\n"
                "لطفاً از سازنده بازی بخواهید یک بازی جدید ایجاد کند."
            )
            return

        if any(p.user_id == user.id for p in game.players):
            await update.message.reply_text("⚠️ شما قبلاً به این بازی پیوسته‌اید!")
            return
            
        if len(game.players) >= 4:
            await update.message.reply_text("❌ ظرفیت این بازی تکمیل شده است (۴ نفر کامل).")
            return

        full_name = get_user_full_name(user)
        is_member, msg = await check_membership(context, user.id)
        
        if not is_member:
            channel = REQUIRED_CHANNEL.lstrip('@')
            keyboard = [[
                InlineKeyboardButton("📢 جوین شو در کانال", url=f"https://t.me/{channel}"),
                InlineKeyboardButton("🔄 بررسی مجدد", callback_data=f"verify:{game.game_id}")
            ]]
            context.user_data['pending_verify'] = (game.game_id, full_name)
            await update.message.reply_text(
                f"❌ برای پیوستن به بازی باید عضو کانال {REQUIRED_CHANNEL} باشید.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        player = Player(user.id, full_name)
        player.verified = True
        if game.add_player(player):
            game_manager.set_user_game(user.id, game.game_id)
            
            for p in game.players:
                if p.user_id != user.id:
                    try:
                        await context.bot.send_message(
                            p.user_id,
                            f"👤 {full_name} به بازی پیوست. ({len(game.players)}/4)"
                        )
                    except:
                        pass
            
            await update.message.reply_text(
                f"✅ عضویت شما تأیید شد!\n"
                f"🎮 به بازی کد {game.game_id[-6:]} پیوستید.\n"
                f"👥 بازیکنان: {len(game.players)}/4"
            )
            
            if len(game.players) == 4:
                creator = game.get_player(game.creator_id)
                if creator:
                    await context.bot.send_message(
                        creator.user_id,
                        f"✅ بازی کد {game.game_id[-6:]} تکمیل شد!\n"
                        f"برای شروع از /startgame استفاده کنید."
                    )
        else:
            await update.message.reply_text("❌ خطا در پیوستن به بازی!")
        return

    full_name = get_user_full_name(user)
    await _show_main_menu(update, context, full_name)

async def _show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, full_name: str):
    text = (
        f"👤 {full_name} عزیز، خوش آمدید!\n\n"
        "🎴 ربات بازی پاسور (حکم)\n\n"
        "📋 دستورات:\n"
        "/newgame - ایجاد بازی جدید\n"
        "/mygame - وضعیت بازی فعلی\n"
        "/leave - ترک بازی\n"
        "/close - بستن بازی (فقط سازنده)\n\n"
        f"📢 کانال اجباری: {REQUIRED_CHANNEL}"
    )
    await update.message.reply_text(text)

# ==================== دستورات بازی ====================
async def newgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id < 0:
        await update.message.reply_text("❌ این دستور فقط در پیوی ربات کار می‌کند!")
        return

    user = update.effective_user
    full_name = get_user_full_name(user)

    current_game = game_manager.get_user_game(user.id)
    if current_game and current_game.state == "waiting":
        await update.message.reply_text(
            f"❌ شما در حال حاضر در بازی کد {current_game.game_id[-6:]} هستید.\n"
            f"لطفاً آن بازی را ترک کنید یا تمام کنید."
        )
        return

    game = game_manager.create_game(user.id)
    creator = Player(user.id, full_name)
    creator.verified = True
    game.add_player(creator)
    game_manager.set_user_game(user.id, game.game_id)

    invite_link = f"https://t.me/{BOT_USERNAME}?start=join_{game.game_id}"
    await update.message.reply_text(
        f"✅ بازی جدید ایجاد شد!\n"
        f"🔢 کد بازی: {game.game_id[-6:]}\n\n"
        f"🔗 **لینک دعوت (تا پایان بازی معتبر است):**\n{invite_link}\n\n"
        f"📌 این لینک را برای دوستان خود بفرستید.\n"
        f"⚠️ توجه: لینک تا زمانی که بازی تمام نشده یا بسته نشده معتبر است.\n"
        f"بعد از پیوستن ۴ نفر، با /startgame بازی را شروع کنید.",
        disable_web_page_preview=True
    )

async def mygame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id < 0:
        return
    user = update.effective_user
    game = game_manager.get_user_game(user.id)
    if not game:
        await update.message.reply_text("❌ شما در هیچ بازی فعالی نیستید.")
        return
    await update.message.reply_text(game.get_status_text())

async def startgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id < 0:
        return
        
    user = update.effective_user
    game = game_manager.get_user_game(user.id)
    if not game or game.creator_id != user.id:
        await update.message.reply_text("❌ شما سازنده این بازی نیستید.")
        return
        
    if game.state != "waiting":
        await update.message.reply_text("⚠️ بازی قبلاً شروع شده است.")
        return
        
    if len(game.players) != 4:
        await update.message.reply_text(
            f"❌ ظرفیت بازی تکمیل نشده!\n"
            f"👥 بازیکنان: {len(game.players)}/4 نفر\n\n"
            f"📌 لطفاً دوستان خود را از طریق لینک دعوت به بازی اضافه کنید."
        )
        return
        
    if not all(p.verified for p in game.players):
        await update.message.reply_text("❌ همه بازیکنان عضویت خود را تأیید نکرده‌اند.")
        return

    if game.start_game():
        for player in game.players:
            cards_text = format_cards(player.cards)
            teammate = game.get_teammate(player)
            teammate_text = f"\n🤝 یار شما: {teammate.display_name}" if teammate else ""
            await context.bot.send_message(
                player.user_id,
                f"🎴 کارت‌های دور اول{teammate_text}\n\n"
                f"🃏 ۵ کارت اولیه\n{cards_text}\n\n"
                f"⏳ منتظر انتخاب حکم..."
            )

        chooser = game.get_player(game.trump_chooser_id)
        if chooser:
            keyboard = [
                [
                    InlineKeyboardButton("♥️ دل", callback_data=f"trump:{game.game_id}:hearts"),
                    InlineKeyboardButton("♦️ خشت", callback_data=f"trump:{game.game_id}:diamonds")
                ],
                [
                    InlineKeyboardButton("♣️ گیشنیز", callback_data=f"trump:{game.game_id}:clubs"),
                    InlineKeyboardButton("♠️ پیک", callback_data=f"trump:{game.game_id}:spades")
                ]
            ]
            await context.bot.send_message(
                chooser.user_id,
                f"👑 شما انتخاب کننده حکم هستید!\n\n"
                f"🔢 کد بازی: {game.game_id[-6:]}\n"
                f"{game._teams_info()}\n"
                f"🏆 امتیازات کلی: تیم ۱ {game.team0_rounds} - {game.team1_rounds} تیم ۲\n"
                f"👇 لطفاً خال حکم را انتخاب کنید:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        await update.message.reply_text("✅ بازی شروع شد!")
    else:
        await update.message.reply_text("❌ خطا در شروع بازی!")

async def leave_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id < 0:
        return
    user = update.effective_user
    game = game_manager.get_user_game(user.id)
    if not game:
        await update.message.reply_text("❌ شما در هیچ بازی نیستید.")
        return
    if game.creator_id == user.id:
        await update.message.reply_text("❌ شما سازنده هستید! برای بستن بازی از /close استفاده کنید.")
        return
    game.remove_player(user.id)
    game_manager.remove_user_game(user.id)
    await update.message.reply_text("✅ شما از بازی خارج شدید.")

async def close_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id < 0:
        return
    user = update.effective_user
    game = game_manager.get_user_game(user.id)
    if not game or game.creator_id != user.id:
        await update.message.reply_text("❌ شما سازنده این بازی نیستید.")
        return
    for player in game.players:
        if player.user_id != user.id:
            try:
                await context.bot.send_message(
                    player.user_id,
                    f"❌ بازی کد {game.game_id[-6:]} توسط سازنده بسته شد."
                )
            except:
                pass
        game_manager.remove_user_game(player.user_id)
    game_manager.delete_game(game.game_id)
    await update.message.reply_text("✅ بازی بسته شد.")

# ==================== کالبک‌ها ====================
async def private_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data

    if data.startswith("verify:"):
        game_id = data[7:]
        game = game_manager.get_game(game_id)
        if not game:
            await query.edit_message_text(
                "❌ این بازی وجود ندارد یا قبلاً به اتمام رسیده است.\n"
                "لطفاً از سازنده بازی بخواهید یک بازی جدید ایجاد کند."
            )
            return

        full_name = None
        if 'pending_verify' in context.user_data:
            stored_gid, full_name = context.user_data['pending_verify']
            if stored_gid != game_id:
                await query.edit_message_text("❌ اطلاعات ناهمخوان است.")
                return
        else:
            full_name = get_user_full_name(user)

        is_member, _ = await check_membership(context, user.id)
        if is_member:
            player = Player(user.id, full_name)
            player.verified = True
            if game.add_player(player):
                game_manager.set_user_game(user.id, game.game_id)
                
                for p in game.players:
                    if p.user_id != user.id:
                        try:
                            await context.bot.send_message(
                                p.user_id,
                                f"👤 {full_name} به بازی پیوست. ({len(game.players)}/4)"
                            )
                        except:
                            pass
                
                await query.edit_message_text(
                    f"✅ عضویت تأیید شد!\n"
                    f"🎮 به بازی کد {game.game_id[-6:]} پیوستید.\n"
                    f"👥 بازیکنان: {len(game.players)}/4"
                )
                if 'pending_verify' in context.user_data:
                    context.user_data.pop('pending_verify')
                if len(game.players) == 4:
                    creator = game.get_player(game.creator_id)
                    if creator:
                        await context.bot.send_message(
                            creator.user_id,
                            f"✅ بازی کد {game.game_id[-6:]} تکمیل شد!\n"
                            f"برای شروع از /startgame استفاده کنید."
                        )
            else:
                await query.edit_message_text("❌ خطا در پیوستن به بازی!")
        else:
            channel = REQUIRED_CHANNEL.lstrip('@')
            keyboard = [[
                InlineKeyboardButton("📢 جوین شو", url=f"https://t.me/{channel}"),
                InlineKeyboardButton("🔄 بررسی مجدد", callback_data=f"verify:{game.game_id}")
            ]]
            await query.edit_message_text(
                f"❌ شما هنوز عضو کانال {REQUIRED_CHANNEL} نیستید!",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    elif data.startswith("trump:"):
        parts = data.split(":")
        if len(parts) != 3:
            await query.answer("❌ خطا در دکمه", show_alert=True)
            return
            
        game_id = parts[1]
        suit_str = parts[2]
        game = game_manager.get_game(game_id)
        
        if not game:
            await query.answer("❌ بازی یافت نشد!", show_alert=True)
            return
            
        if user.id != game.trump_chooser_id:
            await query.answer("❌ فقط انتخاب کننده حکم می‌تواند کلیک کند!", show_alert=True)
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
            await query.edit_message_text(
                f"✅ حکم این دست انتخاب شد: {suit.value} {suit.persian_name}\n"
                f"🃏 ۸ کارت جدید اضافه شد...\n\n"
                f"🏆 امتیازات کلی: تیم ۱ {game.team0_rounds} - {game.team1_rounds} تیم ۲",
                reply_markup=None
            )
            await query.answer(f"✅ حکم: {suit.value} {suit.persian_name}", show_alert=True)

            for player in game.players:
                cards_text = format_cards(player.cards)
                teammate = game.get_teammate(player)
                teammate_text = f"\n🤝 یار شما: {teammate.display_name}" if teammate else ""
                keyboard = make_cards_keyboard(game.game_id, player.cards)

                if player.user_id in game.player_chat_ids:
                    try:
                        await context.bot.delete_message(
                            player.user_id,
                            game.player_chat_ids[player.user_id]
                        )
                    except:
                        pass

                msg = await context.bot.send_message(
                    player.user_id,
                    f"🎴 **کارت‌های شما (۵ کارت اول + ۸ کارت جدید)**{teammate_text}\n\n"
                    f"🃏 حکم این دست: {suit.value} {suit.persian_name}\n"
                    f"{cards_text}\n\n"
                    f"🏆 امتیازات کلی: تیم ۱ {game.team0_rounds} - {game.team1_rounds} تیم ۲\n\n"
                    f"🎯 نوبت: {game.get_player(game.turn_order[game.current_turn_index]).display_name}",
                    reply_markup=keyboard
                )
                game.player_chat_ids[player.user_id] = msg.message_id
        else:
            await query.answer("❌ خطا در انتخاب حکم!", show_alert=True)

    # ========== بخش بازی کارت ==========
    elif data.startswith("play:"):
        parts = data.split(":")
        if len(parts) != 3:
            await query.answer("❌ خطا در دکمه", show_alert=True)
            return
            
        game_id = parts[1]
        try:
            card_idx = int(parts[2])
        except:
            await query.answer("❌ اندیس کارت نامعتبر", show_alert=True)
            return

        game = game_manager.get_game(game_id)
        if not game:
            await query.answer("❌ بازی یافت نشد!", show_alert=True)
            return

        success, card, error = game.play_card(user.id, card_idx)
        
        if success and card:
            await query.answer(f"✅ {card}", show_alert=True)

            player = game.get_player(user.id)
            if player:
                await context.bot.send_message(
                    user.id,
                    f"✅ شما کارت {card} را بازی کردید."
                )
                
                for other in game.players:
                    if other.user_id != user.id:
                        try:
                            await context.bot.send_message(
                                other.user_id,
                                f"🎴 {player.display_name} کارت بازی کرد:\n"
                                f"{card}"
                            )
                        except:
                            pass

            # آپدیت کارت‌های بازیکن
            if player and player.cards:
                cards_text = format_cards(player.cards)
                teammate = game.get_teammate(player)
                teammate_text = f"\n🤝 یار شما: {teammate.display_name}" if teammate else ""
                
                keyboard = make_cards_keyboard(game.game_id, player.cards)
                
                msg = await context.bot.send_message(
                    user.id,
                    f"🎴 کارت‌های شما{teammate_text}\n\n"
                    f"🃏 حکم این دست: {game.trump_suit.value} {game.trump_suit.persian_name}\n"
                    f"{cards_text}\n\n"
                    f"🏆 امتیازات کلی: تیم ۱ {game.team0_rounds} - {game.team1_rounds} تیم ۲\n\n"
                    f"🎯 نوبت: {game.get_player(game.turn_order[game.current_turn_index]).display_name}",
                    reply_markup=keyboard
                )
                
                game.player_chat_ids[user.id] = msg.message_id

            # اعلام برنده دور
            if len(game.current_round.cards_played) == 0 and game.current_round.winner_id:
                winner = game.get_player(game.current_round.winner_id)
                if winner:
                    team0 = [p for p in game.players if p.team == 0]
                    team1 = [p for p in game.players if p.team == 1]
                    team0_names = " و ".join(p.display_name for p in team0)
                    team1_names = " و ".join(p.display_name for p in team1)
                    team0_score = sum(p.tricks_won for p in game.players if p.team == 0)
                    team1_score = sum(p.tricks_won for p in game.players if p.team == 1)
                    
                    for p in game.players:
                        await context.bot.send_message(
                            p.user_id,
                            f"🏆 برنده این دور: {winner.display_name}\n\n"
                            f"📊 امتیازات این دست:\n"
                            f"• {team0_names}: {team0_score}\n"
                            f"• {team1_names}: {team1_score}\n"
                            f"🎯 اولین تیم با ۷ امتیاز = برنده این دست"
                        )
                        
                    if game.state == "playing":
                        next_player = game.get_player(game.turn_order[game.current_turn_index])
                        if next_player:
                            for p in game.players:
                                if p.user_id != next_player.user_id:
                                    await context.bot.send_message(
                                        p.user_id,
                                        f"🎯 نوبت بعدی: {next_player.display_name}"
                                    )
                                else:
                                    await context.bot.send_message(
                                        next_player.user_id,
                                        f"🎯 نوبت شماست! لطفاً یک کارت بازی کنید."
                                    )
            
            # اعلام نوبت عادی
            else:
                if game.state == "playing":
                    next_player = game.get_player(game.turn_order[game.current_turn_index])
                    if next_player:
                        for p in game.players:
                            if p.user_id != next_player.user_id:
                                await context.bot.send_message(
                                    p.user_id,
                                    f"🎯 نوبت: {next_player.display_name}"
                                )
                            else:
                                await context.bot.send_message(
                                    next_player.user_id,
                                    f"🎯 نوبت شماست! لطفاً یک کارت بازی کنید."
                                )
            
            # اعلام برنده دست و شروع دست بعد
            if game.state == "hand_finished":
                team0 = [p for p in game.players if p.team == 0]
                team1 = [p for p in game.players if p.team == 1]
                team0_names = " و ".join(p.display_name for p in team0)
                team1_names = " و ".join(p.display_name for p in team1)
                team0_score = sum(p.tricks_won for p in game.players if p.team == 0)
                team1_score = sum(p.tricks_won for p in game.players if p.team == 1)
                
                winner_team = 0 if team0_score >= 7 else 1
                winner_names = team0_names if winner_team == 0 else team1_names
                winner_score = team0_score if winner_team == 0 else team1_score
                
                # اعلام برنده دست به همه
                for p in game.players:
                    await context.bot.send_message(
                        p.user_id,
                        f"🏆 **دست {game.hand_number} تمام شد!**\n\n"
                        f"🎯 تیم {winner_names} با {winner_score} امتیاز این دست را برد!\n"
                        f"📊 امتیازات کلی: تیم ۱ {game.team0_rounds} - {game.team1_rounds} تیم ۲\n\n"
                        f"🃏 در حال آماده‌سازی دست بعدی..."
                    )
                
                # بررسی پایان بازی نهایی
                if game.team0_rounds >= 7 or game.team1_rounds >= 7:
                    game.state = "finished"
                    for p in game.players:
                        if game.team0_rounds >= 7:
                            await context.bot.send_message(
                                p.user_id,
                                f"🏆 **بازی تمام شد!**\n\n"
                                f"🎯 تیم {team0_names} با {game.team0_rounds} دست به ۷ دست رسیدند!\n"
                                f"🏅 **برنده نهایی بازی:** {team0_names}\n"
                                f"🎉 تبریک به قهرمانان!\n\n"
                                f"📊 **نتیجه نهایی:**\n"
                                f"{team0_names}: {game.team0_rounds} دست\n"
                                f"{team1_names}: {game.team1_rounds} دست"
                            )
                        elif game.team1_rounds >= 7:
                            await context.bot.send_message(
                                p.user_id,
                                f"🏆 **بازی تمام شد!**\n\n"
                                f"🎯 تیم {team1_names} با {game.team1_rounds} دست به ۷ دست رسیدند!\n"
                                f"🏅 **برنده نهایی بازی:** {team1_names}\n"
                                f"🎉 تبریک به قهرمانان!\n\n"
                                f"📊 **نتیجه نهایی:**\n"
                                f"{team0_names}: {game.team0_rounds} دست\n"
                                f"{team1_names}: {game.team1_rounds} دست"
                            )
                        game_manager.remove_user_game(p.user_id)
                    game_manager.delete_game(game.game_id)
                    return
                
                # ریست برای دست بعدی
                game.reset_for_next_hand()
                
                # ارسال کارت‌های دور اول دست جدید
                for player in game.players:
                    cards_text = format_cards(player.cards)
                    teammate = game.get_teammate(player)
                    teammate_text = f"\n🤝 یار شما: {teammate.display_name}" if teammate else ""
                    await context.bot.send_message(
                        player.user_id,
                        f"🎴 **دست {game.hand_number} - کارت‌های دور اول**{teammate_text}\n\n"
                        f"🃏 ۵ کارت اولیه\n{cards_text}\n\n"
                        f"🏆 امتیازات کلی: تیم ۱ {game.team0_rounds} - {game.team1_rounds} تیم ۲\n\n"
                        f"⏳ منتظر انتخاب حکم..."
                    )
                
                # ارسال کیبورد انتخاب حکم به حاکم جدید
                chooser = game.get_player(game.trump_chooser_id)
                if chooser:
                    keyboard = [
                        [
                            InlineKeyboardButton("♥️ دل", callback_data=f"trump:{game.game_id}:hearts"),
                            InlineKeyboardButton("♦️ خشت", callback_data=f"trump:{game.game_id}:diamonds")
                        ],
                        [
                            InlineKeyboardButton("♣️ گیشنیز", callback_data=f"trump:{game.game_id}:clubs"),
                            InlineKeyboardButton("♠️ پیک", callback_data=f"trump:{game.game_id}:spades")
                        ]
                    ]
                    await context.bot.send_message(
                        chooser.user_id,
                        f"👑 **دست {game.hand_number} - شما انتخاب کننده حکم هستید!**\n\n"
                        f"🔢 کد بازی: {game.game_id[-6:]}\n"
                        f"{game._teams_info()}\n"
                        f"🏆 امتیازات کلی: تیم ۱ {game.team0_rounds} - {game.team1_rounds} تیم ۲\n"
                        f"👇 لطفاً خال حکم را انتخاب کنید:",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
            
            # پایان بازی نهایی
            elif game.state == "finished":
                team0 = [p for p in game.players if p.team == 0]
                team1 = [p for p in game.players if p.team == 1]
                team0_names = " و ".join(p.display_name for p in team0)
                team1_names = " و ".join(p.display_name for p in team1)
                
                for p in game.players:
                    if game.team0_rounds >= 7:
                        await context.bot.send_message(
                            p.user_id,
                            f"🏆 **بازی تمام شد!**\n\n"
                            f"🎯 تیم {team0_names} با {game.team0_rounds} دست به ۷ دست رسیدند!\n"
                            f"🏅 **برنده نهایی بازی:** {team0_names}\n"
                            f"🎉 تبریک به قهرمانان!\n\n"
                            f"📊 **نتیجه نهایی:**\n"
                            f"{team0_names}: {game.team0_rounds} دست\n"
                            f"{team1_names}: {game.team1_rounds} دست"
                        )
                    elif game.team1_rounds >= 7:
                        await context.bot.send_message(
                            p.user_id,
                            f"🏆 **بازی تمام شد!**\n\n"
                            f"🎯 تیم {team1_names} با {game.team1_rounds} دست به ۷ دست رسیدند!\n"
                            f"🏅 **برنده نهایی بازی:** {team1_names}\n"
                            f"🎉 تبریک به قهرمانان!\n\n"
                            f"📊 **نتیجه نهایی:**\n"
                            f"{team0_names}: {game.team0_rounds} دست\n"
                            f"{team1_names}: {game.team1_rounds} دست"
                        )
                    game_manager.remove_user_game(p.user_id)
                game_manager.delete_game(game.game_id)
                
        else:
            await query.answer(f"❌ {error}", show_alert=True)

# ==================== چت درون‌بازی ====================
async def private_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id < 0:
        return
    if update.message.text.startswith('/'):
        return

    user = update.effective_user
    game = game_manager.get_user_game(user.id)
    if not game:
        return

    player = game.get_player(user.id)
    if not player:
        return

    full_name = player.display_name
    message_text = update.message.text
    
    await update.message.reply_text(f"💬 {full_name}: {message_text}")
    
    for other in game.players:
        if other.user_id != user.id:
            try:
                await context.bot.send_message(
                    other.user_id,
                    f"💬 {full_name}: {message_text}"
                )
            except:
                pass

# ==================== راه‌اندازی ====================
def main():
    print("=" * 60)
    print("🤖 ربات پاسور - نسخه نهایی")
    print(f"📢 کانال اجباری: {REQUIRED_CHANNEL}")
    print("✅ 52 کارت منحصر به فرد - بدون تکرار")
    print("✅ 5 کارت اول ثابت + 8 کارت بعد از حکم")
    print("✅ تیم‌بندی درست (بازیکنان روبه‌رو)")
    print("✅ هر دست = 1 مسابقه ۷ امتیازی")
    print("✅ ۷ دست = برنده نهایی بازی")
    print("✅ برنده دست = شروع کننده دست بعد")
    print("✅ اعلام برنده دست و شروع خودکار دست بعد")
    print("✅ لینک دعوت تا پایان بازی معتبر")
    print("=" * 60)

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", private_start))
    app.add_handler(CommandHandler("newgame", newgame_command))
    app.add_handler(CommandHandler("mygame", mygame_command))
    app.add_handler(CommandHandler("startgame", startgame_command))
    app.add_handler(CommandHandler("leave", leave_command))
    app.add_handler(CommandHandler("close", close_command))

    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        private_chat_handler
    ))

    app.add_handler(CallbackQueryHandler(private_callback_handler))

    print("✅ ربات آماده است!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
