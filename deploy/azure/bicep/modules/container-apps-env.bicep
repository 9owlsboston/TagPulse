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

@description('When set, the env is provisioned with VNet integration on the supplied subnet (must be delegated to Microsoft.App/environments). Empty string = legacy non-VNet env (Sprint 22 default). NOTE: this property is immutable post-create, so flipping it on an existing env requires `azd down --purge` + reprovision (see docs/runbooks/sprint-23-network-cutover.md).')
param infrastructureSubnetId string = ''

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
    vnetConfiguration: empty(infrastructureSubnetId) ? null : {
      infrastructureSubnetId: infrastructureSubnetId
      // External ingress for the api stays public; only egress is on the VNet.
      // Sprint 24+ Front Door work is what flips this to internal=true.
      internal: false
    }
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
