# tvcli — TradingView Pine Script CLI
#
# Common tasks. No external tooling required beyond the Go toolchain.

.PHONY: build test vet lint skills run clean

BINARY := tvcli

build:
	go build -o $(BINARY) ./cmd/tvcli

test:
	go test ./...

vet:
	go vet ./...

# `staticcheck` is optional; install with `go install honnef.co/go/tools/cmd/staticcheck@latest`.
lint: vet
	@command -v staticcheck >/dev/null 2>&1 && staticcheck ./... || echo "staticcheck not installed; ran go vet only"

skills:
	go run ./cmd/tvcli skills

# List the 21 registered indicator skills as JSON (includes tier / category /
# knownBroken metadata added by the skill-command improvements).
skills-json:
	go run ./cmd/tvcli skills --json

run: build
	./$(BINARY) $(ARGS)

clean:
	rm -f $(BINARY)
