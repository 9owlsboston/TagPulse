// User-assigned managed identity for the workload, with role assignments for ACR
// (image pulls) and Key Vault (secret reads). Created once and shared by all three
// container apps + migrations job — avoids the chicken-and-egg of deploying a system
// identity then trying to grant it AcrPull before the first image pull.

@description('UAMI name.')
param identityName string

@description('Azure region.')
param location string

@description('ACR resource ID for AcrPull role assignment.')
param acrId string

@description('Key Vault resource ID for Key Vault Secrets User role assignment.')
param keyVaultId string

@description('Common tags.')
param tags object = {}

resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: tags
}

// AcrPull built-in role
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
// Key Vault Secrets User built-in role
var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
// Sprint 26 D3 — Key Vault Secrets Officer (write) so the tools-job can
// push freshly issued API keys to KV instead of printing them to stdout.
// The same UAMI is shared by api/worker/migrations/tools-job; granting
// Officer here is broader than strictly needed but avoids splitting the
// identity model. The api/worker code path never *writes* secrets, so
// in practice only the tools-job exercises this role.
var kvSecretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: last(split(acrId, '/'))
}

resource kv 'Microsoft.KeyVault/vaults@2024-04-01-preview' existing = {
  name: last(split(keyVaultId, '/'))
}

resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, uami.id, acrPullRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource kvSecretsAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, uami.id, kvSecretsUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource kvSecretsOfficerAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, uami.id, kvSecretsOfficerRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsOfficerRoleId)
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

output id string = uami.id
output principalId string = uami.properties.principalId
output clientId string = uami.properties.clientId
