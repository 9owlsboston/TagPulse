// Mosquitto MQTT broker on Azure Container Instances — Sprint 23 Phase A.
//
// Sprint 22 mounted the broker config + data from Azure Files. The corporate
// `Modify`-mode policy on `Microsoft.Storage` flips `allowSharedKeyAccess`
// to `false` and ACI cannot mount Azure Files with a managed identity, so
// the broker failed with `CannotAccessStorageAccount … 403`.
//
// Sprint 23 fix: bake the conf + entrypoint into a custom image
// (docker/mosquitto.Dockerfile) pushed to ACR as `tagpulse-mqtt` by
// scripts/azd-mqtt-build.sh, and pull it from ACR via the existing UAMI.
// No storage account, no Files share, no volume mounts. Persistence is now
// container-local (ADR-017 §Phase A trade-off).

@description('ACI container group name.')
param containerGroupName string

@description('Azure region.')
param location string

@description('MQTT broker username (provisioned via Mosquitto password file).')
param mqttUsername string

@description('MQTT broker password.')
@secure()
param mqttPassword string

@description('Common tags.')
param tags object = {}

@description('DNS label prefix for ACI public FQDN. Will be {prefix}.{region}.azurecontainer.io.')
param dnsLabelPrefix string

@description('ACR login server (e.g. tpdevacrxxx.azurecr.io). Used both as the image registry and the imageRegistryCredentials.server for managed-identity ACR pull.')
param acrLoginServer string

@description('Image tag (matches the api/worker/migrations tag for the deploy).')
param imageTag string

@description('Resource ID of the User-Assigned Managed Identity used to pull from ACR. Must already have the AcrPull role on the ACR (granted in identity.bicep).')
param userAssignedIdentityId string

@description('When true, use a public placeholder image instead of the ACR-hosted custom image. Required on first provision (before scripts/azd-mqtt-build.sh has pushed the image). Auto-toggled by scripts/azd-image-check.sh.')
param useImagePlaceholders bool = false

// Public placeholder for ACI's first-provision use. Public images don't need
// imageRegistryCredentials (and ACI rejects the block when present with a
// public image), so we omit it conditionally below.
var aciPlaceholderImage = 'mcr.microsoft.com/azuredocs/aci-helloworld:latest'
var brokerImage = useImagePlaceholders ? aciPlaceholderImage : '${acrLoginServer}/tagpulse-mqtt:${imageTag}'

resource aci 'Microsoft.ContainerInstance/containerGroups@2023-05-01' = {
  name: containerGroupName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  properties: {
    osType: 'Linux'
    restartPolicy: 'Always'
    sku: 'Standard'
    // Pull from the private ACR using the UAMI. Only set when we're actually
    // pulling from ACR — public placeholders don't need (and reject)
    // imageRegistryCredentials.
    imageRegistryCredentials: useImagePlaceholders ? [] : [
      {
        server: acrLoginServer
        identity: userAssignedIdentityId
      }
    ]
    ipAddress: {
      type: 'Public'
      dnsNameLabel: dnsLabelPrefix
      ports: [
        {
          protocol: 'TCP'
          port: 1883
        }
      ]
    }
    containers: [
      {
        name: 'mosquitto'
        properties: {
          image: brokerImage
          ports: [
            { protocol: 'TCP', port: 1883 }
          ]
          resources: {
            requests: {
              cpu: 1
              memoryInGB: 1
            }
          }
          environmentVariables: [
            {
              name: 'MOSQUITTO_USERNAME'
              secureValue: mqttUsername
            }
            {
              name: 'MOSQUITTO_PASSWORD'
              secureValue: mqttPassword
            }
          ]
        }
      }
    ]
  }
}

output fqdn string = aci.properties.ipAddress.fqdn
output mqttUrl string = 'mqtt://${aci.properties.ipAddress.fqdn}:1883'
