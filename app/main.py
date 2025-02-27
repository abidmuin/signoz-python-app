import asyncio
import logging
import os
import random
from contextlib import asynccontextmanager

import asyncpg
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from opentelemetry import trace
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.trace.status import Status, StatusCode

load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")

random.seed(54321)

trace_provider = trace.get_tracer_provider()
tracer = trace_provider.get_tracer(__name__)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DB_POOL = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global DB_POOL
    DB_POOL = await asyncpg.create_pool(user=DB_USER, password=DB_PASSWORD, database=DB_NAME,
                                        host=DB_HOST, port=DB_PORT)
    AsyncPGInstrumentor().instrument(tracer_provider=trace_provider)
    yield
    await DB_POOL.close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def read_root():
    return {"Hello": "World"}


@app.get("/ping")
async def health_check():
    return "pong"


@app.get("/items/{item_id}")
async def read_item(item_id: int, q: str = None):
    if item_id % 2 == 0:
        # mock io - wait for x seconds
        seconds = random.uniform(0, 3)
        await asyncio.sleep(seconds)
    return {"item_id": item_id, "q": q}


@app.get("/invalid")
async def invalid():
    raise ValueError("Invalid ")


@app.get("/exception")
async def exception():
    try:
        raise ValueError("sadness")
    except Exception as ex:
        logger.error(ex, exc_info=True)
        span = trace.get_current_span()

        # generate random number
        seconds = random.uniform(0, 30)

        # record_exception converts the exception into a span event.
        exception = IOError("Failed at " + str(seconds))
        span.record_exception(exception)
        span.set_attributes({'est': True})
        # Update the span status to failed.
        span.set_status(Status(StatusCode.ERROR, "internal error"))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Got sadness")


@app.get("/external-api")
def external_api():
    seconds = random.uniform(0, 3)
    response = requests.get(f"https://httpbin.org/delay/{seconds}")
    response.close()
    return "ok"


@app.post("/insert-data")
async def insert_data(name: str):
    try:
        async with DB_POOL.acquire() as conn:
            async with conn.transaction():
                query = "INSERT INTO users (name) VALUES ($1) RETURNING id"
                user_id = await conn.fetchval(query, name)

                with tracer.start_as_current_span("db-insert") as span:
                    span.set_attribute("db.system", "postgresql")
                    span.set_attribute("db.operation", "INSERT")
                    span.set_attribute("db.table", "users")
                    span.set_attribute("inserted.id", user_id)

                return {"message": "Data inserted", "id": user_id}
    except Exception as e:
        logger.error(e, exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")
