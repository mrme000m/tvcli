/**
 * Mistral Vision client for the `bdg vision` command group.
 *
 * Sends screenshots (and any image files) to Mistral's OpenAI-compatible
 * chat completions endpoint and returns the model's description. The API key
 * is resolved from `MISTRAL_API_KEY` in the environment, then from the
 * browser-debug `.env` — it is never printed or logged.
 */

import { readFile } from 'node:fs/promises';

import { CommandError } from '@/errors/index.js';
import { EXIT_CODES } from '@/utils/exitCodes.js';

const MISTRAL_CHAT_URL = 'https://api.mistral.ai/v1/chat/completions';

/** Default (cost-effective, vision-capable) and frontier vision models. */
export const DEFAULT_VISION_MODEL = 'mistral-small-2603';
export const FRONTIER_VISION_MODEL = 'mistral-medium-3.5';

/**
 * Files checked for `MISTRAL_API_KEY` when it is not set in the environment.
 * The key lives in browser-debug/.env.
 */
const KEY_ENV_FILES = ['../.env', '.env'];

const REQUEST_TIMEOUT_MS = 120_000;

/** An image payload ready to embed in a chat message. */
export interface VisionImage {
  /** Raw image bytes. */
  data: string;
  /** MIME type, e.g. 'image/png'. */
  mime: string;
}

export interface VisionAnalysis {
  model: string;
  content: string;
  prompt: string;
  images: number;
  usage?: {
    promptTokens?: number;
    completionTokens?: number;
    totalTokens?: number;
  };
}

/** Read an image file into a base64 payload, inferring the MIME type. */
export async function imageFromFile(filePath: string): Promise<VisionImage> {
  const buffer = await readFile(filePath).catch((error: unknown) => {
    const detail = error instanceof Error ? error.message : String(error);
    throw new CommandError(
      `Cannot read image file: ${filePath} (${detail})`,
      {},
      EXIT_CODES.RESOURCE_NOT_FOUND
    );
  });

  const lower = filePath.toLowerCase();
  let mime = 'image/png';
  if (lower.endsWith('.jpg') || lower.endsWith('.jpeg')) mime = 'image/jpeg';
  else if (lower.endsWith('.webp')) mime = 'image/webp';
  else if (lower.endsWith('.gif')) mime = 'image/gif';

  return { data: buffer.toString('base64'), mime };
}

/**
 * Resolve the Mistral API key: `MISTRAL_API_KEY` env var first, then the
 * `MISTRAL_API_KEY=` line of the configured `.env` files.
 */
export async function resolveApiKey(): Promise<string> {
  const fromEnv = process.env['MISTRAL_API_KEY'];
  if (fromEnv) return fromEnv;

  for (const file of KEY_ENV_FILES) {
    try {
      const content = await readFile(file, 'utf8');
      const match = content.match(/^MISTRAL_API_KEY=(\S+)$/m);
      if (match?.[1]) return match[1].replace(/^["']|["']$/g, '');
    } catch {
      // Try the next candidate file.
    }
  }

  throw new CommandError(
    'No Mistral API key found. Set MISTRAL_API_KEY in the environment or add ' +
      `MISTRAL_API_KEY=<key> to one of: ${KEY_ENV_FILES.join(', ')}`,
    {},
    EXIT_CODES.INVALID_ARGUMENTS
  );
}

/**
 * Send images to a Mistral vision model and return its text description.
 */
export async function analyzeImages(
  images: VisionImage[],
  prompt: string,
  model: string
): Promise<VisionAnalysis> {
  const apiKey = await resolveApiKey();

  const body = {
    model,
    messages: [
      {
        role: 'user',
        content: [
          { type: 'text', text: prompt },
          ...images.map((image) => ({
            type: 'image_url',
            image_url: { url: `data:${image.mime};base64,${image.data}` },
          })),
        ],
      },
    ],
  };

  let response: Response;
  try {
    response = await fetch(MISTRAL_CHAT_URL, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (error) {
    throw new CommandError(
      `Mistral API request failed: ${error instanceof Error ? error.message : String(error)}`,
      {},
      EXIT_CODES.SOFTWARE_ERROR
    );
  }

  const payload = (await response.json().catch(() => null)) as {
    error?: { message?: string };
    detail?: unknown;
    choices?: Array<{ message?: { content?: string } }>;
    usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
  } | null;

  if (!response.ok || !payload) {
    const detail =
      payload?.error?.message ??
      (payload?.detail ? JSON.stringify(payload.detail).slice(0, 300) : `HTTP ${response.status}`);
    throw new CommandError(
      `Mistral API error (${response.status}): ${detail}`,
      {},
      EXIT_CODES.SOFTWARE_ERROR
    );
  }

  const content = payload.choices?.[0]?.message?.content;
  if (!content) {
    throw new CommandError('Mistral API returned no content', {}, EXIT_CODES.UNHANDLED_EXCEPTION);
  }

  return {
    model,
    content,
    prompt,
    images: images.length,
    ...(payload.usage && {
      usage: {
        ...(payload.usage.prompt_tokens !== undefined && {
          promptTokens: payload.usage.prompt_tokens,
        }),
        ...(payload.usage.completion_tokens !== undefined && {
          completionTokens: payload.usage.completion_tokens,
        }),
        ...(payload.usage.total_tokens !== undefined && {
          totalTokens: payload.usage.total_tokens,
        }),
      },
    }),
  };
}
