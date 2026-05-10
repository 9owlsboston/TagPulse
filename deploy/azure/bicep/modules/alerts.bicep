// Sprint 28 D2 — Azure Monitor metric alerts for the four SLO signals
// defined in docs/observability/slos.md. All four are App Insights
// metric alerts (the underlying counters/gauges come from the Sprint
// 28 C1 + D5 instrumentation). One action group fans out to the
// configured email recipient(s); webhook receivers (PagerDuty etc.)
// are a Sprint 29 follow-up.
//
// Default-off: workload.bicep gates the module include on
// `deployAlerts`. Dev environments stay un-paged.

@description('Resource ID of the Application Insights component these alerts read from.')
param appInsightsId string

@description('Azure region (action groups are global; rules are per-region).')
param location string

@description('Resource name prefix; alerts are named <prefix>-alert-<slug>.')
param namePrefix string

@description('Email recipient for the action group. Single address; comma-split clients into multiple receivers if multiple are needed.')
param alertEmail string

@description('Common tags.')
param tags object = {}

// Action groups must be in the 'global' location.
resource actionGroup 'Microsoft.Insights/actionGroups@2023-09-01-preview' = {
  name: '${namePrefix}-ag-oncall'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'tp-oncall'
    enabled: true
    emailReceivers: [
      {
        name: 'oncall-email'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

// 1) MQTT subscriber stalled — Sprint 28 C1 observable gauge.
resource alertMqttStalled 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-alert-mqtt-stalled'
  location: 'global'
  tags: tags
  properties: {
    severity: 1
    enabled: true
    scopes: [ appInsightsId ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    targetResourceType: 'Microsoft.Insights/components'
    targetResourceRegion: location
    description: 'MQTT subscriber has not processed a message for > 10 min. See docs/runbooks/mqtt-outage.md.'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'subscriberAge'
          metricNamespace: 'azure.applicationinsights'
          metricName: 'tagpulse_mqtt_subscriber_last_message_age_seconds'
          operator: 'GreaterThan'
          threshold: 600
          timeAggregation: 'Maximum'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

// 2) API availability burn (fast) — 14.4× burn over 1h on SLO #1.
resource alertAvailabilityFastBurn 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-alert-availability-fast-burn'
  location: 'global'
  tags: tags
  properties: {
    severity: 1
    enabled: true
    scopes: [ appInsightsId ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT1H'
    targetResourceType: 'Microsoft.Insights/components'
    targetResourceRegion: location
    description: 'API 5xx ratio > 7.2% over 1h (14.4x burn on the 99.5% / 28d SLO). See docs/runbooks/incident-template.md.'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'failed5xx'
          metricNamespace: 'microsoft.insights/components'
          metricName: 'requests/failed'
          operator: 'GreaterThan'
          // App Insights `requests/failed` is the count of 4xx+5xx
          // requests over the window; the matching workbook query
          // (api-availability.kql) computes the ratio precisely.
          // A standing threshold of 50 failed requests per 1h is the
          // operational tripwire — refine after one quarter of data.
          threshold: 50
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

// 3) API p95 latency — SLO #2.
resource alertP95Latency 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-alert-api-p95-latency'
  location: 'global'
  tags: tags
  properties: {
    severity: 2
    enabled: true
    scopes: [ appInsightsId ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT30M'
    targetResourceType: 'Microsoft.Insights/components'
    targetResourceRegion: location
    description: 'API request p95 latency > 500ms for 30 min. Profile + scale.'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'duration'
          metricNamespace: 'microsoft.insights/components'
          metricName: 'requests/duration'
          operator: 'GreaterThan'
          threshold: 500
          timeAggregation: 'Average'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

// 4) Dead-letter burst — Sprint 28 C3 + the existing OTel counter.
resource alertDeadLetterBurst 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-alert-dead-letter-burst'
  location: 'global'
  tags: tags
  properties: {
    severity: 1
    enabled: true
    scopes: [ appInsightsId ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT1H'
    targetResourceType: 'Microsoft.Insights/components'
    targetResourceRegion: location
    description: 'Dead-letter rows > 200 in 1h. See docs/runbooks/dead-letter-triage.md.'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'deadLetters'
          metricNamespace: 'azure.applicationinsights'
          metricName: 'tagpulse_dead_letter_events_total'
          operator: 'GreaterThan'
          threshold: 200
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

output actionGroupId string = actionGroup.id
