# Local monitoring lab

Build an API image from this checkout. Start an API container named
`aurum-m3-api` on Docker network `aurum-m2-test`, with
`RUN_EMBEDDED_SCRAPER=false` and `METRICS_ENABLED=true`. Start Pushgateway
on the same network under the name `aurum-m3-gateway` (image
`prom/pushgateway:v1.11.1`). Create the network if it does not exist.

```sh
docker compose -p aurum-monitor -f deploy/monitoring/compose.yaml up -d
```

Prometheus listens on localhost port 19090. Grafana listens on localhost
port 13030; open `/d/aurum-staging` for the provisioned dashboard. Anonymous
access is for this loopback-only lab. Configure workers with
`METRICS_PUSHGATEWAY=http://aurum-m3-gateway:9091` on that Docker network.

## Verification

Prometheus reported both targets UP, and queries through Grafana's
datasource proxy returned HTTP counts, latency, and worker memory series.
A live worker run in Docker scraped 22 rows, exited 0, and reported 4.3 s,
610902016 bytes container peak and 73576448 bytes Python peak RSS. The
kind worker also exposed cgroup memory peak during failed upstream
navigation attempts. These are lab measurements, not capacity estimates.

On kind, the PostgreSQL-backed split API scaled from one to three ready
pods under CPU load. The staging proxy returned HTTP 200 over HTTPS with
hostname and certificate verification. The database URL and TLS key were
provisioned in Kubernetes Secrets, outside Git.
