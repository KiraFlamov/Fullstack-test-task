import shutil
import threading
from pathlib import Path

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.analyzer import Analyzer
from app.database import Base, SessionLocal, engine
from app.downloader import Downloader
from app.models import File

app = FastAPI(title="File Analyzer")

Base.metadata.create_all(bind=engine)

templates = Jinja2Templates(directory="templates")


_download_state = {
    "status": "idle",
    "start_time": None,
    "total_received": 0,
    "total_downloaded": 0,
    "current_batch": [],
    "message": "Ожидание запуска",
}
_download_thread = None
_download_lock = threading.Lock()
_stop_event = threading.Event()


def _update_download_state(payload):
    global _download_state
    _download_state.update(payload)


def _reset_download_state():
    global _download_state, _download_thread, _stop_event

    _stop_event.set()

    with _download_lock:
        if _download_thread is not None and _download_thread.is_alive():
            _download_thread.join(timeout=3)
        _download_thread = None
        _stop_event = threading.Event()

    _download_state.update({
        "status": "idle",
        "start_time": None,
        "total_received": 0,
        "total_downloaded": 0,
        "current_batch": [],
        "message": "Ожидание запуска",
    })


def _run_download():
    global _download_thread

    downloader = Downloader(stop_event=_stop_event)

    try:
        downloader.download_all(progress_callback=_update_download_state)
    except Exception as exc:
        _update_download_state({
            "status": "error",
            "message": str(exc),
        })
    finally:
        with _download_lock:
            _download_thread = None


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "download_state": _download_state,
        }
    )


@app.post("/download")
def download():
    global _download_thread, _stop_event

    with _download_lock:
        # Останавливаем предыдущий поток, если он ещё жив
        if _download_thread is not None and _download_thread.is_alive():
            _stop_event.set()
            _download_thread.join(timeout=3)
            _download_thread = None

        _stop_event = threading.Event()

        _download_state.update({
            "status": "running",
            "start_time": None,
            "total_received": 0,
            "total_downloaded": 0,
            "current_batch": [],
            "message": "Начинаю скачивание...",
        })

        _download_thread = threading.Thread(target=_run_download, daemon=True)
        _download_thread.start()

    return JSONResponse({"status": "started"})


@app.get("/download/status")
def download_status():
    return JSONResponse(_download_state)


@app.get("/files")
def files(
        request: Request,
        page: int = Query(default=1, ge=1),
        sort: str = Query(default="desc")
):
    db = SessionLocal()

    per_page = 20

    order = File.downloaded_at.asc() if sort == "asc" else File.downloaded_at.desc()
    all_files = db.query(File).order_by(order).all()
    all_filenames = [file.filename for file in all_files]

    total = len(all_files)

    pages = max(1, (total + per_page - 1) // per_page)

    files = all_files[(page - 1) * per_page:(page - 1) * per_page + per_page]

    db.close()

    return templates.TemplateResponse(
        "files.html",
        {
            "request": request,
            "files": files,
            "stats": None,
            "page": page,
            "pages": pages,
            "all_filenames": all_filenames,
            "download_state": _download_state,
            "sort": sort,
        }
    )


@app.post("/files/clear")
def clear_files():
    _reset_download_state()

    db = SessionLocal()

    try:
        db.query(File).delete()
        db.commit()
    finally:
        db.close()

    files_dir = Path("files")
    if files_dir.exists():
        for child in files_dir.iterdir():
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)

    files_dir.mkdir(exist_ok=True)

    return RedirectResponse(url="/", status_code=303)


@app.post("/analyze")
def analyze(
        request: Request,
        page: int = Form(1),
        filenames: list[str] = Form(default=None),
        select_all: str | None = Form(default=None),
):
    if filenames is None:
        filenames = []

    analyzer = Analyzer()

    db = SessionLocal()

    all_files = db.query(File).order_by(File.downloaded_at.desc()).all()
    all_filenames = [file.filename for file in all_files]

    if select_all:
        selected_filenames = all_filenames
    else:
        selected_filenames = filenames

    stats = analyzer.analyze(selected_filenames)

    per_page = 20

    total = len(all_files)

    pages = max(1, (total + per_page - 1) // per_page)

    files = all_files[(page - 1) * per_page:(page - 1) * per_page + per_page]

    db.close()

    return templates.TemplateResponse(
        "files.html",
        {
            "request": request,
            "files": files,
            "stats": stats,
            "page": page,
            "pages": pages,
            "all_filenames": all_filenames,
            "download_state": _download_state,
            "sort": "desc",
        }
    )
