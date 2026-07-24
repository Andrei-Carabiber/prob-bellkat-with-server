import { Router } from 'express';
import { exec } from 'node:child_process';
import { promisify } from 'node:util';
import crypto from 'node:crypto';
import path from 'node:path';
import fs from 'node:fs/promises';
import { createIsolatedWorkspace } from './workspace.js';

// Commands are validated against BellKAT.QuantumPrelude's qcoParser / the
// analogous probabilistic parser. "run"/"execution-trace"/"probability" exist
// in both preludes; "mdp"/"qmdp" only exist once QuantumPrelude (with its
// QBKATTag-specific NetworkBounds/MDP pipelines) is imported.
const PROBABILISTIC_COMMANDS = new Set(['run', 'probability']);
const QUANTUM_COMMANDS = new Set(['mdp', 'qmdp']);

const SHARED_BUILD_DIR = '/opt/pbkat/shared-build-cache';
const execAsync = promisify(exec);

export function createProtocolRouter() {
    const router = Router();

    router.post('/run-protocol', async (req, res) => {
        const code = req.body.code;

        if (!code || typeof code !== 'string') {
            return res.status(400).json({ error: 'Missing "code" in request body' });
        }

        const mode = req.body.mode === 'quantum' ? 'quantum' : 'probabilistic';
        const allowedCommands = mode === 'quantum' ? QUANTUM_COMMANDS : PROBABILISTIC_COMMANDS;
        const command = req.body.command;

        if (!allowedCommands.has(command)) {
            return res.status(400).json({
                error: `Command "${command}" is not valid for ${mode} mode. Available commands: ${[...allowedCommands].join(', ')}.`,
                mode,
                command,
            });
        }

        // Defense in depth: re-derive this from the actual code rather than
        // trusting the client's mode/command alone. A ProbBellKATPolicy value
        // can't be passed where mdp/qmdp expect a QBKATPolicy, so this is
        // rejected even if a stale/tampered client still asks for it.
        if (code.includes('ProbBellKATPolicy') && !PROBABILISTIC_COMMANDS.has(command)) {
            return res.status(400).json({
                error: `Command "${command}" isn't valid: this code uses ProbBellKATPolicy, which can't be run with mdp/qmdp (those require a QBKATPolicy).`,
                mode,
                command,
            });
        }

        // mdp/qmdp only: mirrors resolveExtremalQuery's mutual-exclusivity check
        // in BellKAT.QuantumPrelude, so a bad combo fails fast with a clear
        // message instead of surfacing as an opaque Haskell ioError.
        const hasTruncation = req.body.truncation !== undefined && req.body.truncation !== null && req.body.truncation !== '' && Number(req.body.truncation) !== -1;
        const hasCoverage = req.body.coverage !== undefined && req.body.coverage !== null && req.body.coverage !== '' && Number(req.body.coverage) !== -1;
        if (hasTruncation && hasCoverage && mode === 'quantum') {
            return res.status(400).json({ error: 'Use either "coverage" or "truncation", not both.', mode, command });
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

            if (mode === 'quantum') args.push('--json');


            args.push(command);


            const isMdpFamily = command === 'mdp' || command === 'qmdp';
            if (isMdpFamily && req.body.computeExtremal) args.push('--compute-extremal');
            if (isMdpFamily && req.body.dumpDp) args.push('--dump-dp');
            // Coerce to Number rather than requiring typeof === 'number', since
            // values coming from a form input arrive as strings and were
            // previously being silently dropped. Number(...) still rejects
            // anything non-numeric, so this stays injection-safe.
            if (isMdpFamily && hasTruncation) {
                const truncation = Number(req.body.truncation);
                if (!Number.isFinite(truncation)) {
                    return res.status(400).json({ error: `Invalid truncation value: ${req.body.truncation}`, mode, command });
                }
                args.push(`--truncation ${truncation}`);
            }
            if (isMdpFamily && hasCoverage) {
                const coverage = Number(req.body.coverage);
                if (!Number.isFinite(coverage)) {
                    return res.status(400).json({ error: `Invalid coverage value: ${req.body.coverage}`, mode, command });
                }
                args.push(`--coverage ${coverage}`);
            }

            const argsString = args.join(' ');

            try {
                if (command === 'probability') {
                    console.log("Command RAN : " + `cabal run playground --builddir=${SHARED_BUILD_DIR} -- --json run`)
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
                            mode,
                            command,
                        });
                    }

                    const jsonPath = path.join(workspacePath, 'run-output.json');
                    await fs.writeFile(jsonPath, jsonLine, 'utf-8');

                    console.log("Command RAN : " + `cabal run playground --builddir=${SHARED_BUILD_DIR} -- probability < ${jsonPath}`)
                    const probResult = await execAsync(
                            `cabal run playground --builddir=${SHARED_BUILD_DIR} -- probability < ${jsonPath}`,
                        execOpts
                    );
                    stderr += probResult.stderr;
                    stdout = probResult.stdout;
                } else {
                    console.log("COMMAND RAN : " + `cabal run playground --builddir=${SHARED_BUILD_DIR} -- ${argsString}`)
                    const result = await execAsync(
                        `cabal run playground --builddir=${SHARED_BUILD_DIR} -- ${argsString}`,
                        execOpts
                    );
                    stdout = result.stdout;
                    stderr = result.stderr;
                }
            } catch (error) {
                return res.status(500).json({ error: error.message, stderr: error.stderr ?? stderr, mode, command });
            } finally {
                await fs.rm(workspacePath, { recursive: true, force: true }).catch((cleanupErr) => {
                    console.error(`Failed to clean up workspace ${workspacePath}:`, cleanupErr);
                });
            }

            let msg = stdout.trim();

            if (command === 'probability') {
                // Bare rational output — this branch's cabal invocation never gets --json,
                // regardless of mode, so check this before the mode-based branches.
                const lines = msg.split('\n').map(l => l.trim()).filter(Boolean);
                if (lines.length) {
                    msg = lines[lines.length - 1];
                }
            } else if (mode === 'quantum') {
                // --json was always passed for quantum mode; extract the clean JSON line
                // from Cabal's build noise.
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
            } else {
                const diamondIndex = msg.indexOf("⦅");
                if (diamondIndex !== -1) {
                    msg = msg.slice(diamondIndex, msg.length);
                }
            }

            return res.json({ output: msg, stats: stderr.trim(), mode, command });

        } catch (err) {
            if (workspacePath) {
                await fs.rm(workspacePath, { recursive: true, force: true }).catch(() => {});
            }
            return res.status(500).json({ error: `Server failed to initialize run: ${err.message}`, mode, command });
        }
    });

    return router;
}