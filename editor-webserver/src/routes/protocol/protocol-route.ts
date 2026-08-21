import { LRUCache } from 'lru-cache';
import { generateProtocolCacheKey } from './cache-util.js';
import {Router} from "express";
import {validate} from "../zod/validate-schema.js";
import {ProtocolRequestBody, ProtocolRequestType} from "../zod/run-protocol-body-schema.js";
import {QBKATProbOutput, QBKATProbQualityOutput, runQBKatCommand} from "./runQBKatCommand.js";
import {PBKATOutput, runPBKatCommand} from "./runPBKatCommand.js";
import {promisify} from "node:util";
import {exec} from "node:child_process";

export const SHARED_BUILD_DIR = '/opt/pbkat/shared-build-cache';
export const execAsync = promisify(exec);

const protocolCache = new LRUCache<string, any>({
    max: 200,
    ttl: 1000 * 60 * 60 * 24,
});

export function createProtocolRouter() {
    const router = Router();

    //Should add queuing to reduce latency. Also timeout for long requests.
    router.post('/run-protocol', validate(ProtocolRequestBody), async (req, res) => {
        const payload = req.body as ProtocolRequestType;
        const cacheKey = generateProtocolCacheKey(payload);

        const cachedResult = protocolCache.get(cacheKey);
        if (cachedResult) {
            return res.status(200).json({ ...cachedResult, _cached: true });
        }

        let result: QBKATProbQualityOutput | QBKATProbOutput | PBKATOutput;
        try {
            if (payload.command === 'quantum') {
                result = await runQBKatCommand(payload.code, payload.truncation, payload.coverage, payload.probOnly);
            } else {
                result = await runPBKatCommand(payload.code, payload.command);
            }

            protocolCache.set(cacheKey, result);

            res.status(200).json(result);
        } catch (e) {
            console.log(e);
            res.status(400).json({ error: "Something went wrong: " + String(e) });
        }
    });

    return router;
}