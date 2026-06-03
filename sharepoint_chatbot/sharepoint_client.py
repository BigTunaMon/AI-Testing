from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Dict, List, Optional

import msal
import requests

logger = logging.getLogger(__name__)


class SharePointClient:
    """Authenticates via ROPC (username/password) and queries the SharePoint
    FX Provider Services Knowledge Hub Documents folder through Microsoft Graph API."""

    GRAPH_BASE = "https://graph.microsoft.com/v1.0"
    SITE_HOSTNAME = "flahca.sharepoint.com"
    SITE_PATH = "/sites/OCM"
    # Relative path inside the "Shared Documents" drive
    FOLDER_PATH = "Site Documents/Releases/FX Provider Services Release/Knowledge Hub Documents"
    FOLDER_KEY = "Knowledge Hub Documents"

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        username: str,
        password: str,
    ) -> None:
        self.username = username
        self._password = password
        self._token: Optional[str] = None
        self._site_id: Optional[str] = None
        self._drive_id: Optional[str] = None
        self._msal_app = msal.PublicClientApplication(
            client_id=client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
        )

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> bool:
        """Acquire/refresh an access token via ROPC, using MSAL's token cache."""
        scopes = ["https://graph.microsoft.com/.default"]
        result: Optional[Dict] = None

        accounts = self._msal_app.get_accounts(username=self.username)
        if accounts:
            result = self._msal_app.acquire_token_silent(
                scopes=scopes, account=accounts[0]
            )

        if not result or "access_token" not in result:
            result = self._msal_app.acquire_token_by_username_password(
                username=self.username,
                password=self._password,
                scopes=scopes,
            )

        if result and "access_token" in result:
            self._token = result["access_token"]
            return True

        error = (result or {}).get("error_description") or (result or {}).get("error", "Unknown")
        logger.error("SharePoint authentication failed: %s", error)
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    def _get_site_id(self) -> str:
        if self._site_id:
            return self._site_id
        url = f"{self.GRAPH_BASE}/sites/{self.SITE_HOSTNAME}:{self.SITE_PATH}"
        resp = requests.get(url, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        self._site_id = resp.json()["id"]
        return self._site_id

    def _get_drive_id(self) -> str:
        if self._drive_id:
            return self._drive_id
        site_id = self._get_site_id()
        url = f"{self.GRAPH_BASE}/sites/{site_id}/drives"
        resp = requests.get(url, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        drives: List[Dict] = resp.json().get("value", [])

        # Prefer the main "Documents" / "Shared Documents" library
        preferred_names = {"Documents", "Shared Documents"}
        for drive in drives:
            if drive.get("driveType") == "documentLibrary" and drive.get("name") in preferred_names:
                self._drive_id = drive["id"]
                return self._drive_id
        # Fallback: first document library found
        for drive in drives:
            if drive.get("driveType") == "documentLibrary":
                self._drive_id = drive["id"]
                return self._drive_id

        raise RuntimeError("No document library found in the SharePoint site.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_files(self) -> List[Dict[str, Any]]:
        """Return every item directly inside the Knowledge Hub Documents folder."""
        site_id = self._get_site_id()
        drive_id = self._get_drive_id()
        encoded = urllib.parse.quote(self.FOLDER_PATH, safe="/")
        url = f"{self.GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root:/{encoded}:/children"

        items: List[Dict] = []
        while url:
            resp = requests.get(url, headers=self._headers(), timeout=30)
            resp.raise_for_status()
            data = resp.json()
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")  # handle pagination

        return [self._format_item(i) for i in items]

    def search_files(self, query: str) -> List[Dict[str, Any]]:
        """Search within the drive and return only results inside the Knowledge Hub folder."""
        if not query.strip():
            return self.list_files()

        site_id = self._get_site_id()
        drive_id = self._get_drive_id()
        encoded_query = urllib.parse.quote(query)
        url = f"{self.GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/search(q='{encoded_query}')"

        items: List[Dict] = []
        while url:
            resp = requests.get(url, headers=self._headers(), timeout=30)
            resp.raise_for_status()
            data = resp.json()
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")

        # Filter to our specific folder
        items = [
            i for i in items
            if self.FOLDER_KEY in i.get("parentReference", {}).get("path", "")
        ]
        return [self._format_item(i) for i in items]

    @staticmethod
    def _format_item(item: Dict) -> Dict[str, Any]:
        entry: Dict[str, Any] = {
            "name": item.get("name", "Unknown"),
            "type": "folder" if "folder" in item else "file",
            "size": item.get("size"),
            "last_modified": item.get("lastModifiedDateTime"),
            "url": item.get("webUrl"),
        }
        if "file" in item:
            entry["mime_type"] = item["file"].get("mimeType")
        return entry
