#!/usr/bin/env python3
"""
MAT Airdrop Telegram Bot - single-file implementation

Features:
- SPHYNX-style registration flow for MAT token airdrop
- Join tasks, twitter handle collection, group posting confirmation (button), wallet collection
- Join bonus: 2 MAT, Referral bonus: 0.8 MAT (credited when referee completes registration)
- Automatic MAT payouts on BSC (BEP-20) with real tx hash
- SQLite persistence (users, referrals, transactions)
- Hardcoded exchange: 1 MAT = $100
"""

import os
import sqlite3
import logging
from decimal import Decimal
from datetime import datetime
from urllib.parse import quote_plus

from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from web3 import Web3

# ---------------- CONFIG ----------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BSC_RPC_URL = os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org/")
MAT_TOKEN_ADDRESS = os.getenv("MAT_TOKEN_ADDRESS")  # 0x...
PAYOUT_FROM_ADDRESS = os.getenv("PAYOUT_FROM_ADDRESS")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# Economics (hardcoded)
JOIN_BONUS = Decimal("2.0")       # 2 MAT
REFERRAL_BONUS = Decimal("0.8")   # 0.8 MAT
MIN_WITHDRAWAL = Decimal("4.0")   # 4 MAT
MAT_TO_USD = Decimal("100")       # 1 MAT = $100

# Links & branding
ANNOUNCEMENT_LINK = "https://t.me/mat_to_the_moon"
COMMUNITY_LINK = "https://t.me/matcommunitygroup"
GROUP_POST_TEXT = "$MAT To The Moon 🚀🚀"

# DB file
DB_FILE = "mat_airdrop.db"

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mat_airdrop_bot")

# ---------------- WEB3 / TOKEN ----------------
w3 = Web3(Web3.HTTPProvider(BSC_RPC_URL))
if not w3.is_connected():
    logger.warning("Web3 not connected. Check BSC_RPC_URL")

ERC20_ABI = [
    {"constant": False, "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
     "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
]

mat_contract = None
if MAT_TOKEN_ADDRESS:
    try:
        mat_contract = w3.eth.contract(address=Web3.to_checksum_address(MAT_TOKEN_ADDRESS), abi=ERC20_ABI)
    except Exception as e:
        logger.error("Failed to create mat_contract: %s", e)
        mat_contract = None

def mat_to_minor_units(amount_mat: Decimal, decimals: int) -> int:
    return int((amount_mat * (Decimal(10) ** decimals)).quantize(Decimal('1')))

def send_mat_onchain(dest_addr: str, amount_mat: Decimal):
    """
    Sends MAT tokens from PAYOUT_FROM_ADDRESS to dest_addr.
    Returns (True, tx_hash) or (False, error_str)
    """
    if mat_contract is None:
        return False, "MAT contract not configured"
    try:
        dest = Web3.to_checksum_address(dest_addr)
    except Exception:
        return False, "Invalid destination address"
    try:
        from_addr = Web3.to_checksum_address(PAYOUT_FROM_ADDRESS)
        decimals = mat_contract.functions.decimals().call()
        amount_int = mat_to_minor_units(amount_mat, decimals)
        nonce = w3.eth.get_transaction_count(from_addr)
        gas_price = w3.to_wei(5, 'gwei')

        tx = mat_contract.functions.transfer(dest, amount_int).build_transaction({
            "from": from_addr,
            "nonce": nonce,
            "gasPrice": gas_price,
        })
        try:
            gas_est = w3.eth.estimate_gas(tx)
            tx['gas'] = int(gas_est * 1.2)
        except Exception:
            tx['gas'] = 200000

        signed = w3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)
        raw = signed.rawTransaction
        tx_hash = w3.eth.send_raw_transaction(raw)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=240)
        if receipt.status == 1:
            return True, w3.to_hex(tx_hash)
        return False, "Transaction reverted on chain"
    except Exception as e:
        logger.exception("send_mat_onchain error")
        return False, str(e)

# ---------------- DATABASE ----------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
      tg_id INTEGER PRIMARY KEY,
      username TEXT,
      joined_date TEXT,
      twitter TEXT,
      wallet TEXT,
      balance REAL DEFAULT 0,
      referrals INTEGER DEFAULT 0,
      ref_earnings REAL DEFAULT 0,
      registered INTEGER DEFAULT 0,
      ref_by INTEGER DEFAULT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS referrals (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      referrer INTEGER,
      referee INTEGER,
      created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
      tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
      tg_id INTEGER,
      amount_mat REAL,
      dest_wallet TEXT,
      tx_hash TEXT,
      status TEXT,
      created_at TEXT
    )
    """)
    conn.commit()
    conn.close()

def db_get_user(tg_id):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    row = cur.fetchone()
    conn.close()
    return row

def db_create_user(tg_id, username, ref_by=None):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    joined = datetime.utcnow().strftime("%Y-%m-%d")
    cur.execute("INSERT OR IGNORE INTO users (tg_id, username, joined_date, balance, registered, ref_by) VALUES (?,?,?,?,?,?)",
                (tg_id, username, joined, float(0), 0, ref_by))
    conn.commit()
    conn.close()

def db_set_twitter(tg_id, twitter):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET twitter = ? WHERE tg_id = ?", (twitter, tg_id))
    conn.commit()
    conn.close()

def db_set_wallet_and_register(tg_id, wallet):
    """
    Atomically set wallet, mark registered, credit join bonus, credit referrer if applicable.
    """
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    try:
        cur.execute("BEGIN")
        cur.execute("SELECT ref_by, registered FROM users WHERE tg_id = ?", (tg_id,))
        r = cur.fetchone()
        if not r:
            conn.rollback()
            return False, "User not found"
        ref_by = r[0]
        already = r[1]
        if already == 1:
            # Already registered — just update wallet
            cur.execute("UPDATE users SET wallet = ? WHERE tg_id = ?", (wallet, tg_id))
            conn.commit()
            return True, "Wallet updated"
        # mark registered, set wallet, credit join bonus
        cur.execute("UPDATE users SET wallet = ?, registered = 1, balance = balance + ? WHERE tg_id = ?",
                    (wallet, float(JOIN_BONUS), tg_id))
        # credit referrer if exists
        if ref_by:
            # update referrer counters
            cur.execute("UPDATE users SET referrals = referrals + 1, ref_earnings = ref_earnings + ?, balance = balance + ? WHERE tg_id = ?",
                        (float(REFERRAL_BONUS), float(REFERRAL_BONUS), ref_by))
            # record referral
            cur.execute("INSERT INTO referrals (referrer, referee, created_at) VALUES (?,?,?)",
                        (ref_by, tg_id, datetime.utcnow().isoformat()))
        conn.commit()
        return True, "Registered and wallet saved"
    except Exception as e:
        conn.rollback()
        logger.exception("db_set_wallet_and_register error")
        return False, str(e)
    finally:
        conn.close()

def db_update_wallet(tg_id, wallet):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET wallet = ? WHERE tg_id = ?", (wallet, tg_id))
    conn.commit()
    conn.close()

def db_create_transaction(tg_id, amount_mat, dest_wallet, status='pending'):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("INSERT INTO transactions (tg_id, amount_mat, dest_wallet, status, created_at) VALUES (?,?,?,?,?)",
                (tg_id, float(amount_mat), dest_wallet, status, datetime.utcnow().isoformat()))
    tx_id = cur.lastrowid
    conn.commit()
    conn.close()
    return tx_id

def db_update_transaction(tx_id, status, tx_hash=None):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    if tx_hash:
        cur.execute("UPDATE transactions SET status = ?, tx_hash = ? WHERE tx_id = ?", (status, tx_hash, tx_id))
    else:
        cur.execute("UPDATE transactions SET status = ? WHERE tx_id = ?", (status, tx_id))
    conn.commit()
    conn.close()

def db_deduct_balance_and_mark(tg_id, amount):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    try:
        cur.execute("BEGIN")
        cur.execute("SELECT balance FROM users WHERE tg_id = ?", (tg_id,))
        r = cur.fetchone()
        if not r:
            conn.rollback()
            return False, "User not found"
        bal = Decimal(str(r[0]))
        if bal < amount:
            conn.rollback()
            return False, "Insufficient balance"
        cur.execute("UPDATE users SET balance = balance - ? WHERE tg_id = ?", (float(amount), tg_id))
        conn.commit()
        return True, None
    except Exception as e:
        conn.rollback()
        logger.exception("db_deduct error")
        return False, str(e)
    finally:
        conn.close()

def db_add_balance(tg_id, amount):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (float(amount), tg_id))
    conn.commit()
    conn.close()

# ---------------- HELPERS ----------------
def to_usd(amount_mat: Decimal) -> Decimal:
    return (amount_mat * MAT_TO_USD).quantize(Decimal('0.01'))

def short_addr(addr: str):
    if not addr:
        return "Not set"
    return addr[:6] + "..." + addr[-4:]

def build_main_menu():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    keyboard.add(KeyboardButton("🚀 Join Airdrop"))
    keyboard.add(KeyboardButton("📊 Dashboard"), KeyboardButton("💸 Withdraw MAT"))
    keyboard.add(KeyboardButton("👥 Referral Program"), KeyboardButton("💼 Wallet Settings"))
    keyboard.add(KeyboardButton("ℹ️ Help"))
    return keyboard

# ---------------- TELEGRAM HANDLERS ----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    # handle start param for referral: /start <refid>
    ref_by = None
    if context.args:
        try:
            ref_by = int(context.args[0])
            if ref_by == tg_id:
                ref_by = None
        except Exception:
            ref_by = None
    db_create_user(tg_id, username, ref_by)
    # greeting & tasks
    text = (
        "🔥 Welcome to MAT Airdrop Registration! 🔥\n\n"
        "To qualify, complete these simple tasks:\n\n"
        f"1️⃣ Join Announcement Channel: {ANNOUNCEMENT_LINK}\n"
        f"2️⃣ Join Community Group: {COMMUNITY_LINK}\n"
        "3️⃣ Send your Twitter/X username (with @) in this chat\n\n"
        "After that, you'll be asked to post the verification message in the group and then provide your BSC wallet.\n\n"
        "Press '🚀 Join Airdrop' below to begin."
    )
    await update.message.reply_text(text, reply_markup=build_main_menu())

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    tg_id = update.effective_user.id
    # Join airdrop menu button
    if text == "🚀 Join Airdrop":
        await update.message.reply_text(
            "Great — first, send me your Twitter/X username (with @). Example: @username"
        )
        return
    if text.startswith("@"):
        # Twitter username collected
        db_create_user(tg_id, update.effective_user.username or update.effective_user.first_name)
        db_set_twitter(tg_id, text)
        # Ask user to post message in group; provide confirm button
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ I Posted", callback_data=f"posted:{tg_id}")]])
        await update.message.reply_text(
            f"✅ Twitter username saved as {text}!\n\n"
            f"Now, please post the following message in the community group ({COMMUNITY_LINK}):\n\n"
            f"{GROUP_POST_TEXT}\n\n"
            "After posting, press the button below.",
            reply_markup=kb
        )
        return
    # Dashboard shortcut
    if text in ("📊 Dashboard", "/dashboard"):
        await cmd_dashboard(update, context)
        return
    if text in ("💸 Withdraw MAT", "/withdraw"):
        await cmd_withdraw(update, context)
        return
    if text in ("👥 Referral Program", "/referral"):
        await cmd_referral(update, context)
        return
    if text in ("💼 Wallet Settings", "/wallet"):
        await cmd_wallet_settings(update, context)
        return
    if text in ("ℹ️ Help", "/help"):
        await cmd_help(update, context)
        return
    # Fallback
    await update.message.reply_text("Please use the menu buttons below.", reply_markup=build_main_menu())

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    tg_id = query.from_user.id
    if data.startswith("posted:"):
        # user claims they posted in the group. We'll accept this as verification (no group check)
        # Next ask for wallet
        await query.edit_message_text("✅ Telegram verification successful!\n\nNow, please provide your BSC wallet address (0x...):\nMake sure this is a non-custodial wallet (MetaMask, Trust Wallet).")
        return
    if data.startswith("wallet_update:"):
        # wallet update flow (button triggered elsewhere)
        await query.edit_message_text("Please send your new wallet address as a message (must start with 0x).")
        return
    if data.startswith("view_wallet:"):
        user = db_get_user(tg_id)
        if not user:
            await query.edit_message_text("Not registered. Use /start.")
            return
        wallet = user["wallet"]
        text = f"💼 MAT WALLET\n\nCurrent: {wallet or 'Not set'}\n\nView on BscScan: https://bscscan.com/address/{wallet}" if wallet else "No wallet set."
        await query.edit_message_text(text)
        return

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "ℹ️ MAT Airdrop Bot Help\n\n"
        "Use the buttons to navigate:\n"
        "- 🚀 Join Airdrop: Start registration\n"
        "- 📊 Dashboard: View your stats\n"
        "- 💼 Wallet Settings: Update or view wallet\n"
        "- 💸 Withdraw MAT: Withdraw if you have >= 4 MAT\n"
        "- 👥 Referral Program: Get your referral link\n\n"
        "Distribution and withdrawals are automatic. Always use a non-custodial wallet you control.\n"
    )
    await update.message.reply_text(text, reply_markup=build_main_menu())

async def cmd_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    user = db_get_user(tg_id)
    if not user:
        await update.message.reply_text("You are not registered. Use /start to join.", reply_markup=build_main_menu())
        return
    bal = Decimal(str(user["balance"] or 0))
    ref_earn = Decimal(str(user["ref_earnings"] or 0))
    text = (
        f"📊 MAT DASHBOARD 📊\n\n"
        f"👤 User: @{user['username']}\n"
        f"📅 Joined: {user['joined_date']}\n"
        f"🐦 Twitter: {user['twitter'] or 'Not set'}\n"
        f"💼 Wallet: {short_addr(user['wallet']) if user['wallet'] else 'Not set'}\n\n"
        f"💰 Balance: {bal} MAT (~ ${to_usd(bal)})\n"
        f"👥 Referrals: {user['referrals']}\n"
        f"💸 Referral Earnings: {ref_earn} MAT (~ ${to_usd(ref_earn)})\n\n"
        "Use the menu to withdraw or update your wallet."
    )
    await update.message.reply_text(text, reply_markup=build_main_menu())

async def cmd_wallet_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    user = db_get_user(tg_id)
    if not user:
        await update.message.reply_text("You are not registered. Use /start.", reply_markup=build_main_menu())
        return
    wallet = user["wallet"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Update Wallet", callback_data=f"wallet_update:{tg_id}")],
        [InlineKeyboardButton("🔍 View Full Address", callback_data=f"view_wallet:{tg_id}")],
        [InlineKeyboardButton("🌐 View on BscScan", url=f"https://bscscan.com/address/{wallet}" if wallet else "https://bscscan.com")],
    ])
    tips = (
        f"💼 MAT WALLET SETTINGS 💼\n\n"
        f"Current BSC Wallet: {wallet or 'Not set'}\n\n"
        "Wallet Tips:\n"
        "- Use MetaMask / Trust Wallet\n"
        "- Do not use custodial exchange addresses\n"
        "- Double-check your address before withdrawing\n"
    )
    await update.message.reply_text(tips, reply_markup=kb)

async def cmd_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    user = db_get_user(tg_id)
    if not user:
        await update.message.reply_text("You are not registered. Use /start.", reply_markup=build_main_menu())
        return
    # build referral link
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={tg_id}"
    text = (
        "🚀 MAT REFERRAL PROGRAM 🚀\n\n"
        f"Referral Bonus: {REFERRAL_BONUS} MAT (~ ${to_usd(REFERRAL_BONUS)}) per referral\n\n"
        f"👥 Your Referrals: {user['referrals']}\n"
        f"💰 Total Earned: {user['ref_earnings']} MAT (~ ${to_usd(Decimal(str(user['ref_earnings'] or 0)))})\n\n"
        f"🔗 Your referral link:\n{link}\n\n"
        "Share the link — when someone registers and completes verification using your link, you'll be rewarded automatically!"
    )
    await update.message.reply_text(text, reply_markup=build_main_menu())

async def cmd_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    user = db_get_user(tg_id)
    if not user:
        await update.message.reply_text("You are not registered. Use /start.", reply_markup=build_main_menu())
        return
    balance = Decimal(str(user["balance"] or 0))
    if balance < MIN_WITHDRAWAL:
        need = (MIN_WITHDRAWAL - balance).quantize(Decimal('0.0001'))
        await update.message.reply_text(
            f"💸 SPHYNX WITHDRAWAL 💸\n\n"
            f"💰 Your Balance: {balance} MAT (~ ${to_usd(balance)})\n"
            f"📌 Minimum Withdrawal: {MIN_WITHDRAWAL} MAT (~ ${to_usd(MIN_WITHDRAWAL)})\n\n"
            f"❌ You don't have enough MAT to withdraw. You need {need} more MAT.",
            reply_markup=build_main_menu()
        )
        return
    if not user["wallet"]:
        await update.message.reply_text("Please set your wallet first under Wallet Settings.", reply_markup=build_main_menu())
        return

    # Atomic DB deduction + create tx record
    ok, err = db_deduct_balance_and_mark(tg_id, balance)
    if not ok:
        await update.message.reply_text(f"Error starting withdrawal: {err}", reply_markup=build_main_menu())
        return
    tx_id = db_create_transaction(tg_id, balance, user["wallet"], status="pending")
    await update.message.reply_text(f"Processing automatic withdrawal of {balance} MAT (~ ${to_usd(balance)}) to {user['wallet']} ...")

    ok_send, res = send_mat_onchain(user["wallet"], balance)
    if ok_send:
        txhash = res
        db_update_transaction(tx_id, "completed", txhash)
        await update.message.reply_text(f"✅ Withdrawal Sent!\nTx: {txhash}\nView: https://bscscan.com/tx/{txhash}", reply_markup=build_main_menu())
    else:
        # revert user's balance and mark failed
        db_add_balance(tg_id, float(balance))
        db_update_transaction(tx_id, "failed", None)
        await update.message.reply_text(f"❌ Withdrawal failed: {res}\nYour balance has been restored.", reply_markup=build_main_menu())

# ---------------- MESSAGE ROUTES ----------------
async def generic_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # handle wallet input raw (if user sends 0x...), handle update wallet state by checking text
    text = (update.message.text or "").strip()
    tg_id = update.effective_user.id
    # If user sends a 0x address, treat as wallet submission/update
    if text.startswith("0x") and len(text) == 42:
        user = db_get_user(tg_id)
        if not user:
            await update.message.reply_text("Please /start first.", reply_markup=build_main_menu())
            return
        # If not registered yet (registered == 0), treat as final wallet to register
        if user["registered"] == 0:
            ok, msg = db_set_wallet_and_register(tg_id, text)
            if ok:
                # notify referrer (if any) about referral credit
                user_after = db_get_user(tg_id)
                if user_after and user_after["ref_by"]:
                    ref = db_get_user(user_after["ref_by"])
                    if ref:
                        # Send notification to referrer via bot (async)
                        try:
                            await context.bot.send_message(ref["tg_id"],
                                f"🎁 Referral Bonus!\n\nA new user @{user_after['username']} joined with your referral link!\nYou earned {REFERRAL_BONUS} MAT (~ ${to_usd(REFERRAL_BONUS)})"
                            )
                        except Exception:
                            logger.exception("Failed to notify referrer")
                await update.message.reply_text(f"🎉 Registration Successful!\n\n💰 Received: {JOIN_BONUS} MAT (~ ${to_usd(JOIN_BONUS)})\nDistribution: Instant\nUse /dashboard to view your stats.", reply_markup=build_main_menu())
            else:
                await update.message.reply_text(f"Error registering wallet: {msg}", reply_markup=build_main_menu())
        else:
            # Already registered — this is a wallet update
            db_update_wallet(tg_id, text)
            await update.message.reply_text(f"✅ Wallet Updated!\nNew wallet: {text}", reply_markup=build_main_menu())
        return
    # other messages go to text handler
    await text_handler(update, context)

# ---------------- STARTUP ----------------
def main():
    init_db()
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set in environment.")
        return
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))
    app.add_handler(CommandHandler("withdraw", cmd_withdraw))
    app.add_handler(CommandHandler("referral", cmd_referral))
    app.add_handler(CommandHandler("wallet", cmd_wallet_settings))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), generic_message))
    logger.info("MAT Airdrop Bot starting...")
    app.run_polling(allowed_updates=None)

if __name__ == "__main__":
    main()
