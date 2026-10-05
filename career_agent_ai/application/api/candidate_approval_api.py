"""Authenticated candidate approval API with a dependency-free WSGI adapter."""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Mapping, Protocol

from career_agent_ai.application.career import CandidateApprovalSubmission


class CandidateApprovalLoop(Protocol):
    def get_candidate_approval_prompt(self, run_id: str, *, user_id: str): ...
    def resume_candidate_submission(self, *, user_id: str, submission): ...


class CandidateApprovalWSGIApp:
    """Authenticate a candidate before reading or changing durable loop state."""

    MAX_BODY_BYTES = 16_384

    def __init__(
        self,
        loop: CandidateApprovalLoop,
        *,
        resolve_bearer: Callable[[str], str | None],
    ) -> None:
        if not callable(resolve_bearer):
            raise TypeError("resolve_bearer must be callable.")
        self._loop = loop
        self._resolve_bearer = resolve_bearer

    def __call__(self, environ: Mapping[str, Any], start_response) -> Iterable[bytes]:
        path = str(environ.get("PATH_INFO", ""))
        parts = path.strip("/").split("/")
        if len(parts) != 5 or parts[:3] != ["v1", "candidate", "runs"] or parts[4] != "approval":
            return self._respond(start_response, "404 Not Found", {"error": "not_found"})
        authorization = str(environ.get("HTTP_AUTHORIZATION", ""))
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        user_id = self._resolve_bearer(token) if token else None
        if not user_id:
            return self._respond(
                start_response,
                "401 Unauthorized",
                {"error": "unauthorized"},
                [("WWW-Authenticate", "Bearer")],
            )
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        run_id = parts[3]
        try:
            if method == "GET":
                return self._respond(
                    start_response,
                    "200 OK",
                    self._loop.get_candidate_approval_prompt(run_id, user_id=user_id).to_dict(),
                )
            if method == "POST":
                payload = self._read_json(environ)
                submission = CandidateApprovalSubmission.from_mapping(payload)
                if submission.run_id != run_id:
                    raise ValueError("approval payload run_id does not match the URL.")
                result = self._loop.resume_candidate_submission(
                    user_id=user_id, submission=submission
                )
                return self._respond(
                    start_response,
                    "200 OK",
                    {"run_id": result.run_id, "phase": result.phase.value},
                )
            return self._respond(
                start_response,
                "405 Method Not Allowed",
                {"error": "method_not_allowed"},
                [("Allow", "GET, POST")],
            )
        except PermissionError:
            return self._respond(start_response, "404 Not Found", {"error": "not_found"})
        except (TypeError, ValueError) as exc:
            return self._respond(
                start_response,
                "400 Bad Request",
                {"error": "invalid_request", "message": str(exc)[:500]},
            )
        except Exception:
            return self._respond(
                start_response,
                "409 Conflict",
                {"error": "approval_conflict"},
            )

    def _read_json(self, environ: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            length = int(str(environ.get("CONTENT_LENGTH", "0")))
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer.") from exc
        if length < 1 or length > self.MAX_BODY_BYTES:
            raise ValueError("approval body size is invalid.")
        body = environ["wsgi.input"].read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("approval body must be valid UTF-8 JSON.") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("approval body must be a JSON object.")
        return payload

    @staticmethod
    def _respond(start_response, status: str, payload: Mapping[str, Any], extra=None):
        body = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode()
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
        ]
        headers.extend(extra or [])
        start_response(status, headers)
        return (body,)
