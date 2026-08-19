// Package tradingview implements the TradingView WebSocket protocol client:
// connection and authentication, chart sessions, and the Pine study
// lifecycle (create/remove studies, collect period data, graphics, and
// strategy reports).
//
// This is the transport layer that powers the tvcli CLI. Import it to run
// Pine studies from your own programs:
//
//	client := tradingview.NewClient(
//	    tradingview.WithToken(acct.SessionID),
//	    tradingview.WithSignature(acct.Signature),
//	    tradingview.WithDeviceToken(acct.DeviceToken),
//	)
//	if err := client.Connect(); err != nil { ... }
//	ch := tradingview.NewChartSession(client)
//	ch.SetMarket("OANDA:XAUUSD", map[string]any{"timeframe": "1H", "range": 180})
//	study := ch.Study(indicator) // *tradingview.PineIndicator
//
// The client is credential-agnostic: credentials arrive as ClientOptions, so
// one program can hold clients for many accounts (see pkg/account).
//
// Higher-level orchestration (retry on study-limit, schema-guided parsing)
// lives in pkg/runner; the HTTP-side Pine Facade API is in pkg/pinefacade.
package tradingview