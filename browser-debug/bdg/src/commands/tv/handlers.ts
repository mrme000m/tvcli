/**
 * Shared handler for `bdg tv` commands that run a canned in-page script and
 * format the result. Centralizes the `domEval` + `runCommand` boilerplate.
 */

import { runCommand } from '@/commands/shared/CommandRunner.js';
import type { BaseOptions } from '@/commands/shared/optionTypes.js';
import { domEval } from '@/ipc/client.js';
import { EXIT_CODES } from '@/utils/exitCodes.js';

/**
 * Evaluate a TradingView script in the page and format the result.
 *
 * @param script - JS expression (from `scripts.ts`) run via `domEval`.
 * @param options - CLI options (must carry the `--json` flag).
 * @param formatter - Human-readable formatter for the non-JSON path.
 * @param empty - Fallback value when the page returns nothing.
 */
export async function runTvEval<T>(
  script: string,
  options: BaseOptions,
  formatter: (data: T) => string,
  empty: T
): Promise<void> {
  await runCommand(
    async () => {
      const resp = await domEval(script);
      if (resp.status === 'error' || !resp.data) {
        return {
          success: false,
          error: resp.error ?? 'Failed to evaluate script in page (is a TradingView chart open?)',
          exitCode: EXIT_CODES.CDP_CONNECTION_FAILURE,
        };
      }
      return { success: true, data: (resp.data.value ?? empty) as T };
    },
    options,
    formatter
  );
}
