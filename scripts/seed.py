"""
Seeds demo data for review rounds.

Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env (service role bypasses RLS).
Run this after at least one teammate has signed in once, so a `profiles` row exists
to own the demo workstreams and tasks.

Usage: .venv/bin/python scripts/seed.py
"""
import os
import sys
from datetime import date, timedelta

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

url = os.environ.get("SUPABASE_URL")
service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

if not url or not service_key:
    print("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env", file=sys.stderr)
    sys.exit(1)

supabase = create_client(url, service_key)

STATUSES = ["backlog", "in_progress", "at_risk", "blocked", "in_review", "done"]
PRIORITIES = ["low", "medium", "high", "urgent"]
TITLES = [
    "Draft executive summary slide",
    "Source data for market sizing chart",
    "Align on color palette with client",
    "Build first draft deck skeleton",
    "Client review call prep",
    "Incorporate stakeholder feedback",
    "Finalize speaker notes",
    "QA pass on chart formatting",
    "Translate deck to Arabic",
    "Package final delivery files",
]


def main():
    profiles = supabase.table("profiles").select("id, email").limit(5).execute().data
    if not profiles:
        print("No profiles found yet. Sign in to the app at least once, then re-run the seed.", file=sys.stderr)
        sys.exit(1)
    owner_id = profiles[0]["id"]

    projects = (
        supabase.table("projects")
        .insert(
            [
                {"name": "Product A", "owner_id": owner_id},
                {"name": "Product B", "owner_id": owner_id},
                {"name": "Internal", "owner_id": owner_id},
            ]
        )
        .execute()
        .data
    )

    workstream_names = ["Board Deck Q3", "Brand Refresh", "Template Library v2"]
    workstreams = (
        supabase.table("workstreams")
        .insert(
            [
                {"name": name, "project_id": project["id"], "owner_id": owner_id}
                for project, name in zip(projects, workstream_names)
            ]
        )
        .execute()
        .data
    )

    rows = []
    for ws in workstreams:
        for i, title in enumerate(TITLES):
            rows.append(
                {
                    "workstream_id": ws["id"],
                    "title": title,
                    "status": STATUSES[i % len(STATUSES)],
                    "priority": PRIORITIES[i % len(PRIORITIES)],
                    "assignee_id": owner_id,
                    "created_by": owner_id,
                    "due_date": (date.today() + timedelta(days=i - 4)).isoformat(),
                }
            )

    supabase.table("tasks").insert(rows).execute()
    print(f"Seeded {len(projects)} projects, {len(workstreams)} workstreams, and {len(rows)} tasks.")


if __name__ == "__main__":
    main()
