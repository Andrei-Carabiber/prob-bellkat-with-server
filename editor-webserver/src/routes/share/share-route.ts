import {Router} from "express";
import {validate} from "../zod/validate-schema.js";
import {z} from "zod";
import {createClient} from 'redis';

const redisUrl = process.env.REDIS_URL || 'redis://redis';
const redisClient = createClient({
    url: redisUrl,
    socket: {
        reconnectStrategy: (retries) => Math.min(retries * 100, 3000),
    },
});
redisClient.on('error', (err) => {
    console.error('Redis Client Error', err);
});
await redisClient.connect();

export const SharePostRequestBody = z.object({
    code: z.string(),
    graph: z.object({
        nodes: z.array(z.any()),
        edges: z.array(z.any()),
    }),
    goal: z.array(z.any()),
    goalDisabled: z.boolean(),
    networkCapacity: z.array(z.any()),
    capacityDisabled: z.boolean(),
});

export function createShareRouter() {
    const router = Router();

    router.post('/share', validate(SharePostRequestBody), async (req, res) => {
        try {
            let shortId: string = "";
            let exists = true;


            while (exists) {
                shortId = Math.random().toString(36).substring(2, 10);
                const current = await redisClient.get(shortId);
                if (!current) {
                    exists = false;
                }
            }

            await redisClient.setEx(shortId, 864000, JSON.stringify(req.body));
            res.json({ id: shortId });
        } catch (error) {
            console.error("Failed to save shared state:", error);
            res.status(500).json({ error: "Internal server error" });
        }
    });

    router.get<{ id: string }>('/share/:id', async (req, res) => {
        try {
            const state = await redisClient.get(req.params.id as string) as string | null;

            if (!state) {
                return res.status(404).json({ error: "Share link expired or not found" });
            }

            res.json(JSON.parse(state));
        } catch (error) {
            console.error("Failed to fetch shared state:", error);
            res.status(500).json({ error: "Internal server error" });
        }
    });

    return router;
}