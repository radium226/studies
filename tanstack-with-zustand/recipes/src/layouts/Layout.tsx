import { Outlet } from '@tanstack/react-router';

import { createRecipeRoute } from '../router';

function BotConversation() {
  const { bot } = createRecipeRoute.useRouteContext();

  return (
    <div>
      <button onClick={() => bot.emit({
        type: 'recipeGenerated',
        payload: {
          recipe: {
            name: 'Generated Recipe',
            instructions: ['Step 1: Do something', 'Step 2: Do something else'],
          },
        },
      })}>Generate Recipe</button>
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