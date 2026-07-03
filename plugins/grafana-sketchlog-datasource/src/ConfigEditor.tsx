import React from 'react';
import { InlineField, Input, Alert } from '@grafana/ui';
import { DataSourcePluginOptionsEditorProps } from '@grafana/data';
import { SketchLogDataSourceOptions } from './types';

export function ConfigEditor(props: DataSourcePluginOptionsEditorProps<SketchLogDataSourceOptions>) {
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
      <Alert title="Authentication" severity="info">
        This frontend-only data source does not store SketchLog auth tokens. Use it with an unauthenticated local endpoint,
        a trusted reverse proxy, or the existing Prometheus dashboard path. A future backend data source can add Grafana
        secureJsonData support for server-side token handling.
      </Alert>
    </>
  );
}
