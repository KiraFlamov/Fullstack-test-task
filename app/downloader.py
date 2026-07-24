import io
import os
import time
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import requests
import logging

from app.config import API_URL, HEADERS
from app.database import SessionLocal
from app.models import File

logger = logging.getLogger(__name__)

class Downloader:

    def __init__(self, stop_event=None):
        self.db = SessionLocal()
        self.stop_event = stop_event

        os.makedirs("files", exist_ok=True)

        self.start_time = datetime.now(
            ZoneInfo("Asia/Novosibirsk")
        )

        self.total_received = 0
        self.total_downloaded = 0

    def _emit_progress(self, progress_callback, current_batch=None, message="", status="running", retry_after=None):
        if progress_callback is None:
            return

        progress_callback({
            "status": status,
            "start_time": self.start_time.strftime("%d.%m.%Y %H:%M:%S"),
            "total_received": self.total_received,
            "total_downloaded": self.total_downloaded,
            "current_batch": current_batch or [],
            "message": message,
            "retry_after": retry_after,
        })

    def request(self, method, url, **kwargs):
        progress_callback = kwargs.pop("progress_callback", None)

        attempt = 0
        max_attempts = 50

        while True:
            if self.is_stopped():
                raise InterruptedError("Операция остановлена пользователем")

            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=HEADERS,
                    timeout=30,
                    **kwargs
                )

                if response.status_code == 200:
                    return response

                if response.status_code in (429, 403):
                    retry = int(
                        response.headers.get("Retry-After", 1)
                    )

                    reason = "слишком много запросов" if response.status_code == 429 else "блокировка"

                    logger.info(
                        "%s. Ждем %s секунд...",
                        reason.capitalize(),
                        retry
                    )

                    attempt += 1

                    if attempt >= max_attempts:
                        raise RuntimeError(
                            "Превышено количество попыток подключения."
                        )

                    # Ждем с пошаговым обновлением retry_after каждую секунду
                    for remaining in range(retry, 0, -1):
                        if self.is_stopped():
                            raise InterruptedError("Операция остановлена пользователем")

                        self._emit_progress(
                            progress_callback,
                            current_batch=[],
                            message=f"{reason.capitalize()}. Осталось {remaining} сек...",
                            status="waiting",
                            retry_after=remaining,
                        )

                        time.sleep(1)

                    continue

                response.raise_for_status()

            except requests.exceptions.RequestException as exc:
                raise RuntimeError(
                    f"Не удалось подключиться к API ({API_URL}). "
                    f"Причина: {exc}"
                ) from exc
            
    def is_stopped(self):
        return (
            self.stop_event is not None
            and self.stop_event.is_set()
        )

    def get_names(self, progress_callback=None):

        response = self.request(
            "GET",
            f"{API_URL}/api/files/names",
            progress_callback=progress_callback,
        )

        return response.json()["file_names"]

    def download_batch(self, filenames, progress_callback=None):

        response = self.request(
            "POST",
            f"{API_URL}/api/files/download",
            json={
                "file_names": filenames
            },
            progress_callback=progress_callback,
        )

        return response.content

    def mark_downloaded(self, filenames, progress_callback=None):

        self.request(
            "POST",
            f"{API_URL}/api/files/downloaded",
            json={
                "file_names": filenames
            },
            progress_callback=progress_callback,
        )

    def save_archive(self, archive_bytes):

        archive = zipfile.ZipFile(
            io.BytesIO(archive_bytes)
        )

        for filename in archive.namelist():

            base = Path("files").resolve()
            target = (base / filename).resolve()

            if base not in target.parents and target != base:
                raise RuntimeError("Недопустимый путь в архиве")

            archive.extract(filename, "files")

            exists = (
                self.db.query(File)
                .filter(File.filename == filename)
                .first()
            )

            if exists:
                continue

            self.db.add(
                File(
                    filename=filename,
                    downloaded_at=datetime.now(
                        ZoneInfo("Asia/Novosibirsk")
                    )
                )
            )

        self.db.commit()

    def download_all(self, progress_callback=None):

        try:
            while True:
                if self.is_stopped():
                    self._emit_progress(
                        progress_callback,
                        current_batch=[],
                        message="Операция остановлена пользователем",
                        status="idle"
                    )
                    return

                names = self.get_names(progress_callback=progress_callback)

                if not names:
                    break

                self.total_received += len(names)

                self._emit_progress(
                    progress_callback,
                    current_batch=names,
                    message=f"Получено {len(names)} новых файлов"
                )

                logger.info(
                    "Получено %s новых файлов",
                    len(names)
                )

                for i in range(0, len(names), 3):
                    if self.is_stopped():
                        self._emit_progress(
                            progress_callback,
                            current_batch=[],
                            message="Операция остановлена пользователем",
                            status="idle"
                        )
                        return

                    batch = names[i:i + 3]

                    logger.info(
                        "Скачиваем %s",
                        batch
                    )

                    # Пауза перед скачиванием, чтобы не превысить лимит
                    if self.is_stopped():
                        raise InterruptedError("Операция остановлена пользователем")
                    time.sleep(2)

                    archive = self.download_batch(batch, progress_callback=progress_callback)

                    self.save_archive(archive)

                    # Пауза перед отметкой о скачивании, чтобы не превысить лимит
                    if self.is_stopped():
                        raise InterruptedError("Операция остановлена пользователем")
                    time.sleep(2)

                    self.mark_downloaded(batch, progress_callback=progress_callback)

                    self.total_downloaded += len(batch)

                    self._emit_progress(
                        progress_callback,
                        current_batch=batch,
                        message=f"Скачано {self.total_downloaded} из {self.total_received}"
                    )

                    logger.info(
                        "Скачано %s из %s",
                        self.total_downloaded,
                        self.total_received
                    )

                    # Пауза между батчами, чтобы не превысить лимит запросов
                    if i + 3 < len(names):
                        if self.is_stopped():
                            raise InterruptedError("Операция остановлена пользователем")
                        time.sleep(3)

                    # Дополнительная длинная пауза каждые 10 батчей (30 файлов)
                    # чтобы сбросить кумулятивный лимит запросов API
                    batch_number = i // 3 + 1
                    if batch_number % 10 == 0 and i + 3 < len(names):
                        if self.is_stopped():
                            raise InterruptedError("Операция остановлена пользователем")
                        cooldown = 10
                        for remaining in range(cooldown, 0, -1):
                            if self.is_stopped():
                                raise InterruptedError("Операция остановлена пользователем")
                            self._emit_progress(
                                progress_callback,
                                current_batch=[],
                                message=f"Пауза для сброса лимита API. Осталось {remaining} сек...",
                                status="waiting",
                                retry_after=remaining,
                            )
                            time.sleep(1)


            self._emit_progress(
                progress_callback,
                current_batch=[],
                message="Все файлы скачаны.",
                status="completed"
            )

            logger.info("Все файлы скачаны.")

            return {
                "start_time": self.start_time.strftime(
                    "%d.%m.%Y %H:%M:%S"
                ),
                "downloaded": self.total_downloaded
            }

        except InterruptedError as exc:
            self._emit_progress(
                progress_callback,
                current_batch=[],
                message=str(exc),
                status="idle"
            )
            return

        except Exception as exc:
            self._emit_progress(
                progress_callback,
                current_batch=[],
                message=str(exc),
                status="error"
            )
            raise

        finally:
            self.db.close()