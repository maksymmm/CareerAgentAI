"""Candidate readiness must fail closed on storage corruption after startup."""

from career_agent_ai.application.runtime.candidate_service import CandidateServiceWSGIApp


def test_readyz_rechecks_sqlite_integrity_and_closes_database(monkeypatch):
    from career_agent_ai.application.runtime import candidate_service

    class Connection:
        def execute(self, query):
            assert query == "PRAGMA quick_check"
            return self

        def fetchall(self):
            return [("database disk image is malformed",)]

    class Database:
        connection = Connection()
        closed = False

        def close(self):
            self.closed = True

    database = Database()
    monkeypatch.setattr(
        candidate_service.SQLiteDatabase,
        "open_read_only",
        lambda path: database,
    )

    class Delegate:
        def __call__(self, environ, start_response):
            raise AssertionError("readiness must not delegate to authenticated API")

        def close(self):
            pass

    statuses = []
    app = CandidateServiceWSGIApp(Delegate(), database_path="unused.db")
    body = app(
        {"PATH_INFO": "/readyz", "REQUEST_METHOD": "GET"},
        lambda status, headers: statuses.append((status, dict(headers))),
    )
    assert statuses[0][0] == "503 Service Unavailable"
    assert statuses[0][1]["Cache-Control"] == "no-store"
    assert body == [b'{"status":"unavailable"}']
    assert database.closed
