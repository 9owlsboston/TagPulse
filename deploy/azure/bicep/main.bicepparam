// Bicep parameters file. Secrets resolved from environment variables at deploy time —
// never committed. Use:
//   export AZURE_POSTGRES_ADMIN_PASSWORD=...
//   export AZURE_JWT_SECRET=...
//   export AZURE_MQTT_PASSWORD=...
//   az deployment sub create --location southcentralus --template-file main.bicep \
//     --parameters main.bicepparam

using 'main.bicep'

param location = readEnvironmentVariable('AZURE_LOCATION', 'southcentralus')
param resourceGroupName = readEnvironmentVariable('AZURE_RESOURCE_GROUP', 'tagpulse-rg')
param namePrefix = readEnvironmentVariable('AZURE_NAME_PREFIX', 'tagpulse')
param imageTag = readEnvironmentVariable('AZURE_IMAGE_TAG', 'latest')
param postgresAdminUsername = 'tagpulse_admin'
param postgresAdminPassword = readEnvironmentVariable('AZURE_POSTGRES_ADMIN_PASSWORD')
param jwtSecret = readEnvironmentVariable('AZURE_JWT_SECRET')
param mqttUsername = readEnvironmentVariable('AZURE_MQTT_USERNAME', 'tagpulse')
param mqttPassword = readEnvironmentVariable('AZURE_MQTT_PASSWORD')
param staticWebAppLocation = 'centralus'
param appEnvironment = readEnvironmentVariable('TAGPULSE_ENVIRONMENT', 'production')
param corsOriginsExtra = readEnvironmentVariable('CORS_ORIGINS_EXTRA', 'http://localhost:5173')
param corsOriginRegexOverride = readEnvironmentVariable('CORS_ORIGIN_REGEX', '')
param keyVaultNameSuffix = readEnvironmentVariable('AZURE_KV_NAME_SUFFIX', '')
param useImagePlaceholders = bool(readEnvironmentVariable('AZURE_USE_IMAGE_PLACEHOLDERS', 'false'))
// Sprint 23 Phase B -- both off by default. Set both to true once you've run
// scripts/azd-network-check.sh and reviewed docs/runbooks/sprint-23-network-cutover.md.
param enableVnetIntegration = bool(readEnvironmentVariable('AZURE_ENABLE_VNET', 'false'))
param disablePublicNetworkAccess = bool(readEnvironmentVariable('AZURE_DISABLE_PUBLIC_NETWORK_ACCESS', 'false'))

// Sprint 28 C6 — TLS on the broker. Default off. Set AZURE_MQTT_TLS_ENABLED=true
// + the three cert env vars after seeding the matching KV secrets.
param mqttTlsEnabled = bool(readEnvironmentVariable('AZURE_MQTT_TLS_ENABLED', 'false'))
param mqttTlsCa = readEnvironmentVariable('AZURE_MQTT_TLS_CA', '')
param mqttTlsCert = readEnvironmentVariable('AZURE_MQTT_TLS_CERT', '')
param mqttTlsKey = readEnvironmentVariable('AZURE_MQTT_TLS_KEY', '')

// Sprint 28 D2 — alerts. Default off in dev. Staging/prod flip via env.
param deployAlerts = bool(readEnvironmentVariable('AZURE_DEPLOY_ALERTS', 'false'))
param alertEmail = readEnvironmentVariable('AZURE_ALERT_EMAIL', '')

param tags = {
  workload: 'tagpulse'
  managedBy: 'bicep'
  environment: readEnvironmentVariable('TAGPULSE_ENVIRONMENT', 'production')
  azdEnvironment: readEnvironmentVariable('AZURE_ENV_NAME', 'tagpulse-prod')
}
