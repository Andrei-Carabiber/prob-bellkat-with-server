import {execAsync, SHARED_BUILD_DIR} from "./protocol-route.js";
import crypto from "node:crypto";
import {createIsolatedWorkspace} from "../../workspace.js";
import path from "node:path";
import fs from "node:fs/promises";
import {redisClient} from "../../redis.js";

//Return type parsed with non-determinism or without werner
export type QBKATProbOutput = {
    mode: "probOnly",
    probabilityMax: number[],
    probabilityMin: number[],
    duration: number,
    _cached?: boolean,
}

//Return type parsed with werner
export type QBKATProbQualityOutput = {
    mode: "probQuality",

    probability: number[],
    wernerArray: number[],
    durations: {
        firstDuration : number
        secondDuration: number
    },
    _cached?: boolean,
}


//Pure return type of cabal run function
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

    };
    mdp_rendered: string;
    transition_count:number;
}

/**
 *
 * @param workspacePath path where code was written
 * @returns The MDP and the duration it took to calculate it
 */
export async function computeMDP(workspacePath: string): Promise<{
    mdp: string,
    duration: number
}> {

    let command = `cabal run -v0 playground --builddir=${SHARED_BUILD_DIR} mdp`

    const execOpts = {cwd: workspacePath, maxBuffer: 1024 * 1024 * 10};

    let start = performance.now()
    const {stdout: result} = await execAsync(
        command,
        execOpts
    )
    let duration = performance.now() - start

    return {
        mdp: result,
        duration
    }
}

export type MDPCacheType = QBKATProbOutput | QBKATProbQualityOutput

/**
 * Retrieves a cached mdp payload from Redis using its model.
 *
 * @param mdp the model string
 * @param truncation true if truncation selected, false if coverage selected
 * @param amount amount of truncation or coverage 0 - inf for truncation, 0 - 1 for coverage
 * @returns QBKATProbOutput | QBKATProbQualityOutput | false
 *
 */
export async function searchCache(mdp: string, truncation: boolean, amount: number): Promise<QBKATProbOutput | QBKATProbQualityOutput | false> {

    const key = `mdp:${mdp}`
    let cached: string | null
    try {
        cached = await redisClient.get(key)
        if (cached) {
            const cachedOutput: MDPCacheType = JSON.parse(cached)
            if (truncation) {
                //Long enough
                if (cachedOutput.mode === 'probOnly' && cachedOutput.probabilityMin.length > amount) {
                    console.log("Cached probOnly with truncation")
                    return cachedOutput
                }
                //Long enough
                else if (cachedOutput.mode === 'probQuality' && cachedOutput.probability.length > amount) {
                    console.log("Cached probQuality with truncation")
                    return cachedOutput
                }
                //Not long enough
                else {
                    console.log("Not Cached because truncation not enough")
                    return false
                }
            } else {
                //Long enough
                if (cachedOutput.mode === 'probOnly' && cachedOutput.probabilityMin[cachedOutput.probabilityMin.length - 1] >= amount) {
                    console.log("Cached probOnly with coverage")
                    return cachedOutput
                }
                //Long enough
                else if (cachedOutput.mode === 'probQuality' && cachedOutput.probability[cachedOutput.probability.length - 1] >= amount) {
                    console.log("Cached probQuality with coverage")
                    return cachedOutput
                } else {
                    console.log("Not Cached because not enough coverage")
                    return false
                }

            }
        }

        //not cached at all
        else {
            console.log("Not Cached because not calculated at all")
            return false
        }
    } catch (error) {
        console.error("REDIS CACHE  SEARCH FAILED. Error: ", error)
        return false
    }
}

export function checkAndParseCache(
    cache: QBKATProbOutput | QBKATProbQualityOutput,
    probOnly: boolean,
    truncation: boolean,
    amount: number
): QBKATProbOutput | QBKATProbQualityOutput | false {

    if (cache.mode === 'probOnly') {
        if (!probOnly) {
            // Result is probOnly but user wanted quality too. Check if it was non deterministic.
            let isEqual = true;
            for (let i = 0; i < cache.probabilityMin.length; i++) {
                if (cache.probabilityMin[i] !== cache.probabilityMax[i]) {
                    isEqual = false;
                    break;
                }
            }
            // They are equal, Werner could have been computed but isn't in cache -> MISS
            if (isEqual) return false;
        }

        // Calculate where to cut
        let sliceEnd = amount + 1;
        if (!truncation) { // coverage
            const cutIndex = cache.probabilityMin.findIndex((nr) => nr >= amount);
            sliceEnd = cutIndex === -1 ? cache.probabilityMin.length : cutIndex + 1;
        }

        return {
            mode: "probOnly",
            probabilityMin: cache.probabilityMin.slice(0, sliceEnd),
            probabilityMax: cache.probabilityMax.slice(0, sliceEnd),
            duration: cache.duration
        };

    } else { // cache.mode === 'probQuality'

        // Calculate where to cut
        let sliceEnd = amount + 1;
        if (!truncation) { // coverage
            const cutIndex = cache.probability.findIndex((nr) => nr >= amount);
            sliceEnd = cutIndex === -1 ? cache.probability.length : cutIndex + 1;
        }

        if (probOnly) {
            return {
                mode: "probOnly",
                probabilityMin: cache.probability.slice(0, sliceEnd),
                probabilityMax: cache.probability.slice(0, sliceEnd),
                duration: cache.durations.firstDuration
            };
        } else {
            return {
                mode: "probQuality",
                probability: cache.probability.slice(0, sliceEnd),
                wernerArray: cache.wernerArray.slice(0, sliceEnd),
                durations: cache.durations
            };
        }
    }
}


export async function runQBKatCommand(code: string, truncation: number, coverage: number, probOnly: boolean): Promise<QBKATProbOutput | QBKATProbQualityOutput> {

    if (truncation === -1 && coverage === -1) {
        throw new Error("You need to select truncation or coverage")
    } else if (truncation !== -1 && coverage !== -1) {
        throw new Error("You cannot have both truncation and coverage selected")
    }
    if (truncation === -1) {
        if (coverage < 0 || coverage > 1) {
            throw new Error("Coverage needs to be between 0 and 1")
        }
    } else {
        if (truncation < 0) {
            throw new Error("Truncation cannot be less than 0")
        }
    }

    let command : string;
    const requestId = crypto.randomUUID();
    let start: number;
    let firstDuration: number;
    let secondDuration: number;
    let workspacePath: string | undefined;
    try {
        workspacePath = await createIsolatedWorkspace(requestId);

        const playgroundFile = path.join(workspacePath, 'playground-example/Playground.hs');
        await fs.writeFile(playgroundFile, code, 'utf-8');

        const execOpts = {cwd: workspacePath, maxBuffer: 1024 * 1024 * 10};


        //Searching Cache
        const {mdp} = await computeMDP(workspacePath)
        const cacheKey = `mdp:${mdp}`;

        const output = await searchCache(mdp, truncation !== -1, truncation === -1 ? coverage : truncation)

        if (output) {
            const parsedCache = checkAndParseCache(output, probOnly, truncation !== -1, truncation === -1 ? coverage : truncation)

            if (parsedCache) {
                console.log("Cache Hit! Returning parsed cache.");
                return {...parsedCache, _cached: true};
            }
        }


        //CACHE FAILED, EXECUTING NORMALLY

        const executionMode = probOnly ? "mdp" : "qmdp"


        //Coverage selected
        if (truncation === -1) {
            if (coverage < 0 || coverage > 1) {
                throw new Error("Coverage needs to be between 0 and 1")
            }
            command = `cabal run -v0 playground --builddir=${SHARED_BUILD_DIR} -- --json ${executionMode} --compute-extremal --coverage ${coverage}`;
        }
        //Truncation Selected
        else {
            if (truncation < 0) {
                throw new Error("Truncation cannot be less than 0")
            }
            command = `cabal run -v0 playground --builddir=${SHARED_BUILD_DIR} -- --json ${executionMode} --compute-extremal --truncation ${truncation}`;
        }


        //Calculate mixed state probability
        start = performance.now()
        const {stdout: result} = await execAsync(
            command,
            execOpts
        )
        firstDuration = performance.now() - start

        const resultArray = result.split("\n")
        const parsedOutput: QBKATReturnType = JSON.parse(resultArray[resultArray.length - 1]) as QBKATReturnType

        const series = parsedOutput.extremal.series
        if (series.cdf_max.length !== series.cdf_min.length) {
            throw new Error("CDFMax is not the same length as CDFMin! Something went wrong in the server")
        }
        const canCalculateWerner = series.cdf_max.every((value, index) => value === series.cdf_min[index])

        if (!canCalculateWerner || probOnly) {

            const probOnlyObject = {
                mode: "probOnly",
                probabilityMin: series.cdf_min,
                probabilityMax: series.cdf_max,
                duration: firstDuration
            } as QBKATProbOutput

            try {
                await redisClient.setEx(cacheKey, 3600, JSON.stringify(probOnlyObject));
            } catch (redisErr) {
                console.error("Failed to save to Redis, but returning computed result anyway:", redisErr);
            }
            return probOnlyObject

        }


        //Calculate Werner probability

        //Replace hasSubset in playground with hasPureSubset
        const newCode = code.replace("hasSubset", "hasPureSubset")
        await fs.writeFile(playgroundFile, newCode, 'utf-8');

        if (series.cdf_max.length <= 1) {
            throw new Error("Invalid size. Try to increase coverage or truncation")
        }
        command = `cabal run -v0 playground --builddir=${SHARED_BUILD_DIR} -- --json qmdp --compute-extremal --truncation ${series.cdf_max.length - 1}`;

        start = performance.now()
        const {stdout: pureResult} = await execAsync(
            command,
            execOpts
        )
        secondDuration = performance.now() - start;

        const pureResultArray = pureResult.split("\n")
        const pureResultParsed: QBKATReturnType = JSON.parse(pureResultArray[pureResultArray.length - 1]) as QBKATReturnType;

        const pureSeries = pureResultParsed.extremal.series

        const wernerSeries = pureSeries.cdf_max.map((value, index) => {

            if (series.cdf_max[index] === 0) {
                return -1
            } else return value / series.cdf_max[index]
        })

        const probQualityObject = {
            mode: "probQuality",
            probability: series.cdf_max,
            wernerArray: wernerSeries,
            durations: {
                firstDuration, secondDuration
            }
        } as QBKATProbQualityOutput

        try {
            await redisClient.setEx(cacheKey, 3600, JSON.stringify(probQualityObject));
        } catch (redisErr) {
            console.error("Failed to save to Redis, but returning computed result anyway:", redisErr);
        }

        return probQualityObject

    } finally {
        if (workspacePath) {
            await fs.rm(workspacePath, {recursive: true, force: true}).catch((cleanupErr) => {
                console.error(`Failed to clean up workspace ${workspacePath}:`, cleanupErr);
            });
        }
    }

}