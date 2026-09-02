/**
 * `bdg tv drawings` — list drawings ("line tools") on the active TradingView
 * chart and report the drawing layer's autosave/action capabilities.
 *
 * Drawings are detected via line-tool markers (`_lineToolType` / `isLineTool` /
 * `_properties.lineToolType`). The chart's `_lineToolsSynchronizer` is the
 * service that autosaves drawings to the saved-layout REST endpoint (drawings
 * are NOT streamed over the chart-data WebSocket).
 */

import { type Command } from 'commander';

import { jsonOption } from '@/commands/shared/commonOptions.js';
import type { BaseOptions } from '@/commands/shared/optionTypes.js';
import { runTvEval } from '@/commands/tv/handlers.js';
import { DRAWINGS_SCRIPT } from '@/commands/tv/scripts.js';
import { formatTvDrawings, type TvDrawingsResult } from '@/ui/formatters/tv.js';

export function registerTvDrawingsCommand(tvCmd: Command): void {
  tvCmd
    .command('drawings')
    .description('List drawings (line tools) on the active chart + drawing-layer capabilities')
    .addOption(jsonOption())
    .action(async (options: BaseOptions) => {
      await runTvEval<TvDrawingsResult>(DRAWINGS_SCRIPT, options, formatTvDrawings, {
        chart: '',
        drawingCount: 0,
        drawings: [],
        lineToolSynchronizer: false,
        canExecuteActions: false,
      });
    });
}
