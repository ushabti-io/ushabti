{{/* Chart name, optionally overridden. */}}
{{- define "ushabti.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 40 | trimSuffix "-" }}
{{- end }}

{{/* Full name of the release's resources. */}}
{{- define "ushabti.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 40 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 40 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 40 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/* Labels every resource carries. */}}
{{- define "ushabti.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ include "ushabti.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Labels that select every pod of the release. */}}
{{- define "ushabti.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ushabti.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Name of the ServiceAccount the ushabti run as. */}}
{{- define "ushabti.serviceAccountName" -}}
{{- if .Values.agent.serviceAccount.create }}
{{- default (include "ushabti.fullname" .) .Values.agent.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.agent.serviceAccount.name }}
{{- end }}
{{- end }}

{{/* Name of the overseer's Deployment and Service. */}}
{{- define "ushabti.overseerName" -}}
{{- printf "%s-overseer" (include "ushabti.fullname" .) }}
{{- end }}

{{/* The admin token as an environment variable read from an existing Secret. */}}
{{- define "ushabti.adminTokenEnv" -}}
{{- if .Values.adminToken.existingSecret }}
- name: USHABTI_ADMIN_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ .Values.adminToken.existingSecret | quote }}
      key: {{ .Values.adminToken.key | quote }}
{{- end }}
{{- end }}

{{/*
Refuse combinations of values that cannot work. Every message says what to
change. This runs for helm lint and helm template as well as for installs.
*/}}
{{- define "ushabti.validate" -}}
{{- $count := len .Values.ushabti }}
{{- if and (eq .Values.mode "solo") (ne $count 1) }}
{{- fail (printf "mode is solo, which runs exactly one ushabti, but `ushabti` lists %d. Use mode independent or overseen for several." $count) }}
{{- end }}
{{- if and (eq .Values.mode "overseen") (not .Values.adminToken.existingSecret) }}
{{- fail "mode is overseen, which needs the admin token: set adminToken.existingSecret to the name of a Secret that holds it." }}
{{- end }}
{{- $seen := dict }}
{{- range .Values.ushabti }}
{{- if hasKey $seen .name }}
{{- fail (printf "two ushabti are both named %s. Each needs its own name." .name) }}
{{- end }}
{{- $_ := set $seen .name true }}
{{- if not (hasKey $.Values.spells .spell) }}
{{- fail (printf "ushabti %s runs the spell %s, which is not in `spells`." .name .spell) }}
{{- end }}
{{- end }}
{{- end }}
