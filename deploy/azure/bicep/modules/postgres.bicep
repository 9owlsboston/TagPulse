// Azure Database for PostgreSQL Flexible Server — TimescaleDB via azure.extensions.
// Public access enabled in v1 with firewall allow-all (replace with private endpoint in
// hardening sprint). Burstable B1ms is the cheapest production-eligible SKU (~$15/mo).

@description('Postgres Flexible Server name (globally unique, 3–63 chars).')
param serverName string

@description('Azure region.')
param location string

@description('Postgres admin username.')
param adminUsername string

@description('Postgres admin password.')
@secure()
param adminPassword string

@description('Database name to create.')
param databaseName string = 'tagpulse'

@description('Server SKU name. Default Standard_B1ms = 1 vCPU / 2 GB RAM (Burstable).')
param skuName string = 'Standard_B1ms'

@description('SKU tier: Burstable | GeneralPurpose | MemoryOptimized.')
@allowed([
  'Burstable'
  'GeneralPurpose'
  'MemoryOptimized'
])
param skuTier string = 'Burstable'

@description('Storage size in GiB. Min 32.')
@minValue(32)
param storageSizeGb int = 32

@description('Postgres major version.')
@allowed([
  '14'
  '15'
  '16'
])
param postgresVersion string = '16'

@description('Common tags.')
param tags object = {}

resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: serverName
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: skuTier
  }
  properties: {
    version: postgresVersion
    administratorLogin: adminUsername
    administratorLoginPassword: adminPassword
    storage: {
      storageSizeGB: storageSizeGb
      autoGrow: 'Enabled'
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Enabled'
    }
    authConfig: {
      passwordAuth: 'Enabled'
      activeDirectoryAuth: 'Disabled'
    }
  }
}

// Allow Azure services (ACA, ACI) — replace with private endpoint in hardening sprint.
resource fwAllowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: pg
  name: 'AllowAllAzureIPs'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// TimescaleDB extension allow-list. Must be set before the DB itself runs CREATE EXTENSION.
resource extConfig 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2024-08-01' = {
  parent: pg
  name: 'azure.extensions'
  properties: {
    value: 'TIMESCALEDB,UUID-OSSP,PGCRYPTO,PG_TRGM'
    source: 'user-override'
  }
}

resource sharedPreloadConfig 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2024-08-01' = {
  parent: pg
  name: 'shared_preload_libraries'
  properties: {
    value: 'timescaledb'
    source: 'user-override'
  }
  dependsOn: [
    extConfig
  ]
}

resource db 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: pg
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
  dependsOn: [
    sharedPreloadConfig
    fwAllowAzure
  ]
}

output serverName string = pg.name
output fqdn string = pg.properties.fullyQualifiedDomainName
output databaseName string = db.name
output adminUsername string = adminUsername
