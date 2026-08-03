import asyncio
import logging
import time

import wikipedia
from ddgs import DDGS
from telegram import Update
from telegram.ext import ContextTypes

from ai_service import get_ai_response
from config import MAIN_GROUP_ID, OWNER_ID

logger = logging.getLogger(__name__)

BOT_ALIVE = True

# Separate cooldown buckets so /ask's longer cooldown never bleeds into
# normal chat cooldown (and vice versa).
message_cooldowns: dict[int, float] = {}
ask_cooldowns: dict[int, float] = {}

MESSAGE_COOLDOWN_SECONDS = 3
ASK_COOLDOWN_SECONDS = 5

# How often (in seconds) to sweep stale cooldown entries so the dicts
# don't grow forever over a long-running process.
COOLDOWN_SWEEP_INTERVAL = 3600
_last_sweep_time = time.time()


def _sweep_cooldowns() -> None:
    """Drop cooldown entries older than 1 hour. Cheap, called opportunistically."""
    global _last_sweep_time
    now = time.time()
    if now - _last_sweep_time < COOLDOWN_SWEEP_INTERVAL:
        return
    _last_sweep_time = now
    cutoff = now - COOLDOWN_SWEEP_INTERVAL
    for d in (message_cooldowns, ask_cooldowns):
        stale = [uid for uid, ts in d.items() if ts < cutoff]
        for uid in stale:
            del d[uid]


def _is_on_cooldown(cooldown_dict: dict, user_id: int, seconds: int) -> bool:
    now = time.time()
    last = cooldown_dict.get(user_id)
    if last is not None and now - last < seconds:
        return True
    cooldown_dict[user_id] = now
    return False


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start command"""
    await update.message.reply_text(
        "Hii! 💕 Main Mitsuri hoon! Aapka swagat hai. Main aapse baat karne ke liye bahut excited hoon! 🥰"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /help command"""
    help_text = (
        "Mere commands:\n"
        "/start - Bot ko start karein\n"
        "/help - Ye message dekhein\n"
        "/ask <query> - Internet pe kuch dhoondna ho toh!\n"
        "Bas mujhe message bhejein aur main Hinglish me reply karungi! 💖"
    )
    await update.message.reply_text(help_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for normal text messages to talk to the AI"""
    global BOT_ALIVE
    if not BOT_ALIVE:
        return

    if not update.message or not update.message.text:
        return

    # Ignore messages sent by other bots to prevent infinite bot-to-bot loops
    if update.message.from_user and update.message.from_user.is_bot:
        return

    user_id = update.effective_user.id if update.effective_user else 0

    _sweep_cooldowns()
    if _is_on_cooldown(message_cooldowns, user_id, MESSAGE_COOLDOWN_SECONDS):
        return

    text = update.message.text
    user_name = update.message.from_user.first_name if update.message.from_user else "Someone"

    chat_type = update.effective_chat.type

    # If in a group, only respond if mentioned or replied to
    if chat_type in ["group", "supergroup"]:
        bot_username = (context.bot.username or "").lower()
        text_lower = text.lower()
        is_mentioned = "mitsuri" in text_lower or (bot_username and f"@{bot_username}" in text_lower)
        is_reply_to_bot = (
            update.message.reply_to_message
            and update.message.reply_to_message.from_user
            and update.message.reply_to_message.from_user.id == context.bot.id
        )

        if not (is_mentioned or is_reply_to_bot):
            return  # Ignore message if not targeted at bot

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Prepend user name so AI knows who is speaking
    prompt_with_context = f"[{user_name}]: {text}"

    response = await get_ai_response(prompt_with_context, update.effective_chat.id, user_name)
    await update.message.reply_text(response)


async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /ask command to search the internet"""
    if not context.args:
        await update.message.reply_text(
            "Kya dhoondna hai? Please query bhi likho na! Jaise: /ask What is quantum computing 🥺"
        )
        return

    user_id = update.effective_user.id if update.effective_user else 0

    _sweep_cooldowns()
    if _is_on_cooldown(ask_cooldowns, user_id, ASK_COOLDOWN_SECONDS):
        await update.message.reply_text("Arey thoda slow type karo na! Ek minute ruko please... 🥺")
        return

    query = " ".join(context.args)
    chat_id = update.effective_chat.id

    # Send a thinking message
    thinking_msg = await update.message.reply_text("Ruko main abhi internet pe check karti hoon... 🔍💕")
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    search_context = ""

    # Wikipedia and DDGS are blocking/sync network calls. Running them
    # directly here would freeze the entire bot's event loop for every
    # user until they return. Push them to a worker thread instead, and
    # run them concurrently so the total wait is max(), not sum().
    async def wiki_lookup() -> str:
        try:
            wiki_results = await asyncio.to_thread(wikipedia.search, query, results=1)
            if wiki_results:
                summary = await asyncio.to_thread(wikipedia.summary, wiki_results[0], sentences=3)
                return f"Wikipedia ({wiki_results[0]}): {summary}\n\n"
        except Exception as e:
            logger.warning("Wikipedia lookup failed for query %r: %s", query, e)
        return ""

    async def web_lookup() -> str:
        try:
            results_list = await asyncio.to_thread(
                lambda: list(DDGS().text(query, region="in-en", max_results=3))
            )
            if results_list:
                return "Web Search:\n" + "\n".join(
                    f"- {r['title']}: {r['body']}" for r in results_list
                )
        except Exception as e:
            logger.warning("Web search failed for query %r: %s", query, e)
        return ""

    wiki_part, web_part = await asyncio.gather(wiki_lookup(), web_lookup())
    search_context = wiki_part + web_part

    if not search_context:
        await thinking_msg.edit_text(
            "Arey yaar, internet pe kuch nahi mila iske baare mein... 🥺💔 Please thoda different search karo na!"
        )
        return

    user_name = update.message.from_user.first_name if update.message.from_user else "Someone"
    prompt = (
        f"[{user_name}]: Maine internet par ye dhunda for '{query}':\n{search_context}\n\n"
        "Please isko short aur cute Hinglish mein summarize karke batao aur user ko answer do!"
    )

    try:
        response = await get_ai_response(prompt, chat_id, user_name)
        await thinking_msg.edit_text(response)
    except Exception as e:
        logger.error("AI response failed for /ask: %s", e)
        await thinking_msg.edit_text("Oops, mujhe samajh nahi aa raha kya bolun... 🥺💔")


def _is_owner(update: Update) -> bool:
    return bool(update.effective_user) and update.effective_user.id == OWNER_ID


async def owner_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner command to broadcast a message into the main group.

    Callable from anywhere (DM included) as long as it's the owner —
    it always TARGETS MAIN_GROUP_ID, it doesn't need to be RUN there.
    """
    if not _is_owner(update):
        await update.message.reply_text("Hehe, sorry but ye command sirf mere Owner ke liye hai! 🥺")
        return

    if not context.args:
        await update.message.reply_text("Arey, message toh likho jo broadcast karna hai! 😅")
        return

    message = " ".join(context.args)
    try:
        await context.bot.send_message(chat_id=MAIN_GROUP_ID, text=f"📢 Announcement:\n\n{message}")
        await update.message.reply_text("Message successfully main group mein bhej diya gaya! 💖")
    except Exception as e:
        logger.error("Broadcast failed: %s", e)
        await update.message.reply_text(f"Oops, error aa gaya: {e} 💔")


async def owner_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner command to check if bot is alive"""
    if not _is_owner(update):
        return
    if BOT_ALIVE:
        await update.message.reply_text("Haan owner ji, main bilkul ready aur active hoon! 💖")
    else:
        await update.message.reply_text("Owner ji, process toh chal raha hai, par main abhi so rahi hoon (asleep). /on karke jagao mujhe! 😴")


async def sleep_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner command to put the bot to sleep. Callable from anywhere."""
    global BOT_ALIVE
    if not _is_owner(update):
        return
    BOT_ALIVE = False
    await update.message.reply_text("Theek hai owner ji, main thodi der so jaati hoon... 😴💤 (Bot is now asleep)")


async def wake_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner command to wake the bot up. Callable from anywhere."""
    global BOT_ALIVE
    if not _is_owner(update):
        return
    BOT_ALIVE = True
    await update.message.reply_text("Yay! Main uth gayi aur bilkul ready hoon! 🥰💖 (Bot is now awake)")


# Aliases: you were calling /on and /off, which never existed as
# registered commands before — that's why they silently did nothing.
on_command = wake_command
off_command = sleep_command
