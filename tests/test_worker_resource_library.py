import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class WorkerResourceLibraryTests(unittest.TestCase):
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
                (1, 'worker', 'x', 'Support Worker', 'Support Worker', 1),
                (2, 'manager', 'x', 'Program Manager', 'Program Manager', 1),
                (3, 'admin', 'x', 'Admin User', 'Admin', 1),
                (4, 'inactive', 'x', 'Inactive Worker', 'Support Worker', 0);
        """)
        conn.commit()
        conn.close()

        conn = app.get_db()
        conn.close()

    def login(self, user_id=1, session_role=None):
        role = session_role or {
            1: "Support Worker",
            2: "Program Manager",
            3: "Admin",
            4: "Support Worker",
        }[user_id]
        with self.client.session_transaction() as session:
            session.update(
                user_id=user_id,
                role=role,
                full_name="Test User",
            )

    def seed_resource(
        self,
        *,
        title="Orientation",
        description="Orientation information",
        category="Training",
        resource_type="Video",
        stored_filename="orientation.mp4",
        original_filename="orientation.mp4",
        mime_type="video/mp4",
        file_size_bytes=None,
        display_order=0,
        active=1,
        content=b"video bytes",
    ):
        conn = sqlite3.connect(self.database_path)
        conn.execute("PRAGMA foreign_keys = ON")
        if file_size_bytes is None and content is not None:
            file_size_bytes = len(content)
        if file_size_bytes is None:
            file_size_bytes = 1
        cursor = conn.execute("""
            INSERT INTO worker_resources
            (
                title, description, category, resource_type,
                stored_filename, original_filename, mime_type,
                file_size_bytes, display_order, active,
                uploaded_by_user_id, created_at_utc, updated_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
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
            2,
            "2026-09-19T12:00:00Z",
            "2026-09-19T12:00:00Z",
        ))
        resource_id = cursor.lastrowid
        conn.commit()
        conn.close()

        if content is not None:
            self.storage_path.mkdir(parents=True, exist_ok=True)
            (self.storage_path / stored_filename).write_bytes(content)
        return resource_id

    def update_stored_filename(self, resource_id, stored_filename):
        conn = sqlite3.connect(self.database_path)
        conn.execute(
            "UPDATE worker_resources SET stored_filename = ? WHERE resource_id = ?",
            (stored_filename, resource_id),
        )
        conn.commit()
        conn.close()

    def test_listing_authentication_and_existing_navigation(self):
        self.assertEqual(self.client.get("/worker-resources").status_code, 302)
        self.assertIn(
            "/login",
            self.client.get("/worker-resources").headers["Location"],
        )

        self.login()
        response = self.client.get("/worker-resources")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Leave Requests", response.data)
        self.assertIn(b"Staff Notices", response.data)

        self.login(4)
        self.assertEqual(self.client.get("/worker-resources").status_code, 403)

    def test_listing_shows_active_resources_in_business_and_display_order(self):
        self.seed_resource(
            title="Training Z",
            stored_filename="training-z.mp4",
            display_order=1,
        )
        self.seed_resource(
            title="Training B",
            stored_filename="training-b.mp4",
            display_order=0,
        )
        self.seed_resource(
            title="Training A",
            stored_filename="training-a.mp4",
            display_order=0,
        )
        self.seed_resource(
            title="Policy",
            category="Policies and Procedures",
            stored_filename="policy.pdf",
            resource_type="Document",
            mime_type="application/pdf",
            original_filename="policy.pdf",
            content=b"pdf",
        )
        self.seed_resource(
            title="Staff Form",
            category="Forms and Documents",
            stored_filename="staff-form.txt",
            resource_type="Document",
            mime_type="text/plain",
            original_filename="staff-form.txt",
            content=b"form",
        )
        self.seed_resource(
            title="Reference",
            category="Reference",
            stored_filename="reference.png",
            resource_type="Image",
            mime_type="image/png",
            original_filename="reference.png",
            content=b"png",
        )
        self.seed_resource(
            title="Hidden Resource",
            stored_filename="hidden.mp4",
            active=0,
        )

        self.login()
        response = self.client.get("/worker-resources")
        self.assertEqual(response.status_code, 200)
        body = response.data
        self.assertIn(b"Training &amp; Resources", body)
        self.assertIn(b"Orientation information", body)
        self.assertNotIn(b"Hidden Resource", body)
        category_headings = (
            b"<h4>Training</h4>",
            b"<h4>Policies and Procedures</h4>",
            b"<h4>Forms and Documents</h4>",
            b"<h4>Reference</h4>",
        )
        for heading in category_headings:
            self.assertIn(heading, body)
        heading_positions = [body.index(heading) for heading in category_headings]
        self.assertEqual(heading_positions, sorted(heading_positions))
        self.assertLess(body.index(b"Training A"), body.index(b"Training B"))
        self.assertLess(body.index(b"Training B"), body.index(b"Training Z"))
        self.assertNotIn(str(self.storage_path).encode(), body)
        self.assertNotIn(b"training-a.mp4", body)

    def test_empty_library_has_neutral_state(self):
        self.login()
        response = self.client.get("/worker-resources")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No worker resources are currently available.", response.data)

    def test_add_resource_link_uses_current_database_role(self):
        self.login(2, session_role="Support Worker")
        response = self.client.get("/worker-resources")
        self.assertIn(b"Add Resource", response.data)
        self.assertIn(b"/worker-resources/manage/new", response.data)

        self.login(1, session_role="Admin")
        response = self.client.get("/worker-resources")
        self.assertNotIn(b"Add Resource", response.data)

    def test_view_requires_login_active_resource_and_existing_file(self):
        resource_id = self.seed_resource()
        self.assertEqual(
            self.client.get(f"/worker-resources/{resource_id}/view").status_code,
            302,
        )
        self.login()
        self.assertEqual(
            self.client.get("/worker-resources/9999/view").status_code,
            404,
        )

        inactive_id = self.seed_resource(
            title="Inactive",
            stored_filename="inactive.mp4",
            active=0,
        )
        self.assertEqual(
            self.client.get(f"/worker-resources/{inactive_id}/view").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/worker-resources/{inactive_id}/media").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/worker-resources/{inactive_id}/download").status_code,
            404,
        )

        self.login(4)
        self.assertEqual(
            self.client.get(f"/worker-resources/{resource_id}/view").status_code,
            403,
        )

    def test_video_view_media_mime_inline_bytes_range_and_streaming_path(self):
        content = b"0123456789"
        resource_id = self.seed_resource(content=content)
        self.login()
        response = self.client.get(f"/worker-resources/{resource_id}/view")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<video controls preload="metadata"', response.data)
        self.assertIn(
            f"/worker-resources/{resource_id}/media".encode(),
            response.data,
        )

        with patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read")):
            media = self.client.get(f"/worker-resources/{resource_id}/media")
        self.assertEqual(media.status_code, 200)
        self.assertEqual(media.mimetype, "video/mp4")
        self.assertEqual(media.data, content)
        self.assertTrue(media.headers["Content-Disposition"].startswith("inline"))
        self.assertNotIn(b"attachment", media.headers["Content-Disposition"].lower().encode())

        ranged = self.client.get(
            f"/worker-resources/{resource_id}/media",
            headers={"Range": "bytes=1-3"},
        )
        self.assertEqual(ranged.status_code, 206)
        self.assertEqual(ranged.data, b"123")
        self.assertIn("bytes 1-3/10", ranged.headers["Content-Range"])

    def test_image_view_and_authenticated_media_have_correct_mime(self):
        resource_id = self.seed_resource(
            title="Reference Image",
            resource_type="Image",
            stored_filename="reference.webp",
            original_filename="reference.webp",
            mime_type="image/webp",
            content=b"image bytes",
        )
        self.login()
        response = self.client.get(f"/worker-resources/{resource_id}/view")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<img", response.data)
        self.assertIn(
            f"/worker-resources/{resource_id}/media".encode(),
            response.data,
        )
        media = self.client.get(f"/worker-resources/{resource_id}/media")
        self.assertEqual(media.status_code, 200)
        self.assertEqual(media.mimetype, "image/webp")
        self.assertEqual(media.data, b"image bytes")

    def test_document_view_open_and_download_preserve_mime_and_original_name(self):
        pdf_id = self.seed_resource(
            title="Policy PDF",
            resource_type="Document",
            stored_filename="opaque-pdf-token.pdf",
            original_filename="policy handbook.pdf",
            mime_type="application/pdf",
            content=b"pdf bytes",
        )
        docx_id = self.seed_resource(
            title="Form DOCX",
            resource_type="Document",
            stored_filename="opaque-docx-token.docx",
            original_filename="staff form.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            content=b"docx bytes",
        )
        self.login()

        view = self.client.get(f"/worker-resources/{pdf_id}/view")
        self.assertEqual(view.status_code, 200)
        self.assertIn(b"Open Document", view.data)
        self.assertIn(b"Download Document", view.data)
        media = self.client.get(f"/worker-resources/{pdf_id}/media")
        self.assertEqual(media.mimetype, "application/pdf")
        self.assertTrue(media.headers["Content-Disposition"].startswith("inline"))

        download = self.client.get(f"/worker-resources/{docx_id}/download")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(
            download.mimetype,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        disposition = download.headers["Content-Disposition"]
        self.assertTrue(disposition.startswith("attachment"))
        self.assertIn("staff form.docx", disposition)
        self.assertNotIn(b"opaque-docx-token.docx", download.data)

    def test_corrupt_stored_filenames_cannot_escape_storage_root(self):
        outside = Path(self.temp.name) / "outside-secret.txt"
        outside.write_bytes(b"external secret")
        cases = (
            "../../outside-secret.txt",
            r"..\..\outside-secret.txt",
            r"C:\outside-secret.txt",
            "/etc/passwd",
            str(outside),
        )
        self.login()
        for index, stored_filename in enumerate(cases):
            with self.subTest(stored_filename=stored_filename):
                resource_id = self.seed_resource(
                    title=f"Corrupt {index}",
                    stored_filename=f"safe-{index}.mp4",
                )
                self.update_stored_filename(resource_id, stored_filename)
                media = self.client.get(
                    f"/worker-resources/{resource_id}/media"
                )
                self.assertEqual(media.status_code, 404)
                self.assertNotIn(b"external secret", media.data)

    def test_symlink_inside_storage_cannot_serve_external_file(self):
        external = Path(self.temp.name) / "external-symlink-secret.txt"
        external.write_bytes(b"distinctive external symlink content")
        link = self.storage_path / "linked-resource.mp4"
        resource_id = self.seed_resource(
            title="Symlinked Resource",
            stored_filename=link.name,
            content=None,
        )
        self.storage_path.mkdir(parents=True, exist_ok=True)

        try:
            os.symlink(str(external), str(link), target_is_directory=False)
        except (NotImplementedError, OSError) as error:
            self.skipTest(
                "Real symlink containment case was not executed: "
                f"the current platform/policy cannot create symlinks ({error})."
            )

        self.login()
        response = self.client.get(
            f"/worker-resources/{resource_id}/media"
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b"distinctive external symlink content", response.data)
        self.assertNotIn(str(external).encode(), response.data)
        self.assertNotIn(str(self.storage_path).encode(), response.data)

    def test_missing_file_is_controlled_and_does_not_leak_storage_path(self):
        resource_id = self.seed_resource(
            stored_filename="file-does-not-exist.mp4",
            content=None,
        )
        self.login()
        view = self.client.get(f"/worker-resources/{resource_id}/view")
        media = self.client.get(f"/worker-resources/{resource_id}/media")
        self.assertEqual(view.status_code, 404)
        self.assertEqual(media.status_code, 404)
        self.assertNotIn(str(self.storage_path).encode(), view.data)
        self.assertNotIn(str(self.storage_path).encode(), media.data)


if __name__ == "__main__":
    unittest.main()
