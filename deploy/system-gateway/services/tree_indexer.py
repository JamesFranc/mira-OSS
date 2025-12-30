"""
File tree indexer with SQLite storage and inotify-based updates.

Maintains an index of the workspace filesystem for efficient directory
listing without scanning on every request.
"""

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, List, Optional

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from config import settings

logger = logging.getLogger(__name__)


class TreeIndexer(FileSystemEventHandler):
    """
    Indexes workspace filesystem with incremental updates via inotify.
    
    Uses SQLite for storage (in tmpfs for speed, rebuilt on container restart).
    """
    
    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root)
        self.db_path = settings.index_db_path
        self.debounce_ms = settings.index_update_debounce_ms
        
        self._observer: Optional[Observer] = None
        self._lock = threading.Lock()
        self._pending_updates: set[str] = set()
        self._debounce_timer: Optional[threading.Timer] = None
        
        self._init_database()
    
    def _init_database(self) -> None:
        """Initialize SQLite database schema."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    size INTEGER,
                    mtime REAL,
                    depth INTEGER NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_depth ON files(depth)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_type ON files(type)")
            conn.commit()
    
    def start(self) -> None:
        """Start filesystem monitoring and initial indexing."""
        logger.info(f"Starting tree indexer for {self.workspace_root}")
        
        # Initial full index
        self._full_reindex()
        
        # Start watchdog observer
        self._observer = Observer()
        self._observer.schedule(self, str(self.workspace_root), recursive=True)
        self._observer.start()
        
        logger.info("Tree indexer started")
    
    def stop(self) -> None:
        """Stop filesystem monitoring."""
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
        if self._debounce_timer:
            self._debounce_timer.cancel()
        logger.info("Tree indexer stopped")
    
    def _full_reindex(self) -> None:
        """Perform full reindex of workspace."""
        logger.info("Starting full reindex...")
        start = time.time()
        
        entries = []
        for root, dirs, files in os.walk(self.workspace_root):
            root_path = Path(root)
            rel_root = root_path.relative_to(self.workspace_root)
            depth = len(rel_root.parts)
            
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            
            for d in dirs:
                dir_path = root_path / d
                rel_path = str(rel_root / d) if str(rel_root) != "." else d
                entries.append((rel_path, d, "dir", None, None, depth + 1))
            
            for f in files:
                if f.startswith("."):
                    continue
                file_path = root_path / f
                rel_path = str(rel_root / f) if str(rel_root) != "." else f
                try:
                    stat = file_path.stat()
                    entries.append((rel_path, f, "file", stat.st_size, stat.st_mtime, depth + 1))
                except OSError:
                    continue
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM files")
            conn.executemany(
                "INSERT INTO files (path, name, type, size, mtime, depth) VALUES (?, ?, ?, ?, ?, ?)",
                entries
            )
            conn.commit()
        
        logger.info(f"Indexed {len(entries)} entries in {time.time() - start:.2f}s")

    def get_structure(
        self,
        path: str = "",
        depth: int = 2,
        include_hidden: bool = False,
        pattern: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Get directory structure from index.

        Args:
            path: Relative path within workspace (empty for root)
            depth: Maximum depth to return (1-5)
            include_hidden: Include hidden files/directories
            pattern: Optional glob pattern filter

        Returns:
            Dictionary with tree structure and stats
        """
        import fnmatch

        depth = max(1, min(5, depth))
        base_path = path.strip("/") if path else ""
        base_depth = len(Path(base_path).parts) if base_path else 0
        max_depth = base_depth + depth

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            if base_path:
                query = """
                    SELECT path, name, type, size, depth
                    FROM files
                    WHERE (path = ? OR path LIKE ?) AND depth <= ?
                    ORDER BY type DESC, path
                """
                cursor = conn.execute(query, (base_path, f"{base_path}/%", max_depth))
            else:
                query = """
                    SELECT path, name, type, size, depth
                    FROM files
                    WHERE depth <= ?
                    ORDER BY type DESC, path
                """
                cursor = conn.execute(query, (max_depth,))

            entries = []
            for row in cursor:
                name = row["name"]
                if not include_hidden and name.startswith("."):
                    continue
                if pattern and not fnmatch.fnmatch(name, pattern):
                    continue

                entry = {
                    "path": row["path"],
                    "name": name,
                    "type": row["type"]
                }
                if row["type"] == "file" and row["size"] is not None:
                    entry["size"] = row["size"]
                entries.append(entry)

            # Get total counts
            total_files = conn.execute("SELECT COUNT(*) FROM files WHERE type='file'").fetchone()[0]
            total_dirs = conn.execute("SELECT COUNT(*) FROM files WHERE type='dir'").fetchone()[0]

        return {
            "root": str(self.workspace_root / base_path) if base_path else str(self.workspace_root),
            "tree": entries,
            "stats": {
                "total_files": total_files,
                "total_dirs": total_dirs,
                "returned": len(entries)
            }
        }

    # Watchdog event handlers
    def on_created(self, event: FileSystemEvent) -> None:
        self._queue_update(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._queue_update(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._queue_update(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._queue_update(event.src_path)
        if hasattr(event, "dest_path"):
            self._queue_update(event.dest_path)

    def _queue_update(self, path: str) -> None:
        """Queue a path for index update with debouncing."""
        with self._lock:
            self._pending_updates.add(path)
            if self._debounce_timer:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(
                self.debounce_ms / 1000.0,
                self._flush_updates
            )
            self._debounce_timer.start()

    def _flush_updates(self) -> None:
        """Process pending updates."""
        with self._lock:
            paths = self._pending_updates.copy()
            self._pending_updates.clear()

        if not paths:
            return

        # For simplicity, just reindex affected paths
        # A more sophisticated implementation would do incremental updates
        for path_str in paths:
            path = Path(path_str)
            try:
                rel_path = path.relative_to(self.workspace_root)
            except ValueError:
                continue

            with sqlite3.connect(self.db_path) as conn:
                if path.exists():
                    # Update or insert
                    stat = path.stat() if path.is_file() else None
                    conn.execute(
                        """INSERT OR REPLACE INTO files (path, name, type, size, mtime, depth)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            str(rel_path),
                            path.name,
                            "dir" if path.is_dir() else "file",
                            stat.st_size if stat else None,
                            stat.st_mtime if stat else None,
                            len(rel_path.parts)
                        )
                    )
                else:
                    # Delete
                    conn.execute("DELETE FROM files WHERE path = ? OR path LIKE ?",
                                (str(rel_path), f"{rel_path}/%"))
                conn.commit()

