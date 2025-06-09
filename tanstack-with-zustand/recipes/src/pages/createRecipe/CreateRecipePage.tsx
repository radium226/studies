import RecipeName from "./components/RecipeName";
import RecipeInstructions from "./components/RecipeInstructions";

import { Recipe } from "./models/recipe";
import { useCreateRecipeStore } from "./store/createRecipeStore";

import { createPage } from "../../spi";
import { useState } from "react";



type CreateRecipePageProps = {

}

type WriteRecipe = {
    type: "recipe";
    write: (recipe: Recipe) => void;
}

type ReadRecipe = {
    type: "recipe";
    read: () => Recipe;
}

export const CreateRecipePage = createPage<ReadRecipe, WriteRecipe, CreateRecipePageProps>((useBot) =>
    ({ }: CreateRecipePageProps) => {
        const [ recipe, setRecipe ] = useState<Recipe>({ name: "Unamed recipe", instructions: [] });

        const handleRecipeNameChange = (recipeName: string | null) => {
            handleRecipeChange({ ...recipe, name: recipeName ?? "Unamed recipe" });
        };
    
        const handleRecipeInstructionsChange = (recipeInstructions: string[]) => {
            handleRecipeChange({ ...recipe, instructions: recipeInstructions });
        };
    
        const handleRecipeChange = (newRecipe: Recipe) => {
            setRecipe({ name: newRecipe.name, instructions: newRecipe.instructions });
        };


        useBot({
            reads: {
                recipe: () => recipe,
            },
            writes: {
                recipe: (recipe) => {
                    console.log("Writing recipe:", recipe);
                    setRecipe(recipe);
                }
            },
        });

        return (
            <div>
                <RecipeName initialValue={ recipe.name } onValueChange={ handleRecipeNameChange } />
                <RecipeInstructions
                    initialValue={ recipe.instructions }
                    onValueChange={ handleRecipeInstructionsChange }
                />
            </div>
        );
});

export default CreateRecipePage;