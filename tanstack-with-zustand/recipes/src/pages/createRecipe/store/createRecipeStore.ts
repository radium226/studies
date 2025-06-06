import { create } from 'zustand';

import { Recipe } from '../models/recipe';


export interface CreateRecipeState {
  recipe: Recipe;
  setRecipe: (recipe: Recipe) => void;
}

export const useCreateRecipeStore = create<CreateRecipeState>((set) => ({
    recipe: {
        name: 'Unamed recipe',
        instructions: [],
    },
    setRecipe: (recipe) => set({ recipe }),
}));