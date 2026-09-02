/**
 * `bdg tv studies` — list studies on the active TradingView chart.
 *
 * Reads the chart model's `dataSources()` and classifies each as `pine` (user
 * Pine script), `event` (earnings/dividends/splits overlays), or `other` (e.g.
 * volume profile). Built-in non-study sources (main series, crosshair, event
 * feeds) are excluded.
 */

import { type Command } from 'commander';

import { jsonOption } from '@/commands/shared/commonOptions.js';
import type { BaseOptions } from '@/commands/shared/optionTypes.js';
import { runTvEval } from '@/commands/tv/handlers.js';
import { STUDIES_SCRIPT } from '@/commands/tv/scripts.js';
import { formatTvStudies, type TvStudiesResult } from '@/ui/formatters/tv.js';

export function registerTvStudiesCommand(tvCmd: Command): void {
  tvCmd
    .command('studies')
    .description('List studies (indicators/strategies/event studies) on the active chart')
    .addOption(jsonOption())
    .action(async (options: BaseOptions) => {
      await runTvEval<TvStudiesResult>(STUDIES_SCRIPT, options, formatTvStudies, {
        chart: '',
        studyCount: 0,
        studies: [],
      });
    });
}
