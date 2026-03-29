import os, logging, asyncio
import httpx

log = logging.getLogger(__name__)

DEMO_BASE = "https://demo-api-capital.backend.capital/api/v1"
LIVE_BASE = "https://api-capital.backend.capital/api/v1"


class CapitalClient:
    def __init__(self):
        self.base  = DEMO_BASE if os.getenv("CAPITAL_DEMO", "true").lower() == "true" else LIVE_BASE
        self.email = os.getenv("CAPITAL_EMAIL", "")
        self.password = os.getenv("CAPITAL_PASSWORD", "")
        self.api_key  = os.getenv("CAPITAL_API_KEY", "")
        self.cst   = None
        self.token = None
        self._account_id = None
        log.info(f"Capital.com Client | {'DEMO' if 'demo' in self.base else 'LIVE'} | {self.base}")

    def is_connected(self) -> bool:
        return bool(self.cst and self.token)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "X-CAP-API-KEY": self.api_key}
        if self.cst:
            h["CST"] = self.cst
        if self.token:
            h["X-SECURITY-TOKEN"] = self.token
        return h

    async def connect(self) -> bool:
        if not self.email or not self.password:
            log.error("Capital.com: CAPITAL_EMAIL / CAPITAL_PASSWORD nicht gesetzt")
            return False
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self.base}/session",
                    json={"identifier": self.email, "password": self.password, "encryptedPassword": False},
                    headers={"Content-Type": "application/json", "X-CAP-API-KEY": self.api_key} if self.api_key else {"Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    self.cst   = resp.headers.get("CST")
                    self.token = resp.headers.get("X-SECURITY-TOKEN")
                    data = resp.json()
                    self._account_id = data.get("currentAccountId")
                    log.info(f"✅ Capital.com verbunden | Account: {self._account_id}")
                    return True
                else:
                    log.error(f"Capital.com Login fehlgeschlagen: {resp.status_code} {resp.text[:300]}")
                    return False
        except Exception as e:
            log.error(f"Capital.com Verbindungsfehler: {e}")
            return False

    async def _get(self, path: str) -> dict:
        if not self.is_connected():
            await self.connect()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{self.base}{path}", headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def _post(self, path: str, data: dict) -> dict:
        if not self.is_connected():
            await self.connect()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{self.base}{path}", json=data, headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def _delete(self, path: str) -> dict:
        if not self.is_connected():
            await self.connect()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.delete(f"{self.base}{path}", headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def get_account_info(self) -> dict:
        try:
            data = await self._get("/accounts")
            accounts = data.get("accounts", [])
            if accounts:
                acc = accounts[0]
                return {
                    "accountId":  acc.get("accountId"),
                    "accountName": acc.get("accountName"),
                    "currency":   acc.get("preferred", False),
                    "balance":    acc.get("balance", {}).get("balance", 0),
                    "available":  acc.get("balance", {}).get("available", 0),
                    "deposit":    acc.get("balance", {}).get("deposit", 0),
                    "profitLoss": acc.get("balance", {}).get("profitLoss", 0),
                }
            return {}
        except Exception as e:
            log.error(f"get_account_info Fehler: {e}")
            return {"error": str(e)}

    async def get_prices(self, epic: str) -> dict:
        try:
            data = await self._get(f"/prices/{epic}?resolution=MINUTE&max=1")
            prices = data.get("prices", [])
            if prices:
                p = prices[-1]
                return {
                    "epic":  epic,
                    "bid":   p.get("closePrice", {}).get("bid"),
                    "ask":   p.get("closePrice", {}).get("ask"),
                    "time":  p.get("snapshotTimeUTC"),
                }
            return {"epic": epic, "bid": None, "ask": None}
        except Exception as e:
            log.error(f"get_prices {epic} Fehler: {e}")
            return {"error": str(e)}

    async def get_positions(self) -> dict:
        try:
            data = await self._get("/positions")
            positions = []
            for p in data.get("positions", []):
                pos = p.get("position", {})
                mkt = p.get("market", {})
                positions.append({
                    "dealId":        pos.get("dealId"),
                    "epic":          mkt.get("epic"),
                    "direction":     pos.get("direction"),
                    "size":          pos.get("size"),
                    "openLevel":     pos.get("openLevel"),
                    "currentBid":    mkt.get("bid"),
                    "currentOffer":  mkt.get("offer"),
                    "pnl":           pos.get("unrealisedPnl"),
                    "createdDate":   pos.get("createdDateUTC"),
                })
            return {"positions": positions, "count": len(positions)}
        except Exception as e:
            log.error(f"get_positions Fehler: {e}")
            return {"error": str(e)}

    async def create_position(self, epic: str, direction: str, size: float,
                               stop_loss_pct: float = 1.5, take_profit_pct: float = 3.0) -> dict:
        try:
            # Get current price first
            price_data = await self.get_prices(epic)
            current_price = price_data.get("ask") if direction == "BUY" else price_data.get("bid")

            payload = {
                "epic":      epic,
                "direction": direction.upper(),
                "size":      str(size),
                "guaranteedStop": False,
            }

            # Add SL/TP if we have price
            if current_price:
                if direction.upper() == "BUY":
                    sl_level = round(current_price * (1 - stop_loss_pct / 100), 5)
                    tp_level = round(current_price * (1 + take_profit_pct / 100), 5)
                else:
                    sl_level = round(current_price * (1 + stop_loss_pct / 100), 5)
                    tp_level = round(current_price * (1 - take_profit_pct / 100), 5)

                payload["stopLevel"]   = sl_level
                payload["profitLevel"] = tp_level

            log.info(f"Order: {direction} {epic} x{size} | SL: {payload.get('stopLevel')} | TP: {payload.get('profitLevel')}")
            data = await self._post("/positions", payload)
            deal_ref = data.get("dealReference")
            log.info(f"✅ Order platziert | DealRef: {deal_ref}")
            return {"dealId": deal_ref, "status": "success", "data": data}

        except httpx.HTTPStatusError as e:
            log.error(f"create_position HTTP-Fehler {e.response.status_code}: {e.response.text[:300]}")
            return {"error": f"HTTP {e.response.status_code}", "detail": e.response.text[:300]}
        except Exception as e:
            log.error(f"create_position Fehler: {e}")
            return {"error": str(e)}

    async def close_position(self, deal_id: str) -> dict:
        try:
            data = await self._delete(f"/positions/{deal_id}")
            log.info(f"✅ Position geschlossen: {deal_id}")
            return {"status": "closed", "dealId": deal_id, "data": data}
        except Exception as e:
            log.error(f"close_position Fehler: {e}")
            return {"error": str(e)}

    async def search_markets(self, query: str) -> list:
        try:
            data = await self._get(f"/markets?searchTerm={query}&limit=5")
            return data.get("markets", [])
        except Exception as e:
            log.error(f"search_markets Fehler: {e}")
            return []
