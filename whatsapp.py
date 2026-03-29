import os, logging, urllib.parse, urllib.request

log = logging.getLogger(__name__)

PHONE   = os.getenv("CALLMEBOT_PHONE", "")
API_KEY = os.getenv("CALLMEBOT_APIKEY", "")


def send_whatsapp(message: str) -> bool:
    if not PHONE or not API_KEY:
        log.warning("WhatsApp: CALLMEBOT_PHONE / CALLMEBOT_APIKEY nicht gesetzt")
        return False
    try:
        encoded = urllib.parse.quote(message)
        url = f"https://api.callmebot.com/whatsapp.php?phone={PHONE}&text={encoded}&apikey={API_KEY}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read().decode()
            if resp.status == 200:
                log.info(f"✅ WhatsApp gesendet: {message[:80]}...")
                return True
            else:
                log.error(f"WhatsApp Fehler: {resp.status} {body[:200]}")
                return False
    except Exception as e:
        log.error(f"WhatsApp Ausnahme: {e}")
        return False
