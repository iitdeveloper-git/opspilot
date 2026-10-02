"""Export OpenAPI schema for OpsPilot 2.0 Web Command Center to docs/api/openapi.json."""

from __future__ import annotations

import json
from pathlib import Path

from opspilot.config import Settings
from opspilot.web.app import create_web_app


def export_openapi() -> None:
    settings = Settings(admin_password="doc_generation_only")
    app = create_web_app(settings)
    schema = app.openapi()

    out_dir = Path(__file__).resolve().parent.parent / "docs" / "api"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "openapi.json"

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)

    print(f"OpenAPI schema successfully exported to {out_file}")


if __name__ == "__main__":
    export_openapi()
