import { spawn } from "node:child_process"

export const OpenKnowledgeObserver = async ({ client, directory }) => ({
  event: async ({ event }) => {
    if (event?.type !== "session.idle" || process.env.OPENKNOWLEDGE_OBSERVER === "1") return
    const sessionID = event?.properties?.sessionID ?? event?.properties?.sessionId ?? event?.sessionID ?? event?.session_id
    let trace
    if (sessionID) {
      try {
        const response = await client.session.messages({ path: { id: sessionID } })
        trace = response?.data ?? response
      } catch {
        // Observation is best-effort and must never disrupt the parent session.
      }
    }
    const child = spawn("openknowledge", ["insights", "observe", "--runtime", "opencode"], {
      cwd: directory,
      detached: true,
      stdio: ["pipe", "ignore", "ignore"],
      env: { ...process.env, OPENKNOWLEDGE_HOOK: "1" },
    })
    child.stdin.end(JSON.stringify({ event, trace }))
    child.unref()
  },
})
