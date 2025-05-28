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


ANTHROPIC_API_KEY = environ["ANTHROPIC_API_KEY"]



class PrintText(BaseModel):
    """Print a text to the console."""

    type: Literal["print-text"] = Field("print-text")
    text: str = Field(..., description="The text to print to the console")


class ChangeInputColor(BaseModel):
    """Change the color of the input text."""

    type: Literal["change-color"] = Field("change-color")
    color: str = Field(..., description="The color to change the input to")


class Action(RootModel[Union[PrintText, ChangeInputColor]]):
    """The outcome of the conversation."""
    pass


class Outcome(BaseModel):

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

        async def change_color(color: str):
            """Change the color of the input text."""
            await websocket.send_json({
                "type": "change-color",
                "color": color
            })


        conversation = model.conversation(
            tools=[
                change_color,
            ]
        )

        schema = Outcome.model_json_schema()
        print("Schema:", schema)

        while True:
            question = await websocket.receive_text()
            response = await conversation.chain(question).text()
            await websocket.send_json({
                "type": "print-text",
                "text": response,
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