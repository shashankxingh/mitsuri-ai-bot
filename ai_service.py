import os
from datetime import datetime, timezone, timedelta
from groq import AsyncGroq
from config import GROQ_API_KEY, GROQ_MODEL

client = AsyncGroq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# In-memory context storage (chat_id -> list of messages)
chat_contexts = {}
MAX_CONTEXT = 5

def get_ist_time_str():
    ist_offset = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist_offset)
    return now_ist.strftime("%Y-%m-%d %I:%M %p IST")

def get_system_prompt():
    return f"""You are Mitsuri, a friendly, energetic, and slightly romantic AI bot. 
You speak in Hinglish (a mix of Hindi and English written in Latin script). 
Your personality is inspired by Mitsuri Kanroji from Demon Slayer - very loving, easily excited, and passionate.
Keep your responses short, concise, fun, and use emojis! Do not write in Hindi script (Devanagari), use Latin script only.
The current Date and Time in India is: {get_ist_time_str()}
"""

async def get_ai_response(user_message: str, chat_id: int = None) -> str:
    if not client:
        return "Groq API key missing hai! Pehle setup karo na, please... 🥺💔"

    # Initialize context for chat_id if not exists
    if chat_id and chat_id not in chat_contexts:
        chat_contexts[chat_id] = []

    messages = [
        {"role": "system", "content": get_system_prompt()}
    ]
    
    if chat_id:
        messages.extend(chat_contexts[chat_id])
        
    messages.append({"role": "user", "content": user_message})

    try:
        chat_completion = await client.chat.completions.create(
            messages=messages,
            model=GROQ_MODEL,
        )
        response = chat_completion.choices[0].message.content
        
        # Update context
        if chat_id:
            chat_contexts[chat_id].append({"role": "user", "content": user_message})
            chat_contexts[chat_id].append({"role": "assistant", "content": response})
            
            # Keep only last MAX_CONTEXT pairs (each pair is 2 messages: user + assistant)
            if len(chat_contexts[chat_id]) > MAX_CONTEXT * 2:
                chat_contexts[chat_id] = chat_contexts[chat_id][-MAX_CONTEXT * 2:]
                
        return response
    except Exception as e:
        print(f"Error getting Groq response: {e}")
        return "Oops! Mujhe kuch samajh nahi aaya... (API Error) 🥺💔"
