import { z } from 'zod';


export const User = z.object({
    email: z.string().email().min(5),
    name: z.string(),
    age: z.number().optional(),
})

export type User = z.infer<typeof User>;