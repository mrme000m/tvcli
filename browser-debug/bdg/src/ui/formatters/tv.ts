/**
 * Human-readable formatters for `bdg tv` command output.
 *
 * Each formatter renders the structured object returned by the in-page
 * scripts (see `src/commands/tv/scripts.ts`) as console text. `--json` output
 * bypasses these and serializes the raw object.
 */

import { OutputFormatter } from '@/ui/formatting.js';

export interface TvWsPacket {
  m: string;
  p0: unknown;
  pLen: number;
}

export interface TvWsConnection {
  url: string;
  closed: boolean;
  framesOut: number;
  framesIn: number;
  out: TvWsPacket[];
  in: TvWsPacket[];
  outRaw?: string[];
  inRaw?: string[];
}

export interface TvWsSummary {
  setAuthToken: number | false;
  chartCreateSession: unknown[];
  resolveSymbol: Array<{ ser: string; sym: unknown }>;
  createStudy: Array<{ sid: string; studyType: string }>;
  removeStudy: unknown[];
  studyCompleted: unknown[];
  duKeys: string[];
}

export interface TvWsResult {
  connections: TvWsConnection[];
  summary: TvWsSummary;
}

const arrow = (dir: 'out' | 'in'): string => (dir === 'out' ? '→' : '←');

/** Format the `bdg tv ws` capture: connection list + handshake summary + flow. */
export function formatTvWs(data: TvWsResult): string {
  const fmt = new OutputFormatter();
  const conns = data.connections ?? [];

  if (conns.length === 0) {
    return fmt
      .text('No WebSocket connections captured.')
      .blank()
      .section('Possible reasons:', [
        'No WebSocket traffic on the page.',
        'The socket opened before bdg attached (CDP only reports sockets opened after Network.enable).',
        'Run with --reload (default) so the page reconnects through the probe.',
      ])
      .build();
  }

  fmt.text(`TradingView WS capture: ${conns.length} connection(s)`).blank();

  conns.forEach((c, i) => {
    const state = c.closed ? 'closed' : 'open';
    fmt.text(`[${i}] ${state} | out ${c.framesOut} | in ${c.framesIn} | ${c.url}`);
  });

  const s = data.summary;
  fmt
    .blank()
    .text('Handshake summary')
    .keyValue('set_auth_token', s.setAuthToken === false ? 'not sent' : `${s.setAuthToken} chars`)
    .keyValue('chart_create_session', arrStr(s.chartCreateSession))
    .keyValue(
      'resolve_symbol',
      s.resolveSymbol.map((r) => `${r.ser} -> ${symStr(r.sym)}`).join(', ') || '-'
    )
    .keyValue(
      'create_study',
      s.createStudy.map((x) => `${x.sid} (${x.studyType})`).join(', ') || '-'
    )
    .keyValue('study_completed', arrStr(s.studyCompleted))
    .keyValue('remove_study', arrStr(s.removeStudy))
    .keyValue('du series keys', arrStr(s.duKeys));

  const primary = conns[0];
  if (primary) {
    fmt.blank().text('OUT flow (client -> server)');
    fmt.list(primary.out.map((p) => `${arrow('out')} ${p.m}`));
    fmt.blank().text('IN flow (server -> client)');
    fmt.list(primary.in.map((p) => `${arrow('in')} ${p.m}`));
    if (primary.outRaw || primary.inRaw) {
      fmt.blank().text('Raw frames (first connection):');
      if (primary.outRaw) fmt.section('OUT raw:', primary.outRaw.slice(0, 20), 2);
      if (primary.inRaw) fmt.section('IN raw:', primary.inRaw.slice(0, 20), 2);
    }
  }

  return fmt.build();
}

export interface TvStudy {
  id: string | null;
  name: string | null;
  kind: string;
  description: string | null;
  pineId: string | null;
  isStrategy: boolean;
}

export interface TvStudiesResult {
  chart: string;
  studyCount: number;
  studies: TvStudy[];
}

/** Format `bdg tv studies`: the studies on the active chart. */
export function formatTvStudies(data: TvStudiesResult): string {
  const fmt = new OutputFormatter();
  if (data.studyCount === 0) {
    return fmt.text('No studies on this chart.').build();
  }
  fmt
    .text(`${data.studyCount} stud${data.studyCount === 1 ? 'y' : 'ies'} on ${data.chart}:`)
    .blank();
  fmt.list(
    data.studies.map((s) => {
      const tag = s.isStrategy ? ' [strategy]' : '';
      const desc = s.description ? ` — ${s.description}` : '';
      const pine = s.pineId ? ` (pine: ${s.pineId})` : '';
      return `${s.id ?? '?'} | ${s.kind}${tag} | ${s.name ?? '?'}${desc}${pine}`;
    })
  );
  return fmt.build();
}

export interface TvDrawing {
  id: string | null;
  name: string | null;
  type: string | null;
}

export interface TvDrawingsResult {
  chart: string;
  drawingCount: number;
  drawings: TvDrawing[];
  lineToolSynchronizer: boolean;
  canExecuteActions: boolean;
}

/** Format `bdg tv drawings`: drawings ("line tools") on the active chart. */
export function formatTvDrawings(data: TvDrawingsResult): string {
  const fmt = new OutputFormatter();
  fmt.text(`${data.drawingCount} drawing(s) on ${data.chart}:`).blank();
  if (data.drawingCount === 0) {
    fmt.list(['(no user drawings — create a trendline/shape in the UI to populate)']);
  } else {
    fmt.list(data.drawings.map((d) => `${d.id ?? '?'} | ${d.type ?? '?'} | ${d.name ?? '?'}`));
  }
  fmt
    .blank()
    .text('Drawing layer:')
    .keyValue(
      'lineToolSynchronizer',
      data.lineToolSynchronizer ? 'present (drawings autosave via REST layout service)' : 'absent'
    )
    .keyValue(
      'executeActionById',
      data.canExecuteActions ? 'available (chart actions incl. drawing tools)' : 'unavailable'
    );
  return fmt.build();
}

export interface TvChartResult {
  url: string;
  title: string;
  chartId: string | null;
  mainSeries: { id: string | null; name: string | null } | null;
  dataSourceCount: number;
  dataSources: Array<{ id: string | null; name: string | null }>;
}

/** Format `bdg tv chart`: chart identity + dataSources overview. */
export function formatTvChart(data: TvChartResult): string {
  const fmt = new OutputFormatter();
  fmt.text('TradingView chart').blank();
  fmt.keyValueList([
    ['url', data.url],
    ['title', data.title],
    ['chartId', data.chartId ?? '-'],
    ['mainSeries', data.mainSeries ? `${data.mainSeries.id} (${data.mainSeries.name})` : '-'],
    ['dataSources', String(data.dataSourceCount)],
  ]);
  fmt.blank().text('Data sources:');
  fmt.list(data.dataSources.map((d) => `${d.id ?? '?'} | ${d.name ?? '?'}`));
  return fmt.build();
}

export interface TvStudyAddResult {
  before: string[];
  added: string[];
  createResult: unknown;
  inputsApplied?: {
    matched?: Array<{ id: string; value: unknown }>;
    unmatched?: string[];
    error?: string;
  } | null;
  error?: string;
}

/** Format `bdg tv study add`: which entity id the new study got. */
export function formatTvStudyAdd(data: TvStudyAddResult): string {
  const fmt = new OutputFormatter();
  if (data.error) {
    return fmt.text(`Failed to add study: ${data.error}`).build();
  }
  if (data.added.length === 0) {
    return fmt
      .text('Study added, but no new entity id appeared in dataSources().')
      .blank()
      .section('Possible reasons:', [
        'The study name did not resolve (check the exact display name).',
        'Free tier: 2 user studies per chart — remove one first.',
        'The study was merged into an existing source (e.g. duplicate event overlay).',
      ])
      .build();
  }
  fmt.text(`Added study → entity id: ${data.added.join(', ')}`).blank();
  const applied = data.inputsApplied;
  if (applied) {
    if (applied.error) {
      fmt.keyValue('inputs', `could not apply: ${applied.error}`);
    } else {
      const matched = applied.matched ?? [];
      const unmatched = applied.unmatched ?? [];
      fmt.keyValue(
        'inputs applied',
        matched.map((m) => `${m.id}=${JSON.stringify(m.value)}`).join(', ') || '(none)'
      );
      if (unmatched.length > 0) {
        fmt.keyValue(
          'inputs unmatched',
          `${unmatched.join(', ')} — use the input title or canonical id from \`tvcli inputs\``
        );
      }
    }
  }
  fmt.keyValue(
    'createStudy result',
    data.createResult == null ? 'ok' : JSON.stringify(data.createResult)
  );
  return fmt.build();
}

export interface TvStudyPlot {
  id: string | null;
  title: string | null;
}

export interface TvStudyEntry {
  id: string | null;
  name: string | null;
  plots: TvStudyPlot[];
  lastRow: { n: number; value: unknown } | null;
}

export interface TvStudyValuesResult {
  chart: string;
  studyCount: number;
  studies: TvStudyEntry[];
}

/** Format `bdg tv study values`: per-study plot metadata + last row values. */
export function formatTvStudyValues(data: TvStudyValuesResult, filter: string): string {
  const fmt = new OutputFormatter();
  const studies = filter ? data.studies.filter((s) => s.name?.includes(filter)) : data.studies;
  if (studies.length === 0) {
    return fmt.text(`No studies${filter ? ` matching "${filter}"` : ''} on this chart.`).build();
  }
  fmt.text(`${studies.length} stud${studies.length === 1 ? 'y' : 'ies'} on ${data.chart}:`).blank();
  for (const s of studies) {
    const plotList =
      s.plots.map((p) => `${p.id ?? '?'}${p.title ? ` (${p.title})` : ''}`).join(', ') || '-';
    fmt.text(`${s.id ?? '?'} | ${s.name ?? '?'}`);
    fmt.keyValue('plots', plotList);
    fmt.keyValue(
      'last row',
      s.lastRow
        ? `#${s.lastRow.n} ${JSON.stringify(s.lastRow.value)}`
        : 'no row buffer (strategy/event studies report via du frames)'
    );
    fmt.blank();
  }
  return fmt.build();
}

export interface TvStudyRemoveResult {
  removed: boolean;
  error?: string;
}

/** Format `bdg tv study remove`: confirmation or error. */
export function formatTvStudyRemove(data: TvStudyRemoveResult): string {
  const fmt = new OutputFormatter();
  if (data.error) return fmt.text(`Failed to remove study: ${data.error}`).build();
  return fmt.text('Removed study.').build();
}

export interface TvStudyInputsResult {
  id: string;
  count: number;
  inputs: Array<{ id: string | null; value: unknown }>;
  error?: string;
}

/** Format `bdg tv study inputs`: the study's current in_N id/value map. */
export function formatTvStudyInputs(data: TvStudyInputsResult): string {
  const fmt = new OutputFormatter();
  if (data.error) return fmt.text(`Failed to read inputs: ${data.error}`).build();
  fmt.text(`${data.count} input(s) on study ${data.id}:`).blank();
  fmt.list(
    data.inputs.map((x) => {
      let v: string;
      try {
        v = JSON.stringify(x.value);
      } catch {
        v = String(x.value);
      }
      return `${x.id ?? '?'} = ${v.length > 60 ? v.slice(0, 60) + '...' : v}`;
    })
  );
  return fmt.build();
}

export interface TvGraphicsPrimitive {
  kind: string;
  group: string;
  id: string | null;
  x1?: number;
  y1?: number;
  x2?: number;
  y2?: number;
  ext?: unknown;
  st?: unknown;
  ci?: unknown;
  text?: string;
  visible?: unknown;
}

export interface TvGraphicsEntry {
  id: string | null;
  name: string | null;
  graphics: Partial<Record<string, TvGraphicsPrimitive[]>>;
}

export interface TvGraphicsResult {
  chart: string;
  studyCount: number;
  studies: TvGraphicsEntry[];
}

const KIND_LABEL: Record<string, string> = {
  dwglines: 'lines',
  dwglabels: 'labels',
  dwgboxes: 'boxes',
  dwgtablecells: 'table cells',
};

/** Format `bdg tv study graphics`: per-study Pine-drawn primitives. */
export function formatTvGraphics(data: TvGraphicsResult, filter: string): string {
  const fmt = new OutputFormatter();
  const studies = filter ? data.studies.filter((s) => s.name?.includes(filter)) : data.studies;
  const withGfx = studies.filter((s) => Object.keys(s.graphics).length > 0);

  if (withGfx.length === 0) {
    return fmt
      .text(`No Pine graphics${filter ? ` matching "${filter}"` : ''} on this chart.`)
      .blank()
      .section('Hint:', [
        'Graphics appear only when a Pine script draws with line.new / label.new / box.new / table.new.',
        'The volume profile and event overlays render through other channels, not the graphics collection.',
      ])
      .build();
  }

  fmt
    .text(`${withGfx.length} stud${withGfx.length === 1 ? 'y' : 'ies'} with Pine graphics:`)
    .blank();
  for (const s of withGfx) {
    fmt.text(`${s.id ?? '?'} | ${s.name ?? '?'}`);
    for (const [kind, items] of Object.entries(s.graphics)) {
      const label = KIND_LABEL[kind] ?? kind;
      const list = items ?? [];
      fmt.section(
        `${label} (${list.length}):`,
        list.slice(0, 12).map((p) => {
          const parts = [
            p.x1 != null ? `x1 ${String(p.x1)}` : null,
            p.y1 != null ? `y1 ${String(p.y1)}` : null,
            p.x2 != null ? `x2 ${String(p.x2)}` : null,
            p.y2 != null ? `y2 ${String(p.y2)}` : null,
            p.text ? `"${p.text}"` : null,
          ].filter(Boolean);
          return `${p.kind}#${p.id ?? '?'} [${p.group}] ${parts.join(' ')}`.trim();
        }),
        2
      );
      if (list.length > 12) fmt.text(`  … and ${list.length - 12} more`);
    }
    fmt.blank();
  }
  return fmt.build();
}

function arrStr(arr: unknown[]): string {
  if (!Array.isArray(arr) || arr.length === 0) return '-';
  return arr.map((x) => String(x)).join(', ');
}

function symStr(sym: unknown): string {
  if (typeof sym === 'string') return sym.length > 48 ? sym.slice(0, 48) + '...' : sym;
  if (sym == null) return '?';
  try {
    return JSON.stringify(sym);
  } catch {
    return '?';
  }
}
