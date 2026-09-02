/**
 * `bdg tv` command group registration.
 *
 * TradingView-specialized debugging/exploration commands, designed for agents
 * extending existing tools or building new ones against TradingView's backend
 * (WebSocket chart-data protocol) and frontend (chart widget / drawings):
 *
 * - `ws`      — capture the WebSocket protocol (auth, chart session, studies, du)
 * - `studies` — list studies (indicators/strategies/event studies) on the chart
 * - `drawings`— list drawings (line tools) + drawing-layer capabilities
 * - `chart`   — summarize the active chart (URL, main series, dataSources)
 *
 * Requires an active bdg session attached to a TradingView chart page.
 */

import type { Command } from 'commander';

import { registerTvChartCommand } from '@/commands/tv/chart.js';
import { registerTvDrawingsCommand } from '@/commands/tv/drawings.js';
import { registerTvStudiesCommand } from '@/commands/tv/studies.js';
import { registerTvStudyCommand } from '@/commands/tv/study.js';
import { registerTvWsCommand } from '@/commands/tv/ws.js';

export function registerTvCommands(program: Command): void {
  const tvCmd = program
    .command('tv')
    .description('TradingView debugging/exploration (WebSocket protocol, studies, drawings)');

  registerTvWsCommand(tvCmd);
  registerTvStudiesCommand(tvCmd);
  registerTvStudyCommand(tvCmd);
  registerTvDrawingsCommand(tvCmd);
  registerTvChartCommand(tvCmd);
}
