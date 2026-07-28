import {z} from "zod";

export const ProtocolRequestBody = z.object({
    code: z.string(),
    command: z.enum(["quantum", "run", "probability"]),
    truncation: z.number().min(0, "Truncation has to be at least 0").or(z.literal(-1)),
    coverage: z.number().min(0).max(1).or(z.literal(-1, "Coverage has to be between 0 and 1")),
    probOnly: z.boolean()
})

export type ProtocolRequestType = z.infer<typeof ProtocolRequestBody>