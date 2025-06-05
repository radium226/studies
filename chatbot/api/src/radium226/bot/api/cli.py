from click import command

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket
from os import environ

from pydantic import BaseModel, Field, RootModel
from typing import Union, Literal

import uvicorn

from loguru import logger

import llm

import json


ANTHROPIC_API_KEY = environ["ANTHROPIC_API_KEY"]




class Navigate(BaseModel):
    """Print a text to the console."""

    type: Literal["navigate"] = Field("navigate")
    to: Literal["welcome", "settings", "tasks"] = Field(..., description="Where in the website to navigate to")


class ChangeColor(BaseModel):
    """Change the color of the input text."""

    type: Literal["change-color"] = Field("change-color")
    color: str = Field(..., description="Change the color of the website")



class AnyPageAction(RootModel[Union[Navigate, ChangeColor]]):
    """Possible global actions the bot can do."""
    pass

class WelcomePageAction(RootModel[AnyPageAction]):
    """Possible actions the bot can do on the welcome page."""
    pass

class WelcomePageAnswer(BaseModel):
    message: str | None = Field(
        None,
        description="A message to provide feedback to the user",
    )

    actions: list[WelcomePageAction] = Field(
        ...,
        description="The actions to perform as a result of the conversation",
    )


class UpdateEmail(BaseModel):
    """Update the user's email address."""

    type: Literal["update-email"] = Field("update-email")
    email: str = Field(..., description="The new email address to update to")


class SettingsPageAction(RootModel[Union[AnyPageAction, ChangeColor, UpdateEmail]]):
    """Possible actions the bot can do on the settings page."""
    pass


class SettingsPageAnswer(BaseModel):
    message: str | None = Field(
        None,
        description="A message to provide feedback to the user",
    )

    actions: list[SettingsPageAction] = Field(
        ...,
        description="The actions to perform as a result of the conversation",
    )


class AddTask(BaseModel):
    """Add a task to the user's task list."""

    type: Literal["add-task"] = Field("add-task")
    task_title: str = Field(..., description="The task to add to the user's task list", alias="taskTitle")


class TaskListPageAction(RootModel[Union[AnyPageAction, AddTask]]):
    """Possible actions the bot can do on the task list page."""
    pass


class TaskListPageAnswer(BaseModel):

    message: str | None = Field(
        None,
        description="A message to provide feedback to the user",
    )

    actions: list[TaskListPageAction] = Field(
        ...,
        description="The actions to perform as a result of the conversation",
    )



class Question(BaseModel):
    """A question to ask the user."""

    location: Literal["/welcome", "/settings", "/", "/tasks"]
    message: str = Field(..., description="The question to ask the user")



@command()
def cli():

    async def websocket_endpoint(websocket: WebSocket):
        model = llm.get_async_model("claude-3.5-sonnet")
        model.key = ANTHROPIC_API_KEY
        await websocket.accept()
        logger.info("New connection! ")

        async def receive_state():
            """Receive the current state from the client."""
            await websocket.send_json({
                "actions": [
                    {
                        "type": "send_state",
                        "to": "welcome"
                    }
                ],
                "message": None,
            })
            state = await websocket.receive_json()
            return state

        while True:
            try:
                question_obj = await websocket.receive_json()
                question = Question.model_validate(question_obj)

                schema = {
                    "/": WelcomePageAnswer,
                    "/settings": SettingsPageAnswer,
                    "/tasks": TaskListPageAnswer
                }[question.location]


                print(f"Received question: {question}")

                response = await model.prompt(
                    question.message,
                    schema=schema,
                )
                text = await response.text()
                print(f"Response text: <{text}>")
                feedback = schema.model_validate_json(text)
                print(f"Feedback: {feedback}")
                await websocket.send_json(feedback.model_dump(by_alias=True))
            except Exception as e:
                print(e)
                await websocket.send_json({
                    "actions": [],
                    "message": "An error occurred while processing your request."
                })
        await websocket.close()
       

    async def index_endpoint(request):
        return JSONResponse({'message': 'Hello, World!'})


    app = Starlette(
        debug=True,
        routes=[
            WebSocketRoute("/ws", endpoint=websocket_endpoint),
            Route("/", endpoint=index_endpoint, methods=["GET"]),
        ]
    )

    uvicorn.run(app, host="localhost", port=8000, log_level="info")