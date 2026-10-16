"""Interactive API docs (Swagger UI) with a clearer "try it out" result.

Out of the box, Swagger UI shows the response you just received in the same
style as the list of documented responses directly below it, which makes the
real result easy to miss. This adds a small style layer on top of FastAPI's
default page:

* the live result ("Server response") gets a coloured banner and outline;
* the documented responses that follow are labelled "for reference" and dimmed
  while a live result is on screen (hover to read them at full strength);
* the documented row matching the status code you just got is highlighted,
  so you can see which documented case you hit.

The selectors follow Swagger UI's own markup (``live-response.jsx`` and
``responses.jsx``): ``.live-responses-table`` is the live result,
``.responses-inner > .responses-table`` the documented list, and
``tr.response_current`` the documented row matching the live status code.
"""

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

DOCS_CSS = """
:root {
  --live: #0b6e4f;        /* live result accent */
  --live-tint: #e8f5ef;
  --ref: #6b7280;         /* reference (documented) responses */
}

/* Live result: banner heading + outlined table */
.swagger-ui .responses-inner h4:has(+ .live-responses-table) {
  margin: 18px 0 0;
  padding: 8px 12px;
  border-radius: 6px 6px 0 0;
  background: var(--live);
  color: #fff;
  font-size: 15px;
  letter-spacing: .01em;
}
.swagger-ui .responses-inner h4:has(+ .live-responses-table)::before { content: "\\25B6  Your result: "; }
.swagger-ui .live-responses-table {
  border: 2px solid var(--live);
  border-top: 0;
  border-radius: 0 0 6px 6px;
  background: var(--live-tint);
}
.swagger-ui .live-responses-table tr.response .response-col_status {
  color: var(--live);
  font-size: 18px;
  font-weight: 700;
}

/* Documented responses: clearly secondary once a live result is shown */
.swagger-ui .responses-inner > div > h4:last-child {
  margin-top: 28px;
  padding-top: 14px;
  border-top: 1px dashed var(--ref);
  color: var(--ref);
}
.swagger-ui .responses-inner > div > h4:last-child::after {
  content: "  (all possible responses, for reference)";
  font-weight: 400;
  font-size: 13px;
}
.swagger-ui .responses-inner:has(.live-responses-table) > .responses-table { opacity: .6; transition: opacity .15s; }
.swagger-ui .responses-inner:has(.live-responses-table) > .responses-table:hover { opacity: 1; }

/* The documented case you just hit */
.swagger-ui .responses-table tr.response_current {
  background: var(--live-tint);
  box-shadow: inset 4px 0 0 var(--live);
}

@media (prefers-reduced-motion: reduce) {
  .swagger-ui .responses-inner .responses-table { transition: none; }
}
"""

SWAGGER_UI_PARAMETERS = {
    "tryItOutEnabled": True,  # request forms are editable straight away
    "displayRequestDuration": True,
}


def add_docs_route(app: FastAPI) -> None:
    """Serve Swagger UI at /docs with the extra styles. Create the app with docs_url=None."""

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui() -> HTMLResponse:
        page = get_swagger_ui_html(
            openapi_url=app.openapi_url or "/openapi.json",
            title=f"{app.title} - Swagger UI",
            swagger_ui_parameters=SWAGGER_UI_PARAMETERS,
        )
        html = page.body.decode().replace("</head>", f"<style>{DOCS_CSS}</style></head>", 1)
        return HTMLResponse(html)
