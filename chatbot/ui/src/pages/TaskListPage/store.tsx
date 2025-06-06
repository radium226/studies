import { create } from 'zustand';

import { Task } from './models';


export type TaskListStore = {
  tasks: Task[];
  addTask: (task: Task) => void;
  removeTask: (task: Task) => void;
  toggleTask: (task: Task) => void; // Optional for future use
}


export const useTaskListStore = create<TaskListStore>((set) => ({
  
  tasks: [],
  
  addTask: (task) => 
    set((state) => 
      ({ 
        tasks: [
          ...state.tasks, 
          { 
            id: state.tasks.length,  
            ...task 
          }
        ] 
      })
    ),

  removeTask: (task) =>
    set((state) => 
      ({ tasks: state.tasks.filter((t) => 
        t.id !== task.id
      ) })
    ),

  toggleTask: (task) =>
    set((state) => 
      ({ tasks: state.tasks.map((t) =>
        t.id === task.id ? { ...t, completed: !t.completed } : t
      )})
    )

}));