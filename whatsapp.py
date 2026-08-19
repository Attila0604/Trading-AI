import os, logging, urllib.parse, urllib.request, threading

log = logging.getLogger(__name__)

PHONE   = os.getenv("CALLMEBOT_PHONE", "")
API_KEY = os.getenv("CALLMEBOT_APIKEY", "")


def _senden(message: str) -> bool:
    """Eigentlicher Versand (blockierend) - läuft im Hintergrund-Thread."""
    try:
        encoded = urllib.parse.quote(message)
        url = f"https://api.callmebot.com/whatsapp.php?phone={PHONE}&text={encoded}&apikey={API_KEY}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read().decode()
            if resp.status == 200:
                log.info(f"✅ WhatsApp gesendet: {message[:80]}...")
                return True
            log.error(f"WhatsApp Fehler: {resp.status} {body[:200]}")
            return False
    except Exception as e:
        log.error(f"WhatsApp Ausnahme: {e}")
        return False


def send_whatsapp(message: str) -> bool:
    """
    Nicht-blockierend: startet den Versand in einem Hintergrund-Thread.
    Vorher wartete der Server bis zu 10 Sekunden pro Nachricht - bei
    mehreren Nachrichten stand die ganze App währenddessen still.
    """
    if not PHONE or not API_KEY:
        log.warning("WhatsApp: CALLMEBOT_PHONE / CALLMEBOT_APIKEY nicht gesetzt")
        return False
    threading.Thread(target=_senden, args=(message,), daemon=True).start()
    return True


def send_whatsapp_sync(message: str) -> bool:
    """Blockierende Variante - nur nutzen, wenn das Ergebnis sofort zählt."""
    if not PHONE or not API_KEY:
        log.warning("WhatsApp: CALLMEBOT_PHONE / CALLMEBOT_APIKEY nicht gesetzt")
        return False
    return _senden(message)
