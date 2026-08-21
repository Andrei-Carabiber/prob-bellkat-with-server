import { createClient } from 'redis';

const redisUrl = process.env.REDIS_URL || 'redis://redis';

export const redisClient = createClient({
    url: redisUrl,
    socket: {
        reconnectStrategy: (retries) => Math.min(retries * 100, 3000),
    },
});

redisClient.on('error', (err) => console.error('Redis Client Error', err));