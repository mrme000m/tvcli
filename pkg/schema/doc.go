// Package schema parses TradingView Pine metaInfo (the JSON input/plot/style
// description that accompanies a compiled script) into a typed PineSchema:
// inputs, plots, styles, and semantic classifications. A schema enables
// schema-guided parsing of study output — resolving plot_N indices to
// human-readable names and classifying fields (price, signal, oscillator,
// volume) without statistical guessing.
//
//	schema.FromMetaInfo(pineID, metaInfo)  // → *schema.PineSchema
//
// Schemas also drive input validation and the universal analyzer's
// script-agnostic signal extraction (see pkg/pipeline).
package schema