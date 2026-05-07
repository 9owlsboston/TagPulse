// Generic private-endpoint module — Sprint 23 Phase B.
//
// One module instantiation per (target resource, group ID) pair. Used for:
//   - Key Vault          targetResource = kv.id           groupId = 'vault'           dnsZone = 'privatelink.vaultcore.azure.net'
//   - Postgres Flexible  targetResource = pg.id           groupId = 'postgresqlServer' dnsZone = 'privatelink.postgres.database.azure.com'
//   - ACR                targetResource = acr.id          groupId = 'registry'         dnsZone = 'privatelink.azurecr.io'
//
// The PE goes in the `pe` subnet. We also link a Private DNS Zone to the
// VNet so workloads inside the VNet resolve the public FQDN of the target
// resource to the PE's 10.10.x.x address — no client-side config change in
// any container app.

@description('Name for the private endpoint resource.')
param peName string

@description('Azure region.')
param location string

@description('Target resource ID (KV, Postgres, ACR, etc.).')
param targetResourceId string

@description('Group ID for the PE — see Microsoft docs for the per-service value (vault / postgresqlServer / registry / etc.).')
param groupId string

@description('Private DNS zone name (e.g. privatelink.vaultcore.azure.net).')
param dnsZoneName string

@description('Resource ID of the subnet the PE lives in (typically the `pe` subnet).')
param subnetId string

@description('VNet ID to link the Private DNS Zone to.')
param vnetId string

@description('Common tags.')
param tags object = {}

resource pe 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: peName
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnetId
    }
    privateLinkServiceConnections: [
      {
        name: peName
        properties: {
          privateLinkServiceId: targetResourceId
          groupIds: [
            groupId
          ]
        }
      }
    ]
  }
}

// Private DNS Zone — global. Idempotent: if the same zone is created twice
// in the same RG (e.g. PE for KV + PE for ACR-equivalent shared zone), the
// second deployment becomes a no-op. Each consumer module owns its zone to
// keep dependencies clean.
resource dnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: dnsZoneName
  location: 'global'
  tags: tags
}

resource vnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: dnsZone
  name: '${peName}-vnetlink'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

// Auto-register the PE's NIC IP in the zone so resolution Just Works for
// pods/replicas. The default-zone-config-name varies by service — use a
// generic name; Azure normalises it server-side.
resource peDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: pe
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config'
        properties: {
          privateDnsZoneId: dnsZone.id
        }
      }
    ]
  }
  dependsOn: [
    vnetLink
  ]
}

output id string = pe.id
output dnsZoneId string = dnsZone.id
