# LNhelperBot

A professional Telegram bot for monitoring Bitcoin transaction confirmations, block notifications, and visualizing Lightning liquidity on Magma. Built for reliability, privacy, and open source collaboration.

## Features
- Get notified when your Bitcoin transaction reaches 6 confirmations
- Monitor multiple transactions at once
- Receive notifications for every new Bitcoin block
- View a professional Magma liquidity chart (Amboss)
- Persistent state across restarts
- Clean, mobile-friendly UI and chart

## Setup

### 1. Clone the repository
```bash
git clone https://github.com/yourusername/lnhelperbot.git
cd lnhelperbot/Developer/btc-tx-watcher
```

### 2. Install dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure environment variables
Copy `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
```
Edit `.env` and set:
- `TELEGRAM_BOT_TOKEN` (from @BotFather)
- `AMBOSS_API_KEY` (from amboss.space)

### 4. Run the bot
```bash
python bot.py
```

## Deploying on DigitalOcean
- Create a new Ubuntu droplet
- Install Python 3.10+, git, and pip
- Follow the setup steps above
- Use `tmux` or `screen` to keep the bot running in the background

## Security
- **Never commit your `.env` file or credentials to git!**
- The bot is designed to be open source and safe for public repositories.

## Contributing
Pull requests and issues are welcome! Please open an issue for feature requests or bug reports.

## License
MIT

---

**LNhelperBot** — Professional Bitcoin & Lightning notifications for everyone. 