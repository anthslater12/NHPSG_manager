import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.datastructures import FileStorage

import app


class WorkerResourceUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp.name) / "worker-resources.db")
        self.storage_path = Path(self.temp.name) / "storage"
        self.old_db_name = app.DB_NAME
        self.old_storage_path = app.WORKER_RESOURCE_STORAGE_PATH
        self.old_testing = app.app.config.get("TESTING")
        app.DB_NAME = self.database_path
        app.WORKER_RESOURCE_STORAGE_PATH = str(self.storage_path)
        app.app.config.update(TESTING=True)
        self.create_database()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db_name
        app.WORKER_RESOURCE_STORAGE_PATH = self.old_storage_path
        app.app.config.update(TESTING=self.old_testing)
        self.temp.cleanup()

    def create_database(self):
        conn = sqlite3.connect(self.database_path)
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                password_hash TEXT,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO users VALUES
                (1, 'admin', 'x', 'Admin User', 'Admin', 1),
                (2, 'program-manager', 'x', 'Program Manager', 'Program Manager', 1),
                (3, 'director', 'x', 'Director User', 'Director', 1),
                (4, 'worker', 'x', 'Support Worker', 'Support Worker', 1),
                (5, 'inactive', 'x', 'Inactive Director', 'Director', 0);
        """)
        conn.commit()
        conn.close()

    def login(self, user_id=1, role="Admin"):
        with self.client.session_transaction() as session:
            session.update(
                user_id=user_id,
                role=role,
                full_name="Test User",
            )

    def post_upload(
        self,
        *,
        filename="orientation.mp4",
        content=b"video bytes",
        **overrides,
    ):
        data = {
            "title": "Orientation",
            "description": "New worker orientation",
            "category": "Training",
            "resource_type": "Video",
            "display_order": "0",
        }
        data.update(overrides)
        if filename is not None:
            data["file"] = (io.BytesIO(content), filename)
        return self.client.post(
            "/worker-resources/manage/new",
            data=data,
            content_type="multipart/form-data",
        )

    def rows(self):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM worker_resources ORDER BY resource_id"
        ).fetchall()
        conn.close()
        return rows

    def stored_files(self):
        if not self.storage_path.exists():
            return []
        return sorted(
            path for path in self.storage_path.rglob("*") if path.is_file()
        )

    def test_management_access_requires_active_authorized_database_user(self):
        self.assertEqual(
            self.client.get("/worker-resources/manage/new").status_code,
            302,
        )
        self.assertIn(
            "/login",
            self.client.get("/worker-resources/manage/new").headers["Location"],
        )
        self.login(4, "Support Worker")
        self.assertEqual(
            self.client.get("/worker-resources/manage/new").status_code,
            403,
        )
        self.login(5, "Director")
        self.assertEqual(
            self.client.get("/worker-resources/manage/new").status_code,
            403,
        )

        for user_id, role in ((1, "Admin"), (2, "Program Manager"), (3, "Director")):
            self.login(user_id, role)
            response = self.client.get("/worker-resources/manage/new")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'enctype="multipart/form-data"', response.data)

        self.assertFalse(self.storage_path.exists())

    def test_valid_mp4_saves_content_metadata_actor_and_generated_filename(self):
        self.login(2, "Program Manager")
        content = b"not actually a video, but valid test bytes"
        response = self.post_upload(
            filename="orientation.mp4",
            content=content,
            title="  Orientation  ",
            description="  New worker orientation  ",
            category="Training",
            resource_type="Video",
            display_order="7",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/worker-resources")
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["title"], "Orientation")
        self.assertEqual(row["description"], "New worker orientation")
        self.assertEqual(row["category"], "Training")
        self.assertEqual(row["resource_type"], "Video")
        self.assertEqual(row["mime_type"], "video/mp4")
        self.assertEqual(row["file_size_bytes"], len(content))
        self.assertEqual(row["display_order"], 7)
        self.assertEqual(row["uploaded_by_user_id"], 2)
        self.assertEqual(row["active"], 1)
        self.assertEqual(row["original_filename"], "orientation.mp4")
        self.assertRegex(row["stored_filename"], r"^[0-9a-f]{48}\.mp4$")
        self.assertEqual(row["created_at_utc"][-1], "Z")
        self.assertEqual(row["updated_at_utc"], row["created_at_utc"])
        files = self.stored_files()
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, row["stored_filename"])
        self.assertEqual(files[0].read_bytes(), content)

    def test_valid_document_and_image_extensions_use_explicit_mime_mapping(self):
        self.login()
        for filename, resource_type, mime_type, content in (
            ("policy.PDF", "Document", "application/pdf", b"pdf"),
            ("photo.JPEG", "Image", "image/jpeg", b"jpeg"),
        ):
            with self.subTest(filename=filename):
                response = self.post_upload(
                    filename=filename,
                    content=content,
                    resource_type=resource_type,
                    category="Reference",
                )
                self.assertEqual(response.status_code, 302)

        rows = self.rows()
        self.assertEqual(
            [(row["original_filename"], row["mime_type"]) for row in rows],
            [("policy.PDF", "application/pdf"), ("photo.JPEG", "image/jpeg")],
        )
        self.assertEqual(len(self.stored_files()), 2)

    def test_duplicate_original_names_receive_distinct_opaque_storage_names(self):
        self.login()
        self.assertEqual(self.post_upload().status_code, 302)
        self.assertEqual(self.post_upload().status_code, 302)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["original_filename"] for row in rows}, {"orientation.mp4"})
        self.assertEqual(
            len({row["stored_filename"] for row in rows}),
            2,
        )
        self.assertEqual(len(self.stored_files()), 2)

    def test_validation_rejects_invalid_values_without_creating_storage_or_rows(self):
        self.login()
        invalid_cases = (
            {"title": ""},
            {"title": "x" * 201},
            {"description": "x" * 5001},
            {"category": "Other"},
            {"resource_type": "Audio"},
            {"display_order": "-1"},
            {"display_order": "1.5"},
            {"filename": None},
            {"filename": ""},
            {"filename": "notes.exe"},
            {"filename": "notes.pdf", "resource_type": "Video"},
        )
        for case in invalid_cases:
            with self.subTest(case=case):
                case = dict(case)
                filename = case.pop("filename", "orientation.mp4")
                response = self.post_upload(
                    filename=filename,
                    **case,
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(len(self.rows()), 0)
                self.assertEqual(self.stored_files(), [])

    def test_path_traversal_absolute_and_drive_filenames_are_rejected(self):
        self.login()
        for filename in (
            "../orientation.mp4",
            "..\\orientation.mp4",
            "/tmp/orientation.mp4",
            "C:\\temp\\orientation.mp4",
            "C:orientation.mp4",
            "bad:name.mp4",
        ):
            with self.subTest(filename=filename):
                response = self.post_upload(filename=filename)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.stored_files(), [])

    def test_size_limit_and_empty_file_are_rejected_before_file_save(self):
        self.login()
        with patch.object(app, "WORKER_RESOURCE_MAX_UPLOAD_BYTES", 4):
            response = self.post_upload(content=b"12345")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.stored_files(), [])

        response = self.post_upload(content=b"")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.stored_files(), [])

    def test_file_write_failure_leaves_no_file_or_row(self):
        self.login()
        with patch.object(FileStorage, "save", side_effect=OSError("disk full")):
            response = self.post_upload()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.stored_files(), [])
        self.assertTrue(self.storage_path.is_dir())

    def test_database_commit_failure_removes_new_file_and_row(self):
        self.login()
        real_get_db = app.get_db
        calls = []

        class CommitFailureConnection:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, *args, **kwargs):
                return self.connection.execute(*args, **kwargs)

            def commit(self):
                raise sqlite3.OperationalError("forced commit failure")

            def rollback(self):
                return self.connection.rollback()

            def close(self):
                return self.connection.close()

        def get_db_for_request():
            calls.append(True)
            if len(calls) == 1:
                return real_get_db()
            connection = sqlite3.connect(self.database_path)
            return CommitFailureConnection(connection)

        with patch.object(app, "get_db", side_effect=get_db_for_request):
            response = self.post_upload()

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.stored_files(), [])

    def test_existing_storage_and_rows_survive_later_validation_failure(self):
        self.login()
        self.assertEqual(self.post_upload().status_code, 302)
        existing_files = self.stored_files()
        response = self.post_upload(filename="bad.exe")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.stored_files(), existing_files)
        self.assertEqual(len(self.rows()), 1)

    def test_existing_worker_resource_page_remains_available(self):
        self.login(4, "Support Worker")
        response = self.client.get("/worker-resources")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Worker Resources", response.data)


if __name__ == "__main__":
    unittest.main()
