import { useContext, createContext, type PropsWithChildren } from "react";
import { Bot } from "./bot";

export type BotContext = {
    bot: Bot;
}


export const BotContext = createContext<BotContext | null>(null);


export function useBotContext(): BotContext {
    const context = useContext(BotContext);
    if (!context) {
        throw new Error("useBotContext must be used within a BotProvider");
    }
    return context;
}

export type BotProviderProps = PropsWithChildren<{}>;

export function BotProvider({ children }: BotProviderProps) {
    const bot = new Bot();
    const botContext = {
        bot
    };

    return (
        <BotContext.Provider value={ botContext }>
            {children}
        </BotContext.Provider>
    );
}