import React from 'react';
import { InlineField, Input, SecretInput } from '@grafana/ui';
import { DataSourcePluginOptionsEditorProps } from '@grafana/data';
import { SketchLogDataSourceOptions, SketchLogSecureJsonData } from './types';

export function ConfigEditor(props: DataSourcePluginOptionsEditorProps<SketchLogDataSourceOptions, SketchLogSecureJsonData>) {
  const { options, onOptionsChange } = props;
  const jsonData = options.jsonData || {};

  return (
    <>
      <InlineField label="Endpoint" labelWidth={20} tooltip="SketchLog HTTP origin, for example http://localhost:8000">
        <Input
          width={48}
          value={jsonData.endpoint || ''}
          placeholder="http://localhost:8000"
          onChange={(event) => onOptionsChange({ ...options, jsonData: { ...jsonData, endpoint: event.currentTarget.value } })}
        />
      </InlineField>
      <InlineField label="Default namespace" labelWidth={20}>
        <Input
          width={32}
          value={jsonData.defaultNamespace || 'default'}
          placeholder="default"
          onChange={(event) => onOptionsChange({ ...options, jsonData: { ...jsonData, defaultNamespace: event.currentTarget.value } })}
        />
      </InlineField>
      <InlineField label="Auth token" labelWidth={20} tooltip="Optional X-SketchLog-Auth-Token value. Frontend-only plugins cannot store this as a Grafana secure secret; use only in trusted self-hosted Grafana deployments.">
        <SecretInput
          width={48}
          value={jsonData.authToken ? 'configured' : ''}
          placeholder="optional"
          isConfigured={Boolean(jsonData.authToken)}
          onReset={() => onOptionsChange({ ...options, jsonData: { ...jsonData, authToken: undefined } })}
          onChange={(event) => onOptionsChange({
            ...options,
            jsonData: { ...jsonData, authToken: event.currentTarget.value },
          })}
        />
      </InlineField>
    </>
  );
}
