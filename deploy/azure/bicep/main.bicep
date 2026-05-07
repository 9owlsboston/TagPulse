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

@description('Common tags.')
param tags object = {
  workload: 'tagpulse'
  managedBy: 'bicep'
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
    tags: tags
  }
}

output resourceGroupName string = rg.name
output acrLoginServer string = workload.outputs.acrLoginServer
output acrName string = workload.outputs.acrName
output keyVaultName string = workload.outputs.keyVaultName
output postgresFqdn string = workload.outputs.postgresFqdn
output mqttFqdn string = workload.outputs.mqttFqdn
output mqttStorageAccountName string = workload.outputs.mqttStorageAccountName
output containerAppsEnvName string = workload.outputs.containerAppsEnvName
output apiAppName string = workload.outputs.apiAppName
output apiFqdn string = workload.outputs.apiFqdn
output workerAppName string = workload.outputs.workerAppName
output migrationsJobName string = workload.outputs.migrationsJobName
output staticWebAppName string = workload.outputs.staticWebAppName
output staticWebAppHostname string = workload.outputs.staticWebAppHostname
output appInsightsConnectionString string = workload.outputs.appInsightsConnectionString
