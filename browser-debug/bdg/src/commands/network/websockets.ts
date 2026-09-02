/**
 * `bdg network websockets` — list captured WebSocket connections and frames.
 *
 * Surfaces the `websockets[]` held in the telemetry store. Connections are
 * added live (on creation, see the `startWebSocketCollection` fix), so
 * long-lived sockets — e.g. TradingView's chart-data stream — are visible
 * without waiting for them to close or the session to stop.
 *
 * Frames are captured only if the socket opened after `Network.enable`. To
 * capture an already-open stream, reload the page under the active session,
 * or use `bdg tv ws` for an in-page probe that captures the full handshake.
 */

import { Option, type Command } from 'commander';

import { runCommand } from '@/commands/shared/CommandRunner.js';
import { jsonOption } from '@/commands/shared/commonOptions.js';
import type { BaseOptions } from '@/commands/shared/optionTypes.js';
import { getWebsockets } from '@/ipc/client.js';
import type { WebSocketConnection } from '@/types.js';
import { OutputFormatter } from '@/ui/formatting.js';
import { EXIT_CODES } from '@/utils/exitCodes.js';

interface NetworkWebsocketsOptions extends BaseOptions {
  verbose: boolean;
  last: string;
}

interface WebsocketsResult {
  connections: WebSocketConnection[];
}

function formatWebsockets(data: WebsocketsResult, options: NetworkWebsocketsOptions): string {
  const fmt = new OutputFormatter();
  const conns = data.connections ?? [];

  if (conns.length === 0) {
    return fmt
      .text('No WebSocket connections captured.')
      .blank()
      .section('Why this might be empty:', [
        'No WebSocket traffic on the page.',
        'The socket opened before bdg attached (CDP only reports sockets opened after Network.enable).',
        'Reload the page under the active session, or use `bdg tv ws` for an in-page probe.',
      ])
      .build();
  }

  fmt.text(`${conns.length} WebSocket connection(s):`).blank();
  fmt.list(
    conns.map((c, i) => {
      const inN = c.frames.filter((f) => f.direction === 'received').length;
      const outN = c.frames.filter((f) => f.direction === 'sent').length;
      const state = c.closedTime ? 'closed' : 'open';
      return `[${i}] ${state} | in ${inN} | out ${outN} | ${c.url}`;
    })
  );

  if (options.verbose) {
    const lastN = Number(options.last) || 0;
    conns.forEach((c, i) => {
      fmt.blank().text(`[${i}] ${c.url}`);
      const frames = lastN > 0 ? c.frames.slice(-lastN) : c.frames;
      frames.forEach((f) => {
        const dir = f.direction === 'sent' ? '→' : '←';
        const payload =
          f.payloadData.length > 200 ? f.payloadData.slice(0, 200) + '...' : f.payloadData;
        fmt.text(`  ${dir} ${payload.replace(/\n/g, ' ')}`);
      });
    });
  }

  return fmt.build();
}

export function registerWebsocketsCommand(networkCmd: Command): void {
  networkCmd
    .command('websockets')
    .description('List captured WebSocket connections and frames')
    .addOption(jsonOption())
    .addOption(new Option('-v, --verbose', 'Show frame payloads').default(false))
    .addOption(
      new Option('--last <n>', 'Last N frames per connection (verbose, 0 = all)').default('20')
    )
    .action(async (options: NetworkWebsocketsOptions) => {
      await runCommand(
        async () => {
          const response = await getWebsockets();
          if (response.status === 'error' || !response.data) {
            return {
              success: false,
              error: response.error ?? 'No active session',
              exitCode: EXIT_CODES.CDP_CONNECTION_FAILURE,
            };
          }
          const connections = response.data.connections ?? [];
          return { success: true, data: { connections } };
        },
        options,
        (data: WebsocketsResult) => formatWebsockets(data, options)
      );
    });
}
