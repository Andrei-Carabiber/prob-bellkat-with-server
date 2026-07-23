import { Router } from 'express';
import { exec } from 'node:child_process';
import { promisify } from 'node:util';
import crypto from 'node:crypto';
import path from 'node:path';
import fs from 'node:fs/promises';
import { createIsolatedWorkspace } from './workspace.js';

// Added quantum commands
const ALLOWED_COMMANDS = new Set(['run', 'execution-trace', 'probability', 'mdp', 'qmdp']);
const SHARED_BUILD_DIR = '/opt/pbkat/shared-build-cache';
const execAsync = promisify(exec);

export function createProtocolRouter() {
    const router = Router();

    router.post('/run-protocol', async (req, res) => {
        const code = req.body.code;
        const command = ALLOWED_COMMANDS.has(req.body.command) ? req.body.command : 'run';

        if (!code || typeof code !== 'string') {
            return res.status(400).json({ error: 'Missing "code" in request body' });
        }

        const requestId = crypto.randomUUID();
        let workspacePath;

        try {
            workspacePath = await createIsolatedWorkspace(requestId);

            const playgroundFile = path.join(workspacePath, 'playground-example/Playground.hs');
            await fs.writeFile(playgroundFile, code, 'utf-8');

            const execOpts = { cwd: workspacePath, maxBuffer: 1024 * 1024 * 10 };
            let stdout = '';
            let stderr = '';

            // 1. Safely construct the execution arguments
            const args = [];

            // Support for "pure qmdp" or "pure mdp"
            if (req.body.pure) args.push('pure');

            args.push(command);

            if (req.body.json) args.push('--json');
            if (req.body.computeExtremal) args.push('--compute-extremal');

            // Ensure numeric bounds to prevent command injection
            if (typeof req.body.truncation === 'number') {
                args.push(`--truncation ${req.body.truncation}`);
            }
            if (typeof req.body.coverage === 'number') {
                args.push(`--coverage ${req.body.coverage}`);
            }

            const argsString = args.join(' ');

            try {
                if (command === 'probability') {
                    const runResult = await execAsync(
                        `cabal run playground --builddir=${SHARED_BUILD_DIR} -- --json run`,
                        execOpts
                    );
                    stderr += runResult.stderr;

                    const candidateLines = runResult.stdout.split('\n').map(l => l.trim()).filter(Boolean);
                    let jsonLine = null;
                    for (let i = candidateLines.length - 1; i >= 0; i--) {
                        try {
                            JSON.parse(candidateLines[i]);
                            jsonLine = candidateLines[i];
                            break;
                        } catch {
                            // keep scanning
                        }
                    }

                    if (jsonLine === null) {
                        return res.status(500).json({
                            error: '"--json run" did not produce a parseable JSON line.',
                            stderr,
                            debug: runResult.stdout.slice(0, 4000),
                        });
                    }

                    const jsonPath = path.join(workspacePath, 'run-output.json');
                    await fs.writeFile(jsonPath, jsonLine, 'utf-8');

                    const probResult = await execAsync(
                        `cabal run playground --builddir=${SHARED_BUILD_DIR} -- probability < ${jsonPath}`,
                        execOpts
                    );
                    stderr += probResult.stderr;
                    stdout = probResult.stdout;
                } else {
                    const result = await execAsync(
                        `cabal run playground --builddir=${SHARED_BUILD_DIR} -- ${argsString}`,
                        execOpts
                    );
                    stdout = result.stdout;
                    stderr = result.stderr;
                }
            } catch (error) {
                return res.status(500).json({ error: error.message, stderr: error.stderr ?? stderr });
            } finally {
                await fs.rm(workspacePath, { recursive: true, force: true }).catch((cleanupErr) => {
                    console.error(`Failed to clean up workspace ${workspacePath}:`, cleanupErr);
                });
            }

            let msg = stdout.trim();

            // 2. Parse output based on formatting requirements
            if (req.body.json) {
                // For --json outputs on mdp/qmdp, extract the clean JSON line from Cabal's build noise
                const candidateLines = msg.split('\n').map(l => l.trim()).filter(Boolean);
                let jsonLine = null;
                for (let i = candidateLines.length - 1; i >= 0; i--) {
                    try {
                        JSON.parse(candidateLines[i]);
                        jsonLine = candidateLines[i];
                        break;
                    } catch {}
                }
                msg = jsonLine || '{"error": "Failed to extract JSON from execution output."}';
            } else if (command !== 'probability') {
                // Standard non-JSON output (slices at the convex-set diamond)
                const diamondIndex = msg.indexOf("⦅");
                if (diamondIndex !== -1) {
                    msg = msg.slice(diamondIndex, msg.length);
                }
            } else {
                // Bare rational output for probability
                const lines = msg.split('\n').map(l => l.trim()).filter(Boolean);
                if (lines.length) {
                    msg = lines[lines.length - 1];
                }
            }

            return res.json({ output: msg, stats: stderr.trim() });

        } catch (err) {
            if (workspacePath) {
                await fs.rm(workspacePath, { recursive: true, force: true }).catch(() => {});
            }
            return res.status(500).json({ error: `Server failed to initialize run: ${err.message}` });
        }
    });

    return router;
}