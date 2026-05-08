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

@description('Sprint 25 A2 -- extra CORS allow-origins (comma-separated) for the api. The Static Web App default hostname is auto-appended; only add custom domains or dev origins here.')
param corsOriginsExtra string = 'http://localhost:5173'

@description('Optional short suffix appended to the Key Vault name to dodge soft-delete name reservations after a prior teardown. Empty = clean name.')
param keyVaultNameSuffix string = ''

@description('Use public placeholder images on first provision (before azd deploy has pushed app images to ACR).')
param useImagePlaceholders bool = false

@description('Sprint 23 Phase B — enable VNet integration on the Container Apps environment + provision the per-env VNet, NSGs, and the `pe` subnet for private endpoints. Default false preserves Sprint 22 (no VNet) behaviour.')
param enableVnetIntegration bool = false

@description('Sprint 23 Phase B -- disable public network access on KV / Postgres and bump ACR to Premium with a private endpoint. ONLY safe when enableVnetIntegration=true (otherwise no clients can reach those services). Default false. Both flags default off so the Sprint 22 deploy path still works for envs without the corporate `Deny`-mode policy.')
param disablePublicNetworkAccess bool = false

// Sprint 23 Phase B safety guard. `disablePublicNetworkAccess=true` with
// `enableVnetIntegration=false` would close the public KV/Postgres firewall
// AND skip provisioning the private endpoints that replace them -- bricking
// the env. We coerce the effective value to false in that case and surface
// the override via a deployment output so it is visible in `az deployment
// sub show`. A loud Bicep `assert` would be cleaner, but the assert keyword
// is still experimental as of bicep 0.43.x; coercion + output is the
// portable workaround.
var disablePublicNetworkAccessEffective = enableVnetIntegration && disablePublicNetworkAccess

// Public placeholder images used when ACR has no images yet. azd deploy
// later replaces these via `az containerapp update --image ...`.
var appPlaceholderImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
var jobPlaceholderImage = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

// Naming
var acrName = toLower('${namePrefix}acr${uniqueSuffix}')
var keyVaultName = empty(keyVaultNameSuffix) ? toLower('${namePrefix}-kv-${take(uniqueSuffix, 8)}') : toLower('${namePrefix}-kv-${take(uniqueSuffix, 8)}-${keyVaultNameSuffix}')
var postgresName = toLower('${namePrefix}-pg-${take(uniqueSuffix, 8)}')
var mqttContainerGroupName = '${namePrefix}-mqtt'
var mqttDnsLabel = toLower('${namePrefix}-mqtt-${take(uniqueSuffix, 8)}')
var workspaceName = '${namePrefix}-logs'
var appInsightsName = '${namePrefix}-insights'
var acaEnvName = '${namePrefix}-env'
var uamiName = '${namePrefix}-identity'
var apiAppName = '${namePrefix}-api'
var workerAppName = '${namePrefix}-worker'
var migrationsJobName = '${namePrefix}-migrations'
var toolsJobName = '${namePrefix}-tools'
var swaName = '${namePrefix}-ui'

module monitoring 'modules/monitoring.bicep' = {
  params: {
    workspaceName: workspaceName
    appInsightsName: appInsightsName
    location: location
    tags: tags
  }
}

// Sprint 23 Phase B -- per-env VNet + subnets + NSGs. Off by default; only
// provisioned when enableVnetIntegration=true so the Sprint 22 deploy path
// (no VNet) keeps working unchanged for envs without the corporate policy.
module network 'modules/network.bicep' = if (enableVnetIntegration) {
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
  }
}

module acr 'modules/acr.bicep' = {
  params: {
    acrName: acrName
    location: location
    enablePrivateEndpoint: disablePublicNetworkAccessEffective
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
    disablePublicNetworkAccess: disablePublicNetworkAccessEffective
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
    disablePublicNetworkAccess: disablePublicNetworkAccessEffective
    tags: tags
  }
}

module mqtt 'modules/mqtt.bicep' = {
  params: {
    containerGroupName: mqttContainerGroupName
    dnsLabelPrefix: mqttDnsLabel
    location: location
    mqttUsername: mqttUsername
    mqttPassword: mqttPassword
    acrLoginServer: acr.outputs.loginServer
    imageTag: imageTag
    userAssignedIdentityId: identity.outputs.id
    useImagePlaceholders: useImagePlaceholders
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
    infrastructureSubnetId: enableVnetIntegration ? network!.outputs.acaSubnetId : ''
    tags: tags
  }
}

module apiApp 'modules/container-app.bicep' = {
  params: {
    appName: apiAppName
    location: location
    environmentId: acaEnv.outputs.id
    userAssignedIdentityId: identity.outputs.id
    image: useImagePlaceholders ? appPlaceholderImage : '${acr.outputs.loginServer}/tagpulse-api:${imageTag}'
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
    corsOrigins: '${corsOriginsExtra},https://${ui.outputs.defaultHostname}'
    tags: union(tags, { 'azd-service-name': 'api' })
  }
}

module workerApp 'modules/container-app.bicep' = {
  params: {
    appName: workerAppName
    location: location
    environmentId: acaEnv.outputs.id
    userAssignedIdentityId: identity.outputs.id
    image: useImagePlaceholders ? appPlaceholderImage : '${acr.outputs.loginServer}/tagpulse-worker:${imageTag}'
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
    tags: union(tags, { 'azd-service-name': 'worker' })
  }
}

module migrationsJob 'modules/migrations-job.bicep' = {
  params: {
    jobName: migrationsJobName
    location: location
    environmentId: acaEnv.outputs.id
    userAssignedIdentityId: identity.outputs.id
    image: useImagePlaceholders ? jobPlaceholderImage : '${acr.outputs.loginServer}/tagpulse-migrations:${imageTag}'
    acrLoginServer: acr.outputs.loginServer
    postgresFqdn: postgres.outputs.fqdn
    postgresDatabaseName: postgres.outputs.databaseName
    postgresAdminUsername: postgres.outputs.adminUsername
    postgresAdminPasswordSecretUri: kv.outputs.pgAdminPasswordUri
    appEnvironment: appEnvironment
    tags: union(tags, { 'azd-service-name': 'migrations' })
  }
}

// Sprint 26 B1 -- ad-hoc operational scripts (smoke_setup, simulate_*,
// benchmark_pg_metrics) running in-VNet against the deployed Postgres.
// Reuses the api image (which now ships scripts/ via Sprint 26 A1) so no
// separate build pipeline is required. Default command is a no-op import
// smoke; scripts/azd-job.sh overrides command+args at start time.
module toolsJob 'modules/tools-job.bicep' = {
  params: {
    jobName: toolsJobName
    location: location
    environmentId: acaEnv.outputs.id
    userAssignedIdentityId: identity.outputs.id
    userAssignedIdentityClientId: identity.outputs.clientId
    image: useImagePlaceholders ? appPlaceholderImage : '${acr.outputs.loginServer}/tagpulse-api:${imageTag}'
    acrLoginServer: acr.outputs.loginServer
    postgresFqdn: postgres.outputs.fqdn
    postgresDatabaseName: postgres.outputs.databaseName
    postgresAdminUsername: postgres.outputs.adminUsername
    postgresAdminPasswordSecretUri: kv.outputs.pgAdminPasswordUri
    apiFqdn: apiApp.outputs.fqdn
    keyVaultName: kv.outputs.name
    appEnvironment: appEnvironment
    tags: union(tags, { 'azd-service-name': 'tools' })
  }
}

module ui 'modules/static-web-app.bicep' = {
  params: {
    siteName: swaName
    location: staticWebAppLocation
    // Sprint 25 A2 -- intentionally NOT passing apiUrl. The previous wiring
    // ('https://${apiApp.outputs.fqdn}') created a cycle with the api's
    // CORS_ORIGINS env var (which now references ui.outputs.defaultHostname).
    // The SWA app-setting was cosmetic anyway: the UI repo's deploy workflow
    // bakes VITE_API_BASE_URL at build time from `vars.VITE_API_BASE_URL`
    // (set by scripts/ui-cicd-setup.sh), not from this SWA app-setting.
    tags: union(tags, { 'azd-service-name': 'ui' })
  }
}

// Sprint 23 Phase B -- private endpoints for KV / Postgres / ACR. Only
// provisioned when both flags are on. Each PE creates a Private DNS Zone
// linked to the VNet so workloads inside the env resolve the public FQDN to
// the 10.10.x.x PE address with no client-side change.
module kvPrivateEndpoint 'modules/private-endpoint.bicep' = if (disablePublicNetworkAccessEffective) {
  params: {
    peName: '${namePrefix}-kv-pe'
    location: location
    targetResourceId: kv.outputs.id
    groupId: 'vault'
    dnsZoneName: 'privatelink.vaultcore.azure.net'
    subnetId: network!.outputs.peSubnetId
    vnetId: network!.outputs.id
    tags: tags
  }
}

module postgresPrivateEndpoint 'modules/private-endpoint.bicep' = if (disablePublicNetworkAccessEffective) {
  params: {
    peName: '${namePrefix}-pg-pe'
    location: location
    targetResourceId: postgres.outputs.id
    groupId: 'postgresqlServer'
    dnsZoneName: 'privatelink.postgres.database.azure.com'
    subnetId: network!.outputs.peSubnetId
    vnetId: network!.outputs.id
    tags: tags
  }
}

module acrPrivateEndpoint 'modules/private-endpoint.bicep' = if (disablePublicNetworkAccessEffective) {
  params: {
    peName: '${namePrefix}-acr-pe'
    location: location
    targetResourceId: acr.outputs.id
    groupId: 'registry'
    dnsZoneName: 'privatelink.azurecr.io'
    subnetId: network!.outputs.peSubnetId
    vnetId: network!.outputs.id
    tags: tags
  }
}

output acrLoginServer string = acr.outputs.loginServer
output acrName string = acr.outputs.name
output keyVaultName string = kv.outputs.name
output postgresFqdn string = postgres.outputs.fqdn
output postgresDatabaseName string = postgres.outputs.databaseName
output mqttFqdn string = mqtt.outputs.fqdn
output containerAppsEnvName string = acaEnv.outputs.name
output apiAppName string = apiApp.outputs.name
output apiFqdn string = apiApp.outputs.fqdn
output workerAppName string = workerApp.outputs.name
output migrationsJobName string = migrationsJob.outputs.name
output toolsJobName string = toolsJob.outputs.name
output staticWebAppName string = ui.outputs.name
output staticWebAppHostname string = ui.outputs.defaultHostname
// Sprint 23 Phase B -- surfaces the effective value (after the safety
// coercion above). Useful for verifying the cutover took effect and for
// debugging the silent-override case where DPNA was set without VNet.
output disablePublicNetworkAccessEffective bool = disablePublicNetworkAccessEffective
output appInsightsConnectionString string = monitoring.outputs.appInsightsConnectionString
output userAssignedIdentityId string = identity.outputs.id
output userAssignedIdentityClientId string = identity.outputs.clientId
