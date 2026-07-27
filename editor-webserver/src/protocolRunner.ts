import {Router} from 'express';
import {exec} from 'node:child_process';
import {promisify} from 'node:util';
import crypto from 'node:crypto';
import path from 'node:path';
import fs from 'node:fs/promises';
import {createIsolatedWorkspace} from './workspace.js';
import {z} from 'zod'


const SHARED_BUILD_DIR = '/opt/pbkat/shared-build-cache';
const execAsync = promisify(exec);

const RunRequestBodySchema = z.object({
    code: z.string(),
    command: z.enum(["quantum", "run", "probability"]),
    truncation: z.number().min(0, "Truncation has to be at least 0").or(z.literal(-1)),
    coverage: z.number().min(0).max(1).or(z.literal(-1, "Coverage has to be between 0 and 1"))
})

const QuantumOutputSchema = z.object({
    extremal : {
        series: {
            cdf_max: z.array(z.number()),
            cdf_min: z.array(z.number())
        }
    }
})

function createProtocolRouter() {
    const router = Router();

    const validateBody = (schema) => {
        return (req, res, next) => {
            try {
                req.body = schema.parse(req.body);
                next();
            } catch (error) {
                if (error instanceof z.ZodError) {
                    const errorMessages = error.flatten().fieldErrors;
                    return res.status(400).json({
                        error: 'Validation failed',
                        details: errorMessages
                    });
                }

                // Handle unexpected errors
                res.status(500).json({error: 'Internal Server Error'});
            }
        };
    };

    router.post('/run-protocol', validateBody(RunRequestBodySchema), async (req, res) => {
        const {code, command, truncation, coverage} = req.body

        // mdp/qmdp only: mirrors resolveExtremalQuery's mutual-exclusivity check
        // in BellKAT.QuantumPrelude, so a bad combo fails fast with a clear
        // message instead of surfacing as an opaque Haskell ioError.
        const hasTruncation = truncation !== -1
        const hasCoverage = coverage !== -1
        if (hasTruncation && hasCoverage && command === 'quantum') {
            return res.status(400).json({
                error: 'Use either "coverage" or "truncation", not both.',
                command
            });
        }

        const requestId = crypto.randomUUID();
        let workspacePath;

        try {
            workspacePath = await createIsolatedWorkspace(requestId);

            const playgroundFile = path.join(workspacePath, 'playground-example/Playground.hs');
            await fs.writeFile(playgroundFile, code, 'utf-8');

            const execOpts = {cwd: workspacePath, maxBuffer: 1024 * 1024 * 10};
            let stdout = '';
            let stderr = '';

            // 1. Safely construct the execution arguments
            const args = [];

            if (command === 'quantum') {
                args.push('--json')
                args.push("qmdp")
            } else {
                args.push(command);
            }


            if (command === 'quantum') args.push('--compute-extremal');

            if (hasTruncation) {
                args.push(`--truncation ${truncation}`);
            }
            if (hasCoverage) {
                args.push(`--coverage ${coverage}`);
            }

            const argsString = args.join(' ');

            let lastRanCommand;

            try {
                if (command === 'probability') {
                    lastRanCommand = `cabal run playground --builddir=${SHARED_BUILD_DIR} -- --json run`
                    const runResult = await execAsync(
                        lastRanCommand,
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
                            command,
                        });
                    }

                    const jsonPath = path.join(workspacePath, 'run-output.json');
                    await fs.writeFile(jsonPath, jsonLine, 'utf-8');

                    lastRanCommand = `cabal run playground --builddir=${SHARED_BUILD_DIR} -- probability < ${jsonPath}`
                    const probResult = await execAsync(
                        lastRanCommand,
                        execOpts
                    );
                    stderr += probResult.stderr;
                    stdout = probResult.stdout;
                }
                else /* run or qmdp */ {
                    lastRanCommand = `cabal run playground --builddir=${SHARED_BUILD_DIR} -- ${argsString}`;
                    const result = await execAsync(
                        lastRanCommand,
                        execOpts
                    );
                    stdout = result.stdout;
                    stderr = result.stderr;
                }
            } catch (error) {
                return res.status(500).json({
                    error: error.message,
                    stderr: error.stderr ?? stderr,
                    command
                });
            } finally {
                console.log("Last ran command is: " + lastRanCommand)
                await fs.rm(workspacePath, {recursive: true, force: true}).catch((cleanupErr) => {
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
            } else if (command === 'quantum') {
                const candidateLines = msg.split('\n').map(l => l.trim()).filter(Boolean);
                let jsonLine = null;
                for (let i = candidateLines.length - 1; i >= 0; i--) {
                    try {
                        JSON.parse(candidateLines[i]);
                        jsonLine = candidateLines[i];
                        break;
                    } catch {
                    }
                }
                msg = jsonLine || '{"error": "Failed to extract JSON from execution output."}';

                //TODO: Add parsing
                //
                // const parsed = JSON.parse(msg)
                // const isGood = z.safeParse(QuantumOutputSchema, parsed)
                // if (isGood) {
                //     console.log("Parsed")
                //     console.log(parsed.extremal.series.cdf_max)
                //     console.log(parsed.extremal.series.cdf_min)
                // }

            } else {
                const diamondIndex = msg.indexOf("⦅");
                if (diamondIndex !== -1) {
                    msg = msg.slice(diamondIndex, msg.length);
                }
            }

            return res.json({output: msg, stats: stderr.trim(), command});

        } catch (err) {
            if (workspacePath) {
                await fs.rm(workspacePath, {recursive: true, force: true}).catch(() => {
                });
            }
            return res.status(500).json({
                error: `Server failed to initialize run: ${err.message}`,
                command
            });
        }
    });

    return router;
}