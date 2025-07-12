from typing import Type
from pydantic import BaseModel, TypeAdapter
from typing import overload

from google.genai import Client
from google.genai.chats import Chat
from google.genai.types import GenerateContentConfig, ThinkingConfig



class LLM():

    _chat: Chat

    def __init__(self, client: Client):
        client.chats.create(
            model="gemini-2.5-flash",
        )
        

    @overload
    def ask[T: BaseModel](self, question: str, model_class: Type[T]) -> T:
        ...

    @overload
    def ask(self, question: str) -> str:
        ...

    def ask[T: BaseModel](self, question: str, model_class: Type[T] | None = None) -> str | T:
        answer = self._ask(question)
        if model_class is None:
            return answer
        else:
            return self._parse_answer(answer, model_class)


    def _parse_answer[T: BaseModel](self, answer: str, model_class: Type[T]) -> T:
        schema = TypeAdapter(model_class).json_schema()
        chat = self._client.chats.create(
            model="gemini-2.5-flash",
            config=GenerateContentConfig(
            system_instruction=[
                ""
            ],
            response_mime_type="application/json",
            response_schema=schema,
            thinking_config=ThinkingConfig(
                include_thoughts=True,
            )
        ),
        )



        schema = TypeAdapter(model_class).json_schema()
        self
        