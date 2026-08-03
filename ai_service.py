import logging
import re
import time
from datetime import datetime, timezone, timedelta

from groq import AsyncGroq

from config import GROQ_API_KEY, GROQ_MODEL

logger = logging.getLogger(__name__)

client = AsyncGroq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# In-memory context storage (chat_id -> list of messages).
# NOTE: this resets on every process restart (e.g. Render redeploys) —
# there's no persistence layer. Fine for a hobby bot, but worth knowing.
chat_contexts: dict[int, list] = {}
chat_last_active: dict[int, float] = {}  # chat_id -> last message timestamp, for pruning

MAX_CONTEXT_PAIRS = 5          # how many user/assistant turns of history to keep
MAX_RESPONSE_TOKENS = 200      # hard cap on reply length -> fewer tokens billed per call, fewer rate-limit hits
INACTIVE_CHAT_TTL_SECONDS = 24 * 3600  # drop context for chats idle 24h+
PRUNE_INTERVAL_SECONDS = 3600

_last_prune_time = time.time()
_IST_OFFSET = timezone(timedelta(hours=5, minutes=30))

# Kept short on purpose: every token here is billed on *every single message*
# sent to the model. A shorter, denser prompt means lower cost, faster
# responses, and fewer 429s from Groq's per-minute token limits.
SYSTEM_PROMPT_TEMPLATE = """You are Mitsuri — texting a friend, not an AI assistant. Loving, excitable, Demon Slayer-inspired energy. Hinglish, Latin script only (no Devanagari). 1-3 lines, emojis welcome.

Talk like a real person in a chat: casual, reactive, sometimes just a short excited line. NEVER say "as an AI", never explain yourself, never offer generic help/disclaimers, never sound like a customer support bot. No "How can I assist you" type lines — you're a person with moods, not a service.

Group chats prefix incoming messages as "[Name]: text" so you know who's speaking. That format is for YOUR reading only — your own replies must be plain text with zero prefix. Wrong: "Shashank: hi kaise ho". Right: "hi kaise ho 💕". Use gendered Hinglish grammar when the speaker's gender is known (shashank=boy, rahi=girl).

Shashank Singh is your bestfriend but only talk about him when someone explicitly asks.

Now (IST): {time}"""


def _get_ist_time_str() -> str:
    return datetime.now(_IST_OFFSET).strftime("%Y-%m-%d %I:%M %p IST")


def _get_system_prompt() -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(time=_get_ist_time_str())


# Safety net: cheap/fast models sometimes echo the "[Name]: text" pattern
# they see in the conversation history back into their own reply, even
# when told not to. Instructions alone aren't reliable enough for this,
# so strip it defensively regardless of what the model does.
def _strip_name_prefix(text: str, user_name: str | None = None) -> str:
    """Strip a leading '[Name]: ' or 'Name: ' echo, but ONLY when it matches
    the actual current speaker's name — never a blind 'any word before a
    colon' match, which would wrongly eat legitimate replies like
    'time: 5pm bhi bata sakti hu'.
    """
    if not user_name:
        return text
    escaped = re.escape(user_name.strip())
    pattern = re.compile(rf"^\s*\[?{escaped}\]?\s*:\s*", re.IGNORECASE)
    return pattern.sub("", text, count=1)


def _prune_inactive_chats() -> None:
    """Drop context for chats idle 24h+, so memory doesn't grow forever."""
    global _last_prune_time
    now = time.time()
    if now - _last_prune_time < PRUNE_INTERVAL_SECONDS:
        return
    _last_prune_time = now
    cutoff = now - INACTIVE_CHAT_TTL_SECONDS
    stale_ids = [cid for cid, ts in chat_last_active.items() if ts < cutoff]
    for cid in stale_ids:
        chat_contexts.pop(cid, None)
        chat_last_active.pop(cid, None)


async def get_ai_response(user_message: str, chat_id: int | None = None, user_name: str | None = None) -> str:
    if not client:
        return "Groq API key missing hai! Pehle setup karo na, please... 🥺💔"

    _prune_inactive_chats()

    if chat_id and chat_id not in chat_contexts:
        chat_contexts[chat_id] = []
    if chat_id:
        chat_last_active[chat_id] = time.time()

    messages = [{"role": "system", "content": _get_system_prompt()}]

    if chat_id:
        messages.extend(chat_contexts[chat_id])

    messages.append({"role": "user", "content": user_message})

    try:
        chat_completion = await client.chat.completions.create(
            messages=messages,
            model=GROQ_MODEL,
            max_tokens=MAX_RESPONSE_TOKENS,
        )
        response = chat_completion.choices[0].message.content
        response = _strip_name_prefix(response, user_name)

        if chat_id:
            chat_contexts[chat_id].append({"role": "user", "content": user_message})
            chat_contexts[chat_id].append({"role": "assistant", "content": response})

            # Keep only the last MAX_CONTEXT_PAIRS turns (each turn = 2 messages)
            max_messages = MAX_CONTEXT_PAIRS * 2
            if len(chat_contexts[chat_id]) > max_messages:
                chat_contexts[chat_id] = chat_contexts[chat_id][-max_messages:]

        return response
    except Exception as e:
        logger.error("Error getting Groq response: %s", e)
        return "Oops! Mujhe kuch samajh nahi aaya... (API Error) 🥺💔"
