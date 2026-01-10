# Docker Configs

This repository contains docker-compose configurations for various self-hosted services.

## Services

- **arrs** - Arr stack (Sonarr, Radarr, Prowlarr, Jellyseerr, Bazarr) for media management
- **authelia** - Authentication server
- **esphome** - Home automation platform for ESP devices
- **guacamole** - Remote desktop gateway
- **homepage** - Dashboard for all services
- **immich** - Self-hosted photo management solution
- **jellyfin** - Media server
- **matomo** - Web analytics platform
- **metube** - YouTube downloader
- **owncloud** - File sync and share
- **portainer** - Docker management UI
- **qbittorrent** - BitTorrent client
- **scrutiny** - Hard drive monitoring
- **syncthing** - Continuous file synchronization
- **transmission** - BitTorrent client
- **watchtower** - Automatic container updates
- **wordpress** - WordPress instance

## Setup

1. Copy `.env.example` to `.env` in each service directory that has one
2. Edit `.env` files and replace `CHANGE_ME` values with your actual configuration
3. Start services with:
   ```bash
   cd <service-directory>
   docker-compose up -d
   ```

## Management

### Start all services
```bash
for dir in */; do
  if [ -f "$dir/docker-compose.yml" ] || [ -f "$dir/docker-compose.yaml" ]; then
    (cd "$dir" && docker-compose up -d)
  fi
done
```

### Stop all services
```bash
for dir in */; do
  if [ -f "$dir/docker-compose.yml" ] || [ -f "$dir/docker-compose.yaml" ]; then
    (cd "$dir" && docker-compose down)
  fi
done
```

### Update all services
Watchtower is configured to automatically update containers every Monday at 10:00 AM.

## Notes

- Check all volumes paths
- Check all ports
- Check all .env files