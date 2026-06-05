from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, make_response, render_template, request
from flask_cors import CORS

from jira_client import JiraClient

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Passed to the HTML template so the "Open in JIRA" links are absolute
_JIRA_BASE_URL = os.environ.get("JIRA_BASE_URL", "")

# CSP frame-ancestors value — allows SharePoint to embed this app in an iframe
_SHAREPOINT_ORIGIN = os.environ.get("SHAREPOINT_ORIGIN", "https://*.sharepoint.com")

# Allow cross-origin requests from SharePoint (needed when the iframe origin
# differs from the Flask server origin, e.g. tunnelled HTTPS endpoints)
CORS(app, origins=[_SHAREPOINT_ORIGIN, "https://*.sharepoint.com"])


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _jira_client() -> JiraClient:
    """Build a JiraClient from environment variables."""
    return JiraClient(
        base_url=os.environ["JIRA_BASE_URL"],
        email=os.environ["JIRA_EMAIL"],
        api_token=os.environ["JIRA_API_TOKEN"],
    )


# ------------------------------------------------------------------
# Security headers
# ------------------------------------------------------------------

@app.after_request
def _security_headers(resp: Response) -> Response:
    # Allow this page to be iframed from SharePoint
    resp.headers["Content-Security-Policy"] = (
        f"frame-ancestors 'self' {_SHAREPOINT_ORIGIN}"
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resp


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------

@app.route("/")
def index() -> Response:
    return make_response(
        render_template("index.html", jira_base_url=_JIRA_BASE_URL)
    )


@app.route("/health")
def health() -> Response:
    """Simple liveness check. Visit /health in a browser to confirm the server
    is reachable before embedding in SharePoint."""
    jira_url = os.environ.get("JIRA_BASE_URL", "NOT SET")
    email_set = bool(os.environ.get("JIRA_EMAIL"))
    token_set = bool(os.environ.get("JIRA_API_TOKEN"))
    return jsonify({
        "status": "ok",
        "jira_base_url": jira_url,
        "jira_email_configured": email_set,
        "jira_api_token_configured": token_set,
        "jira_filter_id": os.environ.get("JIRA_FILTER_ID", "NOT SET"),
    })


@app.route("/api/rfcs")
def get_rfcs() -> Response:
    """Return RFC issues as a JSON array of FullCalendar event objects.

    Query params (supplied automatically by FullCalendar):
        start  – ISO date string, lower bound of the visible date range
        end    – ISO date string, upper bound of the visible date range
    """
    try:
        client = _jira_client()

        raw_start = request.args.get("start", "")
        raw_end = request.args.get("end", "")
        start_date = raw_start[:10] if raw_start else None
        end_date = raw_end[:10] if raw_end else None

        filter_id = os.environ.get("JIRA_FILTER_ID") or None
        projects_raw = os.environ.get("JIRA_PROJECTS", "")
        projects = [p.strip() for p in projects_raw.split(",") if p.strip()] or None
        date_field = os.environ.get("JIRA_IMPL_DATE_FIELD", "duedate")

        events = client.search_rfcs(
            filter_id=filter_id,
            projects=projects,
            date_field=date_field,
            start_date=start_date,
            end_date=end_date,
        )

        return jsonify(events)

    except KeyError as exc:
        logger.error("Missing environment variable: %s", exc)
        return jsonify({"error": f"Missing configuration: {exc}"}), 500
    except Exception as exc:
        logger.exception("Error fetching RFCs from JIRA")
        return jsonify({"error": str(exc)}), 500


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5003))
    logger.info("Starting RFC Calendar on port %d", port)
    app.run(debug=False, host="0.0.0.0", port=port)
