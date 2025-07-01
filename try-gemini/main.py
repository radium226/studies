from google import genai
from typing import Literal
from google.genai import types
from pydantic import BaseModel, Field
from enum import StrEnum
from colorama import init as colorama_init
from colorama import Fore
from colorama import Style

colorama_init()

GEMINI_API_KEY= os.environ["GEMINI_API_KEY"]

MODEL = "gemini-2.5-flash"


class Breath(StrEnum):
    """Enum representing different types of breathing instructions."""
    
    INHALE = "INHALE"
    EXHALE = "EXHALE"
    HOLD = "HOLD"


class Instruction(BaseModel):
    """Model representing a single instruction for an exercise or workout."""

    content: str = Field(description="The content of the instruction")
    breath: Breath | None

class Exercise(BaseModel):
    """Model representing a single exercise with instructions."""

    type: Literal["exercise"]
    name: str = Field(description="The name of the exercise")
    instructions: list[Instruction] = Field(description="Instructions for performing the exercise")


class Workout(BaseModel):
    """Model representing a workout consisting of multiple exercises."""

    type: Literal["workout"]
    name: str = Field(description="The name of the workout")
    description: str = Field(description="A brief description of the workout")
    exercises: list[Exercise] = Field(description="List of exercises in the workout")



Outcome = Exercise | Workout | None


class Reponse(BaseModel):
    """Response model for the coach's feedback or advice."""

    feedback: str = Field(
        description="Feedback or advice for the user. This is used when the coach needs more information or is providing general advice."
    )

    outcome: Exercise | Workout | None = Field(
        default=None,
        description="An exercise or workout to help the user improve. If the response is just feedback, this will be None."
    )



def main():
    client = genai.Client(api_key=GEMINI_API_KEY)


    chat = client.chats.create(
        model=MODEL,
        config=types.GenerateContentConfig(
            system_instruction=[
                "Tu est un coach sportif qui aide les gens à atteindre leurs objectifs de fitness.",
                "On va te poser des questions, et tu peux répondre soit avec :"
                "- Uniquement un feedback si tu as besoin d'en savoir plus ou si tu veux juste répondre à une question.",
                "- Un exercice avec des instructions détaillées pour aider l'utilisateur à s'améliorer.",
                "- Un workout complet avec plusieurs exercices et des instructions pour chaque exercice.",
                "Tu dois penser en français."
                "Par contre, tu dois donner le nom des exercices en anglais."
            ],
            response_mime_type="application/json",
            response_schema=Reponse, # Pass your Pydantic model
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
            )
        ),
    )

    while True:
        user_input = input()
        if user_input.strip().lower() == "exit":
            print("Exiting the chat.")
            break

        assistant_output = chat.send_message(user_input)
        for i, candidate in enumerate(assistant_output.candidates):
            content = candidate.content
            for part in content.parts:
                if part.thought:
                    print(f"{Fore.BLUE}{part.text}{Style.RESET_ALL}")
                else:
                    if part.text:
                        response = Reponse.model_validate_json(part.text)
                        print(response.feedback)
                        match response.outcome:
                            case Exercise():
                                print(f"{Fore.GREEN}Exercise: {response.outcome.name}{Style.RESET_ALL}")
                                for instruction in response.outcome.instructions:
                                    print(f"- {instruction}")
                            case Workout():
                                print(f"{Fore.GREEN}Workout: {response.outcome.name}{Style.RESET_ALL}")
                                print(response.outcome.description)
                                for exercise in response.outcome.exercises:
                                    print(f"- {exercise.name}: {', '.join(exercise.instructions)}")
                            case None:
                                pass
                        print()
                    else:
                        raise ExceptionGroup("I don't know how to handle this response! ")

            


if __name__ == "__main__":
    main()
