package tradingview

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
)

var (
	splitterRegex = strings.NewReader
	cleanerStr    = "~h~"
)

type Protocol struct{}

func (Protocol) ParseWSPacket(data string) []any {
	cleaned := strings.ReplaceAll(data, cleanerStr, "")
	var result []any

	for {
		idx := strings.Index(cleaned, "~m~")
		if idx < 0 {
			break
		}

		afterM := cleaned[idx+3:]
		endIdx := strings.Index(afterM, "~m~")
		if endIdx < 0 {
			break
		}

		lenStr := afterM[:endIdx]
		msgLen, err := strconv.Atoi(lenStr)
		if err != nil {
			cleaned = afterM[endIdx+3:]
			continue
		}

		contentStart := idx + 3 + endIdx + 3
		contentEnd := contentStart + msgLen
		if contentEnd > len(cleaned) {
			contentEnd = len(cleaned)
		}

		content := cleaned[contentStart:contentEnd]
		cleaned = cleaned[contentEnd:]

		if content == "" {
			continue
		}

		var parsed any
		if err := json.Unmarshal([]byte(content), &parsed); err != nil {
			if n, err2 := strconv.Atoi(content); err2 == nil {
				result = append(result, n)
			}
			continue
		}
		result = append(result, parsed)
	}

	return result
}

func (Protocol) FormatWSPacket(packet any) string {
	var msg string
	switch v := packet.(type) {
	case string:
		msg = v
	default:
		b, _ := json.Marshal(v)
		msg = string(b)
	}
	return fmt.Sprintf("~m~%d~m~%s", len(msg), msg)
}

type WSMessage struct {
	Type string `json:"m"`
	Data []any  `json:"p"`
}
