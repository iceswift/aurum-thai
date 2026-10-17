# Aurum Thai API

Backend API and price scraper for Thai Gold View. The service retrieves Gold Traders Association data during market hours, maintains one revisioned public snapshot, and sends Firebase Cloud Messaging (FCM) events for real price-data changes.

## Production architecture

```text
/srv/thai-gold-view/
├── app/       # Backend source and Docker Compose definition
├── data/      # Persistent revision, snapshot, and notification state
├── secrets/   # Firebase service-account JSON; never commit this directory
├── web/       # Separate Flutter PWA releases and Nginx configuration
└── backups/   # Deployment backups
```

The `gold-api` container binds only to `127.0.0.1:8000`. Cloudflare Tunnel publishes `https://api.thaigoldview.com`.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Process liveness and Firebase/CORS diagnostics. |
| `GET /ready` | Readiness, data age, staleness, and market status. |
| `GET /api/board` | One consistent revisioned snapshot for the app. |
| `GET /api/latest`, `/api/history`, `/api/gold` | Current and historical gold data. |
| `GET /api/percent_jewelry`, `/api/shops` | Jewelry and shop data. |
| `POST /api/push/subscribe` | Subscribes a Web FCM token to the fixed topic. |

### Board snapshots and ETags

`/api/board` returns one logical snapshot with `revision`, ISO-8601 `updated_at`, `market_status`, `latest`, `history`, `jewelry`, and `shops`. It sets `ETag: W/\"gold-board-<revision>\"`; clients can send `If-None-Match` and receive `304 Not Modified`.

Snapshot state is atomically persisted in `/srv/thai-gold-view/data`, so a container restart does not reset its revision.

## FCM behavior

The topic is `gold_price_updates`. A price-data snapshot change sends a data-only payload:

```json
{
  "type": "gold_snapshot_updated",
  "revision": "541",
  "updated_at": "2026-10-17T10:05:00+07:00",
  "user_visible": "true"
}
```

The Flutter client fetches `/api/board` as the source of truth. A market-status-only change, including the change at midnight when the day changes, is persisted but does not send FCM. This prevents spurious price notifications.

## Configuration

Copy `.env.example` to `.env` on the server:

```dotenv
APP_HOST=0.0.0.0
APP_PORT=8000
CORS_ALLOWED_ORIGINS=https://thaigoldview.com,https://www.thaigoldview.com
FIREBASE_CREDENTIALS_PATH=/run/secrets/firebase-service-account.json
NOTIFICATION_STATE_FILE=/app/data/notification_state.json
SNAPSHOT_STATE_FILE=/app/data/gold_snapshot_state.json
FCM_TOPIC_NAME=gold_price_updates
STALE_AFTER_MINUTES=10
```

`firebase-service-account.json` belongs in `/srv/thai-gold-view/secrets/` with restricted permissions. Never commit it.

## Local development

```bash
git clone https://github.com/iceswift/aurum-thai.git
cd aurum-thai
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python main.py
```

For Docker development, create `.env`, then run `docker compose up --build`.

## Home server deployment

Build while the currently running API continues serving requests:

```bash
cd /srv/thai-gold-view/app
docker compose build gold-api
docker compose up -d --no-deps gold-api
docker compose ps
docker compose logs --tail=100 gold-api
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

Only `gold-api` is recreated. The separate `thai-gold-web` container and Cloudflare Tunnel do not need a restart for an API-only release.

## Rollback

Save replaced production source under `/srv/thai-gold-view/backups/<release>/`. Restore it and recreate only the API container:

```bash
cp /srv/thai-gold-view/backups/<release>/main.py /srv/thai-gold-view/app/main.py
cd /srv/thai-gold-view/app
docker compose build gold-api
docker compose up -d --no-deps gold-api
```
