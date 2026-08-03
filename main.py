import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ChatMemberHandler, CallbackQueryHandler, filters
from config import TELEGRAM_BOT_TOKEN
from handlers import (
    start_command,
    help_command,
    handle_message,
    owner_broadcast,
    owner_ping,
    ask_command,
    sleep_command,
    wake_command,
    on_command,
    off_command,
    on_bot_added_to_group,
    access_command,
    access_callback,
)

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Reduce httpx logging level
logging.getLogger("httpx").setLevel(logging.WARNING)

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type','text/plain')
        self.end_headers()
        self.wfile.write("Mitsuri Bot is running! 💕".encode('utf-8'))

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    logger.info(f"Starting dummy HTTP server on port {port} for Render health checks...")
    server.serve_forever()

def main():
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "PUT_YOUR_TOKEN_HERE":
        logger.error("TELEGRAM_BOT_TOKEN is not set properly in environment variables/ .env file.")
        return
        
    # Start the dummy web server in a background thread
    server_thread = threading.Thread(target=run_dummy_server, daemon=True)
    server_thread.start()
        
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # User commands
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ask", ask_command))

    # Owner commands
    application.add_handler(CommandHandler("cast", owner_broadcast))
    application.add_handler(CommandHandler("ping", owner_ping))
    application.add_handler(CommandHandler("sleep", sleep_command))
    application.add_handler(CommandHandler("wake", wake_command))
    application.add_handler(CommandHandler("on", on_command))
    application.add_handler(CommandHandler("off", off_command))

    # Message handler for AI chat
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Fires when the bot's own membership in a chat changes (e.g. added to a group)
    application.add_handler(ChatMemberHandler(on_bot_added_to_group, ChatMemberHandler.MY_CHAT_MEMBER))

    application.add_handler(CommandHandler("access", access_command))
    application.add_handler(CallbackQueryHandler(access_callback, pattern="^access_"))

    # Run the bot
    logger.info("Mitsuri bot is starting... 💕")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
