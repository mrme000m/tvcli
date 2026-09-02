/**
 * `bdg tv chart` — summarize the active TradingView chart: URL/title, chart
 * id, main series, and all dataSources. A first "what is on this chart" probe.
 */

import { type Command } from 'commander';

import { jsonOption } from '@/commands/shared/commonOptions.js';
import type { BaseOptions } from '@/commands/shared/optionTypes.js';
import { runTvEval } from '@/commands/tv/handlers.js';
import { CHART_SCRIPT } from '@/commands/tv/scripts.js';
import { formatTvChart, type TvChartResult } from '@/ui/formatters/tv.js';

export function registerTvChartCommand(tvCmd: Command): void {
  tvCmd
    .command('chart')
    .description('Summarize the active chart (URL, symbol series, dataSources)')
    .addOption(jsonOption())
    .action(async (options: BaseOptions) => {
      await runTvEval<TvChartResult>(CHART_SCRIPT, options, formatTvChart, {
        url: '',
        title: '',
        chartId: null,
        mainSeries: null,
        dataSourceCount: 0,
        dataSources: [],
      });
    });
}
