/**
 * `bdg tv study` — add any script to the chart with custom input values,
 * read computed study values, and remove studies.
 *
 * This is the frontend-facing counterpart to the go client's study runs:
 * where the tvcli Go client drives studies over the
 * WebSocket API headlessly, these commands drive the LIVE chart widget so the
 * analysis is visible on the chart. Subcommands:
 *
 * - `add <name> [--inputs '{"length": 20}']` — add an indicator/strategy/
 *   event overlay by display name with custom input overrides (the same
 *   `createStudy(name, false, false, [{id, value}])` path the community MCP
 *   servers attempted, verified live here).
 * - `values [--filter <name>]` — read the latest computed values of every
 *   study on the chart (plot metadata + last row buffer).
 * - `graphics [--filter <name>]` — read Pine-drawn graphics (line.new /
 *   label.new / box.new / table.new) per study, the same surface the community
 *   MCP servers read with `data_get_pine_lines`/`labels`/`boxes`/`tables`.
 * - `remove <entityId>` — remove a study/entity by id from `tv studies`.
 */

import { Option, type Command } from 'commander';

import { jsonOption } from '@/commands/shared/commonOptions.js';
import type { BaseOptions } from '@/commands/shared/optionTypes.js';
import { runTvEval } from '@/commands/tv/handlers.js';
import {
  buildStudyAddScript,
  buildStudyInputsScript,
  buildStudyRemoveScript,
  STUDY_GRAPHICS_SCRIPT,
  STUDY_VALUES_SCRIPT,
} from '@/commands/tv/scripts.js';
import {
  formatTvGraphics,
  formatTvStudyAdd,
  formatTvStudyInputs,
  formatTvStudyRemove,
  formatTvStudyValues,
  type TvGraphicsResult,
  type TvStudyAddResult,
  type TvStudyInputsResult,
  type TvStudyRemoveResult,
  type TvStudyValuesResult,
} from '@/ui/formatters/tv.js';

interface TvStudyAddOptions extends BaseOptions {
  inputs: string;
  pine: string;
}

interface TvStudyValuesOptions extends BaseOptions {
  filter: string;
}

interface TvStudyGraphicsOptions extends BaseOptions {
  filter: string;
}

export function registerTvStudyCommand(tvCmd: Command): void {
  const studyCmd = tvCmd
    .command('study')
    .description('Add/read/remove studies on the live chart (custom input values)');

  studyCmd
    .command('add <name>')
    .description('Add an indicator/strategy (by display name, or --pine for a saved/public script)')
    .addOption(jsonOption())
    .addOption(
      new Option('-i, --inputs <json>', 'Input overrides as JSON, e.g. \'{"length": 20}\'').default(
        '{}'
      )
    )
    .addOption(
      new Option(
        '-p, --pine <id>',
        'Add by Pine id (USER;… / PUB;…) — works for any saved/public script, not just built-ins'
      ).default('')
    )
    .action(async (name: string, options: TvStudyAddOptions) => {
      await runTvEval<TvStudyAddResult>(
        buildStudyAddScript(name, options.inputs, options.pine || undefined),
        options,
        formatTvStudyAdd,
        { before: [], added: [], createResult: null }
      );
    });

  studyCmd
    .command('values')
    .description('Read latest computed values of every study on the chart')
    .addOption(jsonOption())
    .addOption(
      new Option('-f, --filter <name>', 'Only show studies whose name contains <name>').default('')
    )
    .action(async (options: TvStudyValuesOptions) => {
      await runTvEval<TvStudyValuesResult>(
        STUDY_VALUES_SCRIPT,
        options,
        (data: TvStudyValuesResult) => formatTvStudyValues(data, options.filter),
        { chart: '', studyCount: 0, studies: [] }
      );
    });

  studyCmd
    .command('graphics')
    .description('Read Pine-drawn graphics (lines/labels/boxes/tables) of every study')
    .addOption(jsonOption())
    .addOption(
      new Option('-f, --filter <name>', 'Only show studies whose name contains <name>').default('')
    )
    .action(async (options: TvStudyGraphicsOptions) => {
      await runTvEval<TvGraphicsResult>(
        STUDY_GRAPHICS_SCRIPT,
        options,
        (data: TvGraphicsResult) => formatTvGraphics(data, options.filter),
        { chart: '', studyCount: 0, studies: [] }
      );
    });

  studyCmd
    .command('inputs <entityId>')
    .description("List a study's current input ids/values (authoritative in_N mapping)")
    .addOption(jsonOption())
    .action(async (entityId: string, options: BaseOptions) => {
      await runTvEval<TvStudyInputsResult>(
        buildStudyInputsScript(entityId),
        options,
        formatTvStudyInputs,
        { id: entityId, count: 0, inputs: [] }
      );
    });

  studyCmd
    .command('remove <entityId>')
    .description('Remove a study/entity by id (from `tv studies`)')
    .addOption(jsonOption())
    .action(async (entityId: string, options: BaseOptions) => {
      await runTvEval<TvStudyRemoveResult>(
        buildStudyRemoveScript(entityId),
        options,
        formatTvStudyRemove,
        { removed: false }
      );
    });
}
