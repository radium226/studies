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

export const createRecipeRoute = createRoute({
  getParentRoute: () => rootRoute,
  component: CreateRecipePage.component,
  path: '/',
  beforeLoad: ({ context }) => CreateRecipePage.beforeLoad(context),
});

const routeTree = rootRoute.addChildren([
    createRecipeRoute,
  ])

export function doCreateRouter(bot: Bot) {
    return createRouter({
        routeTree,
        context: {
            bot,
        },
    })
}