// Container Apps managed environment — shared across api, worker, and migrations job.
// Consumption-only profile (no dedicated workload profiles in v1) to keep costs near zero
// when idle. Add a `D4` workload profile later for Dedicated plan if needed.

@description('Container Apps environment name.')
param envName string

@description('Azure region.')
param location string

@description('Log Analytics workspace customer ID for native log integration.')
param logAnalyticsCustomerId string

@description('Log Analytics primary shared key.')
@secure()
param logAnalyticsSharedKey string

@description('App Insights connection string for Container Apps Dapr telemetry (optional today; carried for future Dapr work).')
@secure()
param appInsightsConnectionString string

@description('Common tags.')
param tags object = {}

resource env 'Microsoft.App/managedEnvironments@2024-10-02-preview' = {
  name: envName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
    daprAIConnectionString: appInsightsConnectionString
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    zoneRedundant: false
  }
}

output id string = env.id
output name string = env.name
output defaultDomain string = env.properties.defaultDomain
