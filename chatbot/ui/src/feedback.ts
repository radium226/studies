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

export const UpdateEmail = z.object({
  type: z.literal('update-email'),
  email: z.string(),
});
export type UpdateEmail = z.infer<typeof UpdateEmail>;


export const Action = z.discriminatedUnion('type', [
  ChangeColor,
  Navigate,
  UpdateEmail
]);
export type Action = z.infer<typeof Action>;


export const Feedback = z.object({
    message: z.string().nullable(),
    actions: z.array(Action),
})
export type Feedback = z.infer<typeof Feedback>;