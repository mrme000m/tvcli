// PocketBase migration: create the grid-autonomy collections.
//
// Runs once, automatically on `serve` (or `migrate up`). Each collection
// mirrors a daemon file artifact so pbclient.py has a schema to write into.
// Field names align with pbclient.py's write-through shapes.
//
// Reference: .agents/skills/pocketbase/SKILL.md + references/grid-integration.md

migrate(
  (app) => {
    // Internal ops collections: authenticated-only. Reads/writes require a
    // valid token (superuser or the daemon's API-key record); deletes stay
    // disabled (daemon never deletes — state is append/upsert only).
    const rules = {
      listRule: "@request.auth.id != ''",
      viewRule: "@request.auth.id != ''",
      createRule: "@request.auth.id != ''",
      updateRule: "@request.auth.id != ''",
      deleteRule: null,
    };
    const collections = [
      new Collection({
        type: "base",
        name: "journal",
        ...rules,
        fields: [
          { name: "kind", type: "text" },
          { name: "msg", type: "text" },
          { name: "at", type: "text" },
          { name: "slot", type: "text" },
          { name: "cycle", type: "text" },
          { name: "extra", type: "json" },
        ],
      }),
      new Collection({
        type: "base",
        name: "decisions",
        ...rules,
        fields: [
          { name: "decision_id", type: "text" }, // daemon decision id dYYYYMMDD-NNN
          { name: "at", type: "text" },
          { name: "symbol", type: "text" },
          { name: "venue", type: "text" },
          { name: "regime", type: "text" },
          { name: "grid_type", type: "text" },
          { name: "decision", type: "text" },
          { name: "score_final", type: "number" },
          { name: "step_pct", type: "number" },
          { name: "slot", type: "text" },
          { name: "action", type: "text" },
          { name: "rationale", type: "text" },
          { name: "risk_multipliers", type: "json" },
          { name: "llm_degraded", type: "bool" },
          { name: "stagnation_policy", type: "text" },
          { name: "channel", type: "text" },
          { name: "payload_digest", type: "text" },
          { name: "outcome", type: "json" },
        ],
      }),
      new Collection({
        type: "base",
        name: "reliability",
        ...rules,
        fields: [
          { name: "ledger", type: "json" },
          { name: "saved_at", type: "text" },
        ],
      }),
      new Collection({
        type: "base",
        name: "bots",
        ...rules,
        fields: [
          { name: "slot", type: "text" },
          { name: "code", type: "text" },
          { name: "status", type: "text" },
          { name: "spec", type: "json" },
        ],
      }),
      new Collection({
        type: "base",
        name: "slots",
        ...rules,
        fields: [
          { name: "slot", type: "text" },
          { name: "plan", type: "json" },
        ],
      }),
      new Collection({
        type: "base",
        name: "market_cache",
        ...rules,
        fields: [
          { name: "source", type: "text" },
          { name: "payload", type: "json" },
        ],
      }),
      new Collection({
        type: "base",
        name: "run_cards",
        ...rules,
        fields: [
          { name: "kind", type: "text" },
          { name: "at", type: "text" },
          { name: "md", type: "text" },
        ],
      }),
    ];

    for (const collection of collections) {
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
    }
  },
  (app) => {
    // revert: drop the collections (best-effort)
    for (const name of [
      "journal",
      "decisions",
      "reliability",
      "bots",
      "slots",
      "market_cache",
      "run_cards",
    ]) {
      try {
        const c = app.findCollectionByNameOrId(name);
        app.delete(c);
      } catch (e) {
        // already gone
      }
    }
  }
);