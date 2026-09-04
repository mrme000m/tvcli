// PocketBase fan-out hooks for the grid-autonomy event-driven persistence layer.
//
// Hooks only — collection creation lives in pb_migrations/ (see
// 1700000000_grid_autonomy_collections.js). Place this in pb_hooks/ next to
// the binary. Loaded on every command, so keep it side-effect-free at load:
// only register hooks; do work inside the callbacks.
//
// IMPORTANT: record CRUD already emits realtime (SSE) events natively — a
// client subscribing to the `journal` or `decisions` collection receives every
// create/update automatically. We do NOT hand-roll a subscriptionsBroker()
// broadcast here: that method is a Go-side API not exposed to the JS runtime,
// and calling it throws inside the hook, which rolls back the write and turns
// a successful create into a 400. See references/event-hooks.md.
//
// Reference: .agents/skills/pocketbase/SKILL.md + references/grid-integration.md

// When a decision is persisted, stamp a defensive `at` so records written by
// callers that forget the timestamp still sort correctly in the feed.
onRecordAfterCreateSuccess(
  (e) => {
    const rec = e.record;
    if (!rec.get("at")) {
      rec.set("at", new Date().toISOString());
      $app.save(rec);
    }
    e.next();
  },
  "decisions"
);