# notifications.py
import logging
import requests

def send_telegram(message: str, token: str, chat_id: str) -> bool:
    """
    Send a message via Telegram Bot API. Returns True on success.
    Silently skips (returns False) if token or chat_id are not configured,
    so the bot works in paper mode without Telegram credentials.
    """
    if not token or not chat_id:
        logging.info(f"[TELEGRAM DISABLED] {message}")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            return True
        else:
            logging.warning(f"Telegram returned HTTP {r.status_code}: {r.text[:200]}")
            return False
    except Exception as e:
        logging.error(f"Telegram send failed: {e}")
        return False
