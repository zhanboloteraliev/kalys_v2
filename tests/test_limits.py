"""Tests for the cost controls (Step 11).

The pure decisions are tested directly. The counter is tested against a real
PostgreSQL when one is running, because the thing worth checking - that two
questions at the same moment cannot both store the same new total - is
behaviour of the SQL statement, which a fake connection would not exercise.
"""

import os

import psycopg
import pytest

from kalys.limits import (
    day_limit_reached,
    questions_today,
    record_question,
    session_limit_reached,
)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://kalys:kalys@localhost:5432/kalys")


class FakeConn:
    """Returns one canned row for the single SELECT in questions_today."""

    def __init__(self, row):
        self.row = row

    def execute(self, *_args, **_kwargs):
        return self

    def fetchone(self):
        return self.row


def test_no_questions_today_when_no_row_exists():
    assert questions_today(FakeConn(None)) == 0


def test_questions_today_reads_the_stored_count():
    assert questions_today(FakeConn((7,))) == 7


def test_day_limit_not_reached_below_the_limit():
    assert day_limit_reached(FakeConn((9,)), limit=10) is False


def test_day_limit_reached_exactly_at_the_limit():
    # The limit is a ceiling: the 10th question of a 10-question day is
    # allowed, the 11th is not, so "reached" must be true at 10.
    assert day_limit_reached(FakeConn((10,)), limit=10) is True


def test_day_limit_reached_above_the_limit():
    assert day_limit_reached(FakeConn((11,)), limit=10) is True


def test_session_limit_counts_questions_already_asked():
    assert session_limit_reached(0, limit=3) is False
    assert session_limit_reached(2, limit=3) is False
    assert session_limit_reached(3, limit=3) is True


def database_running() -> bool:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=2):
            return True
    except Exception:
        return False


@pytest.mark.skipif(not database_running(), reason="needs PostgreSQL (docker compose up -d db)")
def test_record_question_increments_and_is_safe_to_repeat():
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS usage (day DATE PRIMARY KEY, "
            "questions INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute("DELETE FROM usage WHERE day = CURRENT_DATE")
        try:
            assert questions_today(conn) == 0
            assert record_question(conn) == 1
            assert record_question(conn) == 2
            assert questions_today(conn) == 2
        finally:
            conn.execute("DELETE FROM usage WHERE day = CURRENT_DATE")
