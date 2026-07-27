import crypto from "node:crypto";
import {createIsolatedWorkspace} from "../../workspace.js";
import path from "node:path";
import fs from "node:fs/promises";
import {execAsync, SHARED_BUILD_DIR} from "./protocol-route.js";

export type PBKATOutputType = {
    mode: "run" | "probability",
    output: string
}

export async function runPBKatCommand(code: string, command: "run" | "probability"): Promise<PBKATOutputType> {

    const requestId = crypto.randomUUID();
    let workspacePath;
    try {
        workspacePath = await createIsolatedWorkspace(requestId);

        const playgroundFile = path.join(workspacePath, 'playground-example/Playground.hs');
        await fs.writeFile(playgroundFile, code, 'utf-8');

        const execOpts = {cwd: workspacePath, maxBuffer: 1024 * 1024 * 10};

        if (command === 'probability') {
            let commandToExecute = `cabal run playground --builddir=${SHARED_BUILD_DIR} -- --json run`
            const runResult = await execAsync(
                commandToExecute,
                execOpts
            )

            let msgLines: string[] = runResult.stdout.split("\n").map(l => l.trim())
            let jsonLine: string | null = null;
            for (let i = msgLines.length - 1; i >= 0; i--) {
                try {
                    JSON.parse(msgLines[i]);
                    jsonLine = msgLines[i];
                    break;
                } catch {
                    // keep scanning
                }
            }

            if (jsonLine === null) {
                throw new Error("No JSON produced by --json run. Something went wrong");
            }

            const jsonPath = path.join(workspacePath, 'run-output.json');
            await fs.writeFile(jsonPath, jsonLine, 'utf-8');

            const probCmd = `cabal run playground --builddir=${SHARED_BUILD_DIR} -- probability < ${jsonPath}`;
            const probResult = await execAsync(probCmd, execOpts);

            return {
                mode: command,
                output: probResult.stdout
            }

        } else {

            let commandToExecute = `cabal run playground --builddir=${SHARED_BUILD_DIR} -- run`;
            const result = await execAsync(
                commandToExecute,
                execOpts)

            const outputArray = result.stdout.split("\n").filter(line => line.startsWith("⦅"))
            return {
                mode: command,
                output: outputArray[0]
            }
        }
    } catch (e) {
        console.log(e)
        throw e
    } finally {
        await fs.rm(workspacePath, {recursive: true, force: true}).catch((cleanupErr) => {
            console.error(`Failed to clean up workspace ${workspacePath}:`, cleanupErr);
        });
    }
}