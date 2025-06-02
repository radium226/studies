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
    to: str = Field(..., description="Where in the website to navigate to")


class ChangeColor(BaseModel):
    """Change the color of the input text."""

    type: Literal["change-color"] = Field("change-color")
    color: str = Field(..., description="Change the color of the website")


class Action(RootModel[Union[Navigate, ChangeColor]]):
    """Possible actions the bot can do."""
    pass


class Feedback(BaseModel):

    message: str | None = Field(
        None,
        description="A message to provide feedback to the user",
    )

    actions: list[Action] = Field(
        ...,
        description="The actions to perform as a result of the conversation",
    )





@command()
def cli():

    async def websocket_endpoint(websocket: WebSocket):
        model = llm.get_async_model("claude-3.5-sonnet")
        model.key = ANTHROPIC_API_KEY
        await websocket.accept()
        logger.info("New connection! ")

        while True:
            question = await websocket.receive_text()
            print(f"Received question: {question}")
            response = await model.prompt(
                question,
                schema=Feedback,
            )
            text = await response.text()
            print(f"Response text: {text}")
            feedback = Feedback.model_validate_json(text)
            print(f"Feedback: {feedback}")
            await websocket.send_json(feedback.model_dump())
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