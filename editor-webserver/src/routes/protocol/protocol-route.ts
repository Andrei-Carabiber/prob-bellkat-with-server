import {Router} from "express";
import {promisify} from "node:util";
import {exec} from "node:child_process";
import {validate} from "../zod/validate-schema.js";
import {ProtocolRequestBody, ProtocolRequestType} from "../zod/run-protocol-body-schema.js";
import {runQBKatCommand} from "./runQBKatCommand.js";
import {runPBKatCommand} from "./runPBKatCommand.js";

export const SHARED_BUILD_DIR = '/opt/pbkat/shared-build-cache';
export const execAsync = promisify(exec);


export function createProtocolRouter() {
    const router = Router();

    router.post('/run-protocol', validate(ProtocolRequestBody), async (req, res) => {

        const {code, command, truncation, coverage} = req.body as ProtocolRequestType;


        let result;
        try {
            if (command === 'quantum') {
                result = await runQBKatCommand(code, truncation, coverage, false)
            }
            else {
                result = await runPBKatCommand(code, command)
            }

            res.json(result)
        } catch (e) {
            console.log(e)
        }



    })

    return router


}