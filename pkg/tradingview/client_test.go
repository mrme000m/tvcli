package tradingview

import (
	"testing"
)

// TestWSClientImplementsClient is a compile-time + runtime guard that the
// concrete WSClient satisfies the new Client interface so service.RunScript
// and service.FetchOHLCVBars can be table-tested with a fake implementation.
func TestWSClientImplementsClient(t *testing.T) {
	var _ Client = (*WSClient)(nil)
}
