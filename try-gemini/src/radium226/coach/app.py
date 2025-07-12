from click import command
from typing import Annotated, Union
from google import genai
from typing import Literal
from google.genai import types
from pydantic import BaseModel, Field
from enum import StrEnum
from colorama import init as colorama_init
from colorama import Fore
from colorama import Style
import os
from loguru import logger
import json
import fuzzy_json

from .models import Exercise, Workout, Instruction

colorama_init()

GEMINI_API_KEY= os.environ["GEMINI_API_KEY"]

MODEL = "gemini-2.5-flash"


class ExerciseFeedbackPart(BaseModel):

    type: Literal["exercise"]

    exercise: Exercise


class TextFeedbackPart(BaseModel):

    type: Literal["text"]

    text: str

class WorkoutFeedbackPart(BaseModel):

    type: Literal["workout"]

    workout: Workout

type FeedbackPart = Annotated[
    Union[
        ExerciseFeedbackPart, 
        TextFeedbackPart,
        WorkoutFeedbackPart,
    ],
    Field(
        discriminator="type",
        description="Part of the feedback, which can be an exercise, text, or workout."
    )
]

type Feedback = list[FeedbackPart]


class Response(BaseModel):

    feedback: Feedback


@command()
def app() -> None:
    client = genai.Client(api_key=GEMINI_API_KEY)

    available_exercises: list[Exercise] = []
    current_workout: Workout | None = None


    def add_to_available_exercises(exercise: Exercise):
        """Add an exercise to the list of available exercises."""

        logger.info(f"Saving exercise: {exercise.name}")
        available_exercises.append(exercise)

    def list_available_exercises() -> list[Exercise]:
        """List all available exercises."""

        logger.info(f"Listing available exercises...")
        return available_exercises

    
    def set_current_workout(workout: Workout) -> None:
        """Set the current workout to the provided workout."""
        nonlocal current_workout
        logger.info(f"Updating current workout...")
        current_workout = workout

    def get_current_workout() -> Workout | None:
        """Get the current workout."""
        nonlocal current_workout
        logger.info(f"Loading current workout...")
        return current_workout


    chat = client.chats.create(
        model=MODEL,
        config=types.GenerateContentConfig(
            system_instruction=[
                "Tu est un coach sportif qui aide les gens à atteindre leurs objectifs de fitness.",
                "On va te poser des questions, et tu peux répondre soit avec :"
                "- Uniquement un feedback si tu as besoin d'en savoir plus ou si tu veux juste répondre à une question.",
                "- Un exercice avec des instructions détaillées pour aider l'utilisateur à s'améliorer.",
                "- Un workout complet avec plusieurs exercices et des instructions pour chaque exercice.",
                "Si tu mets à jour l'entraînement actuel, ça n'est pas la peine de le répéter dans la réponse.",
                "Tu dois penser en français."
                "Par contre, tu dois donner le nom des exercices en anglais."
                "Tous les exercices utilisé lors d'un entrainement doivent être dans la liste des exercices disponibles.",
                "Tu ne dois répondre qu'avec le schema suivant :",
                json.dumps(Response.model_json_schema()),
                "Ta réponse doit être un JSON valide (PAS de Markdown, PAS de texte brut, PAS de code, PAS de ```json).",
                "Par défaut, tu n'ajoutes JAMAIS les exercices à la liste des exercices disponibles, sauf si on te le demande expressement.",
                "Tu n'ajoute les exercices à la liste des exercices disponibles que si on te l'a expressement demandé.",
            ],
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
            ),
            #response_mime_type="application/json",
            #response_schema=Response,  # Pass your Pydantic model
            tools=[
                add_to_available_exercises,
                list_available_exercises,
                get_current_workout,
                set_current_workout,
            ]
        ),
    )

    while True:
        user_input = input()
        if user_input.strip().lower() == "exit":
            print("Exiting the chat.")
            break

        assistant_output = chat.send_message(
            user_input,
        )
        try:
            for i, candidate in enumerate(assistant_output.candidates or []):
            
                content = candidate.content
                parts = content.parts or [] if content else []
                for part in parts:
                    if part.thought:
                        print(f"{Fore.BLUE}{part.text}{Style.RESET_ALL}")
                    else:
                        text = part.text or ""
                        text.replace("```json", "")
                        text.replace("```", "")
                        response = Response.model_validate(fuzzy_json.loads(text))
                        for feedback_part in response.feedback:
                            match feedback_part:
                                case ExerciseFeedbackPart(exercise=exercise):
                                    print(f"{Fore.GREEN}Exercise: {exercise.name}{Style.RESET_ALL}")
                                    for instruction in exercise.instructions:
                                        print(f"- {instruction.content} ({instruction.breath})")
                                    add_to_available_exercises(exercise)
                                case WorkoutFeedbackPart(workout=workout):
                                    print(f"{Fore.GREEN}Workout: {workout.name}{Style.RESET_ALL}")
                                    print(workout.description)
                                    for set in workout.sets:
                                        for rep in set.reps:
                                            print(f"- {rep.number_of}x {rep.exercise.name}")
                                    set_current_workout(workout)
                                case TextFeedbackPart(text=text):
                                    print(text)
                                case _:
                                    raise Exception("Unknown feedback part type!")
        except:
            print(assistant_output)
            raise Exception("I don't know how to handle this response! ")