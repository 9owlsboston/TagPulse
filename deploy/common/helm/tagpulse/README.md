# TagPulse Helm chart

Sprint 22 B4 deliverable. Provider-agnostic chart deploying:

- `tagpulse-api` Deployment + Service + HPA + PDB (HTTP only;
  `WORKERS_INLINE=false`)
- `tagpulse-worker` Deployment (MQTT + inventory/dwell/alert/analytics/
  webhook workers; `WORKERS_INLINE=true`; HTTP exposed for liveness only,
  no Service)
- `tagpulse-migrations` Job (Helm `pre-install,pre-upgrade` hook running
  `alembic upgrade head` to completion before the api/worker rollouts
  proceed; same image as Sprint 22 B2)
- Optional `ServiceMonitor` for Prometheus Operator clusters
  (Sprint 22 E1)

This chart is the **canonical deployment spec**. Cloud-specific IaC
(`deploy/azure/bicep/`, `deploy/aws/terraform/`,
`deploy/gcp/terraform/`) renders the same shape natively when k8s
isn't the v1 target — Container Apps for Azure, ECS Fargate / EKS for
AWS, Cloud Run / GKE for GCP. See ADR-016 for the layered topology.

## Quick install

```sh
# 1. Create the secret your overlay references
kubectl create secret generic tagpulse-secrets \
  --from-literal=database-url='postgresql+asyncpg://…' \
  --from-literal=jwt-secret="$(openssl rand -hex 32)" \
  --from-literal=mqtt-username='tagpulse' \
  --from-literal=mqtt-password='…'

# 2. Install with overrides
helm install tagpulse ./deploy/common/helm/tagpulse \
  --set image.tag=$(git rev-parse HEAD) \
  --set environment=production \
  --set config.corsOrigins='https://app.example.com' \
  --set mqtt.broker.host='emqx.example.com'
```

## kind (local) sanity check — Sprint 22 acceptance §5

```sh
kind create cluster --name tagpulse-helm-check
helm install tagpulse ./deploy/common/helm/tagpulse \
  --set environment=dev \
  --set config.strictMigrationCheck=false \
  --set migrations.enabled=false  # local kind has no Postgres
helm template tagpulse ./deploy/common/helm/tagpulse | kubectl apply --dry-run=client -f -
```

## Files

| Path                                    | Purpose |
| --------------------------------------- | ------- |
| `Chart.yaml`                            | Chart metadata |
| `values.yaml`                           | Default values; overridden by cloud overlays |
| `templates/_helpers.tpl`                | Naming helpers + shared env-var block |
| `templates/api-deployment.yaml`         | API Deployment (`WORKERS_INLINE=false`) |
| `templates/api-service.yaml`            | API Service (ClusterIP) |
| `templates/api-hpa.yaml`                | API HorizontalPodAutoscaler (CPU-based) |
| `templates/worker-deployment.yaml`      | Worker Deployment (`WORKERS_INLINE=true`) |
| `templates/migrations-job.yaml`         | One-shot migrations Job (Helm pre-rollout hook) |
| `templates/serviceaccount.yaml`         | ServiceAccount (cloud overlays annotate for IRSA / Workload Identity) |
| `templates/poddisruptionbudget.yaml`    | PDB for api (and worker once it scales) |
| `templates/servicemonitor.yaml`         | Prometheus Operator scrape config (optional) |
