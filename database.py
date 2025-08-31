import sqlite3
import logging
from decimal import Decimal
from contextlib import closing
import config

logger = logging.getLogger(__name__)

DB_PATH = 'usdt_airdrop.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Create users table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        username TEXT,
        wallet_address TEXT,
        balance REAL DEFAULT 0,
        referrals INTEGER DEFAULT 0,
        earned_from_referrals REAL DEFAULT 0,
        tasks_completed INTEGER DEFAULT 0,
        registered INTEGER DEFAULT 0
    )
    ''')

    # Create transactions table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS transactions (
        tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id BIGINT,
        amount_mat REAL,
        dest_wallet TEXT,
        tx_hash TEXT,
        status TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def add_user(user_id, username):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT OR IGNORE INTO users (user_id, username, balance) VALUES (?, ?, 0)',
            (user_id, username)
        )
        conn.commit()
        return True
    except sqlite3.Error as e:
        logger.error(f"Error adding user: {e}")
        return False
    finally:
        conn.close()

def get_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def update_user_wallet(user_id, wallet_address):
    """Save wallet and credit initial reward defined in config.INITIAL_REWARD"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'UPDATE users SET wallet_address = ?, registered = 1, balance = balance + ? WHERE user_id = ?',
            (wallet_address, float(config.INITIAL_REWARD), user_id)
        )
        conn.commit()
        return True
    except sqlite3.Error as e:
        logger.error(f"Error updating wallet: {e}")
        return False
    finally:
        conn.close()

def mark_tasks_completed(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'UPDATE users SET tasks_completed = 1 WHERE user_id = ?',
            (user_id,)
        )
        conn.commit()
        return True
    except sqlite3.Error as e:
        logger.error(f"Error marking tasks completed: {e}")
        return False
    finally:
        conn.close()

def add_referral(referrer_id):
    """Credit referral reward to referrer. Uses config.REFERRAL_REWARD"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'UPDATE users SET referrals = referrals + 1, earned_from_referrals = earned_from_referrals + ?, balance = balance + ? WHERE user_id = ?',
            (float(config.REFERRAL_REWARD), float(config.REFERRAL_REWARD), referrer_id)
        )
        conn.commit()
        return True
    except sqlite3.Error as e:
        logger.error(f"Error adding referral: {e}")
        return False
    finally:
        conn.close()

def update_balance(user_id, amount):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'UPDATE users SET balance = balance + ? WHERE user_id = ?',
            (amount, user_id)
        )
        conn.commit()
        return True
    except sqlite3.Error as e:
        logger.error(f"Error updating balance: {e}")
        return False
    finally:
        conn.close()

def reset_user_progress(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'UPDATE users SET tasks_completed = 0, registered = 0, wallet_address = NULL WHERE user_id = ?',
            (user_id,)
        )
        conn.commit()
        return True
    except sqlite3.Error as e:
        logger.error(f"Error resetting user progress: {e}")
        return False
    finally:
        conn.close()

# Transaction helpers
def create_transaction(conn, user_id, amount_mat, dest_wallet, status='pending'):
    cur = conn.cursor()
    cur.execute('INSERT INTO transactions (user_id, amount_mat, dest_wallet, status) VALUES (?, ?, ?, ?)',
                (user_id, float(amount_mat), dest_wallet, status))
    return cur.lastrowid

def update_transaction_status(conn, tx_id, status, tx_hash=None):
    cur = conn.cursor()
    if tx_hash:
        cur.execute('UPDATE transactions SET status=?, tx_hash=? WHERE tx_id=?', (status, tx_hash, tx_id))
    else:
        cur.execute('UPDATE transactions SET status=? WHERE tx_id=?', (status, tx_id))
    conn.commit()
