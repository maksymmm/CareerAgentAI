from __future__ import annotations

from career_agent_ai.application.agents.agent import Agent
from career_agent_ai.application.agents.agent_result import AgentResult
from career_agent_ai.application.brain.agent_context import AgentContext
from career_agent_ai.application.jobs.job_filter import JobFilter
from career_agent_ai.application.jobs.job_query import JobQuery
from career_agent_ai.application.jobs.job_sort import JobSort
from career_agent_ai.application.search.search_response import SearchResponse
from career_agent_ai.application.search.search_service import SearchService


class JobSearchAgent(Agent):
    """
    Job search agent.

    Keeps the original agent contract while supporting
    real job searching through SearchService.
    """

    def __init__(
        self,
        search_service: SearchService | None = None,
    ) -> None:
        self._search_service = search_service

    @property
    def id(self) -> str:
        return "job_search"

    @property
    def name(self) -> str:
        return "Job Search Agent"

    @property
    def version(self) -> str:
        return "1.0"

    @property
    def description(self) -> str:
        return "Handles job search requests."

    def execute(
        self,
        context: AgentContext,
    ) -> AgentResult:
        # Preserve the original standalone-agent contract.
        # The agent may execute without a configured SearchService.
        if self._search_service is None:
            return AgentResult(
                success=True,
                agent_id=self.id,
                messages=(
                    "Job Search Agent executed.",
                ),
            )

        payload = context.payload

        keyword = self._read_string(
            payload,
            "keyword",
        )

        location = self._read_string(
            payload,
            "location",
        )

        country = self._read_string(
            payload,
            "country",
        )

        city = self._read_string(
            payload,
            "city",
        )

        company = self._read_string(
            payload,
            "company",
        )

        remote_only = self._read_bool(
            payload,
            "remote_only",
        )

        employment_type = payload.get(
            "employment_type"
        )

        sort = payload.get(
            "sort",
            JobSort.RELEVANCE,
        )

        if sort is None:
            sort = JobSort.RELEVANCE

        if isinstance(sort, str):
            try:
                sort = JobSort(sort)
            except ValueError:
                sort = JobSort.RELEVANCE

        page = self._read_positive_int(
            payload,
            "page",
            default=1,
        )

        page_size = self._read_positive_int(
            payload,
            "page_size",
            default=20,
        )

        if city and not location:
            location = city

        job_filter = JobFilter(
            keyword=keyword,
            country=country,
            city=location,
            remote_only=remote_only,
            employment_type=employment_type,
            company=company,
        )

        query = JobQuery(
            filters=job_filter,
            page=page,
            page_size=page_size,
            sort=sort,
        )

        try:
            response = self.search(query)
        except Exception as exc:
            return AgentResult(
                success=False,
                agent_id=self.id,
                messages=(
                    f"Job search failed: {exc}",
                ),
                metadata={
                    "query": keyword,
                    "location": location,
                },
            )

        return AgentResult(
            success=True,
            agent_id=self.id,
            messages=(
                self._build_message(
                    keyword=keyword,
                    location=location,
                    response=response,
                ),
            ),
            metadata={
                "total": response.total,
                "count": response.count,
                "page": response.page,
                "page_size": response.page_size,
                "query": keyword,
                "location": location,
                "country": country,
                "city": location,
                "company": company,
                "remote_only": remote_only,
                "sort": sort.value,
                "jobs": response.jobs,
            },
        )

    def search(
        self,
        query: JobQuery,
    ) -> SearchResponse:
        if self._search_service is None:
            raise RuntimeError(
                "JobSearchAgent requires a SearchService."
            )

        return self._search_service.search(query)

    def supports(
        self,
        action: str,
    ) -> bool:
        return action == self.id

    def snapshot(self) -> AgentResult:
        return AgentResult(
            success=True,
            agent_id=self.id,
        )

    @staticmethod
    def _read_string(
        payload,
        key: str,
    ) -> str:
        value = payload.get(key, "")

        if value is None:
            return ""

        return str(value).strip()

    @staticmethod
    def _read_bool(
        payload,
        key: str,
    ) -> bool:
        value = payload.get(
            key,
            False,
        )

        if isinstance(value, bool):
            return value

        if isinstance(value, str):
            return value.strip().lower() in {
                "true",
                "1",
                "yes",
                "y",
                "remote",
            }

        return bool(value)

    @staticmethod
    def _read_positive_int(
        payload,
        key: str,
        default: int,
    ) -> int:
        value = payload.get(
            key,
            default,
        )

        try:
            value = int(value)
        except (TypeError, ValueError):
            return default

        if value < 1:
            return default

        return value

    @staticmethod
    def _build_message(
        *,
        keyword: str,
        location: str,
        response: SearchResponse,
    ) -> str:
        parts = [
            f"Found {response.count} jobs",
        ]

        if keyword:
            parts.append(
                f'for "{keyword}"'
            )

        if location:
            parts.append(
                f"in {location}"
            )

        return " ".join(parts) + "."
