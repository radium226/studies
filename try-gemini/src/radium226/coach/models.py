from pydantic import BaseModel, Field
from typing import Literal


Breath = Literal["inhale", "exhale", "hold"]


class Instruction(BaseModel):
    """Model representing a single instruction for an exercise or workout."""

    content: str = Field(description="The content of the instruction")
    breath: Breath | None


class Exercise(BaseModel):
    """Model representing a single exercise with instructions."""

    type: Literal["exercise"]
    name: str = Field(description="The name of the exercise")
    instructions: list[Instruction] = Field(description="Instructions for performing the exercise")



class Rep(BaseModel):
    """Model representing a single repetition of an exercise."""

    exercise: Exercise = Field(description="The exercise to be performed")
    number_of: int = Field(
        description="The number of repetitions to perform for this exercise"
    )

class Set(BaseModel):
    """Model representing a set of repetitions for an exercise."""

    reps: list[Rep] = Field(
        description="List of repetitions for the set, each containing an exercise and the number of times to perform it"
    )


class Workout(BaseModel):
    """Model representing a workout consisting of multiple exercises."""

    type: Literal["workout"]
    name: str = Field(description="The name of the workout")
    description: str = Field(description="A brief description of the workout")
    sets: list[Set] = Field(description="List of sets in the workout")


