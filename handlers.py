import asyncio
import logging
import time

import wikipedia
from ddgs import DDGS
from telegram import Update, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import db
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

# Once we've confirmed a user/group is already in Mongo (or just inserted
# them), remember it in-process so we don't re-query the DB on every
# single message — only the first message per user/group per process
# needs the round trip.
_known_user_ids: set[int] = set()
_known_group_ids: set[int] = set()


async def _ensure_user_registered(user, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Back-fill a user into Mongo the first time we see them in THIS
    process, whether that's via /start or just a normal message — covers
    people who were already using the bot before the DB existed. Announces
    to the main group only on a genuine first-ever insert."""
    if not user or user.id in _known_user_ids:
        return
    _known_user_ids.add(user.id)

    is_new = await db.register_user_if_new(user.id, user.username, user.first_name)
    if is_new:
        try:
            await context.bot.send_message(
                chat_id=MAIN_GROUP_ID,
                text=f"🎉 {user.first_name} ne abhi mujhe start kiya hai! Welcome unko group mein! 💖",
            )
        except Exception as e:
            logger.warning("Could not send new-user announcement to main group: %s", e)


async def _ensure_group_registered(chat) -> None:
    """Back-fill a group into Mongo the first time we see a message from it
    in THIS process — covers groups the bot was already sitting in before
    the DB existed. Silent: no intro message, since the bot's already
    active there and re-introducing itself would look broken/spammy."""
    if chat.type not in ("group", "supergroup") or chat.id in _known_group_ids:
        return
    _known_group_ids.add(chat.id)
    await db.register_group_if_new(chat.id, chat.title)

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
    """Handler for /start command. On a user's genuine first appearance
    ever (via /start OR any message), saves them to MongoDB and announces
    it in the main group — but only ever once per person."""
    await update.message.reply_text(
        "Hii! 💕 Main Mitsuri hoon! Aapka swagat hai. Main aapse baat karne ke liye bahut excited hoon! 🥰"
    )
    await _ensure_user_registered(update.effective_user, context)


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

    await _ensure_user_registered(update.effective_user, context)
    await _ensure_group_registered(update.effective_chat)

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


async def on_bot_added_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fires whenever the bot's own membership status changes in a chat
    (added, removed, promoted, etc). We only care about the transition
    into being a member of a group for the first time."""
    result: ChatMemberUpdated = update.my_chat_member
    if result is None:
        return

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status

    was_in_chat = old_status in ("member", "administrator", "creator")
    is_in_chat_now = new_status in ("member", "administrator", "creator")

    if was_in_chat or not is_in_chat_now:
        return  # not a fresh "bot just joined" transition

    chat = result.chat
    if chat.type not in ("group", "supergroup"):
        return

    is_new = await db.register_group_if_new(chat.id, chat.title)
    _known_group_ids.add(chat.id)
    if is_new:
        try:
            await context.bot.send_message(
                chat_id=chat.id,
                text="Hii everyone! 💕 Main Mitsuri hoon! Mujhe mention karke ya reply karke baat karo, main hamesha ready hoon! 🥰",
            )
        except Exception as e:
            logger.warning("Could not send intro message to new group %s: %s", chat.id, e)


async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /ask command to search the internet"""
    await _ensure_user_registered(update.effective_user, context)
    await _ensure_group_registered(update.effective_chat)

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


# Telegram messages are capped at 4096 chars — split long lists into
# multiple messages instead of silently failing on a big user/group base.
TELEGRAM_MSG_LIMIT = 4096


def _chunk_lines(lines: list[str], header: str) -> list[str]:
    chunks = []
    current = header
    for line in lines:
        if len(current) + len(line) + 1 > TELEGRAM_MSG_LIMIT:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


async def access_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner command: shows Users/Groups buttons, tapping one lists every
    saved user or group with a clickable link to reach them."""
    if not _is_owner(update):
        await update.message.reply_text("Ye command sirf mere Owner ke liye hai! 🥺")
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👤 Users", callback_data="access_users"),
                InlineKeyboardButton("👥 Groups", callback_data="access_groups"),
            ]
        ]
    )
    await update.message.reply_text("Kya dekhna hai owner ji? 💖", reply_markup=keyboard)


async def access_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the Users/Groups button taps from /access."""
    query = update.callback_query
    await query.answer()

    if not query.from_user or query.from_user.id != OWNER_ID:
        await query.edit_message_text("Ye sirf Owner ke liye hai! 🥺")
        return

    if query.data == "access_users":
        users = await db.get_all_users_full()
        if not users:
            await query.edit_message_text("Abhi tak koi bhi user database mein nahi hai. 🥺")
            return
        lines = []
        for i, u in enumerate(users, 1):
            name = u.get("first_name") or "Unknown"
            username = u.get("username")
            link = f"https://t.me/{username}" if username else f"tg://user?id={u['_id']}"
            lines.append(f"{i}. {name} — {link}")
        chunks = _chunk_lines(lines, f"👤 Total users: {len(users)}\n\n")
        await query.edit_message_text(chunks[0])
        for chunk in chunks[1:]:
            await context.bot.send_message(chat_id=query.message.chat_id, text=chunk)

    elif query.data == "access_groups":
        groups = await db.get_all_groups_full()
        if not groups:
            await query.edit_message_text("Abhi tak koi bhi group database mein nahi hai. 🥺")
            return
        lines = []
        for i, g in enumerate(groups, 1):
            title = g.get("title") or "Untitled group"
            chat_id = g["_id"]
            try:
                invite_link = await context.bot.export_chat_invite_link(chat_id)
                lines.append(f"{i}. {title} — {invite_link}")
            except Exception as e:
                logger.warning("Could not export invite link for group %s: %s", chat_id, e)
                lines.append(f"{i}. {title} — (no link, chat_id: {chat_id})")
        chunks = _chunk_lines(lines, f"👥 Total groups: {len(groups)}\n\n")
        await query.edit_message_text(chunks[0])
        for chunk in chunks[1:]:
            await context.bot.send_message(chat_id=query.message.chat_id, text=chunk)


async def owner_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner command to broadcast a message to every user who has ever
    /start'd the bot AND every group the bot has ever been added to.
    Can only be TRIGGERED from the main group or the owner's own DM —
    but the message goes out to everyone/everywhere saved, not just
    whoever's in the triggering chat.
    """
    if not _is_owner(update):
        await update.message.reply_text("Hehe, sorry but ye command sirf mere Owner ke liye hai! 🥺")
        return

    chat = update.effective_chat
    allowed_here = chat.id == MAIN_GROUP_ID or chat.type == "private"
    if not allowed_here:
        await update.message.reply_text("Ye command sirf main group ya humari DM se chalta hai! 😅")
        return

    if not context.args:
        await update.message.reply_text("Arey, message toh likho jo broadcast karna hai! 😅")
        return

    message = " ".join(context.args)
    text = f"📢 Announcement:\n\n{message}"

    user_ids = await db.get_all_user_ids()
    group_ids = await db.get_all_group_ids()
    targets = [(uid, "user") for uid in user_ids] + [(gid, "group") for gid in group_ids]

    if not targets:
        await update.message.reply_text("Abhi tak koi bhi user ya group database mein nahi hai broadcast karne ke liye! 🥺")
        return

    status_msg = await update.message.reply_text(f"Bhej rahi hoon {len(user_ids)} users aur {len(group_ids)} groups ko... ⏳")

    sent = 0
    failed = 0
    # Telegram enforces a rough ~30 msg/sec global limit. A small delay
    # between sends keeps us comfortably under that instead of getting
    # flood-limited partway through a big broadcast.
    for chat_id, _kind in targets:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text)
            sent += 1
        except Exception as e:
            failed += 1
            logger.warning("Broadcast to %s failed: %s", chat_id, e)
        await asyncio.sleep(0.05)

    await status_msg.edit_text(f"Ho gaya! ✅ {sent} ko mil gaya, {failed} fail ho gaye (blocked/kicked honge). 💖")


async def owner_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner command to check if bot is alive"""
    if not _is_owner(update):
        return
    if BOT_ALIVE:
        await update.message.reply_text("Haan owner ji, main bilkul ready aur active hoon! 💖")
    else:
        await update.message.reply_text("Owner ji, process toh chal raha hai, par main abhi so rahi hoon (asleep). /on karke jagao mujhe! 😴")


async def off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner command to put the bot to sleep. Callable from anywhere."""
    global BOT_ALIVE
    if not _is_owner(update):
        return
    BOT_ALIVE = False
    await update.message.reply_text("Theek hai owner ji, main thodi der so jaati hoon... 😴💤 (Bot is now asleep)")


async def on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner command to wake the bot up. Callable from anywhere."""
    global BOT_ALIVE
    if not _is_owner(update):
        return
    BOT_ALIVE = True
    await update.message.reply_text("Yay! Main uth gayi aur bilkul ready hoon! 🥰💖 (Bot is now awake)")
