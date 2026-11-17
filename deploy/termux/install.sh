#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
ROOT="$HOME/Mumin-AI-Trader"
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
SERVICE_ROOT="$PREFIX/var/service"
BOOT_DIR="$HOME/.termux/boot"
if [ ! -d "$ROOT/.git" ]; then
  printf "Repository not found at %s\n" "$ROOT" >&2
  printf "Clone the repository into ~/Mumin-AI-Trader first.\n" >&2
  exit 1
fi
pkg update -y
pkg install -y python git termux-services
cd "$ROOT"
python -m pip install --upgrade pip
python -m pip install -e .
if [ ! -f "$ROOT/.env" ]; then
  cp .env.example .env
  TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
  python -c 'from pathlib import Path; import sys; p=Path(".env"); s=p.read_text(); p.write_text(s.replace("DASHBOARD_ACCESS_TOKEN=", "DASHBOARD_ACCESS_TOKEN=" + sys.argv[1], 1))' "$TOKEN"
  chmod 600 .env
  printf "Created private .env and generated a dashboard token. Keep it secret.\n"
fi
python scripts/validate_termux_runtime.py
mkdir -p "$SERVICE_ROOT" "$HOME/.mumin-ai-trader/logs" "$BOOT_DIR"
for service in mumin-api mumin-paper-engine mumin-paper-backup mumin-paper-watchdog; do
  mkdir -p "$SERVICE_ROOT/$service"
  cp "$ROOT/deploy/termux/services/$service/run" "$SERVICE_ROOT/$service/run"
  chmod 700 "$SERVICE_ROOT/$service/run"
done
cat > "$BOOT_DIR/00-mumin-ai-trader" <<'BOOT'
#!/data/data/com.termux/files/usr/bin/sh
set -eu
termux-wake-lock >/dev/null 2>&1 || true
if [ -f "$PREFIX/etc/profile.d/start-services.sh" ]; then . "$PREFIX/etc/profile.d/start-services.sh"; fi
sleep 3
sv up mumin-api >/dev/null 2>&1 || true
sv up mumin-paper-engine >/dev/null 2>&1 || true
sv up mumin-paper-backup >/dev/null 2>&1 || true
sv up mumin-paper-watchdog >/dev/null 2>&1 || true
BOOT
chmod 700 "$BOOT_DIR/00-mumin-ai-trader"
if [ -f "$PREFIX/etc/profile.d/start-services.sh" ]; then . "$PREFIX/etc/profile.d/start-services.sh"; fi
sleep 2
sv up mumin-api
sv up mumin-paper-engine
sv up mumin-paper-backup
sv up mumin-paper-watchdog
printf "\nMumin node installed. Dashboard: http://127.0.0.1:8000/dashboard\n"
printf "Health: http://127.0.0.1:8000/health\nLogs: %s\n" "$HOME/.mumin-ai-trader/logs"
printf "Install and open Termux:Boot separately; disable battery optimization for Termux.\n"