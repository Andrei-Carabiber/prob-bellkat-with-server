import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import { WebSocketMessageReader, WebSocketMessageWriter } from 'vscode-ws-jsonrpc';
import { createProcessStreamConnection, type IConnection } from 'vscode-ws-jsonrpc/server';
import { createIsolatedWorkspace } from './workspace.js';
import cp, { type ChildProcess } from "node:child_process";

const POOL_SIZE = 10;
const workerPool: WorkerItem[] = [];

type WorkerItem = {
    id: string;
    workspacePath: string;
    hlsProcess: IConnection;
    serverProcess: ChildProcess;
    cachedInitializeResult: any;
    clientWriter: WebSocketMessageWriter | null;
    readonly isAlive: boolean;
};

function safeWrite(writer: { write: (msg: any) => any } | null | undefined, msg: any): boolean {
    if (!writer) return false;
    try {
        const promiseOrVoid = writer.write(msg);
        if (promiseOrVoid && typeof promiseOrVoid.catch === 'function') {
            promiseOrVoid.catch((err: any) => {
                console.warn('Swallowed writer promise error:', err?.message || err);
            });
        }
        return true;
    } catch (err: any) {
        console.warn('Caught write error on stream:', err?.message || err);
        return false;
    }
}

function spawnHlsQuiet(command: string, args: string[], options: cp.SpawnOptions) {
    const serverProcess = cp.spawn(command, args, options);

    serverProcess.on('error', (err) => {
        console.error(`HLS process spawn error:`, err);
    });

    serverProcess.stdin?.on('error', (err: any) => {
        console.warn('HLS process stdin stream error:', err.message);
    });

    serverProcess.stdout?.on('error', (err: any) => {
        console.warn('HLS process stdout stream error:', err.message);
    });

    const connection = createProcessStreamConnection(serverProcess);

    (connection.writer as any)?.onError?.((err: any) => {
        console.warn('JSON-RPC writer error:', err);
    });

    return { serverProcess, connection };
}

async function spawnWorker(): Promise<WorkerItem> {
    const id = crypto.randomUUID();
    const workspacePath = await createIsolatedWorkspace(id);

    const { serverProcess, connection: hlsProcess } = spawnHlsQuiet(
        'haskell-language-server-wrapper', ['--lsp'],
        { cwd: workspacePath }
    );

    let isExited = false;
    const worker: WorkerItem = {
        id,
        workspacePath,
        hlsProcess,
        serverProcess,
        cachedInitializeResult: null,
        clientWriter: null,
        get isAlive() {
            return !isExited && !serverProcess.killed && Boolean(serverProcess.stdin?.writable);
        }
    };

    hlsProcess.reader.listen((msg: any) => {
        if (!worker.isAlive) return;

        if (msg.id !== undefined && msg.result && worker.cachedInitializeResult === null && msg.result.capabilities) {
            worker.cachedInitializeResult = msg;
        }

        if (worker.clientWriter) {
            safeWrite(worker.clientWriter, msg);
            return;
        }

        if (msg.id !== undefined && msg.method) {
            safeWrite(hlsProcess.writer, {
                jsonrpc: '2.0',
                id: msg.id,
                result: null
            });
        }
    });

    const onExitCleanup = () => {
        if (isExited) return;
        isExited = true;
        console.log(`Pool worker ${id} HLS exited.`);
        const idx = workerPool.indexOf(worker);
        if (idx !== -1) workerPool.splice(idx, 1);
        try { hlsProcess.dispose(); } catch {}
    };

    serverProcess.on('exit', onExitCleanup);
    serverProcess.on('close', onExitCleanup);

    return worker;
}

export function replenishPool() {
    const needed = POOL_SIZE - workerPool.length;
    for (let i = 0; i < needed; i++) {
        spawnWorker().then(worker => {
            if (worker.isAlive) {
                workerPool.push(worker);
                console.log(`Pool: ${workerPool.length}/${POOL_SIZE} workers ready`);
            }
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

export function setupHlsWebSocket(wss: any) {
    const interval = setInterval(() => {
        wss.clients.forEach((ws: any) => {
            if (ws.isAlive === false) {
                ws.missedPongs = (ws.missedPongs ?? 0) + 1;
                if (ws.missedPongs >= 3) {
                    console.log("Client missed 3 pongs, terminating...");
                    return ws.terminate();
                }
            } else {
                ws.missedPongs = 0;
            }
            ws.isAlive = false;
            ws.ping();
        });
    }, 45000);
    wss.on('close', () => clearInterval(interval));

    wss.on('connection', async (ws: any) => {
        ws.isAlive = true;
        ws.on('pong', () => {
            ws.isAlive = true;
        });

        console.log("New client connected. Assigning worker...");

        let worker: WorkerItem | null = null;
        while (workerPool.length > 0) {
            const candidate = workerPool.shift();
            if (candidate && candidate.isAlive) {
                worker = candidate;
                break;
            }
        }

        let workspacePath: string | undefined;
        let hlsProcess: IConnection | null = null;
        let cachedInitializeResult: any = null;
        let activeDocUri: string | null = null;

        const socket = {
            send: (content: any) => ws.send(content),
            onMessage: (cb: any) => ws.on('message', cb),
            onError: (cb: any) => ws.on('error', cb),
            onClose: (cb: any) => ws.on('close', cb),
            dispose: () => ws.close()
        };

        const reader = new WebSocketMessageReader(socket);
        const writer = new WebSocketMessageWriter(socket);

        if (worker) {
            console.log(`Assigned pool worker ${worker.id} (pool: ${workerPool.length}/${POOL_SIZE})`);
            workspacePath = worker.workspacePath;
            hlsProcess = worker.hlsProcess;
            cachedInitializeResult = worker.cachedInitializeResult;
            worker.clientWriter = writer;

            worker.serverProcess.on('exit', () => {
                console.log(`HLS Process for connection ${worker?.id} exited.`);
                hlsProcess = null;
                cachedInitializeResult = null;
                activeDocUri = null;
            });

            replenishPool();
        } else {
            console.warn("Pool exhausted! Spawning HLS inline (slow path)...");
            const connectionId = crypto.randomUUID();

            try {
                workspacePath = await createIsolatedWorkspace(connectionId);

                const inline = spawnHlsQuiet(
                    'haskell-language-server-wrapper', ['--lsp'],
                    { cwd: workspacePath }
                );
                hlsProcess = inline.connection;

                inline.serverProcess.on('exit', () => {
                    console.log(`HLS Process for connection ${connectionId} exited.`);
                    hlsProcess = null;
                    cachedInitializeResult = null;
                    activeDocUri = null;
                });

                hlsProcess.reader.listen((msg: any) => {
                    if (msg.id !== undefined && msg.result && cachedInitializeResult === null && msg.result.capabilities) {
                        cachedInitializeResult = msg;
                    }
                    safeWrite(writer, msg);
                });
            } catch (err) {
                console.error("Failed to initialize client environment:", err);
                ws.close();
                return;
            }

            replenishPool();
        }

        let seenInitialize = false;

        reader.listen((msg: any) => {
            if (!hlsProcess || (worker && !worker.isAlive)) return;

            if (msg.method === 'initialize' && cachedInitializeResult) {
                if (activeDocUri) {
                    safeWrite(hlsProcess.writer, {
                        jsonrpc: '2.0',
                        method: 'textDocument/didClose',
                        params: { textDocument: { uri: activeDocUri } }
                    });
                    activeDocUri = null;
                }
                safeWrite(writer, { ...cachedInitializeResult, id: msg.id });
                seenInitialize = true;
                return;
            }
            if (msg.method === 'initialized' && seenInitialize) {
                return;
            }
            if (msg.method === 'shutdown') {
                safeWrite(writer, { jsonrpc: '2.0', id: msg.id, result: null });
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

            safeWrite(hlsProcess.writer, msg);
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