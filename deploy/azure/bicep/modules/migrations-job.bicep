// Container Apps Job — runs `alembic upgrade head` to completion before api/worker
// rollout. Triggered via `az containerapp job start` (manual) by the deploy workflow,
// or by `azd hooks` postdeploy. Uses the same UAMI as the apps for ACR + KV access.

@description('Job name.')
param jobName string

@description('Azure region.')
param location string

@description('Container Apps environment resource ID.')
param environmentId string

@description('User-assigned managed identity resource ID.')
param userAssignedIdentityId string

@description('Migrations container image, e.g. myacr.azurecr.io/tagpulse-migrations:v1.2.3.')
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
      triggerType: 'Manual'
      replicaTimeout: 600
      replicaRetryLimit: 2
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
      ]
    }
    template: {
      containers: [
        {
          name: 'migrations'
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          command: [ 'alembic' ]
          args: [ 'upgrade', 'head' ]
          env: [
            { name: 'ENVIRONMENT', value: 'production' }
            { name: 'POSTGRES_HOST', value: postgresFqdn }
            { name: 'POSTGRES_DB', value: postgresDatabaseName }
            { name: 'POSTGRES_USER', value: postgresAdminUsername }
            { name: 'POSTGRES_PASSWORD', secretRef: 'postgres-password' }
            { name: 'DATABASE_URL', value: 'postgresql+asyncpg://${postgresAdminUsername}:$(POSTGRES_PASSWORD)@${postgresFqdn}:5432/${postgresDatabaseName}?sslmode=require' }
          ]
        }
      ]
    }
  }
}

output id string = job.id
output name string = job.name
