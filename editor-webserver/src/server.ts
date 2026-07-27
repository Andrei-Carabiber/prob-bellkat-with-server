import express from 'express';
import cors from 'cors';
import http from 'node:http';
import { WebSocketServer } from 'ws';
import { createProtocolRouter } from './protocolRunner.js';
import { setupHlsWebSocket, replenishPool, shutdownPool } from './hlsPool.js';

const app = express();
app.use(cors({ origin: 'http://localhost:3000' }));
app.use(express.json());
app.use(createProtocolRouter());

const server = http.createServer(app);
const wss = new WebSocketServer({ server });

setupHlsWebSocket(wss);

server.listen(8080, () => {
    console.log('HTTP & WebSocket Server running on port 8080');
    console.log('Pre-warming HLS workers...');
    replenishPool();
});

process.on('SIGTERM', async () => { await shutdownPool(); process.exit(0); });
process.on('SIGINT', async () => { await shutdownPool(); process.exit(0); });
