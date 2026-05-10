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

@description('Sprint 28 C6 — when true, open port 8883 on the ACI and inject the TLS material below into the container. Default false so the same Bicep deploys without certificates.')
param mqttTlsEnabled bool = false

@description('PEM-encoded CA bundle for the broker TLS listener. Ignored when mqttTlsEnabled=false.')
@secure()
param mqttTlsCa string = ''

@description('PEM-encoded server certificate for the broker. Ignored when mqttTlsEnabled=false.')
@secure()
param mqttTlsCert string = ''

@description('PEM-encoded server private key for the broker. Ignored when mqttTlsEnabled=false.')
@secure()
param mqttTlsKey string = ''

// Public placeholder for ACI's first-provision use. Public images don't need
// imageRegistryCredentials (and ACI rejects the block when present with a
// public image), so we omit it conditionally below.
var aciPlaceholderImage = 'mcr.microsoft.com/azuredocs/aci-helloworld:latest'
var brokerImage = useImagePlaceholders ? aciPlaceholderImage : '${acrLoginServer}/tagpulse-mqtt:${imageTag}'

// Sprint 28 C6 — port + env-var lists are assembled here so the resource
// body stays linear. When TLS is enabled the entrypoint writes the cert
// material to /mosquitto/config and drops an `include_dir` fragment that
// opens 8883; the 1883 listener stays online for one sprint to give the
// fleet a no-coordination cutover window.
var aciPorts = mqttTlsEnabled ? [
  { protocol: 'TCP', port: 1883 }
  { protocol: 'TCP', port: 8883 }
] : [
  { protocol: 'TCP', port: 1883 }
]

var containerPorts = aciPorts

var baseEnvVars = [
  {
    name: 'MOSQUITTO_USERNAME'
    secureValue: mqttUsername
  }
  {
    name: 'MOSQUITTO_PASSWORD'
    secureValue: mqttPassword
  }
]
var tlsEnvVars = mqttTlsEnabled ? [
  {
    name: 'MOSQUITTO_TLS_CA'
    secureValue: mqttTlsCa
  }
  {
    name: 'MOSQUITTO_TLS_CERT'
    secureValue: mqttTlsCert
  }
  {
    name: 'MOSQUITTO_TLS_KEY'
    secureValue: mqttTlsKey
  }
] : []
var allEnvVars = concat(baseEnvVars, tlsEnvVars)

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
      ports: aciPorts
    }
    containers: [
      {
        name: 'mosquitto'
        properties: {
          image: brokerImage
          ports: containerPorts
          resources: {
            requests: {
              cpu: 1
              memoryInGB: 1
            }
          }
          environmentVariables: allEnvVars
        }
      }
    ]
  }
}

output fqdn string = aci.properties.ipAddress.fqdn
output mqttUrl string = 'mqtt://${aci.properties.ipAddress.fqdn}:1883'
output mqttTlsUrl string = mqttTlsEnabled ? 'mqtts://${aci.properties.ipAddress.fqdn}:8883' : ''
