#!/usr/bin/env bash
set -euo pipefail

docker run --rm \
  -v /etc/letsencrypt:/etc/letsencrypt \
  -v /var/lib/letsencrypt:/var/lib/letsencrypt \
  -v /opt/caseledger-copy/acme:/var/www/acme \
  certbot/certbot:latest renew \
  --cert-name caseledger-copy-ip \
  --non-interactive \
  --no-random-sleep-on-renew

nginx -t
nginx -s reload
