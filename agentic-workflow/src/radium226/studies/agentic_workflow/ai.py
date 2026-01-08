from typing import Annotated, Literal
from enum import StrEnum, auto
from textwrap import dedent

from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from pydantic import BaseModel, Field

from loguru import logger

from .slack import Message


type Prompt = str


class Response(BaseModel):
    type: Literal["response"] = "response"
    content: str = Field(..., description="The content of the response.")


class AskForMoreDetails(BaseModel):
    type: Literal["ask_for_more_details"] = "ask_for_more_details"
    cause: str = Field(..., description="The information needed from the user.")


type Result = Annotated[Response | AskForMoreDetails, Field(discriminator="type")]


class Country(StrEnum):
    FRANCE = auto()
    USA = auto()
    CHINA = auto()
    INDIA = auto()  


class SQL(BaseModel):
    type: Literal["sql"] = "sql"
    value: str

type GenerateSQLResult = Annotated[
    AskForMoreDetails | SQL,
    Field(discriminator="type")
]


class AI():

    agent: Agent
    message_history: list[Message] = []

    def __init__(self, anthropic_api_key: str):
        model = AnthropicModel(
            "claude-sonnet-4-5", 
            provider=AnthropicProvider(api_key=anthropic_api_key)
        )

        self.agent = Agent(
            model,
            system_prompt="You are an AI assistant that responds to user messages. You can use tools to gather more information if needed. ",
        )


        @self.agent.tool
        def provide_random_name(run_context: RunContext, contry: Country) -> str:
            agent = Agent(
                model,
                system_prompt=dedent("""\
                    You are an agent that provides random names from the given country. 
                """),
            )

            class RandomName(BaseModel):
                type: Literal["random_name"] = "random_name"
                value: str

            type Output = Annotated[
                AskForMoreDetails | RandomName,
                Field(discriminator="type")
            ]

            agent_response = agent.run_sync(
                f"Provide a random full name from {contry.name}. ",
                output_type=Output,
            )
            output = agent_response.output
            logger.debug(f"Random name generated: {output}")
            return output

        @self.agent.tool
        def generate_sql(run_context: RunContext, query_description: str) -> GenerateSQLResult:
            """Generate an SQL query based on the user's request."""
            agent = Agent(
                model,
                system_prompt=dedent("""\
                    You are an agent that generates SQL queries based on user requests.
                                     
                    There is two kind of orders, the "blue" and the "green" orders. The "blue" orders are the ones that are paid with credit card, the "green" orders are the ones that are paid with paypal.
                                     
                    The user should only ask for "blue" orders when he wants to filter for credit card payments, and "green" orders when he wants to filter for paypal payments. If the user does not specify the payment method, you should ask for more details.

                    You have access to the following tables:
                    - users(id: int, name: str, email: str, country: str)
                    - blue_orders(id: int, user_id: int, product: str, amount: float, order_date: date)
                    - green_orders(id: int, user_id: int, product: str, amount: float, order_date: date)
                """),
            )

            result = agent.run_sync(
                f"Generate an SQL query for the following request: {query_description}. ",
                output_type=GenerateSQLResult,
            )
            output = result.output
            logger.debug(f"SQL query generated: {output}")
            return output


    def handle_new_messages(self, new_messages: list[Message]) -> Result:
        logger.debug(f"Handling messages: {new_messages}")
        result = self.agent.run_sync(
            [
                f"{message.author}: {message.content}" 
                for message in new_messages
            ],
            output_type=Result,
            message_history=self.message_history,
        )
        logger.debug(f"AI result: {result}")

        self.message_history = result.all_messages()
        return result.output



class Person(BaseModel):
    first_name: str
    last_name: str

class Family(BaseModel):
    father: Person
    mother: Person
    children: list[Person]


def run_workflow(anthropic_api_key: str):
    model = AnthropicModel(
        "claude-sonnet-4-5", 
        provider=AnthropicProvider(api_key=anthropic_api_key)
    )
    fake_data_agent = Agent(  
        model,
        system_prompt="You are an agent that generate fake data. ",
    )


    agent = Agent(
        model,
        system_prompt="You are an agent that create fake families. ",
    )

    @agent.tool
    def generate_parents(context: RunContext) -> tuple[Person, Person]:
        """Generate a fake person."""
        logger.debug("Generating parents...")
        result = fake_data_agent.run_sync(
            "Generate a fake person", 
            output_type=tuple[Person, Person],
        )
        return result.output
    

    @agent.tool
    def generate_children(context: RunContext, father: Person, mother: Person, number_of_children: int) -> list[Person]:
        """Generate fake children given their parents."""
        
        logger.debug(f"Generating {number_of_children} children for father {father.first_name} {father.last_name} and mother {mother.first_name} {mother.last_name}...")
        result = fake_data_agent.run_sync(
            f"Generate {number_of_children} fake children for father {father.first_name} {father.last_name} and mother {mother.first_name} {mother.last_name}.", 
            output_type=list[Person],
        )
        return result.output
    

    result = agent.run_sync(
        "Create a fake family with parents and 2 children.", 
        output_type=Family,
    )

    messages = result.all_messages()
    for message in messages:
        logger.debug(f"Message: {message}")

    family = result.output
    print("Generated family:")
    print(f"Father: {family.father.first_name} {family.father.last_name}")
    print(f"Mother: {family.mother.first_name} {family.mother.last_name}")
    for i, child in enumerate(family.children):
        print(f"Child {i+1}: {child.first_name} {child.last_name}")