package pinefacade

type ScriptResult struct {
	Source   string
	Meta     *ScriptMeta
	MetaInfo map[string]any // Full metaInfo from /translate/ (inputs, plots, etc.)
}

type ScriptMeta struct {
	ScriptName string
	Version    string
	Created    string
	Updated    string
}

type CompileResult struct {
	Success *bool         `json:"success"`
	Result  *CompileInner `json:"result,omitempty"`
	Reason  string        `json:"reason,omitempty"`
}

type CompileInner struct {
	Errors  []CompileError `json:"errors,omitempty"`
	Version string         `json:"version,omitempty"`
}

type CompileError struct {
	Start   *Position `json:"start,omitempty"`
	End     *Position `json:"end,omitempty"`
	Message string    `json:"message,omitempty"`
}

type Position struct {
	Line   int `json:"line"`
	Column int `json:"column"`
}

type SaveResponse struct {
	PineID  string `json:"pineId,omitempty"`
	Version string `json:"version,omitempty"`
	Success *bool  `json:"success,omitempty"`
	Reason  string `json:"reason,omitempty"`
	Raw     any
}

type SearchResult struct {
	Results []SearchItem `json:"results"`
	Next    string       `json:"next,omitempty"`
}

type SearchItem struct {
	ScriptIDPart string       `json:"scriptIdPart"`
	Title        string       `json:"title"`
	ScriptName   string       `json:"scriptName"`
	Author       SearchAuthor `json:"author"`
	Type         string       `json:"type"`
	Access       string       `json:"access"`
	Version      string       `json:"version,omitempty"`
}

type SearchAuthor struct {
	ID       any    `json:"id"`
	Username string `json:"username"`
}
