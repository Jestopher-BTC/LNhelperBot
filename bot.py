import logging
import re
import requests
import os
import time
import json
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.constants import ChatAction, ParseMode
import importlib.util
from dotenv import load_dotenv

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Replace this with your actual bot token
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
MEMPOOL_API_URL = 'https://mempool.space/api/tx/'
MEMPOOL_BLOCKS_API_URL = 'https://mempool.space/api/blocks'

STATE_FILE = 'bot_state.json'

# Store watched txids: { txid: [ { 'chat_id': ..., 'notified': ... }, ... ] }
watched_tx = {}
# Store chat_ids of users who want block notifications
block_notify_users = set()
# Track last seen block height
last_block_height = None

HELP_TEXT = (
    "Available commands:\n"
    "/start - Welcome message and menu\n"
    "/help - Show this help message\n"
    "/notifyblocks - Get notified every time a new Bitcoin block is found\n"
    "/stopblocks - Stop block notifications\n"
    "/liquiditychart - Get the latest Magma liquidity chart\n"
    "/status - Show the status of all monitored transactions for the user\n"
    "/remove <txid> - Stop monitoring a transaction\n"
    "\nJust send a Bitcoin txid to monitor it for 6 confirmations.\n"
)

MAIN_MENU = [["/liquiditychart", "/notifyblocks", "/status"], ["/stopblocks", "/help"]]

# --- Persistence ---
def save_state():
    state = {
        'watched_tx': watched_tx,
        'block_notify_users': list(block_notify_users)
    }
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def load_state():
    global watched_tx, block_notify_users
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
            watched_tx.clear()
            for k, v in state.get('watched_tx', {}).items():
                watched_tx[k] = v
            block_notify_users.clear()
            block_notify_users.update(state.get('block_notify_users', []))

# Simple txid validation (64 hex chars)
def is_valid_txid(txid):
    return bool(re.fullmatch(r'[0-9a-fA-F]{64}', txid))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "👋 <b>Welcome to LNhelperBot!</b>\n\n"
        "This bot helps you monitor Bitcoin transactions for confirmations, get block notifications, and view Lightning liquidity charts.\n\n"
        "<b>How to use:</b>\n"
        "• <b>Monitor a transaction:</b> Send a Bitcoin txid\n"
        "• <b>Check status:</b> /status\n"
        "• <b>Stop monitoring:</b> /remove &lt;txid&gt;\n"
        "• <b>Block notifications:</b> /notifyblocks, /stopblocks\n"
        "• <b>Liquidity chart:</b> /liquiditychart\n\n"
        "Use the menu below or type /help for all commands."
    )
    await update.message.reply_text(welcome, reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True), parse_mode=ParseMode.HTML)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True))

def get_confirmations(txid):
    try:
        resp = requests.get(MEMPOOL_API_URL + txid, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            confirmations = data.get('confirmations')
            if confirmations is not None and confirmations > 0:
                return confirmations
            # If confirmations is 0 or missing, check for block_height
            status = data.get('status', {})
            block_height = status.get('block_height') or data.get('block_height')
            if block_height:
                # Fetch current block height
                blocks_resp = requests.get(MEMPOOL_BLOCKS_API_URL, timeout=10)
                if blocks_resp.status_code == 200:
                    blocks = blocks_resp.json()
                    if blocks and isinstance(blocks, list):
                        current_height = blocks[0]['height']
                        return max(0, current_height - block_height + 1)
            return 0
        else:
            return None
    except Exception as e:
        logger.error(f"Error fetching confirmations for {txid}: {e}")
        return None

async def handle_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    chat_id = update.message.chat_id
    if not is_valid_txid(txid):
        await update.message.reply_text("❌ That doesn't look like a valid Bitcoin transaction ID. Please check and try again.")
        return
    # Immediate check for confirmations
    confirmations = get_confirmations(txid)
    if confirmations is not None:
        if confirmations >= 6:
            await update.message.reply_text(f"✅ Transaction <code>{txid}</code> already has {confirmations} confirmations!", parse_mode=ParseMode.HTML)
            return
        else:
            await update.message.reply_text(f"Transaction <code>{txid}</code> currently has {confirmations} confirmation(s). Monitoring until it reaches 6.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"⚠️ Could not check transaction status right now (API error), but will monitor it for you.")
    # Add txid to watched list
    if txid not in watched_tx:
        watched_tx[txid] = []
    # Avoid duplicate notifications for same user
    if not any(entry['chat_id'] == chat_id for entry in watched_tx[txid]):
        watched_tx[txid].append({'chat_id': chat_id, 'notified': False})
        save_state()
    if confirmations is None or confirmations < 6:
        await update.message.reply_text(f"Monitoring transaction: <code>{txid}</code>\nYou'll be notified when it reaches 6 confirmations.", parse_mode=ParseMode.HTML)

async def notifyblocks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    block_notify_users.add(chat_id)
    save_state()
    await update.message.reply_text("🔔 You will now receive a notification every time a new block is found. Use /stopblocks to turn this off.")

async def stopblocks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id in block_notify_users:
        block_notify_users.remove(chat_id)
        save_state()
        await update.message.reply_text("🚫 Block notifications disabled. You will no longer receive new block alerts.")
    else:
        await update.message.reply_text("You were not receiving block notifications.")

async def check_confirmations(app):
    to_remove = []
    for txid, watchers in watched_tx.items():
        if all(w['notified'] for w in watchers):
            to_remove.append(txid)
            continue
        try:
            confirmations = get_confirmations(txid)
            if confirmations is not None and confirmations >= 6:
                for watcher in watchers:
                    if not watcher['notified']:
                        try:
                            await app.bot.send_message(
                                chat_id=watcher['chat_id'],
                                text=f"✅ Transaction <code>{txid}</code> has reached 6 confirmations!",
                                parse_mode=ParseMode.HTML
                            )
                        except Exception as e:
                            logger.error(f"Failed to notify user: {e}")
                        watcher['notified'] = True
                save_state()
        except Exception as e:
            logger.error(f"Error checking txid {txid}: {e}")
    for txid in to_remove:
        del watched_tx[txid]
        save_state()

async def check_new_block(app):
    global last_block_height
    try:
        resp = requests.get(MEMPOOL_BLOCKS_API_URL, timeout=10)
        if resp.status_code == 200:
            blocks = resp.json()
            if blocks:
                current_height = blocks[0]['height']
                if last_block_height is not None and current_height > last_block_height:
                    # New block found
                    for chat_id in block_notify_users:
                        try:
                            await app.bot.send_message(
                                chat_id=chat_id,
                                text=f"🟦 New Bitcoin block found! Height: {current_height}"
                            )
                        except Exception as e:
                            logger.error(f"Failed to notify user of new block: {e}")
                last_block_height = current_height
    except Exception as e:
        logger.error(f"Error checking new block: {e}")

async def liquiditychart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    progress_msg = await context.bot.send_message(chat_id=chat_id, text="⏳ Generating liquidity chart...")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    chart_path = os.path.join(script_dir, "liquiditychart", "magma_liquidity_chart.py")
    spec = importlib.util.spec_from_file_location("magma_liquidity_chart", chart_path)
    magma_chart = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(magma_chart)
    async def progress_callback(msg):
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=progress_msg.message_id, text=f"⏳ {msg}")
        except Exception:
            pass
    try:
        def sync_progress(msg):
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(progress_callback(msg), loop)
            else:
                loop.run_until_complete(progress_callback(msg))
        chart_path = magma_chart.generate_liquidity_chart(progress_callback=sync_progress)
        with open(chart_path, 'rb') as f:
            await context.bot.send_photo(chat_id=chat_id, photo=f, caption="Here is the latest Magma liquidity chart (updated hourly).")
        await context.bot.delete_message(chat_id=chat_id, message_id=progress_msg.message_id)
    except Exception as e:
        logger.error(f"Error generating or sending liquidity chart: {e}")
        await context.bot.edit_message_text(chat_id=chat_id, message_id=progress_msg.message_id, text="❌ Failed to generate or send the liquidity chart. Please try again later.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_txids = [txid for txid, watchers in watched_tx.items() if any(w['chat_id'] == chat_id and not w['notified'] for w in watchers)]
    if not user_txids:
        await update.message.reply_text("You are not currently monitoring any transactions.")
        return
    status_lines = ["<b>Your monitored transactions:</b>"]
    for txid in user_txids:
        confirmations = get_confirmations(txid)
        if confirmations is not None:
            status_lines.append(f"<code>{txid}</code> : {confirmations} confirmation(s)")
        else:
            status_lines.append(f"<code>{txid}</code> : ⚠️ Error fetching status")
    await update.message.reply_text("\n".join(status_lines), parse_mode=ParseMode.HTML)

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if not context.args:
        await update.message.reply_text("Usage: /remove <txid>")
        return
    txid = context.args[0].strip()
    if not is_valid_txid(txid):
        await update.message.reply_text("❌ That doesn't look like a valid Bitcoin transaction ID.")
        return
    if txid in watched_tx:
        before = len(watched_tx[txid])
        watched_tx[txid] = [w for w in watched_tx[txid] if w['chat_id'] != chat_id]
        after = len(watched_tx[txid])
        if after == 0:
            del watched_tx[txid]
        save_state()
        if before != after:
            await update.message.reply_text(f"Stopped monitoring <code>{txid}</code>.", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(f"You were not monitoring <code>{txid}</code>.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"You were not monitoring <code>{txid}</code>.", parse_mode=ParseMode.HTML)

if __name__ == '__main__':
    load_state()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('notifyblocks', notifyblocks))
    app.add_handler(CommandHandler('stopblocks', stopblocks))
    app.add_handler(CommandHandler('liquiditychart', liquiditychart))
    app.add_handler(CommandHandler('status', status))
    app.add_handler(CommandHandler('remove', remove))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_txid))

    scheduler = AsyncIOScheduler()

    async def on_startup(app):
        scheduler.add_job(check_confirmations, 'interval', seconds=15, args=[app])
        scheduler.add_job(check_new_block, 'interval', seconds=15, args=[app])
        scheduler.start()
        print("Bot is running. Press Ctrl+C to stop.")

    app.post_init = on_startup
    app.run_polling() 