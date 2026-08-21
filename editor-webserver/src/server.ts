import express from 'express';
import cors from 'cors';
import http from 'node:http';
import { WebSocketServer } from 'ws';
import {createProtocolRouter} from './routes/protocol/protocol-route.js'
import { setupHlsWebSocket, replenishPool, shutdownPool } from './hlsPool.js';
import {createShareRouter} from "./routes/share/share-route.js";
import {createClient} from "redis";
import {redisClient} from "./redis.js";

const app = express();
app.use(cors({ origin: 'http://localhost:3000' }));
app.use(express.json());

await redisClient.connect();

app.use(createProtocolRouter());
app.use(createShareRouter());

const server = http.createServer(app);
const wss = new WebSocketServer({ server });

setupHlsWebSocket(wss);

const port = Number(process.env.PORT ?? 8080)
server.listen(port, () => {
    console.log(`HTTP & WebSocket Server running on port ${port}`);
    console.log('Pre-warming HLS workers...');
    replenishPool();
});

async function gracefulShutdown(signal: string) {
    console.log(`\n${signal} received. Shutting down gracefully...`);

    try {
        // 1. Shut down your worker pool
        await shutdownPool();

        // 2. Safely close the Redis connection
        if (redisClient.isOpen) {
            await redisClient.quit();
            console.log('Redis connection closed.');
        }

        // 3. Stop accepting new HTTP/WS requests and close the server
        server.close(() => {
            console.log('HTTP & WebSocket server closed.');
            process.exit(0);
        });

        // Failsafe: if connections are hanging, force quit after 5 seconds
        setTimeout(() => {
            console.error('Could not close connections in time, forcefully shutting down');
            process.exit(1);
        }, 5000).unref();

    } catch (error) {
        console.error('Error during shutdown:', error);
        process.exit(1);
    }
}

process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
process.on('SIGINT', () => gracefulShutdown('SIGINT'));

