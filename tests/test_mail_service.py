import os
import unittest
from unittest.mock import MagicMock, patch

import mail_service


class MailServiceTests(unittest.TestCase):
    def valid_environment(self):
        return {
            "NHPSG_MAIL_SERVER": "smtp.example.com",
            "NHPSG_MAIL_PORT": "587",
            "NHPSG_MAIL_USERNAME": "sender@example.com",
            "NHPSG_MAIL_PASSWORD": "secret",
            "NHPSG_MAIL_FROM": "sender@example.com",
        }

    def test_missing_configuration_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as caught:
                mail_service._mail_configuration()

        self.assertIn(
            "NHPSG mail configuration is incomplete",
            str(caught.exception),
        )

    def test_invalid_port_is_rejected(self):
        environment = self.valid_environment()
        environment["NHPSG_MAIL_PORT"] = "not-a-number"

        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(RuntimeError) as caught:
                mail_service._mail_configuration()

        self.assertIn(
            "NHPSG_MAIL_PORT must be a valid integer",
            str(caught.exception),
        )

    def test_send_email_requires_recipient(self):
        with self.assertRaises(ValueError):
            mail_service.send_email("", "Test", "Body")

    def test_send_email_requires_subject(self):
        with self.assertRaises(ValueError):
            mail_service.send_email(
                "worker@example.com",
                "",
                "Body",
            )

    @patch("mail_service.smtplib.SMTP")
    def test_send_email_uses_starttls_login_and_send_message(
        self,
        smtp_class,
    ):
        smtp = MagicMock()
        smtp_class.return_value.__enter__.return_value = smtp

        with patch.dict(
            os.environ,
            self.valid_environment(),
            clear=True,
        ):
            mail_service.send_email(
                "worker@example.com",
                "NHPSG Schedule",
                "Your schedule is ready.",
            )

        smtp_class.assert_called_once_with(
            "smtp.example.com",
            587,
            timeout=30,
        )

        self.assertEqual(smtp.ehlo.call_count, 2)
        smtp.starttls.assert_called_once_with()
        smtp.login.assert_called_once_with(
            "sender@example.com",
            "secret",
        )
        smtp.send_message.assert_called_once()

        message = smtp.send_message.call_args.args[0]

        self.assertEqual(
            message["From"],
            "sender@example.com",
        )
        self.assertEqual(
            message["To"],
            "worker@example.com",
        )
        self.assertEqual(
            message["Subject"],
            "NHPSG Schedule",
        )
        self.assertEqual(
            message.get_content().strip(),
            "Your schedule is ready.",
        )


if __name__ == "__main__":
    unittest.main()
