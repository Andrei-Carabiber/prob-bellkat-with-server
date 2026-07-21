import { Router } from 'express';
import { exec } from 'node:child_process';
import { promisify } from 'node:util';
import crypto from 'node:crypto';
import path from 'node:path';
import fs from 'node:fs/promises';
import { createIsolatedWorkspace } from './workspace.js';

const ALLOWED_COMMANDS = new Set(['run', 'execution-trace', 'probability']);
const SHARED_BUILD_DIR = '/opt/pbkat/shared-build-cache';
const execAsync = promisify(exec);

export function createProtocolRouter() {
    const router = Router();

    router.post('/run-protocol', async (req, res) => {
        console.log("COMMAND RECEIVED IS : " + req.body.command)
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

            try {
                if (command === 'probability') {
                    const runResult = await execAsync(
                        `cabal run playground --builddir=${SHARED_BUILD_DIR} -- --json run`,
                        execOpts
                    );
                    stderr += runResult.stderr;

                    // cabal prints its build log (Resolving dependencies..., Building
                    // executable..., etc.) to STDOUT when it needs to (re)build, with the
                    // program's real output appended as the last line(s). Scan from the end
                    // for the line that's actually valid JSON rather than assuming stdout
                    // is JSON from the first character.
                    const candidateLines = runResult.stdout.split('\n').map(l => l.trim()).filter(Boolean);
                    let jsonLine = null;
                    for (let i = candidateLines.length - 1; i >= 0; i--) {
                        try {
                            JSON.parse(candidateLines[i]);
                            jsonLine = candidateLines[i];
                            break;
                        } catch {
                            // not the JSON line (probably cabal build-log noise), keep scanning
                        }
                    }

                    if (jsonLine === null) {
                        return res.status(500).json({
                            error: '"--json run" did not produce a parseable JSON line; cannot feed it into "probability". See "debug" for the raw output.',
                            stderr,
                            debug: runResult.stdout.slice(0, 4000),
                        });
                    }

                    // Persist just the JSON line and feed it in as stdin for step 2, once
                    // step 1 (and its build) has fully completed.
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
                        `cabal run playground --builddir=${SHARED_BUILD_DIR} -- ${command}`,
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

            // Only "run" / "execution-trace" output uses the ⦅...⦆ convex-set notation.
            // "probability" output is a bare rational (p(Goal)); cabal may still prefix it
            // with build-log noise on stdout, so take the last non-empty line as the answer
            // rather than slicing on "⦅" (indexOf returns -1 -> slice(-1) keeps just 1 char).
            if (command !== 'probability') {
                const diamondIndex = msg.indexOf("⦅");
                if (diamondIndex !== -1) {
                    msg = msg.slice(diamondIndex, msg.length);
                }
            } else {
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
