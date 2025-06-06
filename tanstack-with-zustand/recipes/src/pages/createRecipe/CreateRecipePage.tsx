import { useEffect, useState } from "react";

import RecipeName from "./components/RecipeName";
import RecipeInstructions from "./components/RecipeInstructions";

import { Recipe } from "./models/recipe";

import { useBotContext } from "../../services/bot/botContext";



export default function CreateRecipePage() {

    const [recipe, setRecipe] = useState<Recipe>({
        name: "Unnamed recipe",
        instructions: [],
    });
    const { bot } = useBotContext();

    const handleRecipeNameChange = (recipeName: string | null) => {
        handleRecipeChange({ ...recipe, name: recipeName ?? "Unamed recipe" });
    };

    const handleRecipeInstructionsChange = (recipeInstructions: string[]) => {
        handleRecipeChange({ ...recipe, instructions: recipeInstructions });
    };

    const handleRecipeChange = (newRecipe: Recipe) => {
        setRecipe({ name: newRecipe.name, instructions: newRecipe.instructions });
    };

    useEffect(() => {
        return bot.subscribe<Recipe>("recipeGenerated", (generatedRecipe: Recipe) => {
            console.log("Recipe generated:", recipe);
            handleRecipeChange({
                name: generatedRecipe.name,
                instructions: [...recipe.instructions, ...generatedRecipe.instructions],
            });
        });
    }, [recipe, bot]);

    return (
        <div>
            <RecipeName initialValue={ recipe.name } onValueChange={ handleRecipeNameChange } />
            <RecipeInstructions
                initialValue={ recipe.instructions }
                onValueChange={ handleRecipeInstructionsChange }
            />
        </div>
    );
}