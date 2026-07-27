import {Router} from "express";
import {promisify} from "node:util";
import {exec} from "node:child_process";
import {validate} from "../zod/validate-schema.js";
import {ProtocolRequestBody, ProtocolRequestType} from "../zod/run-protocol-body-schema.js";
import {QBKATProbOutput, QBKATProbQualityOutput, runQBKatCommand} from "./runQBKatCommand.js";
import {PBKATOutputType, runPBKatCommand} from "./runPBKatCommand.js";

export const SHARED_BUILD_DIR = '/opt/pbkat/shared-build-cache';
export const execAsync = promisify(exec);


export function createProtocolRouter() {
    const router = Router();

    router.post('/run-protocol', validate(ProtocolRequestBody), async (req, res) => {

        const {code, command, truncation, coverage} = req.body as ProtocolRequestType;


        let result : QBKATProbQualityOutput | QBKATProbOutput | PBKATOutputType;
        try {
            if (command === 'quantum') {
                result = await runQBKatCommand(code, truncation, coverage, false)
            }
            else {
                result = await runPBKatCommand(code, command)
            }

            res.status(200).json(result)
        } catch (e) {
            console.log(e)
            res.status(400).json({error: "Something went wrong: " + String(e)})
        }

    })

    return router


}