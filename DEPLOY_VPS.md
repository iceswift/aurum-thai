# Deploy on VPS

This project is ready to run on a Linux VPS with Docker and Docker Compose.

## 1. Prepare the server

Install Docker and Docker Compose plugin on the VPS.

Example for Ubuntu:

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

## 2. Copy project to the VPS

Put the project in a stable path such as:

```bash
/opt/gold-api
```

## 3. Create runtime folders

Inside the project folder:

```bash
mkdir -p secrets data
```

## 4. Add Firebase service account

Copy your Firebase Admin SDK JSON file to:

```bash
./secrets/firebase-service-account.json
```

Do not commit this file to Git.

## 5. Create the runtime env file

Copy the example file:

```bash
cp .env.example .env
```

Then edit `.env` with the real values:

```env
APP_HOST=0.0.0.0
APP_PORT=8000
CORS_ALLOWED_ORIGINS=https://app.thaigoldview.app,https://www.thaigoldview.app
FIREBASE_CREDENTIALS_PATH=/run/secrets/firebase-service-account.json
NOTIFICATION_STATE_FILE=/app/data/notification_state.json
FCM_TOPIC_NAME=gold_price_updates
STALE_AFTER_MINUTES=10
```

If your frontend calls the API from a different domain such as `https://api.thaigoldview.app`, keep `CORS_ALLOWED_ORIGINS` limited to the app/web origins that make browser requests.

## 6. Build and start

```bash
docker compose up -d --build
```

## 7. Check health

```bash
docker compose ps
docker compose logs -f gold-api
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

You want `/health` to show:

- `firebase_enabled: true`
- `firebase_status: ready`

`/ready` may return `not_ready` briefly until the first scrape completes.

## 8. Connect your domain

Recommended setup:

- Cloudflare DNS record for `api.yourdomain.com`
- Reverse proxy on the VPS forwards public HTTPS traffic to `127.0.0.1:8000`
- Keep the container bound to localhost only

That is why `docker-compose.yml` exposes:

```text
127.0.0.1:8000:8000
```

This keeps the API off the public internet except through your proxy.

### Nginx reverse proxy example

This repository includes:

```text
deploy/nginx/api.thaigoldview.app.conf
```

Example install on Ubuntu:

```bash
sudo apt install -y nginx
sudo mkdir -p /etc/ssl/cf
sudo cp deploy/nginx/api.thaigoldview.app.conf /etc/nginx/sites-available/api.thaigoldview.app.conf
sudo ln -s /etc/nginx/sites-available/api.thaigoldview.app.conf /etc/nginx/sites-enabled/api.thaigoldview.app.conf
sudo nginx -t
sudo systemctl restart nginx
```

### Cloudflare Origin Certificate

Create an Origin Certificate in Cloudflare for:

```text
api.thaigoldview.app
```

Then save the files on the VPS as:

```bash
sudo mkdir -p /etc/ssl/cf
sudo nano /etc/ssl/cf/api.thaigoldview.app.pem
sudo nano /etc/ssl/cf/api.thaigoldview.app.key
sudo chmod 600 /etc/ssl/cf/api.thaigoldview.app.key
```

Set Cloudflare SSL/TLS mode to:

```text
Full (strict)
```

If you use a different API domain, replace:

```text
api.thaigoldview.app
```

with your real API hostname before enabling the site.

### Optional hardening

After the site is working, you can restrict the VPS firewall or Nginx allowlist to Cloudflare IP ranges only. This is optional for the first deployment, but recommended later.

## 9. Preserve notification state

The file below stores the last notified price:

```text
./data/notification_state.json
```

Keep the `data` folder when moving or rebuilding the server so notifications do not forget their last state.

## 10. Update the app later

When you change code:

```bash
docker compose up -d --build
```

When you only need to restart:

```bash
docker compose restart gold-api
```
