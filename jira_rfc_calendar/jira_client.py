from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# Maps JIRA status names (lower-cased) to FullCalendar event colors
_STATUS_COLORS: Dict[str, str] = {
    "open":             "#6f42c1",  # purple
    "new":              "#6f42c1",
    "to do":            "#6f42c1",
    "approved":         "#17a2b8",  # teal
    "pending approval": "#fd7e14",  # orange
    "in review":        "#fd7e14",
    "review":           "#fd7e14",
    "in progress":      "#007bff",  # blue
    "implementing":     "#007bff",
    "scheduled":        "#007bff",
    "done":             "#28a745",  # green
    "closed":           "#28a745",
    "resolved":         "#28a745",
    "implemented":      "#28a745",
    "completed":        "#28a745",
    "rejected":         "#dc3545",  # red
    "cancelled":        "#dc3545",
    "failed":           "#dc3545",
}
_DEFAULT_COLOR = "#6f42c1"


def _status_color(status: str) -> str:
    return _STATUS_COLORS.get(status.lower(), _DEFAULT_COLOR)


class JiraClient:
    """JIRA Server / Data Center REST API v2 client.

    Authenticates via Personal Access Token (recommended) or Basic auth.
    """

    _API = "/rest/api/2"

    def __init__(
        self,
        base_url: str,
        pat: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(
            {"Accept": "application/json", "Content-Type": "application/json"}
        )

        if pat:
            self._session.headers["Authorization"] = f"Bearer {pat}"
        elif username and password:
            creds = base64.b64encode(f"{username}:{password}".encode()).decode()
            self._session.headers["Authorization"] = f"Basic {creds}"
        else:
            raise ValueError(
                "Provide JIRA_PAT or both JIRA_USERNAME and JIRA_PASSWORD."
            )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def search_rfcs(
        self,
        projects: Optional[List[str]] = None,
        date_field: str = "duedate",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_results: int = 500,
    ) -> List[Dict[str, Any]]:
        """Return RFC issues as FullCalendar event dicts.

        Parameters
        ----------
        projects:   Optional list of JIRA project keys to restrict the query.
        date_field: JIRA field ID used as the calendar date (default: duedate).
        start_date: ISO date string (YYYY-MM-DD) — lower bound for date_field.
        end_date:   ISO date string (YYYY-MM-DD) — upper bound for date_field.
        max_results: Hard cap on total issues fetched (paginates internally).
        """
        jql = self._build_jql(projects, date_field, start_date, end_date)
        fields = [
            "summary", "status", "priority", "assignee", "reporter",
            date_field, "description", "project", "created", "updated",
        ]

        url = f"{self.base_url}{self._API}/search"
        events: List[Dict[str, Any]] = []
        start_at = 0

        while len(events) < max_results:
            batch = min(100, max_results - len(events))
            resp = self._session.get(
                url,
                params={
                    "jql": jql,
                    "fields": ",".join(fields),
                    "startAt": start_at,
                    "maxResults": batch,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            issues = data.get("issues", [])

            for issue in issues:
                event = self._to_event(issue, date_field)
                if event:
                    events.append(event)

            start_at += len(issues)
            if not issues or start_at >= data.get("total", 0):
                break

        logger.info("Fetched %d RFC events from JIRA.", len(events))
        return events

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_jql(
        projects: Optional[List[str]],
        date_field: str,
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> str:
        conditions = ["issuetype = RFC"]
        if projects:
            quoted = ", ".join(f'"{p}"' for p in projects)
            conditions.append(f"project in ({quoted})")
        if start_date:
            conditions.append(f'{date_field} >= "{start_date}"')
        if end_date:
            conditions.append(f'{date_field} <= "{end_date}"')
        return " AND ".join(conditions) + f" ORDER BY {date_field} ASC"

    def _to_event(
        self, issue: Dict[str, Any], date_field: str
    ) -> Optional[Dict[str, Any]]:
        """Convert a JIRA issue dict to a FullCalendar event object.

        Returns None if the issue has no value for date_field.
        """
        fields = issue.get("fields", {})
        raw_date: Optional[str] = fields.get(date_field)
        if not raw_date:
            return None

        date_str = raw_date[:10]  # normalise to YYYY-MM-DD

        status = (fields.get("status") or {}).get("name", "Open")
        priority = (fields.get("priority") or {}).get("name", "Medium")
        assignee = (fields.get("assignee") or {}).get("displayName", "Unassigned")
        reporter = (fields.get("reporter") or {}).get("displayName", "")
        project = (fields.get("project") or {}).get("name", "")

        desc: str = fields.get("description") or ""
        if len(desc) > 600:
            desc = desc[:597] + "…"

        return {
            "id": issue["key"],
            "title": f"{issue['key']}: {fields.get('summary', '(no summary)')}",
            "start": date_str,
            "color": _status_color(status),
            "extendedProps": {
                "key": issue["key"],
                "summary": fields.get("summary", ""),
                "status": status,
                "priority": priority,
                "assignee": assignee,
                "reporter": reporter,
                "project": project,
                "description": desc,
                "url": f"{self.base_url}/browse/{issue['key']}",
            },
        }
