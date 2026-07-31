from telegram import Update
from telegram.ext import ContextTypes
from ddgs import DDGS
import wikipedia
import time
from config import OWNER_ID, MAIN_GROUP_ID
from ai_service import get_ai_response

BOT_ALIVE = True
user_cooldowns = {}
COOLDOWN_SECONDS = 3

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
        
    user_id = update.effective_user.id if update.effective_user else 0
    current_time = time.time()
    
    # 3 second cooldown per user to prevent spam
    if user_id in user_cooldowns and current_time - user_cooldowns[user_id] < COOLDOWN_SECONDS:
        return
    user_cooldowns[user_id] = current_time

    # Ignore messages sent by other bots to prevent infinite bot-to-bot loops
    if update.message.from_user and update.message.from_user.is_bot:
        return

    text = update.message.text
    user_name = update.message.from_user.first_name if update.message.from_user else "Someone"
    
    chat_type = update.effective_chat.type
    
    # If in a group, only respond if mentioned or replied to
    if chat_type in ["group", "supergroup"]:
        is_mentioned = "mitsuri" in text.lower() or "@mitsuri_1bot" in text.lower()
        is_reply_to_bot = (
            update.message.reply_to_message 
            and update.message.reply_to_message.from_user 
            and update.message.reply_to_message.from_user.id == context.bot.id
        )
        
        if not (is_mentioned or is_reply_to_bot):
            return # Ignore message if not targeted at bot
            
    # Prepend user name so AI knows who is speaking
    prompt_with_context = f"[{user_name}]: {text}"
    
    response = await get_ai_response(prompt_with_context, update.effective_chat.id)
    await update.message.reply_text(response)

async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /ask command to search the internet"""
    if not context.args:
        await update.message.reply_text("Kya dhoondna hai? Please query bhi likho na! Jaise: /ask What is quantum computing 🥺")
        return
        
    user_id = update.effective_user.id if update.effective_user else 0
    current_time = time.time()
    
    # 5 second cooldown for heavy /ask command
    if user_id in user_cooldowns and current_time - user_cooldowns[user_id] < 5:
        await update.message.reply_text("Arey thoda slow type karo na! Ek minute ruko please... 🥺")
        return
    user_cooldowns[user_id] = current_time
        
    query = " ".join(context.args)
    chat_id = update.effective_chat.id
    
    # Send a thinking message
    thinking_msg = await update.message.reply_text("Ruko main abhi internet pe check karti hoon... 🔍💕")
    
    search_context = ""
    
    # Try Wikipedia first for factual queries
    try:
        wiki_results = wikipedia.search(query, results=1)
        if wiki_results:
            summary = wikipedia.summary(wiki_results[0], sentences=3)
            search_context += f"Wikipedia ({wiki_results[0]}): {summary}\n\n"
    except Exception as e:
        print(f"Wiki error: {e}")
        pass

    # Try DDGS
    try:
        results = DDGS().text(query, region='in-en', max_results=3)
        results_list = list(results)
            
        if results_list:
            search_context += "Web Search:\n" + "\n".join([f"- {r['title']}: {r['body']}" for r in results_list])
    except Exception as e:
        print(f"Search error: {e}")
        pass

    if not search_context:
        await thinking_msg.edit_text("Arey yaar, internet pe kuch nahi mila iske baare mein... 🥺💔 Please thoda different search karo na!")
        return
        
    user_name = update.message.from_user.first_name if update.message.from_user else "Someone"
    prompt = f"[{user_name}]: Maine internet par ye dhunda for '{query}':\n{search_context}\n\nPlease isko short aur cute Hinglish mein summarize karke batao aur user ko answer do!"
    
    try:
        response = await get_ai_response(prompt, chat_id)
        await thinking_msg.edit_text(response)
    except Exception as e:
        await thinking_msg.edit_text("Oops, mujhe samajh nahi aa raha kya bolun... 🥺💔")

async def owner_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner specific command to broadcast a message in the main group."""
    if update.effective_user.id != OWNER_ID or update.effective_chat.id != MAIN_GROUP_ID:
        await update.message.reply_text("Hehe, sorry but ye command sirf mere Owner ke liye aur is group ke liye hai! 🥺")
        return

    if not context.args:
        await update.message.reply_text("Arey, message toh likho jo broadcast karna hai! 😅")
        return
        
    message = " ".join(context.args)
    try:
        await context.bot.send_message(chat_id=MAIN_GROUP_ID, text=f"📢 Announcement:\n\n{message}")
        await update.message.reply_text("Message successfully main group mein bhej diya gaya! 💖")
    except Exception as e:
        await update.message.reply_text(f"Oops, error aa gaya: {e} 💔")

async def owner_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner command to check if bot is alive in main group"""
    if update.effective_user.id != OWNER_ID:
        return
        
    await update.message.reply_text("Haan owner ji, main bilkul ready aur active hoon! 💖")

async def sleep_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner command to put bot to sleep"""
    global BOT_ALIVE
    if update.effective_user.id != OWNER_ID or update.effective_chat.id != MAIN_GROUP_ID:
        return
    BOT_ALIVE = False
    await update.message.reply_text("Theek hai owner ji, main thodi der so jaati hoon... 😴💤 (Bot is now asleep)")

async def wake_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner command to wake bot up"""
    global BOT_ALIVE
    if update.effective_user.id != OWNER_ID or update.effective_chat.id != MAIN_GROUP_ID:
        return
    BOT_ALIVE = True
    await update.message.reply_text("Yay! Main uth gayi aur bilkul ready hoon! 🥰💖 (Bot is now awake)")
