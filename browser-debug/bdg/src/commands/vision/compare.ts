/**
 * `bdg vision compare` — send BEFORE and AFTER screenshots to a Mistral
 * vision model and ask what changed in the UI.
 *
 * The agent recipe for "what did my interaction change on screen":
 *   bdg dom screenshot /tmp/before.png
 *   bdg dom click "#buy-button"     (or any other interaction)
 *   bdg dom screenshot /tmp/after.png
 *   bdg vision compare /tmp/before.png /tmp/after.png
 */

import { existsSync } from 'node:fs';

import { Option, type Command } from 'commander';

import { runCommand } from '@/commands/shared/CommandRunner.js';
import { jsonOption } from '@/commands/shared/commonOptions.js';
import type { BaseOptions } from '@/commands/shared/optionTypes.js';
import {
  analyzeImages,
  DEFAULT_VISION_MODEL,
  FRONTIER_VISION_MODEL,
  imageFromFile,
  type VisionAnalysis,
} from '@/commands/vision/mistral.js';
import { CommandError } from '@/errors/index.js';
import { OutputFormatter } from '@/ui/formatting.js';
import { EXIT_CODES } from '@/utils/exitCodes.js';

interface VisionCompareOptions extends BaseOptions {
  prompt: string;
  model: string;
}

const DEFAULT_PROMPT =
  'These are BEFORE and AFTER screenshots of the same web page. Describe exactly ' +
  'what changed in the UI between them: elements added, removed, moved, restyled, ' +
  'or state changes (dialogs, menus, toggles, form values, chart updates). Be ' +
  'precise about locations. If nothing meaningful changed, say so.';

function formatVisionAnalysis(data: VisionAnalysis): string {
  const fmt = new OutputFormatter();
  fmt.text(data.content.trim());
  if (data.usage?.totalTokens !== undefined) {
    fmt
      .blank()
      .text(`[model ${data.model} · ${data.images} image(s) · ${data.usage.totalTokens} tokens]`);
  } else {
    fmt.blank().text(`[model ${data.model} · ${data.images} image(s)]`);
  }
  return fmt.build();
}

export function registerVisionCompareCommand(visionCmd: Command): void {
  visionCmd
    .command('compare')
    .description('Compare BEFORE/AFTER screenshots and describe the UI changes via Mistral vision')
    .argument('<before>', 'Path to the BEFORE screenshot')
    .argument('<after>', 'Path to the AFTER screenshot')
    .addOption(jsonOption())
    .addOption(new Option('--prompt <text>', 'Custom question for the vision model'))
    .addOption(
      new Option('--model <id>', 'Mistral model id')
        .default(DEFAULT_VISION_MODEL)
        .choices([DEFAULT_VISION_MODEL, FRONTIER_VISION_MODEL])
    )
    .action(async (before: string, after: string, options: VisionCompareOptions) => {
      await runCommand(
        async () => {
          for (const file of [before, after]) {
            if (!existsSync(file)) {
              throw new CommandError(
                `Image file not found: ${file}`,
                {},
                EXIT_CODES.RESOURCE_NOT_FOUND
              );
            }
          }

          const images = [await imageFromFile(before), await imageFromFile(after)];
          const prompt = options.prompt || DEFAULT_PROMPT;
          const analysis = await analyzeImages(images, prompt, options.model);
          return { success: true, data: analysis };
        },
        options,
        formatVisionAnalysis
      );
    });
}
