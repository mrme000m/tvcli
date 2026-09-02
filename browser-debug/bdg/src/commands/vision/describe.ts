/**
 * `bdg vision describe` — screenshot the current page (or use an image file)
 * and ask a Mistral vision model to describe the web interface.
 *
 * Agents use this after programmatic interactions (click/fill/scroll) to
 * understand what the UI currently shows. Pair with `bdg vision compare`
 * for before/after change analysis.
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
import { callCDP } from '@/ipc/client.js';
import { OutputFormatter } from '@/ui/formatting.js';
import { EXIT_CODES } from '@/utils/exitCodes.js';

interface VisionDescribeOptions extends BaseOptions {
  prompt: string;
  model: string;
  png: boolean;
  quality: string;
}

const DEFAULT_PROMPT =
  'Describe this web page screenshot for a browser-automation agent: identify the ' +
  'site/app and page, the main UI regions, the visible content, and anything notable ' +
  '(dialogs, menus, forms, charts, loading or error states). Be specific about UI ' +
  'elements the agent may need to interact with.';

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

/** Capture the current viewport as a base64 image payload. */
async function captureScreenshot(
  format: 'png' | 'jpeg',
  quality: number
): Promise<{ data: string; mime: string }> {
  const response = await callCDP('Page.captureScreenshot', {
    format,
    ...(format === 'jpeg' && { quality }),
  });
  const data = (response.data?.result as { data?: string } | undefined)?.data;
  if (!data) {
    throw new CommandError(
      'Page.captureScreenshot returned no image data',
      {},
      EXIT_CODES.CDP_CONNECTION_FAILURE
    );
  }
  return { data, mime: `image/${format}` };
}

export function registerVisionDescribeCommand(visionCmd: Command): void {
  visionCmd
    .command('describe')
    .description(
      'Screenshot the page (or use an image file) and describe the UI via Mistral vision'
    )
    .argument('[image]', 'Image file to describe instead of capturing a screenshot')
    .addOption(jsonOption())
    .addOption(new Option('--prompt <text>', 'Custom question for the vision model'))
    .addOption(
      new Option('--model <id>', 'Mistral model id')
        .default(DEFAULT_VISION_MODEL)
        .choices([DEFAULT_VISION_MODEL, FRONTIER_VISION_MODEL])
    )
    .addOption(new Option('--png', 'Capture PNG (lossless) instead of JPEG').default(false))
    .addOption(new Option('--quality <n>', 'JPEG quality (1-100)').default('80'))
    .action(async (image: string | undefined, options: VisionDescribeOptions) => {
      await runCommand(
        async () => {
          const images = [];
          if (image) {
            if (!existsSync(image)) {
              throw new CommandError(
                `Image file not found: ${image}`,
                {},
                EXIT_CODES.RESOURCE_NOT_FOUND
              );
            }
            images.push(await imageFromFile(image));
          } else {
            const format = options.png ? 'png' : 'jpeg';
            const quality = Math.max(1, Math.min(100, Number(options.quality) || 80));
            images.push(await captureScreenshot(format, quality));
          }

          const prompt = options.prompt || DEFAULT_PROMPT;
          const analysis = await analyzeImages(images, prompt, options.model);
          return { success: true, data: analysis };
        },
        options,
        formatVisionAnalysis
      );
    });
}
