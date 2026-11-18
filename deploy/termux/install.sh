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
python -m pip install -e .
if [ ! -f "$ROOT/.env" ]; then
  cp .env.example .env
fi

if ! grep -Eq '^DASHBOARD_PASSWORD=.+$' "$ROOT/.env"; then
  printf "\nChoose your private dashboard password (minimum 12 characters).\n"
  while true; do
    IFS= read -r -s -p "New dashboard password: " DASHBOARD_PASSWORD
    printf "\n"
    IFS= read -r -s -p "Confirm dashboard password: " DASHBOARD_PASSWORD_CONFIRM
    printf "\n"
    if [ "${#DASHBOARD_PASSWORD}" -lt 12 ]; then
      printf "Password must contain at least 12 characters.\n" >&2
    elif [ "$DASHBOARD_PASSWORD" != "$DASHBOARD_PASSWORD_CONFIRM" ]; then
      printf "Passwords do not match. Try again.\n" >&2
    else
      break
    fi
  done
  printf '%s' "$DASHBOARD_PASSWORD" | python -c 'from pathlib import Path; import shlex,sys; p=Path(".env"); lines=[line for line in p.read_text().splitlines() if not line.startswith(("DASHBOARD_ACCESS_TOKEN=", "DASHBOARD_PASSWORD="))]; lines.append("DASHBOARD_PASSWORD="+shlex.quote(sys.stdin.read())); p.write_text("\n".join(lines)+"\n")'
  unset DASHBOARD_PASSWORD DASHBOARD_PASSWORD_CONFIRM
else
  python -c 'from pathlib import Path; p=Path(".env"); lines=[line for line in p.read_text().splitlines() if not line.startswith("DASHBOARD_ACCESS_TOKEN=")]; p.write_text("\n".join(lines)+"\n")'
fi
chmod 600 "$ROOT/.env"
printf "Private dashboard password configured. The legacy dashboard token is disabled.\n"

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
