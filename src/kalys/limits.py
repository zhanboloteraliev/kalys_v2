"""Step 11: cost controls for the chat.

Every answered question costs money (one Bedrock call), so the web UI is
public-facing spending. Two limits bound it:

- a limit for one chat session, so a single visitor cannot use the whole
  day's budget in one sitting;
- a limit for the whole day, across everybody, which is the real ceiling on
  what a day can cost.

The daily count lives in PostgreSQL, not in the process's memory. An
in-memory counter would reset on every restart, and a spending cap that
forgets what it has allowed is not a cap.

Both numbers come from the environment so they can be changed without
touching the code:

    KALYS_MAX_QUESTIONS_PER_DAY=100
    KALYS_MAX_QUESTIONS_PER_SESSION=15

The command line tool (`python -m kalys.answer`) is deliberately not
limited: that is the owner's own use, not public traffic.
"""

import os

MAX_QUESTIONS_PER_DAY = int(os.environ.get("KALYS_MAX_QUESTIONS_PER_DAY", "100"))
MAX_QUESTIONS_PER_SESSION = int(os.environ.get("KALYS_MAX_QUESTIONS_PER_SESSION", "15"))

DAY_LIMIT_MESSAGE = (
    "This demo has answered all the questions it can today. It is a student "
    "project with a daily budget, so the limit is there to keep the cost "
    "predictable. Please come back tomorrow."
)
SESSION_LIMIT_MESSAGE = (
    "You have reached the number of questions allowed in one conversation. "
    "Start a new chat to continue - this demo runs on a small daily budget."
)


def questions_today(conn) -> int:
    row = conn.execute("SELECT questions FROM usage WHERE day = CURRENT_DATE").fetchone()
    return row[0] if row else 0


def day_limit_reached(conn, limit: int = MAX_QUESTIONS_PER_DAY) -> bool:
    return questions_today(conn) >= limit


def session_limit_reached(asked: int, limit: int = MAX_QUESTIONS_PER_SESSION) -> bool:
    return asked >= limit


def record_question(conn) -> int:
    """Count one answered question against today, and return the new total.

    Written as a single INSERT ... ON CONFLICT so that two questions arriving
    at the same moment cannot both read the old number and store the same new
    one, which would undercount and let the day go over its budget.
    """
    row = conn.execute(
        """
        INSERT INTO usage (day, questions) VALUES (CURRENT_DATE, 1)
        ON CONFLICT (day) DO UPDATE SET questions = usage.questions + 1
        RETURNING questions
        """
    ).fetchone()
    return row[0]
