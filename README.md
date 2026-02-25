# Hiddify-SellBot

Telegram AdminBot + UserBot for Hiddify sales workflows.

## Quick Start (Menu Mode)

```bash
git clone https://github.com/mojtaba-glx/Hiddify-SellBot.git && cd Hiddify-SellBot && chmod +x install.sh && ./install.sh
```

`./install.sh` with no command opens the interactive menu:

```text
1) install  2) update  3) start  4) stop  5) restart ...
```

## First-Time Install (Direct Command)

```bash
cd ~/Hiddify-SellBot && ./install.sh install
```

## One-Line Update (after first install)

```bash
cd ~/Hiddify-SellBot && ./install.sh update
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
