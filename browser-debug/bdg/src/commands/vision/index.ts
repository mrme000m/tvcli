/**
 * `bdg vision` — understand web-interface state and changes with Mistral
 * vision models, driven by bdg's CDP screenshot capture.
 *
 * Subcommands:
 *   describe [image]   Screenshot the page (or use an image file) and describe the UI.
 *   compare <before> <after>   Diff two screenshots and describe the UI changes.
 */

import type { Command } from 'commander';

import { registerVisionCompareCommand } from '@/commands/vision/compare.js';
import { registerVisionDescribeCommand } from '@/commands/vision/describe.js';

export function registerVisionCommands(program: Command): void {
  const visionCmd = program
    .command('vision')
    .description('Mistral vision analysis of page screenshots (UI description & change diff)');

  registerVisionDescribeCommand(visionCmd);
  registerVisionCompareCommand(visionCmd);
}
