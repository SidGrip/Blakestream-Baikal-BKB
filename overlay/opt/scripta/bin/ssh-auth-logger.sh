#!/bin/sh
# Capture sshd login attempts to a sealed persistent log so they survive
# reboot (journald here is RAM-only/volatile). Incremental via journal cursor
# (systemd 229 has --after-cursor/--show-cursor, not --cursor-file). Appends only
# when there are new auth events -> no recurring SD wear on a quiet box. Capped.
SEC=/var/log/blakestream-security/ssh.log
STATE=/run/blakestream-security/sshd-journal.cursor
RUNDIR=/run/blakestream-security
LOGDIR=/var/log/blakestream-security

/bin/sh /opt/scripta/bin/prepare-runtime-dir.sh || exit 1
[ -d "$RUNDIR" ] && [ ! -L "$RUNDIR" ] || exit 1
[ -d "$LOGDIR" ] && [ ! -L "$LOGDIR" ] || exit 1
[ "$(stat -c '%u:%g:%a' "$RUNDIR" 2>/dev/null)" = '0:33:750' ] || exit 1
[ "$(stat -c '%u:%g:%a' "$LOGDIR" 2>/dev/null)" = '0:33:750' ] || exit 1

TMP=$(mktemp "$RUNDIR/.sshd-auth.XXXXXX") || exit 1
trap 'rm -f -- "$TMP" "$CURTMP" "$SECTMP"' EXIT HUP INT TERM
CURTMP=
SECTMP=

if [ -s "$STATE" ]; then
  RAW=$(journalctl _COMM=sshd --after-cursor="$(cat "$STATE")" --show-cursor -o short-iso --no-pager 2>/dev/null)
else
  RAW=$(journalctl _COMM=sshd --show-cursor -o short-iso --no-pager 2>/dev/null)
fi

NEWCUR=$(printf '%s\n' "$RAW" | sed -n 's/^-- cursor: //p' | tail -1)
if [ -n "$NEWCUR" ]; then
  CURTMP=$(mktemp "$RUNDIR/.sshd-cursor.XXXXXX") || exit 1
  printf '%s' "$NEWCUR" > "$CURTMP" || exit 1
  chown root:root "$CURTMP"
  chmod 0600 "$CURTMP"
  mv -f -- "$CURTMP" "$STATE"
  CURTMP=
fi

printf '%s\n' "$RAW" \
  | grep -aE 'Failed password|Accepted password|Invalid user|authentication failure|Connection (reset|closed) by' \
  > "$TMP" 2>/dev/null

if [ -s "$TMP" ]; then
  # Coordinate with web login redaction, firmware audit and retention pruning.
  [ ! -L "$SEC.lock" ] && [ ! -L "$SEC" ] || exit 1
  [ ! -e "$SEC" ] || { [ -f "$SEC" ] && [ "$(stat -c '%u' "$SEC")" = 0 ]; } || exit 1
  exec 9>>"$SEC.lock" || exit 1
  chown root:root "$SEC.lock"
  chmod 0600 "$SEC.lock"
  flock -x 9 || exit 1
  if [ "$(wc -c < "$SEC" 2>/dev/null || echo 0)" -gt 262144 ]; then
    # Discard the partial first line so trimming cannot retain a password fragment.
    SECTMP=$(mktemp "$LOGDIR/.ssh-log.XXXXXX") || exit 1
    tail -c 131072 "$SEC" | sed '1d' > "$SECTMP" || exit 1
    chown root:www-data "$SECTMP"
    chmod 0640 "$SECTMP"
    mv -f -- "$SECTMP" "$SEC"
    SECTMP=
  fi
  sed 's/^/ssh /' "$TMP" >> "$SEC"
  chown root:www-data "$SEC" 2>/dev/null
  chmod 0640 "$SEC" 2>/dev/null
  flock -u 9
fi
rm -f -- "$TMP"
TMP=
trap - EXIT HUP INT TERM
