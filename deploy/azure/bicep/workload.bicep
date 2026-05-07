// Workload orchestrator — runs at resource-group scope, deploys all sub-modules in
// dependency order. Called by main.bicep after the RG is created.

@description('Azure region.')
param location string

@description('Common tags applied to every resource.')
param tags object

@description('Resource name prefix, e.g. "tagpulse" or "tagpulse-stage". Used for app/job/env names.')
param namePrefix string

@description('Globally unique suffix to disambiguate ACR / KV / Postgres / storage names. Recommend `uniqueString(resourceGroup().id)`.')
param uniqueSuffix string

@description('Image tag to deploy (e.g. v1.2.3 or commit SHA). All three images share this tag.')
param imageTag string = 'latest'

@description('Postgres admin username.')
param postgresAdminUsername string = 'tagpulse_admin'

@description('Postgres admin password (seeded into Key Vault).')
@secure()
param postgresAdminPassword string

@description('JWT signing secret (seeded into Key Vault).')
@secure()
param jwtSecret string

@description('MQTT broker username.')
param mqttUsername string = 'tagpulse'

@description('MQTT broker password (seeded into Key Vault).')
@secure()
param mqttPassword string

@description('Static Web App location (must be one of the SWA-supported regions).')
param staticWebAppLocation string = 'centralus'

@description('App-level environment string (read by Settings.environment): dev | staging | production.')
@allowed(['dev','staging','production'])
param appEnvironment string = 'production'

// Naming
var acrName = toLower('${namePrefix}acr${uniqueSuffix}')
var keyVaultName = toLower('${namePrefix}-kv-${take(uniqueSuffix, 8)}')
var postgresName = toLower('${namePrefix}-pg-${take(uniqueSuffix, 8)}')
var mqttStorageName = toLower('${namePrefix}mqtt${take(uniqueSuffix, 8)}')
var mqttContainerGroupName = '${namePrefix}-mqtt'
var mqttDnsLabel = toLower('${namePrefix}-mqtt-${take(uniqueSuffix, 8)}')
var workspaceName = '${namePrefix}-logs'
var appInsightsName = '${namePrefix}-insights'
var acaEnvName = '${namePrefix}-env'
var uamiName = '${namePrefix}-identity'
var apiAppName = '${namePrefix}-api'
var workerAppName = '${namePrefix}-worker'
var migrationsJobName = '${namePrefix}-migrations'
var swaName = '${namePrefix}-ui'

module monitoring 'modules/monitoring.bicep' = {
  params: {
    workspaceName: workspaceName
    appInsightsName: appInsightsName
    location: location
    tags: tags
  }
}

module acr 'modules/acr.bicep' = {
  params: {
    acrName: acrName
    location: location
    tags: tags
  }
}

module kv 'modules/keyvault.bicep' = {
  params: {
    keyVaultName: keyVaultName
    location: location
    secrets: {
      jwtSecret: jwtSecret
      postgresAdminPassword: postgresAdminPassword
      mqttPassword: mqttPassword
    }
    // Purge protection is irreversible and pins the KV name for 7 days after
    // teardown. Enable for staging/production only; dev iterates frequently.
    enablePurgeProtection: appEnvironment != 'dev'
    tags: tags
  }
}

module identity 'modules/identity.bicep' = {
  params: {
    identityName: uamiName
    location: location
    acrId: acr.outputs.id
    keyVaultId: kv.outputs.id
    tags: tags
  }
}

module postgres 'modules/postgres.bicep' = {
  params: {
    serverName: postgresName
    location: location
    adminUsername: postgresAdminUsername
    adminPassword: postgresAdminPassword
    tags: tags
  }
}

module mqtt 'modules/mqtt.bicep' = {
  params: {
    containerGroupName: mqttContainerGroupName
    storageAccountName: mqttStorageName
    dnsLabelPrefix: mqttDnsLabel
    location: location
    mqttUsername: mqttUsername
    mqttPassword: mqttPassword
    tags: tags
  }
}

module acaEnv 'modules/container-apps-env.bicep' = {
  params: {
    envName: acaEnvName
    location: location
    logAnalyticsCustomerId: monitoring.outputs.workspaceCustomerId
    logAnalyticsSharedKey: monitoring.outputs.workspaceSharedKey
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    tags: tags
  }
}

module apiApp 'modules/container-app.bicep' = {
  params: {
    appName: apiAppName
    location: location
    environmentId: acaEnv.outputs.id
    userAssignedIdentityId: identity.outputs.id
    image: '${acr.outputs.loginServer}/tagpulse-api:${imageTag}'
    acrLoginServer: acr.outputs.loginServer
    enableIngress: true
    workersInline: false
    minReplicas: 1
    maxReplicas: 3
    postgresFqdn: postgres.outputs.fqdn
    postgresDatabaseName: postgres.outputs.databaseName
    postgresAdminUsername: postgres.outputs.adminUsername
    postgresAdminPasswordSecretUri: kv.outputs.pgAdminPasswordUri
    jwtSecretUri: kv.outputs.jwtSecretUri
    mqttPasswordSecretUri: kv.outputs.mqttPasswordUri
    mqttBrokerUrl: mqtt.outputs.mqttUrl
    mqttUsername: mqttUsername
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    appEnvironment: appEnvironment
    tags: tags
  }
}

module workerApp 'modules/container-app.bicep' = {
  params: {
    appName: workerAppName
    location: location
    environmentId: acaEnv.outputs.id
    userAssignedIdentityId: identity.outputs.id
    image: '${acr.outputs.loginServer}/tagpulse-worker:${imageTag}'
    acrLoginServer: acr.outputs.loginServer
    enableIngress: false
    workersInline: true
    minReplicas: 1
    maxReplicas: 1
    postgresFqdn: postgres.outputs.fqdn
    postgresDatabaseName: postgres.outputs.databaseName
    postgresAdminUsername: postgres.outputs.adminUsername
    postgresAdminPasswordSecretUri: kv.outputs.pgAdminPasswordUri
    jwtSecretUri: kv.outputs.jwtSecretUri
    mqttPasswordSecretUri: kv.outputs.mqttPasswordUri
    mqttBrokerUrl: mqtt.outputs.mqttUrl
    mqttUsername: mqttUsername
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    appEnvironment: appEnvironment
    tags: tags
  }
}

module migrationsJob 'modules/migrations-job.bicep' = {
  params: {
    jobName: migrationsJobName
    location: location
    environmentId: acaEnv.outputs.id
    userAssignedIdentityId: identity.outputs.id
    image: '${acr.outputs.loginServer}/tagpulse-migrations:${imageTag}'
    acrLoginServer: acr.outputs.loginServer
    postgresFqdn: postgres.outputs.fqdn
    postgresDatabaseName: postgres.outputs.databaseName
    postgresAdminUsername: postgres.outputs.adminUsername
    postgresAdminPasswordSecretUri: kv.outputs.pgAdminPasswordUri
    appEnvironment: appEnvironment
    tags: tags
  }
}

module ui 'modules/static-web-app.bicep' = {
  params: {
    siteName: swaName
    location: staticWebAppLocation
    apiUrl: 'https://${apiApp.outputs.fqdn}'
    tags: tags
  }
}

output acrLoginServer string = acr.outputs.loginServer
output acrName string = acr.outputs.name
output keyVaultName string = kv.outputs.name
output postgresFqdn string = postgres.outputs.fqdn
output postgresDatabaseName string = postgres.outputs.databaseName
output mqttFqdn string = mqtt.outputs.fqdn
output mqttStorageAccountName string = mqtt.outputs.storageAccountName
output containerAppsEnvName string = acaEnv.outputs.name
output apiAppName string = apiApp.outputs.name
output apiFqdn string = apiApp.outputs.fqdn
output workerAppName string = workerApp.outputs.name
output migrationsJobName string = migrationsJob.outputs.name
output staticWebAppName string = ui.outputs.name
output staticWebAppHostname string = ui.outputs.defaultHostname
output appInsightsConnectionString string = monitoring.outputs.appInsightsConnectionString
output userAssignedIdentityId string = identity.outputs.id
output userAssignedIdentityClientId string = identity.outputs.clientId
