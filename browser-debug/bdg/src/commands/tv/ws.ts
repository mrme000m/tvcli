/**
 * `bdg tv ws` — capture TradingView's WebSocket (chart-data) protocol.
 *
 * Installs a `WebSocket` wrapper via `Page.addScriptToEvaluateOnNewDocument`,
 * reloads the page so the data socket reconnects through the wrapper, waits,
 * reads the captured frames (parsed in-page), then removes the probe. This
 * surfaces the `~m~<len>~m~<json>` protocol: `set_auth_token`,
 * `chart_create_session`, `resolve_symbol`, `create_series`, `create_study`,
 * `du`/`timescale_update`, `study_completed`, teardown, heartbeats.
 *
 * The page-level CDP target persists across navigation, so bdg stays attached
 * after the reload. Probe frames are capped large enough to keep `du` payloads
 * intact for study-series key extraction.
 */

import { Option, type Command } from 'commander';

import { runCommand } from '@/commands/shared/CommandRunner.js';
import { jsonOption } from '@/commands/shared/commonOptions.js';
import type { BaseOptions } from '@/commands/shared/optionTypes.js';
import { buildWsReadScript, WS_CLEAR_SCRIPT, WS_PROBE_SCRIPT } from '@/commands/tv/scripts.js';
import { callCDP, domEval } from '@/ipc/client.js';
import { formatTvWs, type TvWsResult } from '@/ui/formatters/tv.js';
import { EXIT_CODES } from '@/utils/exitCodes.js';

interface TvWsOptions extends BaseOptions {
  seconds: string;
  reload: boolean;
  full: boolean;
}

const DEFAULT_SECONDS = 8;

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/** Remove the new-document probe and clear the page global. Best-effort. */
async function cleanupProbe(identifier: string | undefined): Promise<void> {
  if (identifier) {
    await callCDP('Page.removeScriptToEvaluateOnNewDocument', { identifier }).catch(() => {});
  }
  await domEval(WS_CLEAR_SCRIPT).catch(() => {});
}

export function registerTvWsCommand(tvCmd: Command): void {
  tvCmd
    .command('ws')
    .description('Capture TradingView WebSocket protocol (auth, chart session, studies, du)')
    .addOption(jsonOption())
    .addOption(
      new Option('-s, --seconds <n>', 'Seconds to capture after reload').default(
        String(DEFAULT_SECONDS)
      )
    )
    .addOption(new Option('--no-reload', 'Do not reload; only capture frames after attach'))
    .addOption(new Option('--full', 'Include raw frame payloads per connection'))
    .action(async (options: TvWsOptions) => {
      await runCommand(
        async () => {
          // 1. Install the WebSocket probe to run on every new document.
          const installResp = await callCDP('Page.addScriptToEvaluateOnNewDocument', {
            source: WS_PROBE_SCRIPT,
          });
          if (installResp.status === 'error' || !installResp.data) {
            return {
              success: false,
              error: installResp.error ?? 'Failed to install WS probe',
              exitCode: EXIT_CODES.CDP_CONNECTION_FAILURE,
            };
          }
          const identifier = (installResp.data.result as { identifier?: string } | undefined)
            ?.identifier;

          // 2. Reload so the data socket reconnects through the probe.
          if (options.reload) {
            const reloadResp = await callCDP('Page.reload', {});
            if (reloadResp.status === 'error' || !reloadResp.data) {
              await cleanupProbe(identifier);
              return {
                success: false,
                error: reloadResp.error ?? 'Failed to reload page',
                exitCode: EXIT_CODES.CDP_CONNECTION_FAILURE,
              };
            }
          }

          // 3. Wait for the handshake + study restore + streaming.
          const seconds = Number(options.seconds) || DEFAULT_SECONDS;
          await sleep(seconds * 1000);

          // 4. Read the captured frames (parsed in-page to keep output compact).
          const readResp = await domEval(buildWsReadScript(options.full));
          if (readResp.status === 'error' || !readResp.data) {
            await cleanupProbe(identifier);
            return {
              success: false,
              error: readResp.error ?? 'Failed to read WS capture',
              exitCode: EXIT_CODES.CDP_CONNECTION_FAILURE,
            };
          }
          const result = (readResp.data.value ?? { connections: [], summary: {} }) as TvWsResult;

          // 5. Remove the probe + clear the global.
          await cleanupProbe(identifier);

          return { success: true, data: result };
        },
        options,
        (data: TvWsResult) => formatTvWs(data)
      );
    });
}
