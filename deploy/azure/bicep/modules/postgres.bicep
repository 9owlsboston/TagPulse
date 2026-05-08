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

@description('Postgres major version. Pinned to 15 because Azure Database for PostgreSQL Flexible Server removed support for the open-source TimescaleDB extension starting with PG 16 — `CREATE EXTENSION timescaledb` on PG 16 causes the backend to terminate the connection mid-statement (asyncpg surfaces it as ConnectionDoesNotExistError). PG 15 is in active support through Nov 2027.')
@allowed([
  '14'
  '15'
  '16'
])
param postgresVersion string = '15'

@description('Common tags.')
param tags object = {}

@description('When true, sets network.publicNetworkAccess=Disabled and SKIPS the AllowAllAzureIPs firewall rule. Sprint 23 Phase B — only safe when a private endpoint is wired in. Default false preserves Sprint 22 behaviour.')
param disablePublicNetworkAccess bool = false

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
      publicNetworkAccess: disablePublicNetworkAccess ? 'Disabled' : 'Enabled'
    }
    authConfig: {
      passwordAuth: 'Enabled'
      activeDirectoryAuth: 'Disabled'
    }
  }
}

// Allow Azure services (ACA, ACI). Sprint 23 Phase B replaces this with a
// private endpoint; when disablePublicNetworkAccess=true we skip this rule
// because the firewall is unreachable anyway and the API would reject it.
resource fwAllowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = if (!disablePublicNetworkAccess) {
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
    // Must include Azure Flexible Server defaults (`pg_cron`, `pg_stat_statements`)
    // alongside `timescaledb` — this property OVERWRITES the value rather than
    // appending. Setting it to just `timescaledb` causes Postgres to start without
    // any of the platform-managed libraries, and `CREATE EXTENSION timescaledb`
    // then drops the client connection mid-statement (asyncpg surfaces it as
    // `ConnectionDoesNotExistError: connection was closed in the middle of operation`).
    // shared_preload_libraries is a static GUC; the Flex server requires a restart
    // for changes to take effect.
    value: 'pg_cron,pg_stat_statements,timescaledb'
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
output id string = pg.id
