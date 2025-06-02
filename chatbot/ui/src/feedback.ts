import { z } from 'zod';

export const ChangeColor = z.object({
  type: z.literal('change-color'),
  color: z.string(),
});
export type ChangeColor = z.infer<typeof ChangeColor>;


export const Navigate = z.object({
  type: z.literal('navigate'),
  to: z.string(),
});
export type Navigate = z.infer<typeof Navigate>;


export const Action = z.discriminatedUnion('type', [
  ChangeColor,
  Navigate,
]);
export type Action = z.infer<typeof Action>;


export const Feedback = z.object({
    message: z.string().optional(),
    actions: z.array(Action),
})
export type Feedback = z.infer<typeof Feedback>;