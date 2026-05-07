// Generic Container App template — used twice with different parameters for api + worker.
// `enableIngress=true` for api (external HTTP, port 8000); `false` for worker (no ingress).
// `workersInline` env var gates background worker startup in the app's lifespan.

@description('Container App name.')
param appName string

@description('Azure region.')
param location string

@description('Container Apps environment resource ID.')
param environmentId string

@description('User-assigned managed identity resource ID (for ACR pull + KV secrets).')
param userAssignedIdentityId string

@description('Container image reference, e.g. myacr.azurecr.io/tagpulse-api:v1.2.3.')
param image string

@description('ACR login server, e.g. myacr.azurecr.io.')
param acrLoginServer string

@description('Enable external HTTP ingress on port 8000.')
param enableIngress bool = false

@description('Workers-inline flag — true for the worker image, false for api.')
param workersInline bool

@description('Min/max replica count.')
param minReplicas int = 1
param maxReplicas int = 1

@description('CPU cores (0.25 increments).')
param cpu string = '0.5'

@description('Memory in GiB.')
param memory string = '1.0Gi'

@description('Postgres FQDN.')
param postgresFqdn string

@description('Postgres database name.')
param postgresDatabaseName string

@description('Postgres admin username.')
param postgresAdminUsername string

@description('Key Vault secret URI for the Postgres admin password.')
param postgresAdminPasswordSecretUri string

@description('Key Vault secret URI for the JWT secret.')
param jwtSecretUri string

@description('Key Vault secret URI for the MQTT password.')
param mqttPasswordSecretUri string

@description('MQTT broker URL (mqtt:// or mqtts://).')
param mqttBrokerUrl string

@description('MQTT broker username.')
param mqttUsername string

@description('App Insights connection string for OTel.')
param appInsightsConnectionString string

@description('App-level environment string (read by Settings.environment): dev | staging | production.')
@allowed(['dev','staging','production'])
param appEnvironment string = 'production'

@description('Common tags.')
param tags object = {}

resource app 'Microsoft.App/containerApps@2024-10-02-preview' = {
  name: appName
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
      activeRevisionsMode: 'Single'
      ingress: enableIngress ? {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            weight: 100
            latestRevision: true
          }
        ]
      } : null
      registries: [
        {
          server: acrLoginServer
          identity: userAssignedIdentityId
        }
      ]
      secrets: [
        {
          name: 'jwt-secret'
          identity: userAssignedIdentityId
          keyVaultUrl: jwtSecretUri
        }
        {
          name: 'postgres-password'
          identity: userAssignedIdentityId
          keyVaultUrl: postgresAdminPasswordSecretUri
        }
        {
          name: 'mqtt-password'
          identity: userAssignedIdentityId
          keyVaultUrl: mqttPasswordSecretUri
        }
      ]
    }
    template: {
      containers: [
        {
          name: appName
          image: image
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: [
            { name: 'ENVIRONMENT', value: appEnvironment }
            { name: 'WORKERS_INLINE', value: workersInline ? 'true' : 'false' }
            { name: 'JWT_SECRET', secretRef: 'jwt-secret' }
            { name: 'POSTGRES_HOST', value: postgresFqdn }
            { name: 'POSTGRES_DB', value: postgresDatabaseName }
            { name: 'POSTGRES_USER', value: postgresAdminUsername }
            { name: 'POSTGRES_PASSWORD', secretRef: 'postgres-password' }
            { name: 'DATABASE_URL', value: 'postgresql+asyncpg://${postgresAdminUsername}:$(POSTGRES_PASSWORD)@${postgresFqdn}:5432/${postgresDatabaseName}?sslmode=require' }
            { name: 'MQTT_BROKER_URL', value: mqttBrokerUrl }
            { name: 'MQTT_USERNAME', value: mqttUsername }
            { name: 'MQTT_PASSWORD', secretRef: 'mqtt-password' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'OTEL_SERVICE_NAME', value: appName }
            { name: 'OTEL_RESOURCE_ATTRIBUTES', value: 'service.namespace=tagpulse,deployment.environment=production' }
          ]
          probes: enableIngress ? [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health/live'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health/ready'
                port: 8000
              }
              initialDelaySeconds: 15
              periodSeconds: 15
              timeoutSeconds: 10
              failureThreshold: 3
            }
            {
              type: 'Startup'
              httpGet: {
                path: '/health/live'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 5
              timeoutSeconds: 5
              failureThreshold: 30
            }
          ] : null
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: enableIngress ? [
          {
            name: 'http-scaler'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ] : null
      }
    }
  }
}

output id string = app.id
output name string = app.name
output fqdn string = enableIngress ? app.properties.configuration.ingress.fqdn : ''
