// PocketBase migration: create the `recommendations` collection.
//
// The position-optimizer lane (position_optimizer.py + pbclient.recommendation)
// persists advisory revaluation recommendations here, and the mission console
// reads them back via GET /api/recommendations. The 1700000000 migration never
// created this collection, so on deployed hosts every persist 404'd
// ("Missing collection context") while the engine silently counted the failed
// writes toward its per-day cap — the console always showed an empty list.
//
// Runs once, automatically on `serve` (like its predecessor). Field names
// mirror the recommendation record schema built by make_recommendation() +
// analyze_bot(); pbclient renames the engine's `id` to `recommendation_id`
// because PocketBase reserves `id`.

migrate(
  (app) => {
    const rules = {
      listRule: "@request.auth.id != ''",
      viewRule: "@request.auth.id != ''",
      createRule: "@request.auth.id != ''",
      updateRule: "@request.auth.id != ''",
      deleteRule: null,
    };
    const collection = new Collection({
      type: "base",
      name: "recommendations",
      ...rules,
      fields: [
        { name: "recommendation_id", type: "text" }, // engine uuid (id renamed)
        { name: "at", type: "text" },
        { name: "slot", type: "text" },
        { name: "venue", type: "text" },
        { name: "symbol", type: "text" },
        { name: "bot_code", type: "text" },
        { name: "status", type: "text" },
        { name: "trigger", type: "text" },
        { name: "recommendation", type: "text" },
        { name: "expected_delta_pct", type: "number" },
        { name: "confidence", type: "number" },
        { name: "rationale", type: "text" },
        { name: "price", type: "number" },
        { name: "atr_pct", type: "number" },
        { name: "regime", type: "text" },
        { name: "spread_pct", type: "number" },
        { name: "revalue", type: "json" },
        { name: "exit_profile", type: "json" },
        { name: "action", type: "json" },
        { name: "tvcli_structure", type: "json" },
        { name: "applied", type: "bool" },
        { name: "applied_at", type: "text" },
        { name: "dry_run", type: "bool" },
      ],
    });
    let exists = false;
    try {
      app.findCollectionByNameOrId(collection.name);
      exists = true;
    } catch (e) {
      exists = false;
    }
    if (!exists) {
      app.save(collection);
    }
  },
  (app) => {
    // revert: drop the collection (best-effort)
    try {
      const c = app.findCollectionByNameOrId("recommendations");
      app.delete(c);
    } catch (e) {
      // already gone
    }
  }
);
