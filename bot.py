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
            Suit.CLUBS: "گیشنیز",
            Suit.SPADES: "پیک"
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
        self.has_started_bot: bool = False
        self.team: Optional[int] = None  # تیم 0 یا 1
        self.position: Optional[int] = None  # موقعیت در میز (0-3)
        self.last_checked: datetime = datetime.now()
    
    @property
    def display_name(self):
        if self.username:
            return f"@{self.username}"
        return self.first_name or f"User_{self.user_id}"
    
    def get_verification_status(self):
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
        self.turn_order: List[int] = []  # ترتیب نشستن بازیکنان
        self.current_turn_index: int = 0
        self.trump_suit: Optional[Suit] = None
        self.trump_chooser_id: Optional[int] = None
        self.state: str = "waiting"
        self.message_id: Optional[int] = None
        self.created_at = datetime.now()
        self.verification_messages: Dict[int, int] = {}
        self.player_cards_messages: Dict[int, int] = {}
        self.join_requests: Dict[int, str] = {}
        self.teams: Dict[int, List[Player]] = {0: [], 1: []}  # دو تیم
        self.callback_message_id: Optional[int] = None  # پیام callback برای انتخاب حکم
    
    def add_player(self, player: Player) -> bool:
        if len(self.players) >= 4:
            return False
        if any(p.user_id == player.user_id for p in self.players):
            return False
        
        # تعیین موقعیت بازیکن
        player.position = len(self.players)
        self.players.append(player)
        
        # وقتی 4 بازیکن کامل شدند، تیم‌بندی کن
        if len(self.players) == 4:
            self.assign_teams()
        
        return True
    
    def assign_teams(self):
        """تیم‌بندی بازیکنان بر اساس موقعیت"""
        self.teams = {0: [], 1: []}
        
        # بازیکنان در موقعیت‌های زوج و فرد تیم‌های مختلف
        for i, player in enumerate(self.players):
            team = i % 2  # 0 یا 1
            player.team = team
            self.teams[team].append(player)
    
    def remove_player(self, user_id: int) -> bool:
        for i, player in enumerate(self.players):
            if player.user_id == user_id:
                self.players.pop(i)
                # بازسازی موقعیت‌ها
                for idx, p in enumerate(self.players):
                    p.position = idx
                return True
        return False
    
    def get_player(self, user_id: int) -> Optional[Player]:
        for player in self.players:
            if player.user_id == user_id:
                return player
        return None
    
    def get_teammate(self, player: Player) -> Optional[Player]:
        """یار بازیکن را پیدا کن"""
        if player.team is None:
            return None
        
        for p in self.players:
            if p.team == player.team and p.user_id != player.user_id:
                return p
        return None
    
    def get_team_players(self, team: int) -> List[Player]:
        """بازیکنان یک تیم را برگردان"""
        return self.teams.get(team, [])
    
    def initialize_deck(self):
        self.deck = []
        for suit in [Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS, Suit.SPADES]:
            for rank in RANKS.values():
                self.deck.append(Card(suit, rank))
        random.shuffle(self.deck)
    
    def deal_cards(self):
        cards_per_player = 13
        for i, player in enumerate(self.players):
            start = i * cards_per_player
            end = start + cards_per_player
            player.cards = self.deck[start:end]
            # مرتب کردن کارت‌ها: اول بر اساس خال، سپس ارزش
            player.cards.sort(key=lambda c: (c.suit.value, -c.rank.value))
    
    def start_game(self):
        if len(self.players) < 4:
            return False
        
        if not all(player.verified for player in self.players):
            return False
        
        # تیم‌بندی مجدد
        self.assign_teams()
        
        self.initialize_deck()
        self.deal_cards()
        
        # ترتیب نشستن بازیکنان
        self.turn_order = [p.user_id for p in self.players]
        
        # انتخاب تصادفی شروع کننده
        random.shuffle(self.turn_order)
        self.current_turn_index = 0
        
        self.state = "choosing_trump"
        self.trump_chooser_id = self.turn_order[0]
        
        return True
    
    def choose_trump(self, user_id: int, suit: Suit) -> bool:
        if self.state != "choosing_trump" or user_id != self.trump_chooser_id:
            return False
        
        self.trump_suit = suit
        self.state = "playing"
        
        # وقتی حکم انتخاب شد، ترتیب اصلی را برگردان
        self.turn_order = [p.user_id for p in self.players]
        # بازیکن انتخاب کننده حکم شروع می‌کند
        chooser_index = self.turn_order.index(user_id)
        self.current_turn_index = chooser_index
        
        return True
    
    def create_cards_keyboard(self, player_id: int) -> Optional[InlineKeyboardMarkup]:
        player = self.get_player(player_id)
        if not player or not player.cards:
            return None
        
        # گروه‌بندی کارت‌ها بر اساس خال
        cards_by_suit = defaultdict(list)
        for i, card in enumerate(player.cards):
            cards_by_suit[card.suit].append((i, card))
        
        keyboard = []
        
        # ترتیب نمایش: دل، خشت، گیشنیز، پیک
        display_order = [Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS, Suit.SPADES]
        
        for suit in display_order:
            if suit in cards_by_suit:
                row = []
                for card_idx, card in cards_by_suit[suit]:
                    # مرتب کردن کارت‌های هر خال از بزرگ به کوچک
                    cards_by_suit[suit].sort(key=lambda x: -x[1].value)
                
                for card_idx, card in cards_by_suit[suit]:
                    # نمایش: نماد + شکل خال
                    display_text = f"{card.rank.symbol}{card.suit.value}"
                    row.append(InlineKeyboardButton(
                        display_text,
                        callback_data=f"playcard_{self.game_id}_{card_idx}"
                    ))
                
                if row:
                    # اگر تعداد کارت‌ها زیاد است، در چند ردیف نمایش بده
                    chunk_size = 4
                    for i in range(0, len(row), chunk_size):
                        keyboard.append(row[i:i + chunk_size])
        
        return InlineKeyboardMarkup(keyboard) if keyboard else None
    
    def can_play_card(self, player: Player, card: Card) -> bool:
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
        if self.state != "playing":
            return False, None, "بازی در حال اجرا نیست"
        
        current_player_id = self.turn_order[self.current_turn_index]
        if user_id != current_player_id:
            return False, None, "نوبت شما نیست"
        
        player = self.get_player(user_id)
        if not player or card_index >= len(player.cards):
            return False, None, "کارت نامعتبر"
        
        card = player.cards[card_index]
        
        if not self.can_play_card(player, card):
            valid_cards = [c for c in player.cards if self.can_play_card(player, c)]
            if valid_cards:
                valid_names = ", ".join([c.persian_name for c in valid_cards])
                return False, None, f"باید همخال بیاورید. کارت‌های مجاز: {valid_names}"
            else:
                return False, None, "خطا در بررسی کارت"
        
        player.cards.pop(card_index)
        
        if len(self.current_round.cards_played) == 0:
            self.current_round.starting_player_id = user_id
        
        self.current_round.cards_played[user_id] = card
        self.current_turn_index = (self.current_turn_index + 1) % 4
        
        if self.current_round.is_complete(4):
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
    
    def get_game_info_text(self) -> str:
        text = f"🎴 **بازی پاسور (حکم)** - کد: `{self.game_id[-6:]}`\n\n"
        
        if self.state == "waiting":
            text += f"⏳ **در انتظار بازیکنان** ({len(self.players)}/4)\n\n"
            text += "👥 **بازیکنان:**\n"
            for i, player in enumerate(self.players, 1):
                status = player.get_verification_status()
                team_info = f" (تیم {player.team+1})" if player.team is not None else ""
                text += f"{i}. {player.display_name}{team_info} - {status}\n"
            
            if len(self.players) == 4:
                text += "\n🤝 **تیم‌بندی:**\n"
                for team_num, team_players in self.teams.items():
                    if team_players:
                        names = " و ".join([p.display_name for p in team_players])
                        text += f"تیم {team_num+1}: {names}\n"
            
            text += f"\n📢 برای بازی باید عضو کانال {REQUIRED_CHANNEL} باشید.\n"
            
            creator = self.get_player(self.creator_id)
            if creator:
                text += f"🎮 **سازنده:** {creator.display_name}"
        
        elif self.state == "choosing_trump":
            chooser = self.get_player(self.trump_chooser_id)
            text += "👑 **انتخاب خال حکم**\n\n"
            
            # نمایش تیم‌ها
            text += "🤝 **تیم‌ها:**\n"
            for team_num, team_players in self.teams.items():
                if team_players:
                    names = " و ".join([p.display_name for p in team_players])
                    text += f"تیم {team_num+1}: {names}\n"
            
            text += f"\n🎯 **انتخاب کننده حکم:** {chooser.display_name if chooser else '?'}\n"
            text += f"📊 **دست:** {len(self.rounds) + 1}/13\n\n"
            text += "👇 **لطفا خال حکم را انتخاب کنید:**"
        
        elif self.state == "playing":
            current_player = self.get_player(self.turn_order[self.current_turn_index])
            text += f"🎮 **دور:** {len(self.rounds) + 1}/13\n"
            text += f"🃏 **خال حکم:** {self.trump_suit.value if self.trump_suit else '?'} {self.trump_suit.persian_name if self.trump_suit else ''}\n"
            text += f"🎯 **نوبت:** {current_player.display_name if current_player else '?'}\n\n"
            
            text += "🤝 **تیم‌ها:**\n"
            for team_num, team_players in self.teams.items():
                if team_players:
                    names = " و ".join([p.display_name for p in team_players])
                    total_tricks = sum(p.tricks_won for p in team_players)
                    text += f"تیم {team_num+1} ({names}): {total_tricks} دست\n"
            
            if self.current_round.cards_played:
                text += "\n🎴 **کارت‌های این دور:**\n"
                for player_id, card in self.current_round.cards_played.items():
                    player = self.get_player(player_id)
                    text += f"• {player.display_name if player else '?'}: {card.persian_name}\n"
        
        elif self.state == "finished":
            text += "🏆 **بازی تمام شد!**\n\n"
            
            # محاسبه امتیاز تیم‌ها
            team_scores = {0: 0, 1: 0}
            for player in self.players:
                if player.team is not None:
                    team_scores[player.team] += player.tricks_won
            
            text += "📊 **نتایج نهایی:**\n"
            for team_num in [0, 1]:
                team_players = self.teams.get(team_num, [])
                if team_players:
                    names = " و ".join([p.display_name for p in team_players])
                    score = team_scores.get(team_num, 0)
                    text += f"\n🤝 **تیم {team_num+1}** ({names}):\n"
                    for player in team_players:
                        text += f"  • {player.display_name}: {player.tricks_won} دست\n"
                    text += f"  🎯 **مجموع تیم:** {score} دست"
            
            # برنده
            if team_scores[0] > team_scores[1]:
                text += f"\n\n🏅 **برنده: تیم 1**"
            elif team_scores[1] > team_scores[0]:
                text += f"\n\n🏅 **برنده: تیم 2**"
            else:
                text += f"\n\n🤝 **مساوی!**"
        
        return text
    
    def update_verification_status(self, user_id: int, is_verified: bool):
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
        self.user_started_bot: Dict[int, bool] = {}
    
    def create_game(self, chat_id: int, creator: Player) -> Optional[Game]:
        if chat_id > 0:
            return None
        
        game_id = f"hokm_{chat_id}_{int(datetime.now().timestamp())}"
        game = Game(game_id=game_id, chat_id=chat_id, creator_id=creator.user_id)
        
        creator.verified = True
        creator.is_channel_member = True
        creator.has_started_bot = True
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
        self.user_started_bot[user_id] = True
    
    def has_user_started_bot(self, user_id: int) -> bool:
        return self.user_started_bot.get(user_id, False)
    
    def add_join_request(self, user_id: int, game_id: str):
        game = self.get_game(game_id)
        if game:
            game.join_requests[user_id] = game_id

game_manager = GameManager()

# ==================== تایید عضویت ====================

async def check_channel_membership(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Tuple[bool, str]:
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
            text=f"🔐 **تایید عضویت برای بازی پاسور**\n\n"
                 f"📢 **کانال اجباری:** {REQUIRED_CHANNEL}\n"
                 f"🔢 **کد بازی:** `{game.game_id[-6:]}`\n\n"
                 f"📋 **مراحل:**\n"
                 f"۱. روی 'جوین شو در کانال' کلیک کنید\n"
                 f"۲. به کانال بپیوندید (Join)\n"
                 f"۳. روی 'بررسی عضویت من' کلیک کنید\n\n"
                 f"⚠️ **بدون تایید عضویت نمی‌توانید بازی کنید.**",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        
        game.verification_messages[user_id] = message.message_id
        
        return message.message_id
        
    except Exception as e:
        logger.error(f"خطا در ارسال پیام تایید: {e}")
        return None

async def verify_player_membership(context: ContextTypes.DEFAULT_TYPE, user_id: int, game: Game) -> Tuple[bool, str]:
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
            
            return True, "✅ **عضویت شما تایید شد!** حالا می‌توانید بازی کنید."
        else:
            if user_id not in game.verification_messages:
                await send_verification_message(context, user_id, game)
            
            return False, f"❌ {message}\n\nلطفا به کانال {REQUIRED_CHANNEL} بپیوندید."
            
    except Exception as e:
        logger.error(f"خطا در تایید عضویت: {e}")
        return False, f"خطا در بررسی عضویت: {str(e)[:50]}"

async def update_game_message(context: ContextTypes.DEFAULT_TYPE, game: Game):
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
            # دکمه‌های انتخاب حکم با callback_data درست
            keyboard = [
                [
                    InlineKeyboardButton("♥️ دل", callback_data=f"choosetrump_{game.game_id}_hearts"),
                    InlineKeyboardButton("♦️ خشت", callback_data=f"choosetrump_{game.game_id}_diamonds")
                ],
                [
                    InlineKeyboardButton("♣️ گیشنیز", callback_data=f"choosetrump_{game.game_id}_clubs"),
                    InlineKeyboardButton("♠️ پیک", callback_data=f"choosetrump_{game.game_id}_spades")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            reply_markup = None
        
        await context.bot.edit_message_text(
            chat_id=game.chat_id,
            message_id=game.message_id,
            text=game.get_game_info_text(),
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"خطا در آپدیت پیام بازی: {e}")

async def send_trump_choice_message(context: ContextTypes.DEFAULT_TYPE, game: Game):
    """ارسال پیام جداگانه برای انتخاب حکم"""
    try:
        chooser = game.get_player(game.trump_chooser_id)
        
        # ایجاد پیام جدید برای انتخاب حکم
        text = f"👑 **{chooser.display_name if chooser else '?'}**\n"
        text += "لطفا خال حکم را انتخاب کنید:\n\n"
        
        keyboard = [
            [
                InlineKeyboardButton("♥️ دل", callback_data=f"choosetrump_{game.game_id}_hearts"),
                InlineKeyboardButton("♦️ خشت", callback_data=f"choosetrump_{game.game_id}_diamonds")
            ],
            [
                InlineKeyboardButton("♣️ گیشنیز", callback_data=f"choosetrump_{game.game_id}_clubs"),
                InlineKeyboardButton("♠️ پیک", callback_data=f"choosetrump_{game.game_id}_spades")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = await context.bot.send_message(
            chat_id=game.chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        
        game.callback_message_id = message.message_id
        
    except Exception as e:
        logger.error(f"خطا در ارسال پیام انتخاب حکم: {e}")

# ==================== دستورات ربات ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    if chat_id > 0:
        game_manager.mark_user_started(user.id)
        
        user_games = []
        for game_id, game in game_manager.games.items():
            if user.id in game.join_requests:
                user_games.append(game)
        
        if user_games:
            text = f"سلام {user.first_name}! 👋\n\n"
            text += "🎴 **به ربات بازی پاسور خوش آمدید!**\n\n"
            text += "📋 **شما درخواست پیوستن به بازی زیر را دارید:**\n\n"
            
            for game in user_games[:3]:
                creator = game.get_player(game.creator_id)
                text += f"🔢 **کد بازی:** `{game.game_id[-6:]}`\n"
                text += f"👤 **سازنده:** {creator.display_name if creator else '?'}\n"
                text += f"👥 **بازیکنان:** {len(game.players)}/4\n\n"
                
                keyboard = [
                    [
                        InlineKeyboardButton("✅ تایید عضویت و پیوستن", callback_data=f"verify_join_{game.game_id}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
                return
        
        keyboard = [
            [InlineKeyboardButton("📋 راهنمای استفاده", callback_data="help_guide")],
            [InlineKeyboardButton("🎮 بازی‌های فعال من", callback_data="my_games")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"سلام {user.first_name}! 👋\n\n"
            "🎴 **به ربات بازی پاسور (حکم) خوش آمدید!**\n\n"
            "📋 **برای ایجاد بازی:**\n"
            "۱. ربات را به یک گروه اضافه کنید\n"
            "۲. در گروه دستور `/newgame` را وارد کنید\n\n"
            f"📢 **برای بازی باید عضو کانال {REQUIRED_CHANNEL} باشید.**",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"سلام {user.first_name}! 👋\n\n"
            "🎴 **ربات بازی پاسور آماده است!**\n"
            "برای ایجاد بازی جدید `/newgame` را وارد کنید.",
            parse_mode="Markdown"
        )

async def new_game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    
    if chat_id > 0:
        keyboard = [
            [InlineKeyboardButton("➕ اضافه کردن ربات به گروه", url=f"https://t.me/{context.bot.username}?startgroup=new")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "❌ **بازی فقط در گروه قابل ایجاد است!**\n\n"
            "لطفا:\n"
            "۱. روی دکمه زیر کلیک کنید\n"
            "۲. ربات را به گروه مورد نظر اضافه کنید\n"
            "۳. سپس در گروه دستور `/newgame` را وارد کنید",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return
    
    player = Player(user.id, user.username, user.first_name)
    game = game_manager.create_game(chat_id, player)
    
    if not game:
        await update.message.reply_text("❌ **خطا در ایجاد بازی!**", parse_mode="Markdown")
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
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    game.message_id = message.message_id
    
    await update.message.reply_text(
        f"✅ **بازی جدید ایجاد شد!**\n"
        f"🎮 **شما سازنده این بازی هستید.**\n"
        f"🔢 **کد بازی:** `{game.game_id[-6:]}`\n\n"
        f"دیگران می‌توانند با کلیک روی **'پیوستن به بازی'** به این بازی بپیوندند.",
        parse_mode="Markdown"
    )

async def verify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    game = game_manager.get_player_game(user.id)
    
    if not game:
        await update.message.reply_text("❌ **شما در هیچ بازی فعالی نیستید!**", parse_mode="Markdown")
        return
    
    success, message = await verify_player_membership(context, user.id, game)
    
    await update.message.reply_text(message, parse_mode="Markdown")

# ==================== مدیریت کلیک‌ها ====================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data
    
    logger.info(f"📱 کلیک دریافت شد: {data} از کاربر {user.id}")
    
    if data == "help_guide":
        keyboard = [
            [InlineKeyboardButton("➕ اضافه کردن به گروه", url=f"https://t.me/{context.bot.username}?startgroup=new")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text="📋 **راهنمای استفاده از ربات:**\n\n"
                 "🎮 **مراحل ایجاد بازی:**\n"
                 "۱. روی دکمه زیر کلیک کنید\n"
                 "۲. ربات را به گروه اضافه کنید\n"
                 "۳. در گروه دستور `/newgame` را بزنید\n\n"
                 "👥 **مراحل پیوستن به بازی:**\n"
                 "۱. در گروه روی **'پیوستن به بازی'** کلیک کنید\n"
                 "۲. به پیوی ربات می‌روید\n"
                 "۳. عضویت خود را تایید می‌کنید\n"
                 "۴. به صورت خودکار به بازی اضافه می‌شوید\n\n"
                 "🤝 **قوانین بازی:**\n"
                 "• ۴ بازیکن در دو تیم ۲ نفره\n"
                 "• تیم‌ها: بازیکنان روبه‌روی هم یار هستند\n"
                 "• هدف: بردن حداقل ۷ دست از ۱۳ دست\n"
                 "• حکم: توسط اولین بازیکن انتخاب می‌شود\n\n"
                 f"📢 **کانال اجباری:** {REQUIRED_CHANNEL}",
            reply_markup=reply_markup,
            parse_mode="Markdown"
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
        
        game_manager.add_join_request(user.id, game_id)
        
        try:
            channel = REQUIRED_CHANNEL.lstrip('@')
            keyboard = [
                [
                    InlineKeyboardButton("✅ تایید عضویت و پیوستن", callback_data=f"verify_join_{game.game_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=user.id,
                text=f"🎴 **درخواست پیوستن به بازی پاسور**\n\n"
                     f"🔢 **کد بازی:** `{game.game_id[-6:]}`\n"
                     f"📢 **کانال لازم:** {REQUIRED_CHANNEL}\n\n"
                     f"📋 **برای پیوستن:**\n"
                     f"۱. عضو کانال شوید\n"
                     f"۲. روی دکمه زیر کلیک کنید\n"
                     f"۳. عضویت شما بررسی و به بازی اضافه می‌شوید",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            
            await query.answer("✅ به پیوی ربات بروید و عضویت خود را تایید کنید!", show_alert=True)
            
        except Exception as e:
            logger.error(f"خطا در ارسال پیام به کاربر: {e}")
            await query.answer("❌ ابتدا به ربات پیام بدهید: @konkorkhabarbot", show_alert=True)
    
    elif data.startswith("verify_join_"):
        game_id = data[12:]
        game = game_manager.get_game(game_id)
        
        if not game:
            await query.answer("❌ بازی یافت نشد!", show_alert=True)
            return
        
        if any(p.user_id == user.id for p in game.players):
            await query.answer("⚠️ شما قبلاً در بازی هستید!", show_alert=True)
            return
        
        if len(game.players) >= 4:
            await query.answer("❌ بازی تکمیل است!", show_alert=True)
            return
        
        is_member, message = await check_channel_membership(context, user.id)
        
        if is_member:
            player = Player(user.id, user.username, user.first_name)
            player.has_started_bot = True
            player.verified = True
            player.is_channel_member = True
            
            if game.add_player(player):
                game_manager.user_games[user.id] = game.game_id
                game_manager.mark_user_started(user.id)
                
                if user.id in game.join_requests:
                    game.join_requests.pop(user.id)
                
                await update_game_message(context, game)
                
                await query.answer("✅ شما با موفقیت به بازی اضافه شدید!", show_alert=True)
                
                await query.edit_message_text(
                    text=f"✅ **تایید عضویت موفق!**\n\n"
                         f"🎮 **شما به بازی اضافه شدید.**\n"
                         f"🔢 **کد بازی:** `{game.game_id[-6:]}`\n"
                         f"👤 **سازنده:** {game.get_player(game.creator_id).display_name if game.get_player(game.creator_id) else '?'}\n\n"
                         f"📌 **برای دیدن وضعیت بازی به گروه برگردید.**",
                    reply_markup=None,
                    parse_mode="Markdown"
                )
            else:
                await query.answer("❌ خطا در اضافه کردن به بازی!", show_alert=True)
        else:
            channel = REQUIRED_CHANNEL.lstrip('@')
            keyboard = [
                [
                    InlineKeyboardButton("📢 جوین شو در کانال", url=f"https://t.me/{channel}"),
                    InlineKeyboardButton("🔄 بررسی مجدد", callback_data=f"verify_join_{game.game_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                text=f"❌ **شما عضو کانال {REQUIRED_CHANNEL} نیستید!**\n\n"
                     f"⚠️ **لطفا:**\n"
                     f"۱. به کانال بپیوندید\n"
                     f"۲. سپس روی **'بررسی مجدد'** کلیک کنید\n\n"
                     f"🔢 **کد بازی:** `{game.game_id[-6:]}`",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            
            await query.answer("❌ هنوز عضو کانال نیستید!", show_alert=True)
    
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
        
        if game.start_game():
            # آپدیت پیام اصلی
            await update_game_message(context, game)
            
            # ارسال پیام جداگانه برای انتخاب حکم
            await send_trump_choice_message(context, game)
            
            await query.answer("✅ بازی شروع شد! اولین بازیکن باید خال حکم را انتخاب کند.", show_alert=True)
            
            # اطلاع به انتخاب کننده حکم
            chooser = game.get_player(game.trump_chooser_id)
            if chooser:
                try:
                    await context.bot.send_message(
                        chat_id=chooser.user_id,
                        text=f"👑 **شما انتخاب کننده خال حکم هستید!**\n\n"
                             f"لطفا در گروه روی یکی از دکمه‌های زیر کلیک کنید:\n"
                             f"♥️ دل - ♦️ خشت - ♣️ گیشنیز - ♠️ پیک"
                    )
                except:
                    pass
        else:
            await query.answer("❌ خطا در شروع بازی!", show_alert=True)
    
    elif data.startswith("choosetrump_"):
        # انتخاب حکم - CALLBACK_DATA: choosetrump_{game_id}_{suit}
        parts = data.split("_")
        
        if len(parts) >= 3:
            game_id = parts[1]
            suit_str = parts[2]  # hearts, diamonds, clubs, spades
            game = game_manager.get_game(game_id)
            
            if not game:
                await query.answer("❌ بازی یافت نشد!", show_alert=True)
                return
            
            logger.info(f"دریافت انتخاب حکم: game_id={game_id}, suit={suit_str}, user={user.id}")
            
            # بررسی اینکه آیا کاربر همان انتخاب کننده حکم است
            if user.id != game.trump_chooser_id:
                await query.answer("❌ فقط انتخاب کننده حکم می‌تواند خال را انتخاب کند!", show_alert=True)
                return
            
            # تبدیل string به Suit enum
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
            
            # انتخاب حکم
            if game.choose_trump(user.id, suit):
                # حذف پیام انتخاب حکم
                if game.callback_message_id:
                    try:
                        await context.bot.delete_message(
                            chat_id=game.chat_id,
                            message_id=game.callback_message_id
                        )
                    except:
                        pass
                    game.callback_message_id = None
                
                await query.answer(f"✅ خال حکم انتخاب شد: {suit.value} {suit.persian_name}", show_alert=True)
                
                # آپدیت پیام اصلی بازی
                await update_game_message(context, game)
                
                # ارسال پیام اعلان انتخاب حکم
                await context.bot.send_message(
                    chat_id=game.chat_id,
                    text=f"🎉 **خال حکم انتخاب شد!**\n\n"
                         f"🃏 **حکم:** {suit.value} {suit.persian_name}\n"
                         f"👑 **انتخاب کننده:** {user.first_name}\n\n"
                         f"🎮 **بازی آغاز می‌شود...**",
                    parse_mode="Markdown"
                )
                
                # ارسال کارت‌ها به همه بازیکنان
                for player in game.players:
                    if player.cards:
                        keyboard = game.create_cards_keyboard(player.user_id)
                        if keyboard:
                            try:
                                # حذف پیام قبلی اگر وجود دارد
                                if player.user_id in game.player_cards_messages:
                                    try:
                                        await context.bot.delete_message(
                                            chat_id=player.user_id,
                                            message_id=game.player_cards_messages[player.user_id]
                                        )
                                    except:
                                        pass
                                
                                # نمایش یار بازیکن
                                teammate = game.get_teammate(player)
                                teammate_info = f"\n🤝 **یار شما:** {teammate.display_name}" if teammate else ""
                                
                                # ایجاد متن کارت‌ها
                                cards_text = ""
                                current_suit = None
                                for i, card in enumerate(player.cards):
                                    if card.suit != current_suit:
                                        if current_suit is not None:
                                            cards_text += "\n"
                                        cards_text += f"\n**{card.suit.persian_name}:**\n"
                                        current_suit = card.suit
                                    cards_text += f"  {card.rank.symbol}{card.suit.value}  "
                                
                                # ارسال پیام جدید
                                message = await context.bot.send_message(
                                    chat_id=player.user_id,
                                    text=f"🎴 **کارت‌های شما** {teammate_info}\n\n"
                                         f"🃏 **خال حکم:** {suit.value} {suit.persian_name}\n\n"
                                         f"{cards_text}\n\n"
                                         f"🎯 **نوبت:** {game.get_player(game.turn_order[game.current_turn_index]).display_name}",
                                    reply_markup=keyboard,
                                    parse_mode="Markdown"
                                )
                                game.player_cards_messages[player.user_id] = message.message_id
                                
                            except Exception as e:
                                logger.error(f"خطا در ارسال کارت‌ها به کاربر {player.user_id}: {e}")
            else:
                await query.answer("❌ خطا در انتخاب حکم!", show_alert=True)
    
    elif data.startswith("playcard_"):
        # بازی کردن کارت - CALLBACK_DATA: playcard_{game_id}_{card_index}
        parts = data.split("_")
        
        if len(parts) >= 3:
            game_id = parts[1]
            card_index = int(parts[2])
            game = game_manager.get_game(game_id)
            
            if not game:
                await query.answer("❌ بازی یافت نشد!", show_alert=True)
                return
            
            logger.info(f"بازی کردن کارت: game_id={game_id}, card_index={card_index}, user={user.id}")
            
            success, card, error_msg = game.play_card(user.id, card_index)
            
            if success and card:
                await query.answer(f"✅ کارت بازی شد: {card.persian_name}", show_alert=True)
                
                # آپدیت پیام بازی در گروه
                await update_game_message(context, game)
                
                # اطلاع به همه از کارت بازی شده
                player = game.get_player(user.id)
                if player:
                    await context.bot.send_message(
                        chat_id=game.chat_id,
                        text=f"🎴 **{player.display_name}** کارت بازی کرد:\n"
                             f"**{card.persian_name}**",
                        parse_mode="Markdown"
                    )
                
                # آپدیت کارت‌های بازیکن
                if player and player.cards:
                    keyboard = game.create_cards_keyboard(user.id)
                    if keyboard:
                        try:
                            if user.id in game.player_cards_messages:
                                await context.bot.delete_message(
                                    chat_id=user.id,
                                    message_id=game.player_cards_messages[user.id]
                                )
                            
                            # نمایش یار
                            teammate = game.get_teammate(player)
                            teammate_info = f"\n🤝 **یار شما:** {teammate.display_name}" if teammate else ""
                            
                            # ایجاد متن کارت‌ها
                            cards_text = ""
                            current_suit = None
                            for i, card in enumerate(player.cards):
                                if card.suit != current_suit:
                                    if current_suit is not None:
                                        cards_text += "\n"
                                    cards_text += f"\n**{card.suit.persian_name}:**\n"
                                    current_suit = card.suit
                                cards_text += f"  {card.rank.symbol}{card.suit.value}  "
                            
                            message = await context.bot.send_message(
                                chat_id=user.id,
                                text=f"🎴 **کارت‌های شما** {teammate_info}\n\n"
                                     f"🃏 **خال حکم:** {game.trump_suit.value if game.trump_suit else ''} {game.trump_suit.persian_name if game.trump_suit else ''}\n\n"
                                     f"{cards_text}\n\n"
                                     f"🎯 **نوبت:** {game.get_player(game.turn_order[game.current_turn_index]).display_name}",
                                reply_markup=keyboard,
                                parse_mode="Markdown"
                            )
                            game.player_cards_messages[user.id] = message.message_id
                        except Exception as e:
                            logger.error(f"خطا در آپدیت کارت‌های کاربر {user.id}: {e}")
                
                # اگر دور تمام شد
                if len(game.current_round.cards_played) == 0 and game.current_round.winner_id:
                    winner = game.get_player(game.current_round.winner_id)
                    if winner:
                        await context.bot.send_message(
                            chat_id=game.chat_id,
                            text=f"🏆 **برنده دور:** {winner.display_name}\n"
                                 f"✅ دست برده شد!",
                            parse_mode="Markdown"
                        )
                
                # اگر بازی تمام شد
                if game.state == "finished":
                    await context.bot.send_message(
                        chat_id=game.chat_id,
                        text="🎉 **بازی به پایان رسید!**\n\n"
                             "برای دیدن نتایج نازی پیام بازی را چک کنید.",
                        parse_mode="Markdown"
                    )
                    await update_game_message(context, game)
                    
            else:
                await query.answer(f"❌ {error_msg}", show_alert=True)

# ==================== اجرای ربات ====================

def main():
    print("🤖 ربات پاسور Railway در حال راه‌اندازی...")
    print(f"📢 کانال اجباری: {REQUIRED_CHANNEL}")
    print("✅ سیستم تایید عضویت فعال")
    print("🎮 سیستم تیم‌بندی و انتخاب حکم فعال")
    
    application = Application.builder() \
        .token(TOKEN) \
        .connect_timeout(30.0) \
        .read_timeout(30.0) \
        .write_timeout(30.0) \
        .pool_timeout(30.0) \
        .build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("newgame", new_game_command))
    application.add_handler(CommandHandler("verify", verify_command))
    
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
