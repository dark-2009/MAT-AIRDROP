import os
from dotenv import load_dotenv

load_dotenv()

# Bot configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Airdrop configuration (values in MAT)
REFERRAL_REWARD = 0.8    # 0.8 MAT per referral
INITIAL_REWARD = 2       # 2 MAT for registration
MIN_WITHDRAWAL = 4       # 4 MAT minimum to withdraw

# Telegram channels (update appropriately)
YOUR_TELEGRAM_ID = "@NUCLEAR05"
TELEGRAM_GROUP = "https://t.me/fuckincarders"

# Admin configuration (not used for automatic payouts)
ADMIN_IDS = []
