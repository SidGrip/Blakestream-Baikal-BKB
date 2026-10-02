#!/bin/sh
# Capture sshd login attempts to the persistent SD security log so they survive
# reboot (journald here is RAM-only/volatile). Incremental via journal cursor
# (systemd 229 has --after-cursor/--show-cursor, not --cursor-file). Appends only
# when there are new auth events -> no recurring SD wear on a quiet box. Capped.
SEC=/opt/scripta/var/security.log
STATE=/run/blakestream/sshd-journal.cursor
TMP=/run/blakestream/sshd-auth.$$
mkdir -p /run/blakestream 2>/dev/null

if [ -s "$STATE" ]; then
  RAW=$(journalctl _COMM=sshd --after-cursor="$(cat "$STATE")" --show-cursor -o short-iso --no-pager 2>/dev/null)
else
  RAW=$(journalctl _COMM=sshd --show-cursor -o short-iso --no-pager 2>/dev/null)
fi

NEWCUR=$(printf '%s\n' "$RAW" | sed -n 's/^-- cursor: //p' | tail -1)
[ -n "$NEWCUR" ] && printf '%s' "$NEWCUR" > "$STATE"

printf '%s\n' "$RAW" \
  | grep -aE 'Failed password|Accepted password|Invalid user|authentication failure|Connection (reset|closed) by' \
  > "$TMP" 2>/dev/null

if [ -s "$TMP" ]; then
  # Coordinate with web login redaction, firmware audit and retention pruning.
  [ ! -L "$SEC.lock" ] && [ ! -L "$SEC" ] || { rm -f "$TMP"; exit 1; }
  exec 9>>"$SEC.lock" || { rm -f "$TMP"; exit 1; }
  chown root:www-data "$SEC.lock"
  chmod 0660 "$SEC.lock"
  flock -x 9 || { rm -f "$TMP"; exit 1; }
  if [ "$(wc -c < "$SEC" 2>/dev/null || echo 0)" -gt 262144 ]; then
    # Discard the partial first line so trimming cannot retain a password fragment.
    tail -c 131072 "$SEC" | sed '1d' > "$SEC.tmp" && mv "$SEC.tmp" "$SEC"
  fi
  sed 's/^/ssh /' "$TMP" >> "$SEC"
  chown root:www-data "$SEC" 2>/dev/null
  chmod 0660 "$SEC" 2>/dev/null
  flock -u 9
fi
rm -f "$TMP"
