import unittest

import app


class GroceryListEmailRenderingTests(unittest.TestCase):
    def render(self, sections):
        snapshot = {
            "snapshot_id": 42,
            "snapshot_kind": "EMAIL",
            "captured_at_utc": "2026-09-24T18:00:00Z",
        }
        presentation = app._build_grocery_list_snapshot_presentation(
            snapshot,
            sections,
        )
        with app.app.test_request_context("/"):
            text = app._render_grocery_list_email_body(presentation)
            html = app._render_grocery_list_email_html(presentation)
        return presentation, text, html

    def test_html_renders_ordered_snapshot_columns_and_purchase_states(self):
        sections = [
            {
                "snapshot_section_id": 1,
                "name": "First Section",
                "display_order": 0,
                "items": [
                    {
                        "snapshot_item_id": 1,
                        "item_name": "Needed Item",
                        "stock_text": "Low",
                        "needed_text": "2",
                        "purchased": 0,
                        "display_order": 0,
                    },
                    {
                        "snapshot_item_id": 2,
                        "item_name": "Blank Needed",
                        "stock_text": None,
                        "needed_text": None,
                        "purchased": 0,
                        "display_order": 1,
                    },
                    {
                        "snapshot_item_id": 3,
                        "item_name": "Whitespace Needed",
                        "stock_text": "In stock",
                        "needed_text": "   ",
                        "purchased": 1,
                        "display_order": 2,
                    },
                ],
            },
            {
                "snapshot_section_id": 2,
                "name": "Second Section",
                "display_order": 1,
                "items": [
                    {
                        "snapshot_item_id": 4,
                        "item_name": "Later Item",
                        "stock_text": "Some",
                        "needed_text": "",
                        "purchased": 1,
                        "display_order": 0,
                    },
                ],
            },
        ]

        presentation, text, html = self.render(sections)

        self.assertEqual(
            [section["name"] for section in presentation["sections"]],
            ["First Section", "Second Section"],
        )
        self.assertEqual(
            [item["item_name"] for item in presentation["sections"][0]["items"]],
            ["Needed Item", "Blank Needed", "Whitespace Needed"],
        )
        self.assertEqual(
            [item["needs_purchase"] for item in presentation["sections"][0]["items"]],
            [True, False, False],
        )
        for heading in ("Item", "Stock", "Needed", "Purchased"):
            self.assertIn(f">{heading}</th>", html)
        self.assertEqual(text.splitlines()[0], "Grocery List")
        self.assertEqual(html.count("<h1"), 1)
        self.assertIn("Grocery List", html)
        self.assertIn("Purchased", html)
        self.assertIn("Not Purchased", html)
        self.assertEqual(html.count("background-color: #fff8d6;"), 1)
        self.assertNotIn("None", html)
        self.assertLess(html.index("First Section"), html.index("Second Section"))
        self.assertLess(html.index("Needed Item"), html.index("Later Item"))

    def test_html_uses_constrained_container_and_shared_section_column_widths(self):
        sections = [
            {
                "snapshot_section_id": 1,
                "name": "First Section",
                "display_order": 0,
                "items": [],
            },
            {
                "snapshot_section_id": 2,
                "name": "Second Section",
                "display_order": 1,
                "items": [],
            },
        ]

        _presentation, _text, html = self.render(sections)

        self.assertIn('width="100%"', html)
        self.assertIn("max-width: 960px;", html)
        self.assertIn("margin: 0 auto;", html)
        self.assertEqual(html.count('<col width="52%" style="width: 52%;">'), 2)
        self.assertEqual(html.count('<col width="12%" style="width: 12%;">'), 2)
        self.assertEqual(html.count('<col width="16%" style="width: 16%;">'), 2)
        self.assertEqual(html.count('<col width="20%" style="width: 20%;">'), 2)
        self.assertEqual(
            52 + 12 + 16 + 20,
            100,
        )

    def test_html_escapes_all_user_entered_values(self):
        sections = [
            {
                "snapshot_section_id": 1,
                "name": "Section <strong> & One",
                "display_order": 0,
                "items": [
                    {
                        "snapshot_item_id": 1,
                        "item_name": "<script>alert(1)</script>",
                        "stock_text": "<b>stock</b>",
                        "needed_text": "<i>needed</i>",
                        "purchased": 0,
                        "display_order": 0,
                    },
                ],
            },
        ]

        _presentation, text, html = self.render(sections)

        self.assertIn("Section &lt;strong&gt; &amp; One", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("&lt;b&gt;stock&lt;/b&gt;", html)
        self.assertIn("&lt;i&gt;needed&lt;/i&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("<script>alert(1)</script>", text)


if __name__ == "__main__":
    unittest.main()
