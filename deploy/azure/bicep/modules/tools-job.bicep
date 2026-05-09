// Sprint 26 B1 — Container Apps Job for ad-hoc operational scripts.
//
// Same image / identity / network as `migrations-job.bicep`, but:
//   • longer replicaTimeout (1800s) for slower scripts (smoke_setup with --full)
//   • retryLimit 0 — operational scripts are not idempotent in the sense
//     migrations are; if smoke_setup or simulate_devices fails halfway,
//     re-running silently is the wrong default
//   • default command is a no-op import smoke (`python -c "import scripts.smoke_setup"`).
//     scripts/azd-job.sh overrides command + args at start time via
//     `az containerapp job update`.
//
// Auth model: relies on the same UAMI as the api/worker/migrations targets.
// For Sprint 26 D3 (KV push), identity.bicep adds `Key Vault Secrets Officer`
// to the same UAMI so this job can write API keys to KV without the operator
// having to grant a separate role.

@description('Job name.')
param jobName string

@description('Azure region.')
param location string

@description('Container Apps environment resource ID.')
param environmentId string

@description('User-assigned managed identity resource ID.')
param userAssignedIdentityId string

@description('User-assigned managed identity client ID. Surfaced as AZURE_CLIENT_ID so DefaultAzureCredential / ManagedIdentityCredential resolve to the correct UAMI when the script (e.g. smoke_setup --key-vault-name) talks to Azure SDKs that need a token.')
param userAssignedIdentityClientId string

@description('Container image, e.g. myacr.azurecr.io/tagpulse-api:v1.2.3. Reuses the api image which now ships scripts/ (Sprint 26 A1).')
param image string

@description('ACR login server.')
param acrLoginServer string

@description('Postgres FQDN.')
param postgresFqdn string

@description('Postgres database name.')
param postgresDatabaseName string

@description('Postgres admin username.')
param postgresAdminUsername string

@description('Key Vault secret URI for the Postgres admin password.')
param postgresAdminPasswordSecretUri string

@description('In-cluster api FQDN, e.g. tpdev-api.<region>.azurecontainerapps.io. Scripts read this from $TAGPULSE_API_URL.')
param apiFqdn string

@description('Key Vault name (not URI). Set as $TAGPULSE_SMOKE_KEY_VAULT_NAME so smoke_setup.py --key-vault-name is the default code path inside the job.')
param keyVaultName string

@description('Key Vault secret URI for the Test Corp admin API key. Scripts that read $TAGPULSE_API_KEY (all simulators) will Just Work without the two-step KV retrieval dance.')
param apiKeySecretUri string = ''

@description('App-level environment string.')
@allowed(['dev','staging','production'])
param appEnvironment string = 'production'

@description('Common tags.')
param tags object = {}

resource job 'Microsoft.App/jobs@2024-10-02-preview' = {
  name: jobName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      // 30 minutes — covers smoke_setup --full + a slow simulate_devices
      // warm-up. Operational tasks longer than this should live in cron-style
      // schedule jobs (deferred to Sprint 27+ per roadmap).
      triggerType: 'Manual'
      replicaTimeout: 1800
      replicaRetryLimit: 0
      manualTriggerConfig: {
        replicaCompletionCount: 1
        parallelism: 1
      }
      registries: [
        {
          server: acrLoginServer
          identity: userAssignedIdentityId
        }
      ]
      secrets: [
        {
          name: 'postgres-password'
          identity: userAssignedIdentityId
          keyVaultUrl: postgresAdminPasswordSecretUri
        }
        // Sprint 27 D1 — API key so simulators don't need a two-step KV retrieval
        ...(empty(apiKeySecretUri) ? [] : [
          {
            name: 'tagpulse-api-key'
            identity: userAssignedIdentityId
            keyVaultUrl: apiKeySecretUri
          }
        ])
      ]
    }
    template: {
      containers: [
        {
          name: 'tools'
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          // Default = no-op import smoke. scripts/azd-job.sh overrides via
          // `az containerapp job update --command/--args` before each start.
          command: [ 'python' ]
          args: [
            '-c'
            'import scripts.smoke_setup; print("tools job ready: scripts.smoke_setup importable")'
          ]
          env: [
            { name: 'ENVIRONMENT', value: appEnvironment }
            { name: 'POSTGRES_HOST', value: postgresFqdn }
            { name: 'POSTGRES_DB', value: postgresDatabaseName }
            { name: 'POSTGRES_USER', value: postgresAdminUsername }
            { name: 'POSTGRES_PASSWORD', secretRef: 'postgres-password' }
            // asyncpg connect-string; matches migrations-job.bicep.
            { name: 'DATABASE_URL', value: 'postgresql+asyncpg://${postgresAdminUsername}:$(POSTGRES_PASSWORD)@${postgresFqdn}:5432/${postgresDatabaseName}?ssl=require' }
            // smoke_setup.py reads this for raw asyncpg connections (no driver suffix).
            { name: 'TAGPULSE_SMOKE_DB_URL', value: 'postgresql://${postgresAdminUsername}:$(POSTGRES_PASSWORD)@${postgresFqdn}:5432/${postgresDatabaseName}?sslmode=require' }
            // In-cluster api URL so scripts hitting the api don't have to leave the env.
            { name: 'TAGPULSE_API_URL', value: 'https://${apiFqdn}' }
            // Sprint 26 D3 — make KV push the default code path inside the job
            // so plaintext keys never hit stdout / Log Analytics.
            { name: 'TAGPULSE_SMOKE_KEY_VAULT_NAME', value: keyVaultName }
            // Required for DefaultAzureCredential / ManagedIdentityCredential
            // to resolve to *this* UAMI when the container has multiple
            // identities attached (or when ACA's IMDS endpoint is ambiguous
            // about which UAMI to use). Without this, the Azure SDK fails
            // with `(invalid_scope) 400, Unable to load the proper Managed
            // Identity` even though the UAMI is attached.
            { name: 'AZURE_CLIENT_ID', value: userAssignedIdentityClientId }
            // Sprint 27 D1 — API key for simulators (from KV, not plaintext)
            ...(empty(apiKeySecretUri) ? [] : [
              { name: 'TAGPULSE_API_KEY', secretRef: 'tagpulse-api-key' }
            ])
          ]
        }
      ]
    }
  }
}

output id string = job.id
output name string = job.name
