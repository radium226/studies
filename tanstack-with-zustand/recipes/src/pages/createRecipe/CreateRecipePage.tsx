import RecipeName from "./components/RecipeName";
import RecipeInstructions from "./components/RecipeInstructions";

import { Recipe } from "./models/recipe";
import { useCreateRecipeStore } from "./store/createRecipeStore";

import { createPage } from "../../spi";



type CreateRecipePageProps = {

}

type RecipeGeneratedEvent = {
    type: "recipeGenerated";
    payload: {
        recipe: Recipe;
    };
}


export const CreateRecipePage = createPage<RecipeGeneratedEvent, CreateRecipePageProps>((useBot) =>
    ({ }: CreateRecipePageProps) => {
        const { recipe, setRecipe } = useCreateRecipeStore();

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
            recipeGenerated: ({ recipe }) => handleRecipeChange(recipe),
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