import { createHash } from 'node:crypto';
import { ProtocolRequestType } from '../zod/run-protocol-body-schema.js';

export function generateProtocolCacheKey(params: ProtocolRequestType): string {
    const normalizedPayload = JSON.stringify({
        code: params.code?.trim(),
        command: params.command,
        truncation: params.truncation ?? null,
        coverage: params.coverage ?? null,
        probOnly: params.probOnly ?? null,
    });

    return `protocol:${createHash('sha256').update(normalizedPayload).digest('hex')}`;
}