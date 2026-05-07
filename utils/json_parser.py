"""
Defensiver JSON-Parser für Agent-Antworten.
Behandelt Vorspann, Markdown-Codeblöcke und Trailing-Text.
"""
import json
import re
import logging

logger = logging.getLogger(__name__)


def parse_agent_json(raw_response: str, agent_name: str = "agent") -> dict | None:
    """
    Robuster JSON-Parser für Agent-Antworten.
    
    Args:
        raw_response: Rohe Text-Antwort vom LLM
        agent_name: Name des Agents (für Logging)
    
    Returns:
        Geparsetes Dict oder None bei Fehler
    """
    if not raw_response or not raw_response.strip():
        logger.warning(f"[{agent_name}] Leere Antwort")
        return None
    
    # 1. Direkter Parse-Versuch (Happy Path)
    try:
        return json.loads(raw_response.strip())
    except json.JSONDecodeError:
        pass
    
    # 2. Markdown-Codeblock entfernen (```json ... ```)
    cleaned = re.sub(r'^```(?:json)?\s*', '', raw_response.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r'\s*```\s*$', '', cleaned, flags=re.MULTILINE)
    try:
        return json.loads(cleaned.strip())
    except json.JSONDecodeError:
        pass
    
    # 3. Erstes JSON-Objekt extrahieren (Vorspann ignorieren)
    start = cleaned.find('{')
    if start == -1:
        logger.error(f"[{agent_name}] Kein JSON gefunden | Raw: {raw_response[:200]}")
        return None
    
    # Balancierte Klammern finden
    depth = 0
    for i, char in enumerate(cleaned[start:], start):
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                json_str = cleaned[start:i+1]
                try:
                    parsed = json.loads(json_str)
                    logger.info(f"[{agent_name}] JSON aus Vorspann extrahiert")
                    return parsed
                except json.JSONDecodeError as e:
                    logger.error(f"[{agent_name}] Extraktion fehlgeschlagen: {e}")
                    return None
    
    logger.error(f"[{agent_name}] Unbalanciertes JSON | Raw: {raw_response[:200]}")
    return None
