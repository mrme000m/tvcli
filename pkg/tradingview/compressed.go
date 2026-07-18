package tradingview

import (
	"archive/zip"
	"bytes"
	"compress/flate"
	"compress/gzip"
	"compress/zlib"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"strings"
)

// parseCompressed decodes a TradingView compressed strategy report payload.
// The payload is base64-encoded (URL-safe variant); the decoded bytes may be
// a zip archive, zlib stream, raw DEFLATE stream, gzip stream, or plain JSON.
// Returns the parsed JSON object, trying each format in turn.
//
// Mirrors Protocol.parseCompressed in tv-optimized.cjs / TradingView-API-main.
func parseCompressed(dataB64 string) (map[string]any, error) {
	decoded, err := decodeBase64URLSafe(dataB64)
	if err != nil {
		return nil, fmt.Errorf("base64 decode: %w", err)
	}

	// Try in order: raw JSON, zip, zlib, raw flate, gzip.
	if m, err := tryParseJSON[map[string]any](decoded); err == nil {
		return m, nil
	}
	if m, err := parseCompressedZip(decoded); err == nil {
		return m, nil
	}
	if m, err := parseCompressedZlib(decoded); err == nil {
		return m, nil
	}
	if m, err := parseCompressedFlate(decoded); err == nil {
		return m, nil
	}
	if m, err := parseCompressedGzip(decoded); err == nil {
		return m, nil
	}
	return nil, fmt.Errorf("no decompression format matched (%d bytes)", len(decoded))
}

// decodeBase64URLSafe decodes a URL-safe base64 string (with - and _) into bytes,
// adding standard padding. Returns the decoded bytes.
func decodeBase64URLSafe(s string) ([]byte, error) {
	s = strings.ReplaceAll(s, "-", "+")
	s = strings.ReplaceAll(s, "_", "/")
	if mod := len(s) % 4; mod != 0 {
		s += strings.Repeat("=", 4-mod)
	}
	return base64.StdEncoding.DecodeString(s)
}

func tryParseJSON[T any](b []byte) (T, error) {
	var v T
	if err := json.Unmarshal(b, &v); err != nil {
		return v, err
	}
	return v, nil
}

// parseCompressedZip handles a zip archive with a single file (JSZip format).
func parseCompressedZip(b []byte) (map[string]any, error) {
	zr, err := zip.NewReader(bytes.NewReader(b), int64(len(b)))
	if err != nil {
		return nil, err
	}
	if len(zr.File) == 0 {
		return nil, fmt.Errorf("zip: no files")
	}
	// Prefer the first file (JSZip's archive.file(/.*/)[0]).
	f := zr.File[0]
	rc, err := f.Open()
	if err != nil {
		return nil, err
	}
	defer rc.Close()
	data, err := io.ReadAll(rc)
	if err != nil {
		return nil, err
	}
	var m map[string]any
	if err := json.Unmarshal(data, &m); err != nil {
		return nil, err
	}
	return m, nil
}

func parseCompressedZlib(b []byte) (map[string]any, error) {
	r, err := zlib.NewReader(bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	defer r.Close()
	data, err := io.ReadAll(r)
	if err != nil {
		return nil, err
	}
	var m map[string]any
	if err := json.Unmarshal(data, &m); err != nil {
		return nil, err
	}
	return m, nil
}

func parseCompressedFlate(b []byte) (map[string]any, error) {
	r := flate.NewReader(bytes.NewReader(b))
	defer r.Close()
	data, err := io.ReadAll(r)
	if err != nil {
		return nil, err
	}
	var m map[string]any
	if err := json.Unmarshal(data, &m); err != nil {
		return nil, err
	}
	return m, nil
}

func parseCompressedGzip(b []byte) (map[string]any, error) {
	r, err := gzip.NewReader(bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	defer r.Close()
	data, err := io.ReadAll(r)
	if err != nil {
		return nil, err
	}
	var m map[string]any
	if err := json.Unmarshal(data, &m); err != nil {
		return nil, err
	}
	return m, nil
}
