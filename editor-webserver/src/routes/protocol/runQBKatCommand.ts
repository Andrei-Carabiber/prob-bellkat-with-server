import {execAsync, SHARED_BUILD_DIR} from "./protocol-route.js";
import crypto from "node:crypto";
import {createIsolatedWorkspace} from "../../workspace.js";
import path from "node:path";
import fs from "node:fs/promises";

export type QBKATProbOutput = {
    mode: "probOnly",
    probabilityMax: number[],
    probabilityMin: number[],
}
export type QBKATProbQualityOutput = {
    mode: "probQuality",

    probability: number[],
    wernerArray: number[]
}

type QBKATReturnType = {
    extremal: {
        coverage_status: any;
        goal_states: Object[];
        initial_state: {
            bell_pairs: any[];
            pc: number;
            rendered: string;
        };
        resolved_budget: number;
        scheduler_choices: {
            max: any[];
            min: any[];
        }
        series: {
            cdf_max: number[];
            cdf_min: number[];
        }

        states: Object[];
        mdp_rendered: string
    }
}

export async function runQBKatCommand(code: string, truncation: number, coverage: number, probOnly: boolean): Promise<QBKATProbOutput | QBKATProbQualityOutput> {

    if (truncation === -1 && coverage === -1) {
        throw new Error("You need to select truncation or coverage")
    } else if (truncation !== -1 && coverage !== -1) {
        throw new Error("You cannot have both truncation and coverage selected")
    }

    let command;
    const requestId = crypto.randomUUID();
    let workspacePath;
    try {
        workspacePath = await createIsolatedWorkspace(requestId);

        const playgroundFile = path.join(workspacePath, 'playground-example/Playground.hs');
        await fs.writeFile(playgroundFile, code, 'utf-8');

        const execOpts = {cwd: workspacePath, maxBuffer: 1024 * 1024 * 10};

        const executionMode = probOnly ? "mdp" : "qmdp"


        //Coverage selected
        if (truncation === -1) {
            if (coverage < 0 || coverage > 1) {

                throw new Error("Truncation cannot be less than 0")
            }
            command = `cabal run playground --builddir=${SHARED_BUILD_DIR} -- --json ${executionMode} --compute-extremal --coverage ${coverage}`;
        }
        //Truncation Selected
        else {
            if (truncation < 0) {
                throw new Error("Coverage needs to be between 0 and 1")
            }
            command = `cabal run playground --builddir=${SHARED_BUILD_DIR} -- --json ${executionMode} --compute-extremal --truncation ${truncation}`;
        }


        //Calculate mixed state probability
        const {stdout: result} = await execAsync(
            command,
            execOpts
        )

        const resultArray = result.split("\n")
        const parsedOutput: QBKATReturnType = JSON.parse(resultArray[resultArray.length - 1]) as QBKATReturnType

        const series = parsedOutput.extremal.series
        if (series.cdf_max.length !== series.cdf_min.length) {
            throw new Error("CDFMax is not the same length as CDFMin! Something went wrong in the server")
        }
        const canCalculateWerner = series.cdf_max.every((value, index) => value === series.cdf_min[index])

        if (!canCalculateWerner || probOnly) {
            return {
                mode: "probOnly",
                probabilityMin: series.cdf_min,
                probabilityMax: series.cdf_max
            } as QBKATProbOutput
        }


        //Calculate Werner probability

        //Replace hasSubset in playground with hasPureSubset
        const newCode = code.replace("hasSubset", "hasPureSubset")
        await fs.writeFile(playgroundFile, newCode, 'utf-8');


        if (truncation === -1) {
            command = `cabal run playground --builddir=${SHARED_BUILD_DIR} -- --json qmdp --compute-extremal --coverage ${coverage}`;
        } else {
            command = `cabal run playground --builddir=${SHARED_BUILD_DIR} -- --json qmdp --compute-extremal --truncation ${truncation}`;
        }

        const {stdout: pureResult} = await execAsync(
            command,
            execOpts
        )

        const pureResultArray = pureResult.split("\n")
        const pureResultParsed: QBKATReturnType = JSON.parse(pureResultArray[pureResultArray.length - 1]) as QBKATReturnType;

        const pureSeries = pureResultParsed.extremal.series

        console.log("PureSeries")
        console.log(pureSeries)

        console.log("NormalSeries")
        console.log(series)

        // Werner quality = [pureSerie] / [MixedSerie]

        const wernerSeries = pureSeries.cdf_max.map((value, index) => {

            if (series.cdf_max[index] === 0) {
                return -1
            }

            else return value / series.cdf_max[index]
        })

        return {
            mode: "probQuality",
            probability: series.cdf_max,
            wernerArray: wernerSeries
        } as QBKATProbQualityOutput


    } catch (e) {
        console.log(e)
        throw e
    } finally {
        await fs.rm(workspacePath, {recursive: true, force: true}).catch((cleanupErr) => {
            console.error(`Failed to clean up workspace ${workspacePath}:`, cleanupErr);
        });
    }

}