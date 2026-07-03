import { DataSourcePlugin } from '@grafana/data';
import { SketchLogDataSource } from './datasource';
import { ConfigEditor } from './ConfigEditor';
import { QueryEditor } from './QueryEditor';
import { SketchLogDataSourceOptions, SketchLogQuery } from './types';

export const plugin = new DataSourcePlugin<SketchLogDataSource, SketchLogQuery, SketchLogDataSourceOptions>(SketchLogDataSource)
  .setConfigEditor(ConfigEditor)
  .setQueryEditor(QueryEditor);
