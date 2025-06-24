import { doCreateRouter } from './router'

declare module '@tanstack/react-router' {
    interface Register {
        // This infers the type of our router and registers it across your entire project
        router: ReturnType<typeof doCreateRouter>;
    }
}