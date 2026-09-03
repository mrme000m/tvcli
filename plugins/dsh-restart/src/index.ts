/**
 * `/restart` — relaunch the running dsh process so newly installed or removed
 * plugins load. Spawns a detached replacement with the exact launch command,
 * waits for the current process to release its port, then requests the
 * launcher's graceful shutdown (SIGTERM) so the session log flushes before
 * the replacement binds.
 *
 * @module dsh-restart
 */

import { spawn } from 'node:child_process'
import { resolve } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import type { CommandInvocation, CommandResult } from '@deepseek-ai/dsh-commands'

export const name = 'dsh-restart'
export const inject = ['commands']

/** Seconds the replacement waits before booting, so the current process releases its port first. */
const RELAUNCH_DELAY_SECONDS = 4

/** Milliseconds after the command result settles before the current process begins shutdown. */
const SHUTDOWN_GRACE_MS = 300

/** Usage text returned for any unexpected input. */
const USAGE = 'Usage: /restart (no arguments)'

/**
 * Register `/restart` for every composed human-command adapter.
 * @param ctx - context carrying the command registry.
 */
export function apply(ctx: Context): void {
  // Capture launch facts once at mount: a later chdir must not skew the relaunch.
  const execPath = process.execPath
  const execArgv = [...process.execArgv]
  const entry = resolve(process.argv[1])
  const args = [...process.argv.slice(2)]
  const cwd = process.cwd()
  const env = { ...process.env }

  let restarting = false

  ctx.effect(function* () {
    yield ctx.commands.register({
      name: 'restart',
      description: 'Restart the DSH process to reload plugins',
      handler: (invocation: CommandInvocation): CommandResult => {
        if (invocation.rawInput.trim().length > 0) return { kind: 'error', text: USAGE }
        if (restarting) return { kind: 'error', text: 'A restart is already in progress.' }
        restarting = true

        const command = [execPath, ...execArgv, entry, ...args]
        const child = spawn(
          'sh',
          ['-c', `sleep ${RELAUNCH_DELAY_SECONDS}; exec "$@"`, 'sh', ...command],
          { cwd, detached: true, stdio: 'inherit', env },
        )
        if (child.pid === undefined) {
          restarting = false
          return { kind: 'error', text: 'Could not start the replacement process; the current instance is still running.' }
        }

        let timer: ReturnType<typeof setTimeout> | undefined
        child.once('error', (error: unknown) => {
          if (timer !== undefined) clearTimeout(timer)
          restarting = false
          ctx.logger.warn(`dsh-restart: replacement process failed before exec: ${String(error)}`)
        })
        child.unref()

        // Let the result and its command/done record settle, then ask the
        // launcher's SIGTERM handler to dispose the tree and exit 0.
        timer = setTimeout(() => { process.kill(process.pid, 'SIGTERM') }, SHUTDOWN_GRACE_MS)

        return {
          kind: 'success',
          text: `Restarting… a fresh instance will boot in about ${RELAUNCH_DELAY_SECONDS} seconds.`,
        }
      },
    })
  }, 'dsh-restart lifecycle')
}