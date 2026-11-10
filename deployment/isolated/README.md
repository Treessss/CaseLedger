# Isolated CaseLedger-copy deployment

This deployment is dedicated to `192.3.236.57` and does not use the repository's
legacy `deploy.sh`. It keeps CaseLedger in `/opt/caseledger-copy`, uses its own
PostgreSQL and Redis containers, binds Flask to `127.0.0.1:5082`, and exposes
the application through Nginx at `https://192.3.236.57:8443`.

Private files are outside the source checkout:

- `/opt/caseledger-copy/config/app.env`: application credentials (mode 0600)
- `/opt/caseledger-copy/config/compose.env`: dedicated database password (mode 0600)
- `/opt/caseledger-copy/data/`: PostgreSQL, Redis, and application instance data
- `/opt/caseledger-copy/backups/`: daily database archives, retained for 30 days

Useful commands on the server:

```bash
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f /opt/caseledger-copy/current/deployment/isolated/compose.yaml ps
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f /opt/caseledger-copy/current/deployment/isolated/compose.yaml logs --tail=100 web worker beat
systemctl status caseledger-copy-cert.timer caseledger-copy-backup.timer
```

The certificate is an IP-address certificate with a six-day lifetime. The
`caseledger-copy-cert.timer` renews it twice daily and reloads Nginx. The
`caseledger-copy-backup.timer` backs up the dedicated database daily.

Do not run both local and server automatic tasks against the same production
Shopify store unless duplicate synchronization and independent database state
are intentional. This deployment does not disable local tasks.
