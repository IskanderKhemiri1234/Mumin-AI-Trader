from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import sys
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://testnet.binance.vision"
RECV_WINDOW_MS = 5000
TIMEOUT_SECONDS = 12


def request_json(path: str, *, api_key: str | None = None, query: dict[str, str] | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-MBX-APIKEY"] = api_key
    url = f"{BASE_URL}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {"msg": "Non-JSON response from Binance."}
        raise RuntimeError(f"Binance HTTP {exc.code}: {payload.get('msg', payload)}") from None
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Network connection failed: {exc}") from None


def main() -> int:
    print("Mumin — Binance Spot Testnet READ-ONLY account check")
    print("This script is pinned to testnet.binance.vision.")
    print("It can only read server time and account balances; it cannot place or cancel orders.\n")

    api_key = getpass.getpass("Spot Testnet API key (input hidden): ").strip()
    api_secret = getpass.getpass("Spot Testnet API secret (input hidden): ").strip()
    if not api_key or not api_secret:
        print("Missing credentials. Nothing was sent.", file=sys.stderr)
        return 2

    try:
        server_time = int(request_json("/api/v3/time")["serverTime"])
        params = {
            "timestamp": str(server_time),
            "recvWindow": str(RECV_WINDOW_MS),
        }
        payload = urlencode(params)
        params["signature"] = hmac.new(
            api_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        account = request_json("/api/v3/account", api_key=api_key, query=params)
        balances = {
            str(item.get("asset", "")).upper(): (
                Decimal(str(item.get("free", "0"))),
                Decimal(str(item.get("locked", "0"))),
            )
            for item in account.get("balances", [])
        }
        free, locked = balances.get("USDT", (Decimal("0"), Decimal("0")))

        print("\nBINANCE SPOT TESTNET ACCOUNT: ACCESS OK")
        print(f"Account type: {account.get('accountType', 'not supplied')}")
        print(f"Spot trading enabled: {account.get('canTrade', 'not supplied')}")
        print(f"Withdrawals enabled: {account.get('canWithdraw', 'not supplied')}")
        print(f"USDT free: {free}")
        print(f"USDT locked: {locked}")
        print(f"USDT total: {free + locked}")
        print("Order submission: NOT ATTEMPTED")
        print("Credentials were not saved or printed.")
        return 0
    except RuntimeError as exc:
        message = str(exc)
        print(f"\nACCOUNT CHECK FAILED: {message}", file=sys.stderr)
        if "HTTP 451" in message:
            print(
                "Binance rejected this network location. This is not proof that the API key or secret is invalid.",
                file=sys.stderr,
            )
        elif "HTTP 401" in message or "-2015" in message or "-1022" in message:
            print(
                "Check that both credentials were copied from Spot Test Network and that the key has Spot trading permission.",
                file=sys.stderr,
            )
        elif "-1021" in message:
            print("The signed request timestamp was rejected. Retry once after checking the device clock.", file=sys.stderr)
        print("No order was submitted.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
