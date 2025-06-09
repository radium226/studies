import { type ReactNode, useEffect } from "react";

import { useRouteContext } from "@tanstack/react-router";

export type Event<T> = {
    type: string;
    payload: T;
}


export type PageComponent<TProps> = (props: TProps) => ReactNode;


export type Page<TEvents extends Event<any>, TProps> = {

    component: PageComponent<TProps>;

    beforeLoad: <T extends { bot: Bot<never> }>(options: { context: T }) => { bot: Bot<TEvents> };

}

export type ExtractEventsFromPage<TPage> = TPage extends Page<infer TEvents, any> ? TEvents : never;

export type CallbacksForEvents<TEvents extends Event<any>> = {
    [K in TEvents['type'] as string]: (payload: Extract<TEvents, { type: K }>['payload']) => void;
}


export class Bot<TEvents extends Event<any> = never> {

    private callbacks: Record<string, (payload: any) => void>;

    constructor(callbacks?: Record<string, (payload: any) => void>) {
        console.log(`Creating a new Bot instance with callbacks:`, callbacks);
        this.callbacks = callbacks ?? {};
    }

    subscribe(callbacks: CallbacksForEvents<TEvents>): () => void {
        this.callbacks = Object.fromEntries(
            Object.entries(callbacks).map(([type, callback]) => [
                type,
                (payload: any) => {
                    console.log(`Callback for event type "${type}" called with payload:`, payload);
                    callback(payload);
                },
            ]),
        );
        console.log(this.callbacks);
        return () => {
            
        };
    }

    emit<TEvent extends TEvents>(event: TEvent): void {
        console.log(`Emitting event: ${event.type}`, event.payload);
        console.log(this.callbacks[event.type])
        this.callbacks[event.type](event.payload);
    }

}


export type UseBot<TEvents extends Event<any>> = (callbacks: CallbacksForEvents<TEvents>) => void;



export function createPage<
    TEvents extends Event<any>, 
    TProps
>(
    createComponent: (useBot: UseBot<TEvents>) => PageComponent<TProps>,
): Page<TEvents, TProps> {

    function useBot(callbacks: CallbacksForEvents<TEvents>) {
        const bot = useRouteContext({ strict: false }).bot as Bot<TEvents>;
        useEffect(() => {
            const unsubscribe = bot.subscribe(callbacks);
            return () => {
                unsubscribe();
            };
        }, [bot, callbacks]);
    }

    return {
        
        component: createComponent(useBot),
        
        beforeLoad: ({ context }) => {
            return { bot: context.bot as Bot<TEvents> };
        },
    };
}