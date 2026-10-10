"""
Sicheres Schreiben von Dateien auf dem Railway-Volume.

Vorher wurde direkt in die Zieldatei geschrieben. Ein Absturz oder Redeploy
mitten in wb.save() hinterließ eine halbe, unlesbare Excel - danach schlug
jeder Zugriff fehl, auch /status. Jetzt wird erst in eine Temp-Datei im
SELBEN Verzeichnis geschrieben und dann per os.replace getauscht: das ist
atomar, die Zieldatei ist immer entweder ganz alt oder ganz neu.
"""
import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


def atomar_schreiben(ziel, schreib_fn) -> None:
    """
    schreib_fn(pfad) schreibt den Inhalt nach `pfad` (z.B. wb.save).
    Danach wird die Temp-Datei atomar an die Stelle von `ziel` gesetzt.
    """
    ziel = Path(ziel)
    fd, tmp = tempfile.mkstemp(prefix=f".{ziel.stem}_", suffix=ziel.suffix, dir=str(ziel.parent))
    os.close(fd)
    try:
        schreib_fn(tmp)
        os.replace(tmp, ziel)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def atomar_speichern(wb, ziel) -> None:
    """openpyxl-Workbook atomar speichern."""
    atomar_schreiben(ziel, wb.save)


def tages_backup(quelle, behalten: int = 14) -> None:
    """
    Einmal pro Tag eine Kopie nach <DATA_DIR>/backup/<name>_<JJJJMMTT>.xlsx,
    die letzten `behalten` Kopien bleiben liegen. Fehler hier dürfen das
    eigentliche Speichern nie verhindern.
    """
    try:
        quelle = Path(quelle)
        if not quelle.exists():
            return
        ordner = quelle.parent / "backup"
        ordner.mkdir(exist_ok=True)
        ziel = ordner / f"{quelle.stem}_{datetime.now().strftime('%Y%m%d')}{quelle.suffix}"
        if ziel.exists():
            return
        shutil.copy2(quelle, ziel)
        alte = sorted(ordner.glob(f"{quelle.stem}_*{quelle.suffix}"))
        for p in alte[:-behalten]:
            p.unlink()
        log.info(f"🗄️ Backup: {ziel.name}")
    except Exception as e:
        log.warning(f"Backup fehlgeschlagen: {e}")
