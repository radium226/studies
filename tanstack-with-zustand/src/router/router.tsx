import { createRootRouteWithContext, createRoute, createRouter } from '@tanstack/react-router'
import { CreateRecipePage } from '../pages/createRecipe/CreateRecipePage'
import Layout from '../layouts/Layout'
import { Bot } from '../spi'


type RouteContext = {
    bot: Bot;
}



const rootRoute = createRootRouteWithContext<RouteContext>()({
    component: Layout,
});

export const CreateRecipeRoute = createRoute({
  getParentRoute: () => rootRoute,
  component: CreateRecipePage.component,
  path: "/create-recipe",
  beforeLoad: CreateRecipePage.beforeLoad,
});

// export const GenerateRandomNumberRoute = createRoute({
//   getParentRoute: () => rootRoute,
//   component: CreateRecipePage.component,
//   path: "/random-number",
//   beforeLoad: CreateRecipePage.beforeLoad,
// });

const routeTree = rootRoute.addChildren([
    CreateRecipeRoute,
])

export function doCreateRouter(bot: Bot) {
    return createRouter({
        routeTree,
        context: {
            bot,
        },
    })
}