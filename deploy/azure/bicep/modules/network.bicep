// VNet + subnets + NSG for Sprint 23 Phase B network hardening.
//
// Layout (per env):
//   tp{env}-vnet                    10.10.0.0/16
//     subnet aca-infra              10.10.0.0/23   delegated to Microsoft.App/environments
//     subnet pe                     10.10.2.0/27   private endpoints (KV / Postgres / ACR)
//     subnet mgmt                   10.10.3.0/27   reserved for Bastion / future jumpbox
//
// Subnet sizing notes:
//   - aca-infra needs at least /23 for Container Apps (consumption profile).
//     ACA reserves a /27 per replica revision burst; /23 leaves comfortable
//     headroom for 100+ replicas.
//   - pe is /27 (29 usable IPs) — one IP per private endpoint, plenty for
//     KV + Postgres + ACR + future expansion.
//   - mgmt is /27 — Azure Bastion requires /26 minimum, but we provision
//     mgmt as a placeholder; if Bastion is enabled in production, expand to
//     a parallel `AzureBastionSubnet` (/26) inside the same VNet. Sprint
//     23's ADR-017 documents this as a Sprint 24+ activity.

@description('Resource name prefix for env (e.g. tpdev / tpstaging / tpprod). Drives VNet + subnet names.')
param namePrefix string

@description('Azure region.')
param location string

@description('Common tags.')
param tags object = {}

@description('VNet address space. Default leaves room for parallel envs by varying the second octet (10.10/10.20/10.30).')
param vnetAddressPrefix string = '10.10.0.0/16'

@description('aca-infra subnet CIDR. /23 minimum for Container Apps consumption profile.')
param acaSubnetPrefix string = '10.10.0.0/23'

@description('Private endpoint subnet CIDR.')
param peSubnetPrefix string = '10.10.2.0/27'

@description('Management/Bastion subnet CIDR (placeholder).')
param mgmtSubnetPrefix string = '10.10.3.0/27'

var vnetName = '${namePrefix}-vnet'
var acaNsgName = '${namePrefix}-aca-nsg'
var peNsgName = '${namePrefix}-pe-nsg'

// NSG on aca-infra: default-deny inbound; permit Container Apps control-plane
// traffic via the AzureCloud service tag (this is the documented pattern for
// VNet-integrated ACA — Microsoft.App calls back to the env over 443).
resource acaNsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: acaNsgName
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'Allow-AzureCloud-Inbound-443'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'AzureCloud'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '443'
        }
      }
    ]
  }
}

// PE subnet: no NSG rules required by default (private endpoints bypass NSG
// on the subnet they live in, per Azure default). We attach an empty NSG so
// auditing tools see it as managed rather than missing.
resource peNsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: peNsgName
  location: location
  tags: tags
  properties: {
    securityRules: []
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressPrefix
      ]
    }
    subnets: [
      {
        name: 'aca-infra'
        properties: {
          addressPrefix: acaSubnetPrefix
          networkSecurityGroup: {
            id: acaNsg.id
          }
          delegations: [
            {
              name: 'aca-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'pe'
        properties: {
          addressPrefix: peSubnetPrefix
          networkSecurityGroup: {
            id: peNsg.id
          }
          // Private endpoints require this until ACA-injection-style implicit
          // disable lands GA — explicit in template.
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: 'mgmt'
        properties: {
          addressPrefix: mgmtSubnetPrefix
        }
      }
    ]
  }
}

output id string = vnet.id
output name string = vnet.name
output acaSubnetId string = '${vnet.id}/subnets/aca-infra'
output peSubnetId string = '${vnet.id}/subnets/pe'
output mgmtSubnetId string = '${vnet.id}/subnets/mgmt'
