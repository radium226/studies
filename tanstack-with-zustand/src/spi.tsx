import { type ReactNode, useEffect } from "react";

import { useRouteContext } from "@tanstack/react-router";

export type Write<T> = {
    type: string;
    write: (payload: T) => void;
}

export type Payload<TWrite extends Write<any>> = TWrite extends Write<infer TPayload> ? TPayload : never;

export type Read<T> = {
    type: string;
    read: () => T;
}

export type PageComponent<TProps> = (props: TProps) => ReactNode;


export type Page<TReads extends Read<any>, TWrites extends Write<any>, TProps> = {

    component: PageComponent<TProps>;

    beforeLoad: <T extends { bot: Bot<never> }>(options: { context: T }) => { bot: Bot<TReads, TWrites> };

}

export type CallbacksForWrites<TWrites extends Write<any>> = {
    [K in TWrites['type'] as string]: Extract<TWrites, { type: K }>['write'];
}

export type CallbacksForReads<TReads extends Read<any>> = {
    [K in TReads['type'] as string]: Extract<TReads, { type: K }>['read'];
}

export type Callbacks<TReads extends Read<any>, TWrites extends Write<any>> = {
    reads: CallbacksForReads<TReads>;
    writes: CallbacksForWrites<TWrites>;
}


export class Bot<TReads extends Read<any> = never, TWrites extends Write<any> = never> {

    private writeCallbacks: Record<string, (payload: any) => void> = {};
    private readCallbacks: Record<string, () => { payload: any }> = {};

    constructor() { 
    }

    subscribe(callbacks: Callbacks<TReads, TWrites>): () => void {
        this.writeCallbacks = Object.fromEntries(
            Object.entries(callbacks.writes).map(([type, callback]) => [
                type,
                (payload: any) => {
                    console.log(`Callback for event type "${type}" called with payload:`, payload);
                    callback(payload);
                },
            ]),
        );

        this.readCallbacks = Object.fromEntries(
            Object.entries(callbacks.reads).map(([type, callback]) => [
                type,
                () => {
                    console.log(`Read callback for event type "${type}" called`);
                    return callback();
                },
            ]),
        );
        return () => {
            
        };
    }

    write<TWriteType extends TWrites['type']>(type: TWriteType, payload: Payload<Extract<TWrites, { type: TWriteType }>>): void {
        this.writeCallbacks[type](payload);
    }

    read<TReadType extends TReads['type']>(type: TReadType): ReturnType<Extract<TReads, { type: TReadType }>['read']> {
        const readCallback = this.readCallbacks[type];
        if (!readCallback) {
            throw new Error(`No read callback found for type "${type}"`);
        }
        return readCallback() as ReturnType<Extract<TReads, { type: TReadType }>['read']>;
    }

}


export type UseBot<TReads extends Read<any>, TWrites extends Write<any>> = 
    (callbacks: Callbacks<TReads, TWrites>) => void;



export function createPage<
    TReads extends Read<any>,
    TWrites extends Write<any>, 
    TProps
>(
    createComponent: (useBot: UseBot<TReads, TWrites>) => PageComponent<TProps>,
): Page<TReads, TWrites, TProps> {

    function useBot(callbacks: Callbacks<TReads, TWrites>) {
        const bot = useRouteContext({ strict: false }).bot as unknown as Bot<TReads, TWrites>;
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
            return { bot: context.bot as unknown as Bot<TReads, TWrites> };
        },
    };
}