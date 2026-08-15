# Open Knowledge Maintenance Rules

## General Rules

- Always validate knowledge edits with `openknowledge validate Wiki` before committing.
- Use `openknowledge search Wiki "query"` to find relevant context before making changes.
- Treat the repository and knowledge base as source evidence; do not invent facts.
- Respect publication boundaries. Insights must always set `okf_publish: false`.

## Capturing Insights

When you discover a knowledge gap or durable insight:
1. Create an insight with evidence:
   ```bash
   openknowledge insights create "<summary>" --target <knowledge-path> --evidence "<source-grounded evidence>"
   ```
2. The insight will be saved under `Wiki/insights/` for later processing.

## Working with the Knowledge Base

- Read files: `openknowledge get Wiki` or `openknowledge get Wiki <path>`
- Search: `openknowledge search Wiki "<query>"` or `openknowledge search Wiki "<query>" --budget 1200`
- List structure: `openknowledge list Wiki`
- Validate: `openknowledge validate Wiki`
- View in browser: `openknowledge view Wiki`

## Publishing

- Insights are always private (`okf_publish: false`).
- Only publish content that has been reviewed and marked for publication.
- Use `openknowledge export html --out ./site Wiki` to create a static site.
