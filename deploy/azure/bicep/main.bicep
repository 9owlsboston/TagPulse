// Subscription-scope entrypoint. Creates the resource group, then deploys workload.bicep
// inside it. Run with:
//   az deployment sub create --location southcentralus --template-file main.bicep \
//     --parameters main.bicepparam

targetScope = 'subscription'

@description('Azure region for the resource group + most resources.')
param location string = 'southcentralus'

@description('Resource group name. Will be created if missing.')
param resourceGroupName string = 'tagpulse-rg'

@description('Resource name prefix.')
param namePrefix string = 'tagpulse'

@description('Image tag to deploy across api, worker, and migrations.')
param imageTag string = 'latest'

@description('Postgres admin username.')
param postgresAdminUsername string = 'tagpulse_admin'

@description('Postgres admin password.')
@secure()
param postgresAdminPassword string

@description('JWT signing secret.')
@secure()
param jwtSecret string

@description('MQTT broker username.')
param mqttUsername string = 'tagpulse'

@description('MQTT broker password.')
@secure()
param mqttPassword string

@description('Static Web App location (SWA available regions only).')
param staticWebAppLocation string = 'centralus'

@description('App-level environment string (read by Settings.environment): dev | staging | production.')
@allowed(['dev','staging','production'])
param appEnvironment string = 'production'

@description('Optional short suffix appended to the Key Vault name to dodge soft-delete name reservations from a prior teardown. Set automatically by scripts/azd-kv-recover.sh when a purge-protected collision is detected.')
param keyVaultNameSuffix string = ''

@description('Use public placeholder images instead of ACR-hosted ones. Required on first provision (before azd deploy has pushed any images). The preprovision hook auto-toggles this based on whether the migrations image exists in ACR.')
param useImagePlaceholders bool = false

@description('Sprint 23 Phase B — enable VNet integration on the ACA env + provision per-env VNet/subnets/NSGs. Default false. Set via env var AZURE_ENABLE_VNET in main.bicepparam.')
param enableVnetIntegration bool = false

@description('Sprint 23 Phase B — disable public access on KV/Postgres + ACR Premium with PE. Requires enableVnetIntegration=true. Default false. Set via env var AZURE_DISABLE_PUBLIC_NETWORK_ACCESS in main.bicepparam.')
param disablePublicNetworkAccess bool = false

@description('Common tags.')
param tags object = {
  workload: 'tagpulse'
  managedBy: 'bicep'
  environment: appEnvironment
}

resource rg 'Microsoft.Resources/resourceGroups@2024-07-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module workload 'workload.bicep' = {
  scope: rg
  params: {
    location: location
    namePrefix: namePrefix
    uniqueSuffix: uniqueString(rg.id)
    imageTag: imageTag
    postgresAdminUsername: postgresAdminUsername
    postgresAdminPassword: postgresAdminPassword
    jwtSecret: jwtSecret
    mqttUsername: mqttUsername
    mqttPassword: mqttPassword
    staticWebAppLocation: staticWebAppLocation
    appEnvironment: appEnvironment
    keyVaultNameSuffix: keyVaultNameSuffix
    useImagePlaceholders: useImagePlaceholders
    enableVnetIntegration: enableVnetIntegration
    disablePublicNetworkAccess: disablePublicNetworkAccess
    tags: tags
  }
}

output resourceGroupName string = rg.name
output acrLoginServer string = workload.outputs.acrLoginServer
output acrName string = workload.outputs.acrName
output keyVaultName string = workload.outputs.keyVaultName
output postgresFqdn string = workload.outputs.postgresFqdn
output mqttFqdn string = workload.outputs.mqttFqdn
output containerAppsEnvName string = workload.outputs.containerAppsEnvName
output apiAppName string = workload.outputs.apiAppName
output apiFqdn string = workload.outputs.apiFqdn
output workerAppName string = workload.outputs.workerAppName
output migrationsJobName string = workload.outputs.migrationsJobName
output staticWebAppName string = workload.outputs.staticWebAppName
output staticWebAppHostname string = workload.outputs.staticWebAppHostname
output appInsightsConnectionString string = workload.outputs.appInsightsConnectionString
// Sprint 23 Phase B -- surfaces whether the safety-coerced flag landed as
// `true` or fell back to `false` (set DPNA without VNet -> bricked env).
// Operators verify post-cutover via:
//   az deployment sub show --name $AZURE_ENV_NAME \
//     --query 'properties.outputs.disablePublicNetworkAccessEffective.value'
output disablePublicNetworkAccessEffective bool = workload.outputs.disablePublicNetworkAccessEffective
