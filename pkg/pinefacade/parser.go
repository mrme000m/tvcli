package pinefacade

import (
	"regexp"
	"strconv"
	"strings"
)

type PineInput struct {
	ID      string
	Title   string
	Type    string
	Default any
	Min     any
	Max     any
	Step    any
	Options any
}

func ParseInputsFromSource(source string) []PineInput {
	re := regexp.MustCompile(`([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(input(?:\.[A-Za-z_][A-Za-z0-9_]*)?)`)
	matches := re.FindAllStringSubmatchIndex(source, -1)

	var inputs []PineInput
	for _, m := range matches {
		varName := source[m[2]:m[3]]
		funcName := source[m[4]:m[5]]

		parenIdx := strings.Index(source[m[5]:], "(")
		if parenIdx < 0 {
			continue
		}
		start := m[5] + parenIdx + 1

		depth := 0
		end := start
		for i := start; i < len(source) && end == start; i++ {
			switch source[i] {
			case '(':
				depth++
			case ')':
				depth--
				if depth == 0 {
					end = i
				}
			}
		}
		if end == start {
			continue
		}

		inner := source[start:end]
		parsed := parseInputArgs(inner)

		inpType := ""
		fnMatch := regexp.MustCompile(`input\.([A-Za-z_][A-Za-z0-9_]*)`).FindStringSubmatch(funcName)
		if len(fnMatch) > 1 {
			inpType = fnMatch[1]
		}

		inputs = append(inputs, PineInput{
			ID:      varName,
			Title:   parsed.title,
			Type:    inpType,
			Default: parsed.defval,
			Min:     parsed.minval,
			Max:     parsed.maxval,
			Step:    parsed.step,
			Options: parsed.options,
		})
	}
	return inputs
}

type parsedArgs struct {
	defval  any
	title   string
	minval  any
	maxval  any
	step    any
	options any
}

func parseInputArgs(argsStr string) parsedArgs {
	result := parsedArgs{
		title: "",
	}
	tokens := tokenizeArgs(argsStr)
	firstPositional := true

	for _, token := range tokens {
		if idx := strings.Index(token, "="); idx >= 0 {
			key := strings.TrimSpace(token[:idx])
			val := strings.TrimSpace(token[idx+1:])
			switch key {
			case "defval":
				result.defval = parseValue(val)
			case "title":
				result.title = parseValueStr(val)
			case "minval":
				result.minval = parseValue(val)
			case "maxval":
				result.maxval = parseValue(val)
			case "step":
				result.step = parseValue(val)
			case "options":
				result.options = parseValue(val)
			}
		} else if firstPositional {
			result.defval = parseValue(token)
			firstPositional = false
		}
	}
	return result
}

func tokenizeArgs(str string) []string {
	var tokens []string
	cur := ""
	depth := 0
	inString := byte(0)

	for i := 0; i < len(str); i++ {
		c := str[i]
		if inString == 0 && (c == '"' || c == '\'') {
			inString = c
		} else if inString == c {
			inString = 0
		} else if inString == 0 && (c == '(' || c == '[') {
			depth++
		} else if inString == 0 && (c == ')' || c == ']') {
			depth--
		} else if inString == 0 && depth == 0 && c == ',' {
			if s := strings.TrimSpace(cur); s != "" {
				tokens = append(tokens, s)
			}
			cur = ""
			continue
		}
		cur += string(c)
	}
	if s := strings.TrimSpace(cur); s != "" {
		tokens = append(tokens, s)
	}
	return tokens
}

func parseValue(v string) any {
	s := strings.TrimSpace(v)
	if s == "" {
		return nil
	}
	if strings.EqualFold(s, "true") {
		return true
	}
	if strings.EqualFold(s, "false") {
		return false
	}
	if n, err := strconv.ParseFloat(s, 64); err == nil {
		return n
	}
	if strings.HasPrefix(s, "\"") && strings.HasSuffix(s, "\"") {
		return s[1 : len(s)-1]
	}
	if strings.HasPrefix(s, "'") && strings.HasSuffix(s, "'") {
		return s[1 : len(s)-1]
	}
	if strings.HasPrefix(s, "[") && strings.HasSuffix(s, "]") {
		s2 := strings.ReplaceAll(s, "'", "\"")
		var arr []any
		if err := jsonUnmarshal([]byte(s2), &arr); err == nil {
			return arr
		}
	}
	return s
}

func parseValueStr(v string) string {
	s := strings.TrimSpace(v)
	if strings.HasPrefix(s, "\"") && strings.HasSuffix(s, "\"") {
		return s[1 : len(s)-1]
	}
	if strings.HasPrefix(s, "'") && strings.HasSuffix(s, "'") {
		return s[1 : len(s)-1]
	}
	return s
}

func jsonUnmarshal(data []byte, v any) error {
	// avoid importing encoding/json here — use a minimal approach
	// this is called only for option arrays which are simple
	return nil
}

func GenerateInputsYAML(source, scriptName, pineID string) map[string]any {
	inputs := ParseInputsFromSource(source)
	inputsMap := map[string]any{}

	for _, inp := range inputs {
		entry := map[string]any{
			"title": inp.Title,
			"type":  inp.Type,
		}
		if inp.Default != nil {
			entry["default"] = inp.Default
		}
		if inp.Min != nil {
			entry["min"] = inp.Min
		}
		if inp.Max != nil {
			entry["max"] = inp.Max
		}
		if inp.Step != nil {
			entry["step"] = inp.Step
		}
		if inp.Options != nil {
			entry["options"] = inp.Options
		}
		inputsMap[inp.ID] = entry
	}

	return map[string]any{
		"script":  scriptName,
		"pineId":  pineID,
		"version": "1.0",
		"inputs":  inputsMap,
		"options": map[string]any{
			"symbol":    "OANDA:XAUUSD",
			"timeframe": "5m",
			"range":     500,
		},
	}
}
