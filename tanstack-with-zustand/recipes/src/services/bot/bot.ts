import { useCreateRecipeStore } from "../../pages/createRecipe/store/createRecipeStore";

type EventType = "recipeGenerated"


type EventCallback<T> = (data: T) => void;

type Unsubscribe = () => void;


export class Bot {

    private eventCallbacksByEventType: Record<EventType, EventCallback<any>[]> = {
        "recipeGenerated": [],
    }

    constructor() {

    }
    
    subscribe<T>(
        eventType: EventType, 
        eventCallback: EventCallback<T>,
    ): Unsubscribe {
        this.eventCallbacksByEventType[eventType].push(eventCallback);

        return () => {
            const callbacks = this.eventCallbacksByEventType[eventType];
            const index = callbacks.indexOf(eventCallback);
            if (index !== -1) {
                callbacks.splice(index, 1);
            }
        };
    }

    emit<T>(eventType: EventType, data: T) {
        const callbacks = this.eventCallbacksByEventType[eventType];
        for (const callback of callbacks) {
            callback(data);
        }
    }

    generateRecipe() {
        const recipe = {
            name: "Sample Recipe",
            instructions: ["Ingredient 1", "Ingredient 2"],
        };
        this.emit("recipeGenerated", recipe);

    }
}