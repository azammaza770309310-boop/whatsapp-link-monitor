# Deployment

## Docker (Recommended)

### Quick Start

```bash
git clone https://github.com/azammaza770309310-boop/whatsapp-link-monitor.git
cd whatsapp-link-monitor
cp config/.env.example .env
# Edit .env with your credentials
docker-compose up -d
```

### Logs

```bash
docker-compose logs -f bot
```

### Stop

```bash
docker-compose down
```

### Rebuild after code changes

```bash
docker-compose up -d --build
```

## Render.com

### Prerequisites
- GitHub account
- Repository forked/cloned
- Bot token from @BotFather
- Channel ID where bot is admin

### Steps

1. Go to https://render.com and sign up with GitHub
2. Click **New +** → **Web Service**
3. Connect your repository
4. Configure:
   - **Name**: `whatsapp-link-monitor`
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python -m src.main`
   - **Instance Type**: `Free` or `Starter`
5. Add Environment Variables:
   - `BOT_TOKEN` - Your bot token
   - `ADMIN_IDS` - Comma-separated admin Telegram IDs
   - `CHANNEL_ID` - Destination channel ID
6. Click **Create Web Service**
7. Wait for deployment (3-5 minutes)
8. Set up UptimeRobot to keep the service alive (for free tier)

### UptimeRobot Setup (Free tier)

1. Go to https://uptimerobot.com
2. Create a free account
3. Add a new monitor:
   - **Type**: HTTP(s)
   - **URL**: `https://your-app.onrender.com/health`
   - **Interval**: 5 minutes
4. Save

## Linux VPS

### Prerequisites
- Python 3.11+
- pip
- systemd (for service management)

### Install

```bash
sudo apt update
sudo apt install python3.11 python3.11-venv
git clone https://github.com/azammaza770309310-boop/whatsapp-link-monitor.git
cd whatsapp-link-monitor
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config/.env.example .env
# Edit .env
```

### Systemd Service

Create `/etc/systemd/system/whatsapp-bot.service`:

```ini
[Unit]
Description=WhatsApp Link Monitor Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/whatsapp-link-monitor
EnvironmentFile=/home/ubuntu/whatsapp-link-monitor/.env
ExecStart=/home/ubuntu/whatsapp-link-monitor/venv/bin/python -m src.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable whatsapp-bot
sudo systemctl start whatsapp-bot
sudo systemctl status whatsapp-bot
```

Logs:

```bash
sudo journalctl -u whatsapp-bot -f
```

### Nginx Reverse Proxy (Optional)

For HTTPS and domain:

```nginx
server {
    listen 80;
    server_name bot.example.com;

    location / {
        proxy_pass http://127.0.0.1:10000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Use certbot for SSL:

```bash
sudo certbot --nginx -d bot.example.com
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BOT_TOKEN` | Yes | - | Telegram bot token |
| `ADMIN_IDS` | Yes | - | Comma-separated admin user IDs |
| `CHANNEL_ID` | Yes | - | Destination channel ID |
| `DATABASE_PATH` | No | `data/links.db` | SQLite path |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `LOG_FORMAT` | No | `json` | `json` or `text` |
| `VALIDATION_INTERVAL_HOURS` | No | `24` | Link validation interval |
| `BACKUP_INTERVAL_HOURS` | No | `24` | Backup interval |
| `BACKUP_RETENTION_DAYS` | No | `30` | Backup retention |
| `RATE_LIMIT_PER_MINUTE` | No | `10` | Per-user rate limit |
| `HTTP_PORT` | No | `10000` | HTTP server port |
| `PLUGINS` | No | - | Comma-separated plugin paths |

## Health Check

The bot exposes an HTTP endpoint at `/health`:

```bash
curl https://your-app.onrender.com/health
```

Response:
```json
{
  "status": "ok",
  "uptime_seconds": 3600,
  "link_count": 150,
  "user_count": 25,
  "timestamp": "2026-01-01T12:00:00"
}
```

## Backups

Backups are automatically created every 24 hours (configurable) and stored in `data/backups/`. Old backups are deleted based on `BACKUP_RETENTION_DAYS`.

Manual backup:
```bash
python scripts/backup.py
```

## Troubleshooting

### Bot not responding
1. Check logs: `docker-compose logs -f bot`
2. Verify `BOT_TOKEN` is correct
3. Ensure bot is admin in the channel
4. Check `ADMIN_IDS` includes your Telegram ID

### Database errors
1. Check file permissions on `data/` directory
2. Verify disk space
3. Run migrations manually: `python scripts/migrate.py`

### Migration errors
1. Check `migrations/` directory exists
2. Verify migration files have `upgrade(db)` function
3. Check `_migrations` table in database

### Memory issues
1. Reduce `HISTORY_MAX_PER_CHAT`
2. Increase container memory in docker-compose
3. Check for memory leaks in plugins
