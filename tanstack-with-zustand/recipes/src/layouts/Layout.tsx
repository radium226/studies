import { Outlet } from '@tanstack/react-router';

import { CreateRecipeRoute } from '../router';

function BotConversation() {
  const { bot } = CreateRecipeRoute.useRouteContext();

  function generateRecipe() {
    const recipe = bot.read('recipe');
    const newRecipe = {
      name: `Generated Recipe for ${recipe.name}`,
      instructions: recipe.instructions.map((instruction, index) => `Step ${index + 1}: ${instruction}`),
    };
    bot.write('recipe', newRecipe);
    console.log('Generated Recipe:', newRecipe);
  }

  return (
    <div>
      <button onClick={() => generateRecipe() }>Generate Recipe</button>
    </div>
  );
}


export default function Layout() {
  return (
    <div>
      <h1>Recipe Generator</h1>
      <Outlet />
      <BotConversation />
    </div>
  );
}