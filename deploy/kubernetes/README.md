# Aurum Thai Kubernetes migration

The first milestone runs the existing FastAPI service in an isolated staging
namespace. It uses a single replica, a persistent snapshot volume, separate
health probes, and a ClusterIP service. It does not mount Firebase credentials
or expose an ingress. The existing application still scrapes the live source
on startup and follows its normal scheduler afterwards. Keep staging runs
short while the production scraper is active.

## Run the staging baseline

Use a local Kubernetes cluster with a default dynamic StorageClass. The
manifest expects a locally available image on the node; load it into all
eligible nodes for a multi-node cluster (for example, with `kind load
docker-image aurum-thai:staging --name <cluster>` for kind).

From the repository root, after verifying that the current context is your
local cluster:

```sh
kubectl config current-context
docker build -t aurum-thai:staging .
kubectl kustomize deploy/kubernetes
kubectl apply -k deploy/kubernetes
kubectl -n aurum-staging rollout status deployment/aurum-api --timeout=180s
kubectl -n aurum-staging port-forward service/aurum-api 18000:8000
```

From another terminal:

```sh
curl -fsS http://localhost:18000/health
curl -fsS http://localhost:18000/ready
curl -i http://localhost:18000/api/board
```

Replay the returned ETag in `If-None-Match` to check for a bodyless 304 while
the revision is unchanged. Restart the staging deployment and verify that the
revision is restored rather than reset. Stop staging with
`kubectl -n aurum-staging scale deployment/aurum-api --replicas=0` to keep its
PVC without running the extra scraper.

If the pod stays Pending, inspect the PVC and StorageClass. If the pod runs but
does not become ready, inspect its logs and verify it can reach the source
website; the readiness probe requires a populated snapshot.

## Staging verification (2026-11-13)

A fresh namespace and empty PVC on `kind-aurum-staging` reached readiness
without seeded data. The scraper found 20 live price rows and persisted
revision 1. The Service returned HTTP 200 for readiness and the board, and
HTTP 304 with an empty body for a matching ETag.

After a rollout restart, the replacement pod served revision 1 from the PVC
even though the upstream navigation failed on that startup. Readiness and
conditional GET checks passed again. Firebase was disabled in staging.

## Next milestones

### Split runtime development

`RUN_EMBEDDED_SCRAPER=false` starts the HTTP API without starting Chromium or
the scheduler. `python -m scrape_once` runs a single market-hours check and
scrape, exits successfully when the market is closed, and returns a nonzero
exit code on scrape failure. Existing deployments keep the embedded scheduler
by default. The file-backed API loads the snapshot at startup; keep the M1
manifest in embedded mode. Use PostgreSQL for separate API and worker processes.

For split deployments, set `SNAPSHOT_DATABASE_URL` on both API and worker
and set `RUN_EMBEDDED_SCRAPER=false` on the API. PostgreSQL initializes a
single snapshot table and serializes commits; price endpoints read the
current record on each request. Use an isolated database for staging.

`worker-cronjob.yaml` is opt-in and suspended by default. It expects the
`aurum-snapshot-db` Kubernetes Secret with key `url`. The worker enforces
Bangkok market hours even when the CronJob triggers outside the Saturday or
weekday cutoff. Keep it suspended until the split API and database are ready.

1. **Split the runtime.** Extract a one-shot scraper command that checks
   Bangkok market hours, and disable scraping in the API process. Move the
   snapshot into a shared transactional store (proposed: PostgreSQL). Keep
   the snapshot and revision in one transaction and retain the HTTP contract.
   Prove that two API replicas agree on revision and ETag and that repeated
   jobs cannot publish the same snapshot twice.
2. **Schedule and observe.** Start with one CronJob using
   `timeZone: Asia/Bangkok` and `concurrencyPolicy: Forbid`. Keep the market
   check in the worker and use an idempotent commit because scheduling is
   approximate. Record scrape duration/outcome and API latency/errors.
   For short-lived jobs, export durable job metrics rather than relying on
   Prometheus to discover each pod before it exits.
3. **Automate staging.** Publish immutable images, have Argo CD track the
   staging manifests, and prove rollback through a Git revert. Add TLS
   ingress and secret delivery for the chosen environment. Enable API HPA
   after the shared store, resource requests, and metrics are verified.
4. **Migrate production deliberately.** Compare board responses in staging,
   retain the live hostname and Docker Compose rollback path, and ensure
   there is only one active production scraper and notification publisher
   during cutover.

References: [Kubernetes CronJobs](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/)
and [Argo CD automated sync](https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/).
