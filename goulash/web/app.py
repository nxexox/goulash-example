"""Web UI + JSON API. Online path reads only the prepared database: no external sites, no LLM."""

from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from goulash import db, repo
from goulash.geo import REGION_NAMES
from goulash.search.engine import Filters, search
from goulash.taxonomy import CATEGORIES

HERE = Path(__file__).parent
TZ = ZoneInfo("Europe/Moscow")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from goulash.cli import load_seed
    from goulash.collector.worker import register_sources

    with db.connect() as conn:
        db.init_schema(conn)
        register_sources(conn)
        if conn.execute("SELECT count(*) AS n FROM suppliers").fetchone()["n"] == 0:
            load_seed(conn)  # fresh database (first deploy): bootstrap from the committed seed
        conn.commit()
    yield
    db.close_pool()


app = FastAPI(title="Goulash — поиск поставщиков", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


def _ago(dt: datetime | None) -> str:
    if not dt:
        return "—"
    days = (datetime.now(UTC) - dt).days
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    return f"{days} дн. назад"


templates.env.filters["ago"] = _ago
templates.env.filters["date"] = lambda dt: dt.astimezone(TZ).strftime("%d.%m.%Y") if dt else "—"
templates.env.globals.update(
    REGIONS=REGION_NAMES,
    CATEGORIES=CATEGORIES,
    FIELDS=repo.DECISION_FIELDS,
    STATUSES=repo.SHORTLIST_STATUSES,
)


def get_conn():
    with db.conn_from_pool() as conn:
        yield conn


def _filters(region: str, category: str, only_region: str, has_contacts: str, has_conditions: str) -> Filters:
    return Filters(
        region=region or None,
        category=category or None,
        only_region=bool(only_region),
        has_contacts=bool(has_contacts),
        has_conditions=bool(has_conditions),
    )


@app.get("/", response_class=HTMLResponse)
def home(request: Request, conn=Depends(get_conn)):
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "stats": repo.catalog_stats(conn),
            "categories": repo.category_counts(conn),
            "regions": repo.region_counts(conn)[:12],
            "examples": [
                "моцарела",
                "шоколад Москва",
                "специи халяль",
                "соки Краснодар",
                "упаковка",
                "ароматизаторы пищевые",
                "сыр плавленый Екатеринбург",
            ],
        },
    )


@app.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    q: str = "",
    region: str = "",
    category: str = "",
    only_region: str = "",
    has_contacts: str = "",
    has_conditions: str = "",
    conn=Depends(get_conn),
):
    filters = _filters(region, category, only_region, has_contacts, has_conditions)
    result = search(conn, q, filters)
    filters.region = result.region  # show a region parsed from the query in the select
    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "q": q,
            "f": filters,
            "result": result,
            "shortlist": set(repo.shortlist_ids(conn)),
        },
    )


@app.get("/supplier/{supplier_id}", response_class=HTMLResponse)
def supplier_page(request: Request, supplier_id: int, region: str = "", conn=Depends(get_conn)):
    cards = repo.load_cards(conn, [supplier_id])
    if not cards:
        raise HTTPException(404, "Поставщик не найден")
    return templates.TemplateResponse(request, "supplier.html", {"s": cards[0], "region": region or None})


@app.post("/supplier/{supplier_id}/notes")
def add_note(supplier_id: int, text: str = Form(...), conn=Depends(get_conn)):
    if text.strip():
        repo.add_note(conn, supplier_id, text)
    return RedirectResponse(f"/supplier/{supplier_id}#notes", status_code=303)


@app.post("/notes/{note_id}/delete")
def delete_note(note_id: int, supplier_id: int = Form(...), conn=Depends(get_conn)):
    repo.delete_note(conn, note_id)
    return RedirectResponse(f"/supplier/{supplier_id}#notes", status_code=303)


@app.post("/supplier/{supplier_id}/shortlist")
def shortlist_set(
    request: Request, supplier_id: int, status: str = Form(""), back: str = Form(""), conn=Depends(get_conn)
):
    repo.set_shortlist(conn, supplier_id, status or None)
    target = back if back.startswith("/") else f"/supplier/{supplier_id}"
    return RedirectResponse(target, status_code=303)


@app.get("/compare", response_class=HTMLResponse)
def compare(request: Request, ids: str = "", region: str = "", conn=Depends(get_conn)):
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()][:6]
    return templates.TemplateResponse(
        request,
        "compare.html",
        {
            "cards": repo.load_cards(conn, id_list),
            "region": region or None,
        },
    )


@app.get("/shortlist", response_class=HTMLResponse)
def shortlist(request: Request, conn=Depends(get_conn)):
    cards = repo.load_cards(conn, repo.shortlist_ids(conn))
    return templates.TemplateResponse(request, "shortlist.html", {"cards": cards})


@app.get("/about", response_class=HTMLResponse)
def about(request: Request, conn=Depends(get_conn)):
    sources = conn.execute("SELECT * FROM sources ORDER BY id").fetchall()
    discovery = conn.execute(
        "SELECT query, region, results, created_at FROM discovery_requests ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    return templates.TemplateResponse(
        request,
        "about.html",
        {
            "stats": repo.catalog_stats(conn),
            "sources": sources,
            "discovery": discovery,
        },
    )


@app.get("/api/search")
def api_search(
    q: str = "",
    region: str = "",
    category: str = "",
    only_region: str = "",
    has_contacts: str = "",
    has_conditions: str = "",
    conn=Depends(get_conn),
):
    r = search(conn, q, _filters(region, category, only_region, has_contacts, has_conditions))
    return JSONResponse(
        {
            "query": q,
            "region": r.region,
            "corrections": r.corrections,
            "unmatched": r.unmatched,
            "relaxed": r.relaxed,
            "hints": r.query.hints,
            "results": [
                {
                    "id": h.card.id,
                    "name": h.card.name,
                    "score": h.score,
                    "exact": h.exact,
                    "region_fit": h.region_fit,
                    "reasons": h.reasons,
                    "website": h.card.website,
                    "city": h.card.city,
                    "url": f"/supplier/{h.card.id}",
                }
                for h in r.hits
            ],
        }
    )


@app.get("/api/suppliers/{supplier_id}")
def api_supplier(supplier_id: int, conn=Depends(get_conn)):
    cards = repo.load_cards(conn, [supplier_id])
    if not cards:
        raise HTTPException(404)
    return JSONResponse(_jsonable(asdict(cards[0]) | {"questions": cards[0].questions}))


def _jsonable(x):
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_jsonable(v) for v in x]
    if isinstance(x, datetime):
        return x.isoformat()
    return x


@app.get("/healthz")
def healthz(conn=Depends(get_conn)):
    conn.execute("SELECT 1")
    return {"ok": True}


def search_url(**params) -> str:
    return "/search?" + urlencode({k: v for k, v in params.items() if v})


templates.env.globals["search_url"] = search_url
