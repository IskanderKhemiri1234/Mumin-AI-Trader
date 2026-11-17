from __future__ import annotations
import sys
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / '.env'

def main() -> int:
    if not ENV_FILE.is_file():
        print('Missing .env. Copy .env.example and configure DASHBOARD_ACCESS_TOKEN.', file=sys.stderr)
        return 2
    values = dotenv_values(ENV_FILE)
    required = {
        'TRADING_ENV': 'TESTNET',
        'TRADING_MODE': 'PAPER',
        'ALLOW_MAINNET': 'false',
        'LIVE_TRADING_ENABLED': 'false',
        'TESTNET_ORDER_PREFLIGHT_ENABLED': 'false',
        'TESTNET_SPOT_EXECUTION_ENABLED': 'false',
    }
    errors = [f'{key} must be {expected!r} (found {values.get(key)!r})'
              for key, expected in required.items()
              if str(values.get(key, '')).strip().lower() != expected.lower()]
    token = str(values.get('DASHBOARD_ACCESS_TOKEN') or '').strip()
    if len(token) < 32:
        errors.append('DASHBOARD_ACCESS_TOKEN must contain at least 32 characters.')
    if errors:
        print('Unsafe Termux runtime configuration:', file=sys.stderr)
        for error in errors:
            print(f'- {error}', file=sys.stderr)
        return 2
    print('Termux runtime safety verified: PAPER only; Testnet order gates OFF; Mainnet OFF.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
