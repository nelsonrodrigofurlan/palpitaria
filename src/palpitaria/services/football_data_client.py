from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from palpitaria.config import settings


class FootballDataError(Exception):
    pass


class FootballDataClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token or settings.football_data_token
        self.base_url = settings.football_data_base_url.rstrip("/")

    def _headers(self, unfold_goals: bool = False) -> dict[str, str]:
        headers = {"X-Auth-Token": self.token}
        if unfold_goals:
            headers["X-Unfold-Goals"] = "true"
        return headers

    @retry(
        retry=retry_if_exception_type((httpx.RemoteProtocolError, httpx.ReadTimeout)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    def _get(self, path: str, params: dict | None = None, unfold_goals: bool = False) -> dict:
        if not self.token:
            raise FootballDataError("FOOTBALL_DATA_TOKEN not configured")

        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, headers=self._headers(unfold_goals), params=params or {})

        if response.status_code == 429:
            raise FootballDataError("Rate limit exceeded — wait and retry")
        if response.status_code >= 400:
            raise FootballDataError(f"API error {response.status_code}: {response.text[:300]}")

        return response.json()

    def get_competition(self, code: str) -> dict:
        return self._get(f"/competitions/{code}")

    def get_competition_matches(
        self,
        code: str,
        *,
        season: int | None = None,
        matchday: int | None = None,
        status: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict]:
        params: dict[str, str | int] = {}
        if season is not None:
            params["season"] = season
        if matchday is not None:
            params["matchday"] = matchday
        if status:
            params["status"] = status
        if date_from:
            params["dateFrom"] = date_from
        if date_to:
            params["dateTo"] = date_to
        payload = self._get(f"/competitions/{code}/matches", params=params, unfold_goals=True)
        return payload.get("matches", [])

    def get_competition_teams(self, code: str, *, season: int | None = None) -> list[dict]:
        params = {"season": season} if season else None
        payload = self._get(f"/competitions/{code}/teams", params=params)
        return payload.get("teams", [])

    def get_team_matches(self, team_id: int, *, limit: int = 20, status: str = "FINISHED") -> list[dict]:
        payload = self._get(
            f"/teams/{team_id}/matches",
            params={"limit": limit, "status": status},
            unfold_goals=True,
        )
        return payload.get("matches", [])

    def get_standings(self, code: str, *, season: int | None = None) -> dict:
        params = {"season": season} if season else None
        return self._get(f"/competitions/{code}/standings", params=params)
