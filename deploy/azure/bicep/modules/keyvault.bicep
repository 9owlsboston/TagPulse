// Key Vault — stores JWT secret + Postgres admin password + MQTT broker creds.
// ACA pulls secrets at runtime via managed identity with Key Vault Secrets User role.
// RBAC mode (no access policies) per Azure best practice.

@description('Globally unique Key Vault name (3–24 chars, alphanumeric + hyphen).')
param keyVaultName string

@description('Azure region.')
param location string

@description('Tenant ID for Key Vault.')
param tenantId string = subscription().tenantId

@description('Initial secrets to seed. Values are not echoed in deployment outputs.')
@secure()
param secrets object

@description('Common tags applied to every resource.')
param tags object = {}

@description('When true, enables purge protection (irrevocable 7-day soft-delete window). Required for production; leave false in dev so teardowns do not lock the KV name for a week.')
param enablePurgeProtection bool = true

@description('When true, sets publicNetworkAccess=Disabled and networkAcls.defaultAction=Deny. Sprint 23 Phase B — use only when a private endpoint is wired in (deploy/azure/bicep/modules/private-endpoint.bicep). Default false preserves Sprint 22 behaviour for envs not on the corporate `Deny`-mode policy.')
param disablePublicNetworkAccess bool = false

resource kv 'Microsoft.KeyVault/vaults@2024-04-01-preview' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: enablePurgeProtection ? true : null
    publicNetworkAccess: disablePublicNetworkAccess ? 'Disabled' : 'Enabled'
    networkAcls: disablePublicNetworkAccess ? {
      bypass: 'AzureServices'
      defaultAction: 'Deny'
    } : {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

resource jwtSecret 'Microsoft.KeyVault/vaults/secrets@2024-04-01-preview' = {
  parent: kv
  name: 'jwt-secret'
  properties: {
    value: secrets.jwtSecret
  }
}

resource pgAdminPassword 'Microsoft.KeyVault/vaults/secrets@2024-04-01-preview' = {
  parent: kv
  name: 'postgres-admin-password'
  properties: {
    value: secrets.postgresAdminPassword
  }
}

resource mqttPassword 'Microsoft.KeyVault/vaults/secrets@2024-04-01-preview' = {
  parent: kv
  name: 'mqtt-broker-password'
  properties: {
    value: secrets.mqttPassword
  }
}

output id string = kv.id
output name string = kv.name
output uri string = kv.properties.vaultUri
// The following outputs are URIs (not secret values themselves); ACA secret refs need them.
#disable-next-line outputs-should-not-contain-secrets
output jwtSecretUri string = jwtSecret.properties.secretUri
#disable-next-line outputs-should-not-contain-secrets
output pgAdminPasswordUri string = pgAdminPassword.properties.secretUri
#disable-next-line outputs-should-not-contain-secrets
output mqttPasswordUri string = mqttPassword.properties.secretUri
