import { z } from 'zod';


export const Task = z.object({
  id: z.number().optional(),
  title: z.string(),
  completed: z.boolean().default(false),
});

export type Task = z.infer<typeof Task>;