import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.datastructures import FileStorage

import app


class _CommitFailingConnection:
    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def commit(self):
        raise sqlite3.OperationalError("simulated commit failure")


class WorkerResourceManagementTests(unittest.TestCase):
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
        conn.executescript(
            """
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
                (1, 'worker', 'x', 'Support Worker', 'Support Worker', 1),
                (2, 'manager', 'x', 'Program Manager', 'Program Manager', 1),
                (3, 'director', 'x', 'Director User', 'Director', 1),
                (4, 'admin', 'x', 'Admin User', 'Admin', 1),
                (5, 'inactive', 'x', 'Inactive Director', 'Director', 0);
            """
        )
        conn.commit()
        conn.close()
        conn = app.get_db()
        conn.close()

    def login(self, user_id=1, session_role=None):
        role = session_role or {
            1: "Support Worker",
            2: "Program Manager",
            3: "Director",
            4: "Admin",
            5: "Director",
        }[user_id]
        with self.client.session_transaction() as session:
            session.update(user_id=user_id, role=role, full_name="Spoofed Name")

    def seed_resource(
        self,
        *,
        title="Orientation",
        description="Orientation information",
        category="Training",
        resource_type="Video",
        stored_filename="opaque-original.mp4",
        original_filename="orientation.mp4",
        mime_type="video/mp4",
        display_order=0,
        active=1,
        content=b"old content",
        uploaded_by_user_id=2,
        created_at_utc="2026-09-19T12:00:00Z",
        updated_at_utc="2026-09-19T12:00:00Z",
    ):
        conn = sqlite3.connect(self.database_path)
        conn.execute("PRAGMA foreign_keys = ON")
        file_size_bytes = len(content) if content is not None else 1
        cursor = conn.execute(
            """
            INSERT INTO worker_resources
            (
                title, description, category, resource_type,
                stored_filename, original_filename, mime_type,
                file_size_bytes, display_order, active,
                uploaded_by_user_id, created_at_utc, updated_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                description,
                category,
                resource_type,
                stored_filename,
                original_filename,
                mime_type,
                file_size_bytes,
                display_order,
                active,
                uploaded_by_user_id,
                created_at_utc,
                updated_at_utc,
            ),
        )
        resource_id = cursor.lastrowid
        conn.commit()
        conn.close()

        if content is not None:
            self.storage_path.mkdir(parents=True, exist_ok=True)
            (self.storage_path / stored_filename).write_bytes(content)
        return resource_id

    def row(self, resource_id):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM worker_resources WHERE resource_id = ?",
            (resource_id,),
        ).fetchone()
        conn.close()
        return row

    def replacement_data(
        self,
        *,
        filename="replacement.mp4",
        content=b"replacement content",
        **overrides,
    ):
        data = {
            "title": "Updated title",
            "description": "Updated description",
            "category": "Training",
            "resource_type": "Video",
            "display_order": "3",
            "file": (io.BytesIO(content), filename),
        }
        data.update(overrides)
        return data

    def post_edit(self, resource_id, **overrides):
        return self.client.post(
            f"/worker-resources/manage/{resource_id}/edit",
            data=self.replacement_data(**overrides),
            content_type="multipart/form-data",
        )

    def test_management_auth_uses_active_database_identity(self):
        self.assertEqual(self.client.get("/worker-resources/manage").status_code, 302)

        self.login(1, session_role="Admin")
        self.assertEqual(self.client.get("/worker-resources/manage").status_code, 403)

        self.login(5, session_role="Admin")
        self.assertEqual(self.client.get("/worker-resources/manage").status_code, 403)

        for user_id, role in (
            (2, "Support Worker"),
            (3, "Support Worker"),
            (4, "Support Worker"),
        ):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role=role)
                self.assertEqual(
                    self.client.get("/worker-resources/manage").status_code,
                    200,
                )

    def test_management_list_shows_all_statuses_and_safe_metadata(self):
        inactive_id = self.seed_resource(
            title="Inactive policy",
            category="Policies and Procedures",
            resource_type="Document",
            stored_filename="opaque-inactive.pdf",
            original_filename="policy.pdf",
            mime_type="application/pdf",
            active=0,
        )
        active_id = self.seed_resource(
            title="Active training",
            stored_filename="opaque-active.mp4",
        )
        self.login(2)
        response = self.client.get("/worker-resources/manage")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Active training", response.data)
        self.assertIn(b"Inactive policy", response.data)
        self.assertIn(b">Active<", response.data)
        self.assertIn(b">Inactive<", response.data)
        self.assertIn(b"orientation.mp4", response.data)
        self.assertIn(b"policy.pdf", response.data)
        self.assertNotIn(b"opaque-active.mp4", response.data)
        self.assertNotIn(b"opaque-inactive.pdf", response.data)
        self.assertIn(b"Add Resource", response.data)
        self.assertIn(f"/worker-resources/manage/{active_id}/edit".encode(), response.data)
        self.assertIn(f"/worker-resources/manage/{inactive_id}/edit".encode(), response.data)
        self.assertIn(
            f"/worker-resources/manage/{active_id}/deactivate".encode(),
            response.data,
        )
        self.assertIn(
            f"/worker-resources/manage/{inactive_id}/activate".encode(),
            response.data,
        )
        self.assertNotIn(b"/worker-resources/manage/" + str(active_id).encode() + b"/activate", response.data)

    def test_management_list_uses_active_category_display_title_order(self):
        self.seed_resource(
            title="Alpha late",
            stored_filename="alpha-late.mp4",
            display_order=2,
        )
        self.seed_resource(
            title="Alpha early",
            stored_filename="alpha-early.mp4",
            display_order=1,
        )
        self.seed_resource(
            title="Bravo",
            category="Policies and Procedures",
            resource_type="Document",
            stored_filename="policy.pdf",
            original_filename="policy.pdf",
            mime_type="application/pdf",
            content=b"pdf",
        )
        self.seed_resource(
            title="Charlie",
            category="Forms and Documents",
            resource_type="Document",
            stored_filename="form.txt",
            original_filename="form.txt",
            mime_type="text/plain",
            content=b"form",
        )
        self.seed_resource(
            title="Delta",
            category="Reference",
            resource_type="Image",
            stored_filename="reference.png",
            original_filename="reference.png",
            mime_type="image/png",
            content=b"image",
        )
        self.login(2)
        body = self.client.get("/worker-resources/manage").data
        positions = [body.index(title.encode()) for title in (
            "Alpha early",
            "Bravo",
            "Charlie",
            "Delta",
        )]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(body.index(b"Alpha early"), body.index(b"Alpha late"))

    def test_edit_get_and_metadata_only_update_preserve_file_identity(self):
        resource_id = self.seed_resource()
        before = self.row(resource_id)
        old_path = self.storage_path / before["stored_filename"]
        old_bytes = old_path.read_bytes()
        self.login(3)

        get_response = self.client.get(
            f"/worker-resources/manage/{resource_id}/edit"
        )
        self.assertEqual(get_response.status_code, 200)
        self.assertIn(b'value="Orientation"', get_response.data)
        self.assertIn(b'value="0"', get_response.data)
        self.assertNotIn(b'name="active"', get_response.data)

        response = self.client.post(
            f"/worker-resources/manage/{resource_id}/edit",
            data={
                "title": "  Revised orientation  ",
                "description": "  Revised description  ",
                "category": "Reference",
                "resource_type": "Video",
                "display_order": "9",
            },
        )
        self.assertEqual(response.status_code, 302)
        after = self.row(resource_id)
        self.assertEqual(after["title"], "Revised orientation")
        self.assertEqual(after["description"], "Revised description")
        self.assertEqual(after["category"], "Reference")
        self.assertEqual(after["display_order"], 9)
        self.assertEqual(after["stored_filename"], before["stored_filename"])
        self.assertEqual(after["original_filename"], before["original_filename"])
        self.assertEqual(after["uploaded_by_user_id"], before["uploaded_by_user_id"])
        self.assertEqual(after["created_at_utc"], before["created_at_utc"])
        self.assertNotEqual(after["updated_at_utc"], before["updated_at_utc"])
        self.assertEqual(old_path.read_bytes(), old_bytes)

    def test_edit_validation_rejects_invalid_metadata_and_incompatible_type(self):
        resource_id = self.seed_resource()
        self.login(2)
        invalid_forms = (
            {"title": ""},
            {"category": "Other"},
            {"resource_type": "Audio"},
            {"display_order": "-1"},
            {"title": "x" * 201},
            {"description": "x" * 5001},
        )
        for override in invalid_forms:
            data = {
                "title": "Valid",
                "description": "Description",
                "category": "Training",
                "resource_type": "Video",
                "display_order": "0",
            }
            data.update(override)
            with self.subTest(override=override):
                response = self.client.post(
                    f"/worker-resources/manage/{resource_id}/edit",
                    data=data,
                )
                self.assertEqual(response.status_code, 400)

        response = self.client.post(
            f"/worker-resources/manage/{resource_id}/edit",
            data={
                "title": "Valid",
                "description": "Description",
                "category": "Training",
                "resource_type": "Document",
                "display_order": "0",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.row(resource_id)["resource_type"], "Video")

    def test_valid_replacements_update_metadata_and_remove_old_file_after_commit(self):
        cases = (
            ("replacement.mp4", "Video", "Training", "video/mp4", b"new video"),
            ("replacement.PDF", "Document", "Reference", "application/pdf", b"new pdf"),
            ("replacement.PNG", "Image", "Reference", "image/png", b"new image"),
        )
        self.login(2)
        for filename, resource_type, category, mime_type, content in cases:
            with self.subTest(filename=filename):
                resource_id = self.seed_resource(
                    title=f"{filename} old",
                    category="Training",
                    resource_type="Video",
                    stored_filename=f"old-{filename.lower()}",
                    original_filename="old.mp4",
                    mime_type="video/mp4",
                )
                before = self.row(resource_id)
                old_path = self.storage_path / before["stored_filename"]
                response = self.post_edit(
                    resource_id,
                    filename=filename,
                    content=content,
                    title="Replaced",
                    category=category,
                    resource_type=resource_type,
                    display_order="4",
                )
                self.assertEqual(response.status_code, 302)
                after = self.row(resource_id)
                self.assertNotEqual(after["stored_filename"], before["stored_filename"])
                self.assertEqual(after["original_filename"], filename)
                self.assertEqual(after["mime_type"], mime_type)
                self.assertEqual(after["file_size_bytes"], len(content))
                new_path = self.storage_path / after["stored_filename"]
                self.assertEqual(new_path.read_bytes(), content)
                self.assertFalse(old_path.exists())

    def test_replacement_validation_rejects_unsafe_or_oversized_files(self):
        resource_id = self.seed_resource()
        before = self.row(resource_id)
        old_path = self.storage_path / before["stored_filename"]
        self.login(2)
        invalid_cases = (
            {"filename": "replacement.exe"},
            {"filename": "replacement.pdf", "resource_type": "Video"},
            {"filename": "../replacement.mp4"},
            {"filename": "C:\\replacement.mp4"},
        )
        for case in invalid_cases:
            with self.subTest(case=case):
                response = self.post_edit(resource_id, **case)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.row(resource_id)["stored_filename"], before["stored_filename"])
                self.assertEqual(old_path.read_bytes(), b"old content")

        with patch.object(app, "WORKER_RESOURCE_MAX_UPLOAD_BYTES", 3):
            response = self.post_edit(resource_id, content=b"too large")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.row(resource_id)["stored_filename"], before["stored_filename"])
        self.assertEqual(sorted(path.name for path in self.storage_path.iterdir()), [before["stored_filename"]])

    def test_replacements_with_duplicate_original_names_keep_distinct_storage_files(self):
        first_id = self.seed_resource(
            title="First",
            stored_filename="first-old.mp4",
        )
        second_id = self.seed_resource(
            title="Second",
            stored_filename="second-old.mp4",
        )
        self.login(2)
        for resource_id, content in ((first_id, b"first replacement"), (second_id, b"second replacement")):
            with self.subTest(resource_id=resource_id):
                response = self.post_edit(
                    resource_id,
                    filename="same-original-name.mp4",
                    content=content,
                )
                self.assertEqual(response.status_code, 302)

        first = self.row(first_id)
        second = self.row(second_id)
        self.assertEqual(first["original_filename"], "same-original-name.mp4")
        self.assertEqual(second["original_filename"], "same-original-name.mp4")
        self.assertNotEqual(first["stored_filename"], second["stored_filename"])
        self.assertEqual(
            (self.storage_path / first["stored_filename"]).read_bytes(),
            b"first replacement",
        )
        self.assertEqual(
            (self.storage_path / second["stored_filename"]).read_bytes(),
            b"second replacement",
        )

    def test_replacement_file_write_failure_preserves_old_row_and_file(self):
        resource_id = self.seed_resource()
        before = self.row(resource_id)
        old_path = self.storage_path / before["stored_filename"]
        self.login(2)
        with patch.object(FileStorage, "save", side_effect=OSError("write failed")):
            response = self.post_edit(resource_id)
        self.assertEqual(response.status_code, 500)
        after = self.row(resource_id)
        self.assertEqual(after["stored_filename"], before["stored_filename"])
        self.assertEqual(old_path.read_bytes(), b"old content")
        self.assertEqual([path.name for path in self.storage_path.iterdir()], [before["stored_filename"]])

    def test_replacement_commit_failure_removes_new_file_and_preserves_old_state(self):
        resource_id = self.seed_resource()
        before = self.row(resource_id)
        old_path = self.storage_path / before["stored_filename"]
        self.login(2)
        original_get_db = app.get_db

        def failing_get_db():
            return _CommitFailingConnection(original_get_db())

        with patch.object(app, "get_db", side_effect=failing_get_db):
            response = self.post_edit(resource_id)
        self.assertEqual(response.status_code, 500)
        after = self.row(resource_id)
        self.assertEqual(after["stored_filename"], before["stored_filename"])
        self.assertEqual(after["title"], before["title"])
        self.assertEqual(old_path.read_bytes(), b"old content")
        self.assertEqual([path.name for path in self.storage_path.iterdir()], [before["stored_filename"]])

    def test_cleanup_failure_after_commit_keeps_new_row_and_logs_only_resource_id(self):
        resource_id = self.seed_resource()
        before = self.row(resource_id)
        old_path = self.storage_path / before["stored_filename"]
        self.login(2)
        original_unlink = Path.unlink

        def fail_old_cleanup(path, *args, **kwargs):
            if path == old_path:
                raise OSError("cleanup failed")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", autospec=True, side_effect=fail_old_cleanup):
            with patch.object(app.app.logger, "exception") as log_exception:
                response = self.post_edit(resource_id)
        self.assertEqual(response.status_code, 302)
        after = self.row(resource_id)
        self.assertNotEqual(after["stored_filename"], before["stored_filename"])
        self.assertTrue(old_path.exists())
        messages = [call.args[0] for call in log_exception.call_args_list]
        self.assertTrue(
            any("Worker Resource cleanup failed for resource %s" in message for message in messages)
        )
        log_arguments = " ".join(
            str(argument)
            for call in log_exception.call_args_list
            for argument in call.args
        )
        self.assertNotIn(str(self.storage_path), log_arguments)
        self.assertNotIn(str(old_path), log_arguments)

    def test_replacement_rollback_cleanup_log_does_not_expose_storage_path(self):
        resource_id = self.seed_resource()
        before = self.row(resource_id)
        self.login(2)
        original_get_db = app.get_db

        def failing_get_db():
            return _CommitFailingConnection(original_get_db())

        def fail_cleanup(path, *args, **kwargs):
            raise OSError("cleanup failed")

        with patch.object(app, "get_db", side_effect=failing_get_db):
            with patch.object(Path, "unlink", autospec=True, side_effect=fail_cleanup):
                with patch.object(app.app.logger, "exception") as log_exception:
                    response = self.post_edit(resource_id)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.row(resource_id)["stored_filename"], before["stored_filename"])
        messages = [call.args[0] for call in log_exception.call_args_list]
        self.assertIn("Worker Resource cleanup failed", messages)
        self.assertNotIn(
            "Worker Resource cleanup failed for resource %s",
            messages,
        )
        log_arguments = " ".join(
            str(argument)
            for call in log_exception.call_args_list
            for argument in call.args
        )
        self.assertNotIn(str(self.storage_path), log_arguments)
        self.assertNotIn(before["stored_filename"], log_arguments)

    def test_status_changes_are_post_only_and_control_worker_visibility(self):
        resource_id = self.seed_resource()
        self.login(2)
        self.assertEqual(
            self.client.get(f"/worker-resources/manage/{resource_id}/deactivate").status_code,
            405,
        )
        before = self.row(resource_id)
        response = self.client.post(
            f"/worker-resources/manage/{resource_id}/deactivate"
        )
        self.assertEqual(response.status_code, 302)
        after = self.row(resource_id)
        self.assertEqual(after["active"], 0)
        self.assertNotEqual(after["updated_at_utc"], before["updated_at_utc"])
        self.assertTrue((self.storage_path / after["stored_filename"]).exists())

        self.login(1)
        self.assertNotIn(b"Orientation", self.client.get("/worker-resources").data)
        for suffix in ("view", "media", "download"):
            self.assertEqual(
                self.client.get(f"/worker-resources/{resource_id}/{suffix}").status_code,
                404,
            )

        self.login(2)
        response = self.client.post(
            f"/worker-resources/manage/{resource_id}/activate"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.row(resource_id)["active"], 1)
        self.login(1)
        self.assertIn(b"Orientation", self.client.get("/worker-resources").data)

    def test_status_requires_management_role_and_activation_requires_contained_file(self):
        resource_id = self.seed_resource()
        self.login(1, session_role="Admin")
        self.assertEqual(
            self.client.post(
                f"/worker-resources/manage/{resource_id}/deactivate"
            ).status_code,
            403,
        )

        missing_id = self.seed_resource(
            title="Missing",
            stored_filename="missing.mp4",
            content=None,
            active=0,
        )
        traversal_id = self.seed_resource(
            title="Traversal",
            stored_filename="../outside.mp4",
            content=None,
            active=0,
        )
        self.login(2)
        for invalid_id in (missing_id, traversal_id):
            with self.subTest(resource_id=invalid_id):
                response = self.client.post(
                    f"/worker-resources/manage/{invalid_id}/activate"
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.row(invalid_id)["active"], 0)

    def test_display_order_edit_changes_worker_facing_order(self):
        first_id = self.seed_resource(
            title="First",
            stored_filename="first.mp4",
            display_order=0,
        )
        second_id = self.seed_resource(
            title="Second",
            stored_filename="second.mp4",
            display_order=1,
        )
        self.login(2)
        self.login(1)
        initial_body = self.client.get("/worker-resources").data
        self.assertLess(initial_body.index(b"First"), initial_body.index(b"Second"))

        self.login(2)
        response = self.client.post(
            f"/worker-resources/manage/{first_id}/edit",
            data={
                "title": "First",
                "description": "Description",
                "category": "Training",
                "resource_type": "Video",
                "display_order": "2",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.login(1)
        body = self.client.get("/worker-resources").data
        self.assertLess(body.index(b"Second"), body.index(b"First"))
        self.assertEqual(self.row(first_id)["display_order"], 2)
        self.assertEqual(self.row(second_id)["display_order"], 1)


if __name__ == "__main__":
    unittest.main()
