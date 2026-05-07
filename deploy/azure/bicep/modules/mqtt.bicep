// Mosquitto MQTT broker on Azure Container Instances — v1 single-node, no HA.
// ~$15/mo. Replace with EMQX Cloud or AKS-hosted EMQX for production HA (deferred).
// Persistent volume for retained messages via Azure Files.

@description('ACI container group name.')
param containerGroupName string

@description('Storage account name backing the persistent Files share. Must be globally unique, 3–24 lowercase alphanumeric.')
param storageAccountName string

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

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    publicNetworkAccess: 'Enabled'
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}

resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: 'mosquitto-data'
  properties: {
    accessTier: 'Hot'
    shareQuota: 5
  }
}

resource configShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: 'mosquitto-config'
  properties: {
    accessTier: 'Hot'
    shareQuota: 1
  }
}

// NOTE: ACI cannot inject files into a volume on first boot. The mosquitto-config share
// must be seeded once (post-deployment) with mosquitto.conf and a password file generated
// via `mosquitto_passwd`. See deploy/azure/README.md → "Bootstrap MQTT broker".

resource aci 'Microsoft.ContainerInstance/containerGroups@2023-05-01' = {
  name: containerGroupName
  location: location
  tags: tags
  properties: {
    osType: 'Linux'
    restartPolicy: 'Always'
    sku: 'Standard'
    ipAddress: {
      type: 'Public'
      dnsNameLabel: dnsLabelPrefix
      ports: [
        {
          protocol: 'TCP'
          port: 1883
        }
        {
          protocol: 'TCP'
          port: 8883
        }
      ]
    }
    containers: [
      {
        name: 'mosquitto'
        properties: {
          image: 'eclipse-mosquitto:2'
          ports: [
            { protocol: 'TCP', port: 1883 }
            { protocol: 'TCP', port: 8883 }
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
              value: mqttUsername
            }
            {
              name: 'MOSQUITTO_PASSWORD'
              secureValue: mqttPassword
            }
          ]
          volumeMounts: [
            {
              name: 'data'
              mountPath: '/mosquitto/data'
            }
            {
              name: 'config'
              mountPath: '/mosquitto/config'
            }
          ]
        }
      }
    ]
    volumes: [
      {
        name: 'data'
        azureFile: {
          shareName: share.name
          storageAccountName: storage.name
          storageAccountKey: storage.listKeys().keys[0].value
        }
      }
      {
        name: 'config'
        azureFile: {
          shareName: configShare.name
          storageAccountName: storage.name
          storageAccountKey: storage.listKeys().keys[0].value
        }
      }
    ]
  }
}

output fqdn string = aci.properties.ipAddress.fqdn
output mqttUrl string = 'mqtt://${aci.properties.ipAddress.fqdn}:1883'
output storageAccountName string = storage.name
