# Authentication

Fully stateless: a client is authenticated as long as it sends a valid
`Authorization: <YOUR_AUTH_TOKEN>` header. No server sessions.

## Password (identity/password)

Must enable the **Identity/Password** option on an Auth collection (default
identity field is `email`; can be any UNIQUE field like `username`).

```js
const pb = new PocketBase('http://127.0.0.1:8090');
const authData = await pb.collection('users').authWithPassword('test@example.com', '1234567890');
console.log(pb.authStore.isValid);  // true
console.log(pb.authStore.token);    // -> use as Authorization header
console.log(pb.authStore.record.id);
pb.authStore.clear();               // logout
```

## OTP (one-time password)

Enable the OTP option; flow: request code → user receives email → validate.
`requestOTP()` returns an `otpId` (even for unknown emails, as enumeration
protection). On validation the email is auto-verified. OTP alone is weaker
(0-9 digit codes, guessable) — pair with MFA for security-critical apps.

## API keys (machine clients)

For non-human / server-to-server clients, PocketBase supports per-record
API-key auth — the right choice for the grid-autonomy daemon talking to the
backend (no interactive login).

## OAuth2

`authWithOAuth2()` for external providers; OAuth2 redirects also emit realtime
events.

## Auth hooks (server-side validation)

- `onRecordAuthRequest`, `onRecordAuthRefreshRequest`,
  `onRecordAuthWithPasswordRequest`, `onRecordAuthWithOTPRequest` — fire on
  the corresponding auth API requests; allow extra validation or a custom
  identity lookup (`e.record` may be null, reassign to locate a different
  record).

## Realtime auth

The current auth record for a realtime connection is accessible server-side
via `client.get("auth")` in `$app.subscriptionsBroker().clients()`.