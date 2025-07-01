import dlt 
import pendulum
from pendulum import DateTime
from loguru import logger
import duckdb
from pathlib import Path
from typing import Literal



@dlt.source(name="app")
def dlt_source():
    
    @dlt.resource(
        name="contacts",
        write_disposition="merge",
        primary_key="id",
    )
    def contacts(
        state: dlt.sources.incremental[DateTime] = dlt.sources.incremental(
            "updated_at", 
            initial_value=None
        ,
        primary_key="id")
    ):
        updated_at: DateTime | None = state.last_value
        logger.info("updated_at={updated_at}", updated_at=updated_at)

        if updated_at is None:
            yield from [
                {"id": 1, "name": "John Doe", "updated_at": pendulum.now().subtract(days=1)},
                {"id": 2, "name": "Jane Smith", "updated_at": pendulum.now().subtract(days=1)},
            ]
        else:
            yield from [
                {"id": 1, "name": "John Doe 2", "updated_at": pendulum.now()},
                {"id": 3, "name": "Peter McKalloway", "updated_at": pendulum.now()},
            ]

    @dlt.resource(
        name="events",
        write_disposition="merge",
        primary_key="id",
    )
    def events(
        state: dlt.sources.incremental[DateTime] = dlt.sources.incremental(
            "updated_at", 
            initial_value=None
        ,
        primary_key="id")
    ):
        updated_at: DateTime | None = state.start_value
        if updated_at is None:
            yield from [
                {"id": 1, "name": "Spring Sale", "updated_at": pendulum.now().subtract(days=1)},
                {"id": 2, "name": "Summer Promo", "updated_at": pendulum.now().subtract(days=1)},
            ]
        else:
            yield from [
                {"id": 1, "name": "Spring Sale 2023", "updated_at": pendulum.now()},
                {"id": 3, "name": "Winter Clearance", "updated_at": pendulum.now()},
            ]

    yield contacts()
    yield events()



class Source():

    type Part = Literal["crm", "marketing"]

    def load(
        self,
        full: dict[Part, bool] | bool | None = None,
    ) -> None:
        
        logger.debug("Loading data with full={full}", full=full)
        duckdb_connection = duckdb.connect(
            Path("./app.db"),
            read_only=False,
        )
        
        dlt_destination = dlt.destinations.duckdb(
            credentials=duckdb_connection,
        )

        dlt_pipeline = dlt.pipeline(
            pipeline_name="app",
            destination=dlt_destination,
            # pipelines_dir=Path("./pipelines"),
        )

        dlt_pipeline.sync_destination()

        dlt_resources_by_app_stream = {
            "crm": ["contacts"],
            "marketing": ["events"],
        }

        match full:
            case bool():
                full = {
                    "crm": full,
                    "marketing": full,
                }
            
            case dict():
                full = {
                    "crm": full.get("crm", False),
                    "marketing": full.get("marketing", False),
                }

            case None:
                full = {
                    "crm": False,
                    "marketing": False,
                }

        logger.debug("full={full}", full=full)
        for stream, refresh in full.items():
            dlt_resources = dlt_resources_by_app_stream[stream]
            extract_info = dlt_pipeline.extract(
                dlt_source().with_resources(*dlt_resources),
                refresh="drop_resources" if refresh else None,
            )
            logger.debug("extract_info={extract_info}", extract_info=extract_info)

        normalize_info = dlt_pipeline.normalize()
        logger.debug("normalize_info={normalize_info}", normalize_info=normalize_info)
        
        load_info = dlt_pipeline.load()
        logger.debug("load_info={load_info}", load_info=load_info)
        
