// Azure Container Registry — Basic SKU is sufficient for v1 (~$5/mo, 10GB storage).
// ACA pulls images via managed identity assigned the AcrPull role (no admin user, no PATs).

@description('Globally unique ACR name (lowercase alphanumeric, 5–50 chars).')
param acrName string

@description('Azure region.')
param location string

@description('Common tags applied to every resource in the workload.')
param tags object = {}

@description('When true, bumps SKU to Premium (~$16/mo) so a private endpoint can be attached. Sprint 23 Phase B -- Basic SKU does not support private endpoints. Default false keeps the Sprint 22 Basic SKU (~$5/mo).')
param enablePrivateEndpoint bool = false

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: enablePrivateEndpoint ? 'Premium' : 'Basic'
  }
  properties: {
    adminUserEnabled: false
    // ADR-017 Section Phase B explicitly keeps public ACR access ON in Sprint 23
    // (GHA hosted-runner pushes need it; closing it is Sprint 24+ work). The
    // Premium SKU + PE is added so VNet-resident clients can pull privately.
    publicNetworkAccess: 'Enabled'
    anonymousPullEnabled: false
  }
}

output id string = acr.id
output name string = acr.name
output loginServer string = acr.properties.loginServer
