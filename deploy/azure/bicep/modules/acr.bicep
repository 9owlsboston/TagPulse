// Azure Container Registry — Basic SKU is sufficient for v1 (~$5/mo, 10GB storage).
// ACA pulls images via managed identity assigned the AcrPull role (no admin user, no PATs).

@description('Globally unique ACR name (lowercase alphanumeric, 5–50 chars).')
param acrName string

@description('Azure region.')
param location string

@description('Common tags applied to every resource in the workload.')
param tags object = {}

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
    anonymousPullEnabled: false
  }
}

output id string = acr.id
output name string = acr.name
output loginServer string = acr.properties.loginServer
