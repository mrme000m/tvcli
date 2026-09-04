# Realtime (SSE subscriptions)

PocketBase's realtime is **Server-Sent Events**. By default it emits events
only for **record create/update/delete** (and OAuth2 redirects). That default
is what makes "write a record == publish an event" true — the persistence
layer doubles as the event stream for CRUD-shaped state.

## Client subscribe

```js
import PocketBase from 'pocketbase';
const pb = new PocketBase('http://127.0.0.1:8090');

// subscribe to a collection — fires on that collection's record CRUD
await pb.realtime.subscribe('decisions', (e) => console.log(e));

// subscribe to a custom topic
await pb.realtime.subscribe('grid.tick', (e) => console.log(e));
```

(Dart: `pb.realtime.subscribe('decisions', (e) => print(e));`.)

## Server-side: custom messages via the broker

To push **arbitrary** events (not tied to record CRUD), use
`$app.subscriptionsBroker()`:

```js
const message = new SubscriptionMessage({
  name: "grid.tick",
  data: JSON.stringify({ cycle: "...", slot: 1, action: "rotate" }),
});
const clients = $app.subscriptionsBroker().clients(); // id-indexed map
for (let clientId in clients) {
  if (clients[clientId].hasSubscription("grid.tick")) {
    clients[clientId].send(message);
  }
}
```

- `clients()` returns all connected `subscriptions.Client`s keyed by
  connection id.
- A client's auth record: `client.get("auth")`.
- A single authenticated user can hold **multiple** connections (multiple
  tabs/devices) — each is a separate client in the map.

## Server-side: realtime hooks

- `onRealtimeConnectRequest` — on SSE connect.
- `onRealtimeSubscribeRequest` — on subscription change; can validate/rewrite
  `e.subscriptions`.
- `onRealtimeMessageSend` — before a message is sent to a client.

These let you gate who can subscribe to what, and shape outbound messages.

## For the grid-autonomy integration

Two viable event paths:

1. **CRUD-driven (zero custom code):** the daemon writes journal/decision
   records; any dashboard or listener just subscribes to those collections and
   receives create/update/delete events.
2. **Broker-driven (custom topics):** for non-record signals (e.g. a
   "heartbeat", a guardrail veto that doesn't map to a persisted record yet),
   a JS hook calls `subscriptionsBroker()` to fan out to `grid.*` topics.

Prefer path 1 wherever state is already a record; reserve path 2 for
transient/derived signals.