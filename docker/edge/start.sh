#!/bin/sh
set -eu
case "${AGENTHUB_PUBLIC_HOST:-}" in
    ''|*[!a-z0-9.-]*)
        printf '%s\n' 'Set AGENTHUB_PUBLIC_HOST to a lowercase DNS hostname.' >&2
        exit 1
        ;;
esac
envsubst '$AGENTHUB_PUBLIC_HOST' </etc/nginx/agenthub.conf.template >/tmp/nginx.conf
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
