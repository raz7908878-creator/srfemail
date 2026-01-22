import os
import asyncio
import io
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# --- CONFIGURATION (Loaded from Render Environment) ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
# Admin ID must be an integer
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0")) 
# Channel ID must be an integer (usually starts with -100)
DB_CHANNEL_ID = int(os.environ.get("DB_CHANNEL_ID", "0")) 

# --- DUMMY WEB SERVER (Keeps Render Awake) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run_http():
    # Render assigns the PORT automatically
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# --- STORAGE LOGIC (Telegram Channel as Database) ---
async def get_emails_from_channel(bot):
    """Fetches the latest email list from the pinned message in the private channel."""
    try:
        # Get the chat object for the channel
        chat = await bot.get_chat(DB_CHANNEL_ID)
        
        # Get the pinned message
        pinned_msg = await chat.get_pinned_message()
        
        # If no pin or no document, return empty list
        if not pinned_msg or not pinned_msg.document:
            return []

        # Download file to memory (RAM)
        f = await pinned_msg.document.get_file()
        byte_array = await f.download_as_bytearray()
        
        # Decode and split into lines
        text_content = byte_array.decode('utf-8', errors='ignore')
        lines = [line.strip() for line in text_content.splitlines() if line.strip()]
        return lines
    except Exception as e:
        print(f"Error reading DB: {e}")
        return []

async def update_storage_in_channel(bot, lines):
    """Uploads the updated list to the channel and pins it."""
    try:
        # Convert list back to text in memory
        text_content = "\n".join(lines)
        file_bytes = io.BytesIO(text_content.encode('utf-8'))
        file_bytes.name = "database.txt"

        # Send to channel
        msg = await bot.send_document(
            chat_id=DB_CHANNEL_ID, 
            document=file_bytes, 
            caption=f"Database Updated.\nEmails remaining: {len(lines)}"
        )
        
        # Pin the new message (Unpins the old one automatically)
        await msg.pin()
        return True
    except Exception as e:
        print(f"Error saving DB: {e}")
        return False

# --- BOT HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Hello {user.first_name}!\n"
        "Type a number (e.g., '10') to extract that many emails."
    )

async def handle_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin Only: Upload a new .txt file to add to stock."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Authorization Failed.")
        return

    # 1. Download the uploaded file
    file = await update.message.document.get_file()
    byte_array = await file.download_as_bytearray()
    new_lines = [l.strip() for l in byte_array.decode('utf-8', errors='ignore').splitlines() if l.strip()]

    if not new_lines:
        await update.message.reply_text("⚠️ File appears empty.")
        return

    msg = await update.message.reply_text("⏳ Syncing with database...")

    # 2. Get current stock
    current_emails = await get_emails_from_channel(context.bot)
    
    # 3. Combine old + new
    total_emails = current_emails + new_lines
    
    # 4. Save to channel
    success = await update_storage_in_channel(context.bot, total_emails)
    
    if success:
        await msg.edit_text(f"✅ Success! Added {len(new_lines)} emails.\nTotal Stock: {len(total_emails)}")
    else:
        await msg.edit_text("❌ Error saving to channel. Check permissions.")

async def handle_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User: Request specific amount of emails."""
    text = update.message.text.strip()
    
    # Validation
    if not text.isdigit():
        await update.message.reply_text("Please enter a valid number.")
        return

    amount = int(text)
    if amount <= 0:
        await update.message.reply_text("Number must be greater than 0.")
        return

    # 1. Fetch Stock
    lines = await get_emails_from_channel(context.bot)
    
    if len(lines) < amount:
        await update.message.reply_text(f"⚠️ Low Stock. Only {len(lines)} available.")
        return

    # 2. Process Order
    emails_to_send = lines[:amount]
    emails_remaining = lines[amount:]

    # 3. Save Remaining Stock
    success = await update_storage_in_channel(context.bot, emails_remaining)

    if not success:
        await update.message.reply_text("❌ System Error: Could not update database.")
        return

    # 4. Deliver File to User
    out_text = "\n".join(emails_to_send)
    out_file = io.BytesIO(out_text.encode('utf-8'))
    out_file.name = f"emails_{amount}.txt"
    
    await update.message.reply_document(
        document=out_file, 
        caption=f"✅ Here are your {amount} emails."
    )

if __name__ == '__main__':
    # Start Web Server
    keep_alive()
    
    # Start Bot
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Document.MimeType("text/plain"), handle_upload))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_request))
    
    print("Bot is running...")
    application.run_polling()
