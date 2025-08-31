import os
import logging
import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from database import init_db, add_user, get_user, update_user_wallet, mark_tasks_completed, add_referral, update_balance, reset_user_progress, get_db_connection, create_transaction, update_transaction_status
from config import BOT_TOKEN, REFERRAL_REWARD, INITIAL_REWARD, MIN_WITHDRAWAL, YOUR_TELEGRAM_ID, TELEGRAM_GROUP
from decimal import Decimal
from dotenv import load_dotenv

# Web3
from web3 import Web3

load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize bot
bot = telebot.TeleBot(BOT_TOKEN)

# Initialize database
init_db()

# Store user states
user_states = {}
user_wallets = {}

# Main menu keyboard
def main_menu_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("🚀 Join Airdrop"))
    keyboard.add(KeyboardButton("📊 Dashboard"), KeyboardButton("💸 Withdraw MAT"))
    keyboard.add(KeyboardButton("👥 Referral Program"), KeyboardButton("ℹ️ Help"))
    return keyboard

# --- Web3 / MAT setup ---
BSC_RPC_URL = os.getenv('BSC_RPC_URL')
MAT_TOKEN_ADDRESS = os.getenv('MAT_TOKEN_ADDRESS')
PAYOUT_FROM_ADDRESS = os.getenv('PAYOUT_FROM_ADDRESS')
PRIVATE_KEY = os.getenv('PRIVATE_KEY')
GAS_PRICE_GWEI = int(os.getenv('GAS_PRICE_GWEI', '5'))

w3 = Web3(Web3.HTTPProvider(BSC_RPC_URL))
if not w3.is_connected():
    logger.warning("Web3 not connected. Check BSC_RPC_URL")

ERC20_ABI = [
    {"constant":False,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
    {"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},
]

if MAT_TOKEN_ADDRESS:
    mat_contract = w3.eth.contract(address=Web3.to_checksum_address(MAT_TOKEN_ADDRESS), abi=ERC20_ABI)
else:
    mat_contract = None

def mat_to_minor_units(amount_mat: Decimal, decimals: int) -> int:
    return int((amount_mat * (Decimal(10) ** decimals)).quantize(Decimal('1')))

def send_mat(dest_addr: str, amount_mat: Decimal):
    """Send MAT tokens to user. Returns (ok:bool, tx_hash_or_error_str)."""
    if mat_contract is None:
        return False, "MAT contract not configured"
    try:
        dest = Web3.to_checksum_address(dest_addr)
    except Exception:
        return False, "Invalid wallet address"

    from_addr = Web3.to_checksum_address(PAYOUT_FROM_ADDRESS)
    try:
        decimals = mat_contract.functions.decimals().call()
    except Exception as e:
        return False, f"Error reading token decimals: {e}"
    amount = mat_to_minor_units(amount_mat, decimals)

    try:
        nonce = w3.eth.get_transaction_count(from_addr)
        gas_price = w3.to_wei(GAS_PRICE_GWEI, 'gwei')

        tx = mat_contract.functions.transfer(dest, amount).build_transaction({
            'from': from_addr,
            'nonce': nonce,
            'gasPrice': gas_price,
        })

        try:
            gas_est = w3.eth.estimate_gas(tx)
            tx['gas'] = int(gas_est * 1.2)
        except Exception:
            tx['gas'] = 200000

        signed = w3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)

        if receipt.status == 1:
            return True, w3.to_hex(tx_hash)
        else:
            return False, "Transaction reverted on-chain"
    except Exception as e:
        return False, str(e)

# Start command
@bot.message_handler(commands=['start', 'help', 'dashboard', 'withdraw', 'referral'])
def handle_commands(message):
    user_id = message.from_user.id
    command = message.text.split()[0].lower()

    if command == '/start':
        start_command(message)
    elif command == '/help':
        help_command(message)
    elif command == '/dashboard':
        dashboard_command(message)
    elif command == '/withdraw':
        withdraw_command(message)
    elif command == '/referral':
        referral_command(message)

def start_command(message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    # Reset any existing state
    if user_id in user_states:
        del user_states[user_id]
    if user_id in user_wallets:
        del user_wallets[user_id]

    # Check if this is a referral (start <referrer_id>)
    referral_id = None
    if message.text and len(message.text.split()) > 1:
        try:
            referral_id = int(message.text.split()[1])
            logger.info(f"New user came from referral: {referral_id}")
        except ValueError:
            pass

    # Register new user or get existing
    user = get_user(user_id)
    if user is None:
        add_user(user_id, username)

        # Reward referrer if applicable
        if referral_id and get_user(referral_id):
            add_referral(referral_id)
            logger.info(f"Rewarded referral {referral_id} with {REFERRAL_REWARD} MAT")

    welcome_message = (
        f"🚀 Welcome to MAT Airdrop Bot! 🚀\n\n"
        f"Hello {username}, I'm your guide to earning MAT tokens : )\n\n"
        f"🌐 Network: BNB Smart Chain (BEP-20)\n\n"
        f"🎁 What you'll get:\n"
        f"• {INITIAL_REWARD} MAT for registration\n"
        f"• {REFERRAL_REWARD} MAT per referral\n\n"
        f"⏰ Distribution: Instant\n\n"
        f"🔹 Available Commands:\n"
        f"/start - Start/Restart the bot\n"
        f"/dashboard - View your account\n"
        f"/withdraw - Withdraw your MAT\n"
        f"/referral - Get referral link\n"
        f"/help - Show help information\n\n"
        f"Press 'Join Airdrop' below or type /start to begin!"
    )

    bot.send_message(message.chat.id, welcome_message, reply_markup=main_menu_keyboard())

def help_command(message):
    help_text = (
        "🤖 MAT Airdrop Bot Help\n\n"
        "🔹 Available Commands:\n"
        "/start - Start/Restart the bot\n"
        "/dashboard - View your account dashboard\n"
        "/withdraw - Withdraw your MAT \n"
        "/referral - Get referral link\n"
        "/help - Show this help message\n\n"
        "📋 How to participate:\n"
        "1. Click 'Join Airdrop' or type /start\n"
        "2. Complete the simple tasks\n"
        "3. Enter your wallet address\n"
        "4. Start earning MAT!\n\n"
        "💡 Tips:\n"
        f"• Minimum withdrawal: {MIN_WITHDRAWAL} MAT\n"
        f"• Each referral earns you {REFERRAL_REWARD} MAT\n"
        "• Use Trust Wallet or MetaMask for best experience"
    )
    bot.send_message(message.chat.id, help_text, reply_markup=main_menu_keyboard())

# Handle text messages
@bot.message_handler(func=lambda message: True)
def handle_text_messages(message):
    user_id = message.from_user.id
    text = message.text.strip()

    if text == "🚀 Join Airdrop" or text.lower()=='join airdrop':
        join_airdrop(message)
    elif text == "📊 Dashboard" or text.lower()=='dashboard':
        dashboard_command(message)
    elif text == "💸 Withdraw MAT" or text.lower()=='withdraw mat' or text.lower()=='withdraw':
        withdraw_command(message)
    elif text == "👥 Referral Program" or text.lower()=='referral program':
        referral_command(message)
    elif text == "ℹ️ Help" or text.lower()=='help':
        help_command(message)
    elif user_states.get(user_id) == 'awaiting_wallet':
        handle_wallet_input(message)
    else:
        bot.send_message(message.chat.id, "Please choose an option from the menu below 👇", reply_markup=main_menu_keyboard())

def join_airdrop(message):
    user_id = message.from_user.id

    # Reset user progress if they start again
    reset_user_progress(user_id)
    user_states[user_id] = None

    # Airdrop registration message
    keyboard = [[InlineKeyboardButton("✅ I Completed Tasks", callback_data='check_tasks')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    airdrop_message = (
        "🔥 Welcome to Meta Asset Token Airdrop Registration!\n\n"
        "To qualify, complete these simple tasks:\n\n"
        f"1. SEND 'MAT TO THE MOON!' TO OUR GROUP ({YOUR_TELEGRAM_ID})\n"
        f"2. Join our Telegram Group: {TELEGRAM_GROUP}\n\n"
        "👇 Press the button below after completing these tasks."
    )

    bot.send_message(message.chat.id, airdrop_message, reply_markup=reply_markup)

# Handle button callbacks
@bot.callback_query_handler(func=lambda call: True)
def button_handler(call):
    user_id = call.from_user.id
    user = get_user(user_id)

    if call.data == 'check_tasks':
        # For demo purposes, we'll assume tasks are completed
        mark_tasks_completed(user_id)
        user_states[user_id] = 'awaiting_wallet'

        bot.edit_message_text(
            "✅ Tasks verified successfully!\n\n"
            "Please provide your BNB (BEP-20) wallet address for receiving MAT :\n\n"
            "Enter your wallet address below:",
            call.message.chat.id,
            call.message.message_id
        )

    elif call.data == 'confirm_wallet_yes':
        wallet_address = user_wallets.get(user_id)
        if wallet_address:
            # Save wallet address and credit initial reward inside update_user_wallet
            if update_user_wallet(user_id, wallet_address):
                user = get_user(user_id)

                success_message = (
                    "✅ Registration Successful! 🎉\n\n"
                    f"Congratulations {call.from_user.first_name}!\n"
                    f"💰 Received: {INITIAL_REWARD} MAT\n\n"
                    f"⏰ Distribution: Distribution is Live Now!!\n\n"
                    f"Use the dashboard below to check your balance and invite friends!"
                )

                keyboard = [
                    [InlineKeyboardButton("📊 Dashboard", callback_data='dashboard')],
                    [InlineKeyboardButton("👥 Refer Friends", callback_data='copy_ref')]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                bot.edit_message_text(success_message, call.message.chat.id, call.message.message_id, reply_markup=reply_markup)

                # Clear states
                if user_id in user_states:
                    del user_states[user_id]
                if user_id in user_wallets:
                    del user_wallets[user_id]
            else:
                bot.edit_message_text("❌ Error saving wallet address. Please try /start again.", call.message.chat.id, call.message.message_id)
        else:
            bot.edit_message_text("❌ Wallet address not found. Please try /start again.", call.message.chat.id, call.message.message_id)

    elif call.data == 'confirm_wallet_no':
        user_states[user_id] = 'awaiting_wallet'
        bot.edit_message_text(
            "Please enter your wallet address again:",
            call.message.chat.id,
            call.message.message_id
        )

    elif call.data == 'dashboard':
        dashboard_callback(call)

    elif call.data == 'withdraw':
        withdraw_callback(call)

    elif call.data == 'copy_ref':
        bot.answer_callback_query(call.id, "Referral link copied to clipboard!", show_alert=True)

def handle_wallet_input(message):
    user_id = message.from_user.id
    wallet_address = message.text.strip()

    # Store wallet for confirmation (basic validation)
    user_wallets[user_id] = wallet_address
    user_states[user_id] = 'confirm_wallet'

    # Ask for confirmation
    keyboard = [
        [InlineKeyboardButton("✅ Yes, use this address", callback_data='confirm_wallet_yes')],
        [InlineKeyboardButton("❌ No, enter different address", callback_data='confirm_wallet_no')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    bot.send_message(
        message.chat.id,
        f"🔐 Please confirm your wallet address:\n\n{wallet_address}\n\n⚠️ Is this the address you want to use for receiving MAT?",
        reply_markup=reply_markup
    )

def dashboard_command(message):
    user_id = message.from_user.id
    user = get_user(user_id)

    if user and user['registered']:
        dashboard_message = (
            f"📊 MAT Airdrop Dashboard 📊\n\n"
            f"👤 User: {user['username'] or 'N/A'}\n"
            f"🔗 Wallet: {user['wallet_address'] or 'Not set'}\n\n"
            f"💰 MAT Balance: {user['balance']}\n"
            f"👥 Referrals: {user['referrals']}\n"
            f"💸 Referral Earnings: {user['earned_from_referrals']} MAT\n\n"
            f"⏰ Token Distribution: LIVE!\n\n"
            f"🔹 Available Commands:\n"
            f"/withdraw - Withdraw your MAT\n"
            f"/referral - Get referral link\n"
            f"/start - Restart registration"
        )

        bot.send_message(message.chat.id, dashboard_message, reply_markup=main_menu_keyboard())
    else:
        bot.send_message(message.chat.id, "Please complete registration first using /start", reply_markup=main_menu_keyboard())

def dashboard_callback(call):
    user_id = call.from_user.id
    user = get_user(user_id)

    if user and user['registered']:
        dashboard_message = (
            f"📊 MAT Airdrop Dashboard 📊\n\n"
            f"👤 User: {user['username'] or 'N/A'}\n"
            f"🔗 Wallet: {user['wallet_address'] or 'Not set'}\n\n"
            f"💰 MAT Balance: {user['balance']}\n"
            f"👥 Referrals: {user['referrals']}\n"
            f"💸 Referral Earnings: {user['earned_from_referrals']} MAT\n\n"
            f"⏰ Token Distribution: LIVE!"
        )

        bot.edit_message_text(dashboard_message, call.message.chat.id, call.message.message_id)
    else:
        bot.edit_message_text("Please complete registration first using /start", call.message.chat.id, call.message.message_id)

def withdraw_command(message):
    user_id = message.from_user.id
    user = get_user(user_id)

    if not user:
        bot.send_message(message.chat.id, "Please complete registration first using /start", reply_markup=main_menu_keyboard())
        return

    balance = Decimal(str(user['balance'] or 0))
    if balance < Decimal(str(MIN_WITHDRAWAL)):
        bot.send_message(
            message.chat.id,
            f"❌ Withdrawal Failed\n\nMinimum withdrawal amount: {MIN_WITHDRAWAL} MAT\nYour current balance: {balance} MAT",
            reply_markup=main_menu_keyboard()
        )
        return

    # Automatic on-chain transfer of MAT
    dest = user['wallet_address']
    if not dest:
        bot.send_message(message.chat.id, "Please set your wallet address first.", reply_markup=main_menu_keyboard())
        return

    # Perform DB atomic deduction and create transaction record
    conn = get_db_connection()
    try:
        conn.execute('BEGIN')
        # Deduct full balance
        conn.execute('UPDATE users SET balance = 0 WHERE user_id = ?', (user_id,))
        tx_id = create_transaction(conn, user_id, float(balance), dest, status='pending')
        conn.commit()
    except Exception as e:
        conn.rollback()
        bot.send_message(message.chat.id, f"DB error: {e}", reply_markup=main_menu_keyboard())
        conn.close()
        return

    bot.send_message(message.chat.id, f"Processing automatic withdrawal of {balance} MAT to {dest} ...")

    ok, res = send_mat(dest, balance)
    if ok:
        txhash = res
        try:
            conn = get_db_connection()
            update_transaction_status(conn, tx_id, 'completed', txhash)
            conn.close()
        except Exception as e:
            logger.error(f"Failed to update transaction status: {e}")
        bot.send_message(message.chat.id, f"✅ Withdrawal sent!\nTx: {txhash}\nView: https://bscscan.com/tx/{txhash}", reply_markup=main_menu_keyboard())
    else:
        # revert balance and mark failed
        try:
            conn = get_db_connection()
            conn.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (float(balance), user_id))
            update_transaction_status(conn, tx_id, 'failed', None)
            conn.close()
        except Exception as e:
            logger.error(f"Failed to revert balance after failed tx: {e}")
        bot.send_message(message.chat.id, f"❌ Withdrawal failed: {res}\nYour balance has been restored.", reply_markup=main_menu_keyboard())

def withdraw_callback(call):
    # deprecated - kept for compatibility
    withdraw_command(call.message)

def referral_command(message):
    user_id = message.from_user.id
    user = get_user(user_id)

    if user:
        bot_username = bot.get_me().username if hasattr(bot, 'get_me') else 'MATBot'
        referral_link = f"https://t.me/{bot_username}?start={user_id}"

        referral_message = (
            f"🚀 Referral Program 🚀\n\n"
            f"Referral Bonus: {REFERRAL_REWARD} MAT per referral\n\n"
            f"👥 Your Referrals: {user['referrals']}\n"
            f"💰 Total Earned: {user['earned_from_referrals']} MAT\n\n"
            f"🔗 Your Referral Link:\n{referral_link}\n\n"
            f"How to invite friends:\n"
            f"• Share your referral link\n"
            f"• Ask them to join using your link\n"
            f"• They must complete all tasks\n"
            f"• You'll receive tokens automatically\n\n"
            f"💡 Tip: Copy the link above and share it with friends!"
        )

        bot.send_message(message.chat.id, referral_message, reply_markup=main_menu_keyboard())
    else:
        bot.send_message(message.chat.id, "Please complete registration first using /start", reply_markup=main_menu_keyboard())

# Main function
if __name__ == '__main__':
    print("🤖 MAT Airdrop Bot is starting...")
    print("🔹 Available commands: /start, /dashboard, /withdraw, /referral, /help")
    bot.infinity_polling()
