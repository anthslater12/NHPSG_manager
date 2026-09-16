import re
import unittest

import app


class LoginPrivacyNoticeTests(unittest.TestCase):

    def setUp(self):
        self.client = app.app.test_client()

    def test_login_page_preserves_form_and_renders_privacy_notice(self):
        response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<form method="post">', response.data)
        self.assertIn(b'name="username"', response.data)
        self.assertIn(b'name="password"', response.data)
        self.assertIn(b'type="submit">Login</button>', response.data)
        self.assertIn(b"Privacy and Use Notice", response.data)
        rendered_text = re.sub(
            r"\s+",
            " ",
            response.data.decode("utf-8"),
        )
        self.assertIn(
            "By signing in, you acknowledge that your first name, last name, "
            "and email address are stored in the NHPSG Manager system for the "
            "purpose of providing access to and supporting your work. This "
            "information is stored on NHPSG’s local server and is not stored "
            "outside Canada. Users are expected to enter information accurately "
            "and truthfully.",
            rendered_text,
        )
        self.assertNotIn(b'type="checkbox"', response.data)
        self.assertEqual(response.data.count(b"required"), 2)

    def test_invalid_login_does_not_create_a_session(self):
        with self.client as client:
            response = client.post(
                "/login",
                data={"username": "unknown", "password": "incorrect"},
            )

            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Invalid username or password.", response.data)
            with client.session_transaction() as session:
                self.assertNotIn("user_id", session)


if __name__ == "__main__":
    unittest.main()
