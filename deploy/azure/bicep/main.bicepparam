// Bicep parameters file. Secrets resolved from environment variables at deploy time —
// never committed. Use:
//   export AZURE_POSTGRES_ADMIN_PASSWORD=...
//   export AZURE_JWT_SECRET=...
//   export AZURE_MQTT_PASSWORD=...
//   az deployment sub create --location southcentralus --template-file main.bicep \
//     --parameters main.bicepparam

using 'main.bicep'

param location = 'southcentralus'
param resourceGroupName = 'tagpulse-rg'
param namePrefix = 'tagpulse'
param imageTag = readEnvironmentVariable('AZURE_IMAGE_TAG', 'latest')
param postgresAdminUsername = 'tagpulse_admin'
param postgresAdminPassword = readEnvironmentVariable('AZURE_POSTGRES_ADMIN_PASSWORD')
param jwtSecret = readEnvironmentVariable('AZURE_JWT_SECRET')
param mqttUsername = 'tagpulse'
param mqttPassword = readEnvironmentVariable('AZURE_MQTT_PASSWORD')
param staticWebAppLocation = 'centralus'

param tags = {
  workload: 'tagpulse'
  managedBy: 'bicep'
  environment: readEnvironmentVariable('AZURE_ENV_NAME', 'production')
}
