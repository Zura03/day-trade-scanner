#!/usr/bin/env bash
# Run the live monitors automatically every market day (macOS launchd).
#
#   ./scripts/autostart.sh install          US + India monitors and the dashboard
#   ./scripts/autostart.sh uninstall
#   ./scripts/autostart.sh status
#   ./scripts/autostart.sh run-now [us|india]   start today's job immediately
#
# Start times are local (Mac) time, Mon-Fri:
#   US     START_TIME=18:00        1-2 h before the US open in IST
#   India  START_TIME_INDIA=09:00  15 min before the NSE open
#
# Each monitor runs in --session mode: it exits at once on that market's
# holidays, follows the market's own timezone (US daylight-saving changes need
# no edits), and stops 5 minutes after the close. caffeinate keeps the Mac from
# idle-sleeping while it runs. If the Mac was asleep at the start time, launchd
# runs the job as soon as it wakes.
#
# install also keeps the dashboard running in the background (starts at login,
# restarts if it crashes) so you can open it from your phone any time.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
START_TIME="${START_TIME:-18:00}"
START_TIME_INDIA="${START_TIME_INDIA:-09:00}"

US_LABEL="com.daytrader.monitor"
IN_LABEL="com.daytrader.monitor.india"
DASH_LABEL="com.daytrader.dashboard"
US_LOG="$APP_DIR/data/monitor.log"
IN_LOG="$APP_DIR/data/india/monitor.log"
DASH_LOG="$APP_DIR/data/dashboard.log"

load() {  # label plist
  launchctl bootout "$DOMAIN/$1" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do  # wait for a running job to finish exiting
    launchctl print "$DOMAIN/$1" >/dev/null 2>&1 || break
    sleep 0.5
  done
  launchctl bootstrap "$DOMAIN" "$2"
}

write_monitor() {  # label start_time log market
  local label=$1 hour=$((10#${2%%:*})) minute=$((10#${2##*:})) log=$3 market=$4 days=""
  for wd in 1 2 3 4 5; do
    days+="    <dict><key>Weekday</key><integer>$wd</integer><key>Hour</key><integer>$hour</integer><key>Minute</key><integer>$minute</integer></dict>
"
  done
  mkdir -p "$(dirname "$log")"
  cat > "$AGENTS/$label.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-i</string>
    <string>$APP_DIR/run.sh</string>
    <string>--market</string>
    <string>$market</string>
    <string>monitor</string>
    <string>--session</string>
  </array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>StartCalendarInterval</key>
  <array>
$days  </array>
  <key>StandardOutPath</key><string>$log</string>
  <key>StandardErrorPath</key><string>$log</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
</dict>
</plist>
EOF
  load "$label" "$AGENTS/$label.plist"
}

install() {
  mkdir -p "$AGENTS" "$APP_DIR/data"
  write_monitor "$US_LABEL" "$START_TIME" "$US_LOG" us
  echo "Installed: US monitor starts Mon-Fri at $START_TIME local time. Log: $US_LOG"
  write_monitor "$IN_LABEL" "$START_TIME_INDIA" "$IN_LOG" india
  echo "Installed: India monitor starts Mon-Fri at $START_TIME_INDIA local time. Log: $IN_LOG"

  cat > "$AGENTS/$DASH_LABEL.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$DASH_LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$APP_DIR/run.sh</string><string>dashboard</string></array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$DASH_LOG</string>
  <key>StandardErrorPath</key><string>$DASH_LOG</string>
</dict>
</plist>
EOF
  load "$DASH_LABEL" "$AGENTS/$DASH_LABEL.plist"
  echo "Installed: dashboard runs in the background. Log: $DASH_LOG"
}

uninstall() {
  for l in "$US_LABEL" "$IN_LABEL" "$DASH_LABEL"; do
    launchctl bootout "$DOMAIN/$l" 2>/dev/null || true
    rm -f "$AGENTS/$l.plist"
  done
  echo "Removed the US and India monitors and the dashboard job"
}

monitor_status() {  # name label log
  local plist="$AGENTS/$2.plist"
  if launchctl print "$DOMAIN/$2" >/dev/null 2>&1; then
    local state
    state=$(launchctl print "$DOMAIN/$2" | grep -m1 "state =" | sed 's/^[[:space:]]*//')
    echo "$1 monitor: $state, Mon-Fri $(plutil -extract StartCalendarInterval.0.Hour raw "$plist"):$(printf %02d "$(plutil -extract StartCalendarInterval.0.Minute raw "$plist")") local"
  else
    echo "$1 monitor: not installed"
  fi
  [ -f "$3" ] && tail -n 3 "$3" | sed 's/^/  | /'
  return 0
}

status() {
  monitor_status US "$US_LABEL" "$US_LOG"
  monitor_status India "$IN_LABEL" "$IN_LOG"
  if launchctl print "$DOMAIN/$DASH_LABEL" >/dev/null 2>&1; then
    echo "Dashboard: $(launchctl print "$DOMAIN/$DASH_LABEL" | grep -m1 'state =' | sed 's/^[[:space:]]*//')"
    [ -f "$DASH_LOG" ] && grep -E "^Phone|^Local" "$DASH_LOG" | tail -n 2 | sed 's/^/  /'
  else
    echo "Dashboard: not installed"
  fi
}

case "${1:-}" in
  install) install ;;
  uninstall) uninstall ;;
  status) status ;;
  run-now)
    case "${2:-us}" in
      us) launchctl kickstart -k "$DOMAIN/$US_LABEL" && echo "Started today's US job. Log: $US_LOG" ;;
      india) launchctl kickstart -k "$DOMAIN/$IN_LABEL" && echo "Started today's India job. Log: $IN_LOG" ;;
      *) echo "run-now takes us or india"; exit 1 ;;
    esac ;;
  *) sed -n '2,21p' "$0"; exit 1 ;;
esac
