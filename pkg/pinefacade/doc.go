// Package pinefacade is an HTTP client for TradingView's Pine Facade API.
// It compiles Pine source, saves/retrieves/deletes private scripts, fetches
// compiled IL (what the WebSocket study protocol needs), searches the public
// script library, and validates symbols.
//
// All functions take credentials as arguments (cookie header + username), so
// the client is safe to use with multiple accounts (see pkg/account).
//
// Typical flow for running a script by ID:
//
//	client := pinefacade.NewClient(baseURL, userName, timeout)
//	res, err := client.Get(pineID, "last", cookieHeader) // compiled IL + metaInfo
package pinefacade