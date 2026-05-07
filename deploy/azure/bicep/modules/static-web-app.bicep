// Static Web App for the TagPulse-UI React SPA. Free tier is sufficient for v1.
// The UI repo (TagPulse-UI) deploys via its own GHA workflow into this resource by
// referencing the deployment token output here.

@description('Static Web App name.')
param siteName string

@description('Azure region. SWA is only available in select regions; default to westus2 as a safe regional fallback.')
@allowed([
  'westus2'
  'centralus'
  'eastus2'
  'westeurope'
  'eastasia'
  'eastasiastage'
])
param location string = 'centralus'

@description('Backend API URL to inject into the SPA at build time (optional — UI repo can also read from runtime config).')
param apiUrl string = ''

@description('Common tags.')
param tags object = {}

resource swa 'Microsoft.Web/staticSites@2024-04-01' = {
  name: siteName
  location: location
  tags: tags
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {
    allowConfigFileUpdates: true
    stagingEnvironmentPolicy: 'Enabled'
    enterpriseGradeCdnStatus: 'Disabled'
  }
}

resource appSettings 'Microsoft.Web/staticSites/config@2024-04-01' = if (!empty(apiUrl)) {
  parent: swa
  name: 'appsettings'
  properties: {
    VITE_API_BASE_URL: apiUrl
  }
}

output id string = swa.id
output name string = swa.name
output defaultHostname string = swa.properties.defaultHostname
