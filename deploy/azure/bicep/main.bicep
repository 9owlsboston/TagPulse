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

@description('Sprint 25 A2 -- extra CORS allow-origins (comma-separated) for the api. The Static Web App default hostname is auto-appended; only add custom domains or dev origins here.')
param corsOriginsExtra string = 'http://localhost:5173'

@description('Optional short suffix appended to the Key Vault name to dodge soft-delete name reservations from a prior teardown. Set automatically by scripts/azd-kv-recover.sh when a purge-protected collision is detected.')
param keyVaultNameSuffix string = ''

@description('Use public placeholder images instead of ACR-hosted ones. Required on first provision (before azd deploy has pushed any images). The preprovision hook auto-toggles this based on whether the migrations image exists in ACR.')
param useImagePlaceholders bool = false

@description('Sprint 23 Phase B — enable VNet integration on the ACA env + provision per-env VNet/subnets/NSGs. Default false. Set via env var AZURE_ENABLE_VNET in main.bicepparam.')
param enableVnetIntegration bool = false

@description('Sprint 23 Phase B — disable public access on KV/Postgres + ACR Premium with PE. Requires enableVnetIntegration=true. Default false. Set via env var AZURE_DISABLE_PUBLIC_NETWORK_ACCESS in main.bicepparam.')
param disablePublicNetworkAccess bool = false

@description('Sprint 28 C6 — enable Mosquitto 8883 TLS listener. Default false; requires the three mqtt-tls-* params below to be non-empty.')
param mqttTlsEnabled bool = false

@description('PEM-encoded CA. Set via env var AZURE_MQTT_TLS_CA.')
@secure()
param mqttTlsCa string = ''

@description('PEM-encoded server cert. Set via env var AZURE_MQTT_TLS_CERT.')
@secure()
param mqttTlsCert string = ''

@description('PEM-encoded server private key. Set via env var AZURE_MQTT_TLS_KEY.')
@secure()
param mqttTlsKey string = ''

@description('Sprint 28 D2 — deploy Azure Monitor alerts + action group.')
param deployAlerts bool = false

@description('On-call email for the action group. Required when deployAlerts=true.')
param alertEmail string = ''

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
    corsOriginsExtra: corsOriginsExtra
    keyVaultNameSuffix: keyVaultNameSuffix
    useImagePlaceholders: useImagePlaceholders
    enableVnetIntegration: enableVnetIntegration
    disablePublicNetworkAccess: disablePublicNetworkAccess
    mqttTlsEnabled: mqttTlsEnabled
    mqttTlsCa: mqttTlsCa
    mqttTlsCert: mqttTlsCert
    mqttTlsKey: mqttTlsKey
    deployAlerts: deployAlerts
    alertEmail: alertEmail
    tags: tags
  }
}

output resourceGroupName string = rg.name
output acrLoginServer string = workload.outputs.acrLoginServer
output acrName string = workload.outputs.acrName
// azd looks specifically for an output named `AZURE_CONTAINER_REGISTRY_ENDPOINT`
// (canonical name) and auto-promotes it to azd env values. This makes the
// registry available to the docker-package step on subsequent `azd up` runs,
// eliminating the chicken-and-egg where package runs before provision and
// `${AZURE_ACR_LOGIN_SERVER}` is empty on first run.
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = workload.outputs.acrLoginServer
output keyVaultName string = workload.outputs.keyVaultName
output postgresFqdn string = workload.outputs.postgresFqdn
output mqttFqdn string = workload.outputs.mqttFqdn
output containerAppsEnvName string = workload.outputs.containerAppsEnvName
output apiAppName string = workload.outputs.apiAppName
output apiFqdn string = workload.outputs.apiFqdn
output workerAppName string = workload.outputs.workerAppName
output migrationsJobName string = workload.outputs.migrationsJobName
output toolsJobName string = workload.outputs.toolsJobName
output staticWebAppName string = workload.outputs.staticWebAppName
output staticWebAppHostname string = workload.outputs.staticWebAppHostname
output appInsightsConnectionString string = workload.outputs.appInsightsConnectionString
// Sprint 23 Phase B -- surfaces whether the safety-coerced flag landed as
// `true` or fell back to `false` (set DPNA without VNet -> bricked env).
// Operators verify post-cutover via:
//   az deployment sub show --name $AZURE_ENV_NAME \
//     --query 'properties.outputs.disablePublicNetworkAccessEffective.value'
output disablePublicNetworkAccessEffective bool = workload.outputs.disablePublicNetworkAccessEffective
