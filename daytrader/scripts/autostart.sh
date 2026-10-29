#!/usr/bin/env bash
# Run the live monitor automatically every US market day (macOS launchd).
#
#   ./scripts/autostart.sh install     start daily at $START_TIME local time, Mon-Fri
#   ./scripts/autostart.sh uninstall
#   ./scripts/autostart.sh status
#   ./scripts/autostart.sh run-now     start today's session job immediately
#
# install also keeps the dashboard running in the background (starts at login,
# restarts if it crashes) so you can open it from your phone any time.
#
# The job starts the monitor in --session mode: it exits at once on US market
# holidays, follows US Eastern time itself (so US daylight-saving changes need
# no edits), and stops 5 minutes after the close. caffeinate keeps the Mac
# from idle-sleeping while it runs. If the Mac was asleep at the start time,
# launchd runs the job as soon as it wakes.
set -euo pipefail

LABEL="com.daytrader.monitor"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$APP_DIR/data/monitor.log"
DASH_LABEL="com.daytrader.dashboard"
DASH_PLIST="$HOME/Library/LaunchAgents/$DASH_LABEL.plist"
DASH_LOG="$APP_DIR/data/dashboard.log"
START_TIME="${START_TIME:-18:00}"   # local time; 18:00 IST = 1-2 h before the US open
HOUR=$((10#${START_TIME%%:*}))
MINUTE=$((10#${START_TIME##*:}))
DOMAIN="gui/$(id -u)"

install() {
  mkdir -p "$HOME/Library/LaunchAgents" "$APP_DIR/data"
  local days=""
  for wd in 1 2 3 4 5; do
    days+="    <dict><key>Weekday</key><integer>$wd</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MINUTE</integer></dict>
"
  done
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-i</string>
    <string>$APP_DIR/run.sh</string>
    <string>monitor</string>
    <string>--session</string>
  </array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>StartCalendarInterval</key>
  <array>
$days  </array>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
</dict>
</plist>
EOF
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$PLIST"
  echo "Installed: monitor starts Mon-Fri at $START_TIME local time. Log: $LOG"

  cat > "$DASH_PLIST" <<EOF
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
  launchctl bootout "$DOMAIN/$DASH_LABEL" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$DASH_PLIST"
  echo "Installed: dashboard runs in the background. Log: $DASH_LOG"
}

uninstall() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  launchctl bootout "$DOMAIN/$DASH_LABEL" 2>/dev/null || true
  rm -f "$PLIST" "$DASH_PLIST"
  echo "Removed $LABEL and $DASH_LABEL"
}

status() {
  if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    launchctl print "$DOMAIN/$LABEL" | grep -E "state =|last exit code|runs =" | sed 's/^[[:space:]]*/  /'
    echo "  schedule: Mon-Fri $(plutil -extract StartCalendarInterval.0.Hour raw "$PLIST"):$(printf %02d "$(plutil -extract StartCalendarInterval.0.Minute raw "$PLIST")") local"
  else
    echo "Not installed"
  fi
  [ -f "$LOG" ] && { echo "--- last log lines ($LOG)"; tail -n 5 "$LOG"; }
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
  run-now) launchctl kickstart -k "$DOMAIN/$LABEL" && echo "Started today's job. Log: $LOG" ;;
  *) sed -n '2,13p' "$0"; exit 1 ;;
esac
