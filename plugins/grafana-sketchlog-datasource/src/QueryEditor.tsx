import React from 'react';
import { DataSourcePluginOptionsEditorProps, QueryEditorProps } from '@grafana/data';
import { InlineField, Input, Select } from '@grafana/ui';
import { SketchLogDataSource } from './datasource';
import { SketchLogDataSourceOptions, SketchLogQuery, SketchLogQueryFunction } from './types';

const functionOptions: Array<{ label: string; value: SketchLogQueryFunction }> = [
  { label: 'p50(stream)', value: 'p50' },
  { label: 'p95(stream)', value: 'p95' },
  { label: 'p99(stream)', value: 'p99' },
  { label: 'unique_count(stream)', value: 'unique_count' },
  { label: 'event_count(stream, event)', value: 'event_count' },
  { label: 'slo_burn_rate(stream)', value: 'slo_burn_rate' },
  { label: 'SQL', value: 'sql' },
];

type Props = QueryEditorProps<SketchLogDataSource, SketchLogQuery, SketchLogDataSourceOptions>;

export function QueryEditor(props: Props) {
  const { query, onChange, onRunQuery } = props;
  const functionName = query.functionName || 'p99';

  const update = (patch: Partial<SketchLogQuery>) => {
    onChange({ ...query, ...patch });
    onRunQuery();
  };

  return (
    <>
      <InlineField label="Function" labelWidth={16}>
        <Select
          width={32}
          value={functionOptions.find((option) => option.value === functionName)}
          options={functionOptions}
          onChange={(option) => update({ functionName: option.value })}
        />
      </InlineField>
      {functionName === 'sql' ? (
        <InlineField label="SQL" labelWidth={16} grow>
          <Input
            value={query.sql || ''}
            placeholder="SELECT p99(latency) FROM api.latency"
            onChange={(event) => update({ sql: event.currentTarget.value })}
          />
        </InlineField>
      ) : (
        <>
          <InlineField label="Namespace" labelWidth={16}>
            <Input
              width={24}
              value={query.namespace || ''}
              placeholder="default"
              onChange={(event) => update({ namespace: event.currentTarget.value })}
            />
          </InlineField>
          <InlineField label="Stream" labelWidth={16}>
            <Input
              width={32}
              value={query.stream || ''}
              placeholder="api.latency"
              onChange={(event) => update({ stream: event.currentTarget.value })}
            />
          </InlineField>
          {functionName === 'event_count' && (
            <InlineField label="Event" labelWidth={16}>
              <Input
                width={24}
                value={query.eventName || ''}
                placeholder="errors"
                onChange={(event) => update({ eventName: event.currentTarget.value })}
              />
            </InlineField>
          )}
          {functionName === 'slo_burn_rate' && (
            <InlineField label="Baseline" labelWidth={16}>
              <Input
                width={32}
                value={query.baselineStream || ''}
                placeholder="api.latency.baseline"
                onChange={(event) => update({ baselineStream: event.currentTarget.value })}
              />
            </InlineField>
          )}
        </>
      )}
    </>
  );
}

export type SketchLogConfigEditorProps = DataSourcePluginOptionsEditorProps<SketchLogDataSourceOptions>;
