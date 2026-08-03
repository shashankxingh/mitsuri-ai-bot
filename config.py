import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MONGODB_URI = os.getenv("MONGODB_URI")  # e.g. mongodb+srv://user:pass@cluster.mongodb.net/?appName=Mitsuri

# Hardcoded IDs
BOT_ID = 8438262512 # Mitsuri bot ID
OWNER_ID = 8162412883
MAIN_GROUP_ID = -1002759296936

# Groq Model
GROQ_MODEL = "llama-3.3-70b-versatile"
