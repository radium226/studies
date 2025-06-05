import { z } from 'zod';


export const AddTask = z.object({
  type: z.literal('add-task'),
  taskTitle: z.string(),
});
export type AddTask = z.infer<typeof AddTask>;


export const TaskListPageAction = z.discriminatedUnion('type', [
  AddTask,
]);
export type TaskListPageAction = z.infer<typeof TaskListPageAction>;