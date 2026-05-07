{{/*
Common helpers for the TagPulse chart.
*/}}

{{- define "tagpulse.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tagpulse.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "tagpulse.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "tagpulse.labels" -}}
app.kubernetes.io/name: {{ include "tagpulse.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "tagpulse.selectorLabels" -}}
app.kubernetes.io/name: {{ include "tagpulse.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "tagpulse.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "tagpulse.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Image reference for a given component (api, worker, migrations).
*/}}
{{- define "tagpulse.image" -}}
{{- $component := index . 0 -}}
{{- $values := index . 1 -}}
{{- printf "%s/%s/tagpulse-%s:%s" $values.image.registry $values.image.repository $component $values.image.tag -}}
{{- end -}}

{{/*
Common env vars shared by api + worker. Secrets are pulled from the
existing Secret named in .Values.secrets.existing.
*/}}
{{- define "tagpulse.commonEnv" -}}
- name: ENVIRONMENT
  value: {{ .Values.environment | quote }}
- name: LOG_LEVEL
  value: {{ .Values.config.logLevel | quote }}
- name: CORS_ORIGINS
  value: {{ .Values.config.corsOrigins | quote }}
- name: GEOFENCE_EVALUATION_ENABLED
  value: {{ .Values.config.geofenceEvaluationEnabled | quote }}
- name: RATE_LIMIT_ENABLED
  value: {{ .Values.config.rateLimitEnabled | quote }}
- name: STRICT_MIGRATION_CHECK
  value: {{ .Values.config.strictMigrationCheck | quote }}
- name: INGEST_CLOCK_ENFORCE
  value: {{ .Values.config.ingestClockEnforce | quote }}
- name: MQTT_BROKER_HOST
  value: {{ .Values.mqtt.broker.host | quote }}
- name: MQTT_BROKER_PORT
  value: {{ .Values.mqtt.broker.port | quote }}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existing }}
      key: database-url
- name: JWT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existing }}
      key: jwt-secret
{{- end -}}
