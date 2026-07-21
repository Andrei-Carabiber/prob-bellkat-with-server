import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import { WebSocketMessageReader, WebSocketMessageWriter } from 'vscode-ws-jsonrpc';
import { createServerProcess } from 'vscode-ws-jsonrpc/server';
import { createIsolatedWorkspace } from './workspace.js';

const POOL_SIZE = 10;
const workerPool = [];

async function spawnWorker() {
    const id = crypto.randomUUID();
    const workspacePath = await createIsolatedWorkspace(id);

    const hlsProcess = createServerProcess(
        'Haskell', 'haskell-language-server-wrapper', ['--lsp'],
        { cwd: workspacePath }
    );

    const worker = { id, workspacePath, hlsProcess, cachedInitializeResult: null, clientWriter: null };

    // Single listener for the lifetime of this HLS process.
    // During pre-warming: respond to HLS requests with stubs so it doesn't hang.
    // After a client connects: forward everything to the client's writer.
    hlsProcess.reader.listen((msg) => {
        if (msg.id !== undefined && msg.result && worker.cachedInitializeResult === null && msg.result.capabilities) {
            worker.cachedInitializeResult = msg;
        }

        if (worker.clientWriter) {
            worker.clientWriter.write(msg);
            return;
        }

        // Pre-warming phase: HLS sends requests (with id + method) that need responses
        if (msg.id !== undefined && msg.method) {
            hlsProcess.writer.write({
                jsonrpc: '2.0',
                id: msg.id,
                result: null
            });
        }
    });

    hlsProcess.onExit?.(() => {
        console.log(`Pool worker ${id} HLS exited.`);
        const idx = workerPool.indexOf(worker);
        if (idx !== -1) workerPool.splice(idx, 1);
    });

    return worker;
}

export function replenishPool() {
    const needed = POOL_SIZE - workerPool.length;
    for (let i = 0; i < needed; i++) {
        spawnWorker().then(worker => {
            workerPool.push(worker);
            console.log(`Pool: ${workerPool.length}/${POOL_SIZE} workers ready`);
        }).catch(err => {
            console.error('Failed to spawn pool worker:', err);
        });
    }
}

export async function shutdownPool() {
    console.log(`Shutting down ${workerPool.length} pooled workers...`);
    const workers = workerPool.splice(0);
    await Promise.allSettled(workers.map(async (w) => {
        try { w.hlsProcess.dispose(); } catch {}
        try { await fs.rm(w.workspacePath, { recursive: true, force: true }); } catch {}
    }));
}

export function setupHlsWebSocket(wss) {
    wss.on('connection', async (ws) => {
        console.log("New client connected. Assigning worker...");

        let worker = workerPool.shift();
        let workspacePath;
        let hlsProcess = null;
        let cachedInitializeResult = null;
        let activeDocUri = null;

        const socket = {
            send: (content) => ws.send(content),
            onMessage: (cb) => ws.on('message', cb),
            onError: (cb) => ws.on('error', cb),
            onClose: (cb) => ws.on('close', cb),
            dispose: () => ws.close()
        };

        const reader = new WebSocketMessageReader(socket);
        const writer = new WebSocketMessageWriter(socket);

        if (worker) {
            console.log(`Assigned pool worker ${worker.id} (pool: ${workerPool.length}/${POOL_SIZE})`);
            workspacePath = worker.workspacePath;
            hlsProcess = worker.hlsProcess;
            cachedInitializeResult = worker.cachedInitializeResult;

            // Redirect the existing listener to pipe messages to this client
            worker.clientWriter = writer;

            hlsProcess.onExit?.(() => {
                console.log(`HLS Process for connection ${worker.id} exited.`);
                hlsProcess = null;
                cachedInitializeResult = null;
                activeDocUri = null;
            });

            replenishPool();
        } else {
            // Fallback: no workers available, spawn inline (slow path)
            console.warn("Pool exhausted! Spawning HLS inline (slow path)...");
            const connectionId = crypto.randomUUID();

            try {
                workspacePath = await createIsolatedWorkspace(connectionId);

                hlsProcess = createServerProcess(
                    'Haskell', 'haskell-language-server-wrapper', ['--lsp'],
                    { cwd: workspacePath }
                );

                hlsProcess.onExit?.(() => {
                    console.log(`HLS Process for connection ${connectionId} exited.`);
                    hlsProcess = null;
                    cachedInitializeResult = null;
                    activeDocUri = null;
                });

                hlsProcess.reader.listen((msg) => {
                    if (msg.id !== undefined && msg.result && cachedInitializeResult === null && msg.result.capabilities) {
                        cachedInitializeResult = msg;
                    }
                    writer.write(msg);
                });
            } catch (err) {
                console.error("Failed to initialize client environment:", err);
                ws.close();
                return;
            }

            replenishPool();
        }

        let seenInitialize = false;

        reader.listen((msg) => {
            if (!hlsProcess) return;

            if (msg.method === 'initialize' && cachedInitializeResult) {
                if (activeDocUri) {
                    hlsProcess.writer.write({
                        jsonrpc: '2.0',
                        method: 'textDocument/didClose',
                        params: { textDocument: { uri: activeDocUri } }
                    });
                    activeDocUri = null;
                }
                writer.write({ ...cachedInitializeResult, id: msg.id });
                seenInitialize = true;
                return;
            }
            if (msg.method === 'initialized' && seenInitialize) {
                return;
            }
            if (msg.method === 'shutdown') {
                writer.write({ jsonrpc: '2.0', id: msg.id, result: null });
                return;
            }
            if (msg.method === 'exit') {
                return;
            }
            if (msg.method === '$/setTrace') {
                return;
            }
            if (msg.method === 'textDocument/didOpen') {
                activeDocUri = msg.params?.textDocument?.uri ?? activeDocUri;
            }

            hlsProcess.writer.write(msg);
        });

        ws.on('close', async () => {
            const id = worker?.id ?? 'inline';
            console.log(`Client ${id} disconnected. Cleaning up...`);

            if (hlsProcess) {
                try {
                    hlsProcess.dispose();
                } catch (e) {
                    console.error("Error disposing HLS process:", e);
                }
            }

            if (workspacePath) {
                try {
                    await fs.rm(workspacePath, { recursive: true, force: true });
                    console.log(`Workspace ${workspacePath} deleted.`);
                } catch (cleanupErr) {
                    console.error(`Failed to delete workspace ${workspacePath}:`, cleanupErr);
                }
            }
        });
    });
}
