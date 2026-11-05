#!/bin/bash
# Runs once as root, then hands over to the unprivileged `fantoma` user.
#
# The only root work is ownership: a profiles volume created by an image
# older than 0.13 belongs to root, and the log directory is baked in as root.
# After that every process (supervisord, Xvfb, x11vnc, websockify, the
# server and the browsers) runs as uid 1000.
set -eu

FANTOMA_USER=fantoma
FANTOMA_HOME=/home/$FANTOMA_USER
DATA_DIR=${FANTOMA_PROFILE_BASE:-$FANTOMA_HOME/.local/share/fantoma}
UID_WANT=$(id -u $FANTOMA_USER)

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR" /var/log/supervisor /tmp/.X11-unix
    chmod 1777 /tmp/.X11-unix
    # Only re-own when needed: a chown -R over a big volume on every start
    # would be slow. The top directory's owner is the tell.
    if [ "$(stat -c %u "$DATA_DIR")" != "$UID_WANT" ]; then
        echo "entrypoint: re-owning $DATA_DIR for $FANTOMA_USER (one-off)"
        chown -R $FANTOMA_USER:$FANTOMA_USER "$DATA_DIR"
    fi
    chown -R $FANTOMA_USER:$FANTOMA_USER /var/log/supervisor
    exec setpriv --reuid=$FANTOMA_USER --regid=$FANTOMA_USER --init-groups \
        env HOME=$FANTOMA_HOME "$@"
fi

exec "$@"
