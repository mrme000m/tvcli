# dsh-restart

A small DeepSeek Harness plugin that adds a `/restart` slash command. It
relaunches the running `dsh` process with the exact launch command, so plugins
added or removed with `dsh plugin add` / `dsh plugin remove` take effect without
manually stopping and restarting the app.

## Install

```sh
dsh plugin --profile web add "link:/Users/m/.dsh/plugins/dsh-restart"
```

Then restart once (plugins mount at startup); afterwards `/restart` handles
future plugin changes.

## How it works

`/restart` captures the launch command once at mount (`process.execPath`,
`process.execArgv`, the entry script, and the app arguments), spawns a detached
replacement that sleeps 4 seconds before booting, and then sends `SIGTERM` to
the current process. The launcher's `SIGTERM` handler disposes the tree — which
flushes the session log — and exits 0, releasing the port before the replacement
binds it.

## Known limitations

- POSIX only: the replacement is launched through `sh -c 'sleep …; exec "$@"'`.
- The port handoff relies on the graceful shutdown finishing within the 4-second
  delay; a pathological tree that hits the launcher's 5-second forced-exit
  backstop could race the replacement's bind.
- `/restart` only makes sense on long-lived surfaces (web, TUI), not the
  one-shot `headless` profile.