#!/usr/bin/env python3
"""
webchat.py — GBase WebSocket Chat Channel

A production-grade WebSocket chat backend for GBase agents.
Supports streaming responses, file uploads, knowledge injection, and tool chain visibility.

Usage:
    channel = WebChatChannel(kernel, storage)
    app = channel.create_app()
"""

import asyncio
import base64
import contextlib
import json
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("gbase.webchat")


class WebChatChannel:
    """WebSocket-based chat channel with streaming responses."""

    def __init__(
        self,
        kernel: Any,
        storage: Any | None = None,
        data_dir: str | None = None,
        max_upload_mb: int = 10,
    ):
        self.kernel = kernel
        self.storage = storage
        self.data_dir = data_dir or os.environ.get("GBASE_DATA_DIR", "data")
        self.max_upload_mb = max_upload_mb
        self._static_dir = Path(__file__).parent.parent.parent / "webchat"

    def create_app(self, title: str = "GBase Web Chat") -> FastAPI:
        app = FastAPI(title=title)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Serve static files
        static_path = self._static_dir
        static_path.mkdir(parents=True, exist_ok=True)
        app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

        # Serve the main HTML page
        @app.get("/", response_class=HTMLResponse)
        async def index():
            html_path = static_path / "index.html"
            if html_path.exists():
                return HTMLResponse(html_path.read_text(encoding="utf-8"))
            return HTMLResponse("<h1>GBase Web Chat</h1><p>Frontend not found.</p>")

        @app.get("/health")
        async def health():
            return {"status": "ok", "app": "gbase-webchat"}

        @app.post("/ask")
        async def ask_http(request: Request):
            """HTTP fallback for non-streaming chat (for testing)."""
            body = await request.json()
            message = body.get("message", "")
            response = await self.kernel.run(
                user_message=message,
                platform="webchat",
            )
            return JSONResponse({"reply": response})

        # WebSocket chat endpoint
        @app.websocket("/ws")
        async def websocket_endpoint(ws: WebSocket):
            await ws.accept()
            logger.info("WebSocket connected")

            try:
                while True:
                    raw = await ws.receive_text()

                    # Parse incoming message (could be text or JSON with files)
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        data = {"type": "text", "content": raw}

                    msg_type = data.get("type", "text")

                    if msg_type == "text":
                        user_msg = data.get("content", "").strip()
                        if not user_msg:
                            continue

                        # Notify streaming start
                        await ws.send_json({"type": "status", "content": "processing"})

                        # Send knowledge hits if available
                        try:
                            if self.storage:
                                hits = self.storage.search(user_msg)
                                if hits:
                                    await ws.send_json(
                                        {
                                            "type": "knowledge",
                                            "content": hits[:5],
                                        }
                                    )
                        except Exception:
                            pass

                        # Run kernel
                        try:
                            response = await self.kernel.run(
                                user_message=user_msg,
                                platform="webchat",
                            )

                            # Stream response character by character for cool effect
                            # but batch into chunks for practicality
                            chunk_size = 20
                            for i in range(0, len(response), chunk_size):
                                chunk = response[i : i + chunk_size]
                                await ws.send_json(
                                    {
                                        "type": "chunk",
                                        "content": chunk,
                                    }
                                )
                                await asyncio.sleep(0.01)  # Small delay for streaming feel

                            # Send completion marker with metrics
                            await ws.send_json(
                                {
                                    "type": "done",
                                    "content": response,
                                    "meta": {
                                        "length": len(response),
                                    },
                                }
                            )

                        except Exception as e:
                            logger.error("Kernel error: %s", e, exc_info=True)
                            await ws.send_json(
                                {
                                    "type": "error",
                                    "content": str(e),
                                }
                            )

                    elif msg_type == "file":
                        # File upload handling
                        file_name = data.get("name", "unknown")
                        file_data_b64 = data.get("data", "")
                        file_mime = data.get("mime", "")

                        if not file_data_b64:
                            await ws.send_json({"type": "error", "content": "No file data"})
                            continue

                        try:
                            file_bytes = base64.b64decode(file_data_b64)
                            file_size_mb = len(file_bytes) / (1024 * 1024)

                            if file_size_mb > self.max_upload_mb:
                                await ws.send_json(
                                    {
                                        "type": "error",
                                        "content": f"File too large: {file_size_mb:.1f}MB (max {self.max_upload_mb}MB)",
                                    }
                                )
                                continue

                            # Save to uploads
                            upload_dir = Path(self.data_dir) / "uploads"
                            upload_dir.mkdir(parents=True, exist_ok=True)
                            safe_name = file_name.replace("/", "_").replace("\\", "_")
                            save_path = upload_dir / safe_name
                            save_path.write_bytes(file_bytes)

                            # Analyze content
                            result = await self._process_upload(file_name, file_bytes, file_mime)

                            await ws.send_json(
                                {
                                    "type": "file_processed",
                                    "content": result,
                                    "meta": {"name": file_name, "size_kb": len(file_bytes) // 1024},
                                }
                            )

                        except Exception as e:
                            logger.error("File processing error: %s", e)
                            await ws.send_json(
                                {
                                    "type": "error",
                                    "content": f"File processing failed: {e}",
                                }
                            )

            except WebSocketDisconnect:
                logger.info("WebSocket disconnected")
            except Exception as e:
                logger.error("WebSocket error: %s", e, exc_info=True)
                with contextlib.suppress(Exception):
                    await ws.close()

        return app

    async def _process_upload(self, name: str, data: bytes, mime: str) -> dict:
        """Process an uploaded file and extract usable content."""
        ext = Path(name).suffix.lower()
        result = {
            "name": name,
            "mime": mime or mimetypes.guess_type(name)[0] or "application/octet-stream",
            "size": len(data),
            "preview": "",
            "content": "",
        }

        # Text files
        if ext in (
            ".txt",
            ".md",
            ".csv",
            ".json",
            ".yaml",
            ".yml",
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".html",
            ".css",
            ".xml",
            ".toml",
            ".ini",
            ".cfg",
            ".conf",
            ".log",
            ".sh",
            ".bash",
            ".zsh",
            ".fish",
        ):
            try:
                text = data.decode("utf-8")
                result["content"] = text
                result["preview"] = text[:500]
            except UnicodeDecodeError:
                result["preview"] = "[Binary text file — cannot decode as UTF-8]"

        # PDF
        elif ext == ".pdf":
            try:
                import io

                import PyPDF2

                pdf_file = io.BytesIO(data)
                reader = PyPDF2.PdfReader(pdf_file)
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
                result["content"] = text
                result["preview"] = text[:500]
                result["meta"] = {"pages": len(reader.pages)}
            except ImportError:
                result["preview"] = "[PDF support requires: pip install PyPDF2]"
            except Exception as e:
                result["preview"] = f"[PDF parse error: {e}]"

        # Word documents
        elif ext in (".docx", ".doc"):
            try:
                import docx

                doc = docx.Document(io.BytesIO(data))
                text = "\n".join(p.text for p in doc.paragraphs)
                result["content"] = text
                result["preview"] = text[:500]
            except ImportError:
                result["preview"] = "[DOCX support requires: pip install python-docx]"
            except Exception as e:
                result["preview"] = f"[DOCX parse error: {e}]"

        # Excel
        elif ext in (".xlsx", ".xls"):
            try:
                import openpyxl

                wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
                rows = []
                for ws in wb.worksheets[:1]:  # First sheet only
                    for row in ws.iter_rows(values_only=True):
                        rows.append(" | ".join(str(c) if c is not None else "" for c in row[:10]))
                text = "\n".join(rows[:100])
                result["content"] = text
                result["preview"] = text[:500]
            except ImportError:
                result["preview"] = "[Excel support requires: pip install openpyxl]"
            except Exception as e:
                result["preview"] = f"[Excel parse error: {e}]"

        # Images
        elif ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            import base64

            b64 = base64.b64encode(data).decode("utf-8")
            result["preview"] = f"data:{result['mime']};base64,{b64}"
            result["is_image"] = True

        # Default: binary
        else:
            result["preview"] = f"[Binary file: {name}, {len(data)} bytes]"

        return result
