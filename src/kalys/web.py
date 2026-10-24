"""Step 8: the chat web UI, built with Chainlit.

Keeps everything from kalys.answer unchanged - this file only wires that
pipeline (hybrid search -> article context -> Bedrock) to a chat window,
and streams the answer token-by-token as it comes back from Bedrock, for
the "typing" effect of a normal AI chat app.

The embedding model and Bedrock client are created once, when this module
is first loaded (Chainlit keeps one Python process running), not on every
message - loading the local embedding model takes a few seconds.

Run:
  docker compose up -d db
  uv run python -m kalys.embed                   (once, after kalys.translate)
  uv run chainlit run src/kalys/web.py -w
"""

import asyncio
import os
from pathlib import Path

import chainlit as cl
import psycopg
from chainlit.server import app as chainlit_app

from kalys import gate
from kalys.answer import BedrockClient, build_prompt, gather_contexts
from kalys.embed import check_embedder_matches, embedder_from_env
from kalys.kyrgyz import answer_to_kyrgyz, question_to_russian
from kalys.limits import (
    DAY_LIMIT_MESSAGE,
    SESSION_LIMIT_MESSAGE,
    day_limit_reached,
    record_question,
    session_limit_reached,
)

SCHEMA_FILE = Path("db/schema.sql")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://kalys:kalys@localhost:5432/kalys")

embedder = embedder_from_env()

# The robot gate goes in front of Chainlit's own FastAPI app, so it also covers
# the websocket the chat talks over - not just the page. It installs itself only
# when the Turnstile keys are set, which keeps local runs and the SSM tunnel
# working with no keys at all.
print(gate.describe(), flush=True)
gate.install(chainlit_app)
bedrock = BedrockClient()


@cl.set_starters
async def starters() -> list[cl.Starter]:
    return [
        cl.Starter(
            label="Stopped by police",
            message="The police stopped me. What are my rights?",
        ),
        cl.Starter(
            label="Search of a home",
            message="Can the police search my home?",
        ),
        cl.Starter(
            label="Being arrested",
            message="What happens when someone is arrested?",
        ),
    ]


@cl.on_message
async def on_message(message: cl.Message) -> None:
    question = message.content

    asked = cl.user_session.get("asked", 0)
    if session_limit_reached(asked):
        await cl.Message(content=SESSION_LIMIT_MESSAGE).send()
        return

    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        check_embedder_matches(conn, embedder)
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
        if day_limit_reached(conn):
            await cl.Message(content=DAY_LIMIT_MESSAGE).send()
            return
        # A Kyrgyz question is searched and answered in Russian, then the
        # answer is translated back (see kalys.kyrgyz). English and Russian
        # questions pass through untouched.
        search_question, was_kyrgyz = question_to_russian(bedrock, question)
        contexts = gather_contexts(conn, embedder, search_question)
        if contexts:
            # Counted before the answer is generated, not after: a question
            # that fails halfway still cost a Bedrock call, and counting only
            # successes would let repeated failures run past the budget.
            record_question(conn)

    if not contexts:
        await cl.Message(content="No matching articles were found for this question.").send()
        return

    cl.user_session.set("asked", asked + 1)
    prompt = build_prompt(search_question, contexts)

    if was_kyrgyz:
        # The answer cannot be streamed here: it has to be finished in Russian
        # before it can be translated, and streaming Russian only to replace it
        # with Kyrgyz would be worse than a short wait.
        reply = cl.Message(content="")
        await reply.send()
        async with cl.Step(name="Жооп даярдалууда", type="run"):
            russian = await asyncio.to_thread(bedrock.ask, prompt)
            reply.content = await asyncio.to_thread(answer_to_kyrgyz, bedrock, russian)
        await reply.update()
        return

    reply = cl.Message(content="")
    await reply.send()
    for piece in bedrock.ask_stream(prompt):
        await reply.stream_token(piece)
    await reply.update()
