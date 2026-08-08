import json
import time
import os
from contextlib import asynccontextmanager
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters
from fastapi import FastAPI, Request, Response
from http import HTTPStatus

# --- CONFIGURATION (Environment Variables) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
AIPIPE_TOKEN = os.getenv("AIPIPE_TOKEN", "")
LOG_URL = os.getenv(
    "LOG_URL", 
    "https://raw.githubusercontent.com/23f1001032/jsonl_file/refs/heads/main/run.jsonl"
)
# FALLBACK: We use your exact live Render URL if RENDER_EXTERNAL_URL is blank!
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "https://tds-project1-q5-1.onrender.com")
# --------------------------------------------------------------------------

# Safety check
if not TELEGRAM_BOT_TOKEN:
    print("❌ CRITICAL ERROR: TELEGRAM_BOT_TOKEN is empty! Please check your Render Environment tab.")
if not AIPIPE_TOKEN:
    print("❌ CRITICAL ERROR: AIPIPE_TOKEN is empty! Please check your Render Environment tab.")

# Initialize OpenAI Client
client = OpenAI(base_url="https://aipipe.org/openai/v1", api_key=AIPIPE_TOKEN)
LOG_FILE = "run.jsonl"

conversation_history = {}

# Initialize the Bot Application
bot_app = (
    ApplicationBuilder()
    .token(TELEGRAM_BOT_TOKEN)
    .updater(None)  # Disable background polling
    .build()
)

def log_event(event: dict):
    event["timestamp"] = time.time()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command."""
    greeting = (
        "🤖 Hello! I am your AI Data Analyst Bot. I am active, online, and "
        "monitoring updates. Send me a data-analysis question, and I will analyze "
        "it and reply in the requested JSON format!"
    )
    await update.message.reply_text(greeting)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles plain text messages."""
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
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system_prompt}] + history[-6:],
        )
        reply_text = response.choices[0].message.content.strip()
    except Exception as e:
        reply_text = json.dumps({"error": f"AI completion failed: {str(e)}"})

    history.append({"role": "assistant", "content": reply_text})

    # Bulletproof nested parser to completely prevent handler crashes
    parsed = {}
    try:
        parsed = json.loads(reply_text)
    except json.JSONDecodeError:
        try:
            start, end = reply_text.find("{"), reply_text.rfind("}")
            if start != -1 and end != -1:
                parsed = json.loads(reply_text[start:end + 1])
            else:
                parsed = {"raw_response": reply_text}
        except Exception:
            parsed = {"raw_response": reply_text}
            
    parsed["log_url"] = LOG_URL
    final_reply = json.dumps(parsed)

    log_event({"type": "outgoing", "chat_id": chat_id, "text": final_reply})
    await update.message.reply_text(final_reply)

# Register handlers
bot_app.add_handler(CommandHandler("start", start_command))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))


# --- FASTAPI WEBHOOK INTEGRATION ---

@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    """
    On startup, we initialize the Telegram application, set the webhook 
    to our public Render URL, and start servicing traffic.
    """
    await bot_app.initialize()
    await bot_app.start()
    
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/telegram-webhook"
        await bot_app.bot.set_webhook(url=webhook_url)
        print(f"🚀 Webhook successfully set to: {webhook_url}")
    else:
        print("⚠️ Warning: RENDER_EXTERNAL_URL is not set. Webhook was not established.")
        
    yield  # FastAPI handles web traffic here
    
    # ❌ FIX: We DO NOT call delete_webhook() on shutdown anymore!
    # This prevents the old container from clearing the new container's webhook during deployment.
    print("🛑 Shutting down FastAPI server...")
    await bot_app.stop()
    await bot_app.shutdown()


app = FastAPI(lifespan=lifespan)

@app.post("/telegram-webhook")
async def process_update_webhook(request: Request):
    """
    Endpoint where Telegram posts new messages.
    """
    try:
        req_json = await request.json()
        update = Update.de_json(req_json, bot_app.bot)
        await bot_app.process_update(update)
    except Exception as e:
        print(f"Error processing update: {e}")
    return Response(status_code=HTTPStatus.OK)

@app.get("/")
def health_check():
    return {
        "status": "healthy", 
        "details": "FastAPI is active.",
        "webhook_url": f"{RENDER_EXTERNAL_URL}/telegram-webhook"
    }