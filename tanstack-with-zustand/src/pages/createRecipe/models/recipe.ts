import { z } from 'zod';


export const Recipe = z.object({
  name: z.string().min(1, 'Recipe name is required'),
  instructions: z.string().array().min(1, 'At least one instruction is required'),
});

export type Recipe = z.infer<typeof Recipe>;