import json
import time
import os
from contextlib import asynccontextmanager
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
from fastapi import FastAPI

# --- CONFIGURATION (Environment Variables are best practice for Render!) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "your-token-here")
AIPIPE_TOKEN = os.getenv("AIPIPE_TOKEN", "your-token-bot-here")
LOG_URL = os.getenv(
    "LOG_URL", 
    "https://raw.githubusercontent.com/23f1001032/jsonl_file/refs/heads/main/run.jsonl"
)
# --------------------------------------------------------------------------

# Initialize OpenAI Client
client = OpenAI(base_url="https://aipipe.org/openai/v1", api_key=AIPIPE_TOKEN)
LOG_FILE = "run.jsonl"

# Conversation history cache
conversation_history = {}

def log_event(event: dict):
    event["timestamp"] = time.time()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Prevent bot from crashing if a message does not contain text or chat info
    if not update.effective_chat or not update.message or not update.message.text:
        return
        
    chat_id = update.effective_chat.id
    user_text = update.message.text
    log_event({"type": "incoming", "chat_id": chat_id, "text": user_text})

    history = conversation_history.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_text})

    system_prompt = (
        "You are a careful data analyst. The user's LAST message asks a data-analysis "
        "question and tells you exactly what JSON shape to reply with. Work out the "
        "real answer (use any public data you know, e.g. MOSPI statistics, general "
        "world knowledge, or arithmetic on numbers given in the message). "
        "Reply with ONLY that exact JSON object and absolutely nothing else — no "
        "explanation, no markdown, no code fences, just the raw JSON."
    )
    
    # Request completion from AIpipe
    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "system", "content": system_prompt}] + history[-6:],
    )
    reply_text = response.choices[0].message.content.strip()
    history.append({"role": "assistant", "content": reply_text})

    # Gracefully format and clean up reply to ensure valid JSON output with log_url
    try:
        parsed = json.loads(reply_text)
    except json.JSONDecodeError:
        # Fallback to extract first dictionary block if model adds commentary
        start, end = reply_text.find("{"), reply_text.rfind("}")
        if start != -1 and end != -1:
            parsed = json.loads(reply_text[start:end + 1])
        else:
            parsed = {"error": "Could not parse AI response", "raw": reply_text}
            
    parsed["log_url"] = LOG_URL
    final_reply = json.dumps(parsed)

    log_event({"type": "outgoing", "chat_id": chat_id, "text": final_reply})
    await update.message.reply_text(final_reply)


# --- RUN TELEGRAM CONCURRENTLY WITH FASTAPI ---

@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    """
    This lifespan block runs on FastAPI startup, starts the Telegram bot
    polling in the background, and stops it when the server stops.
    """
    # 1. Setup the Telegram Application
    bot_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 2. Start the Polling Engine asynchronously
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling()
    print("🚀 Telegram Bot is polling in the background...")
    
    yield # FastAPI serves web traffic on port 8000 here
    
    # 3. Graceful Shutdown
    print("🛑 Shutting down Telegram Bot...")
    await bot_app.updater.stop()
    await bot_app.stop()
    await bot_app.shutdown()
    print("✅ Offline.")


# Instantiate the ASGI app that Uvicorn expects
app = FastAPI(lifespan=lifespan)

@app.get("/")
def health_check():
    """
    Render calls this endpoint to ensure your application is healthy.
    Responding with 200 OK guarantees Render marks your deploy as successful!
    """
    return {
        "status": "healthy", 
        "details": "FastAPI is active and Telegram Bot is polling in the background!"
    }