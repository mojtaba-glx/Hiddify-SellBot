# Hiddify-SellBot

Telegram AdminBot + UserBot for Hiddify sales workflows.

## Quick Start

```bash
cd ~/Hiddify-SellBot
./install.sh install
```

## Main Commands

```bash
./install.sh install       # First-time setup
./install.sh update        # Safe backup + git update + restart
./install.sh start         # Start bots
./install.sh stop          # Stop bots
./install.sh restart       # Restart bots
./install.sh status        # Check status
./install.sh config        # Configure .env interactively
./install.sh factory-reset # Reset bot data to factory defaults
./install.sh version       # Show current version
```

## Notes

- Dependencies are installed automatically from `requirements.txt`.
- `.env` is required (`ADMIN_ID`, `ADMIN_BOT_TOKEN`, `USER_BOT_TOKEN`).
- `factory-reset` does not modify code or `.env`; it only resets runtime data.
