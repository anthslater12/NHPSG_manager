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
        self.assertEqual(
            presentation["week_ending_heading"],
            "Grocery List \u2014 Week Ending Sunday, September 27, 2026",
        )
        for heading in ("Item", "Stock", "Needed", "Purchased"):
            self.assertIn(f">{heading}</th>", html)
        self.assertEqual(
            text.splitlines()[0],
            "Grocery List \u2014 Week Ending Sunday, September 27, 2026",
        )
        self.assertEqual(html.count("<h1"), 1)
        self.assertIn(
            "Week Ending Sunday, September 27, 2026",
            html,
        )
        self.assertIn("Purchased", html)
        self.assertIn("Not Purchased", html)
        self.assertEqual(html.count("background-color: #fff8d6;"), 1)
        self.assertNotIn("None", html)
        self.assertLess(html.index("First Section"), html.index("Second Section"))
        self.assertLess(html.index("Needed Item"), html.index("Later Item"))

    def test_week_ending_heading_uses_vancouver_local_week(self):
        self.assertEqual(
            app.format_grocery_list_week_ending_heading(
                "2026-09-28T06:59:59Z"
            ),
            "Grocery List \u2014 Week Ending Sunday, September 27, 2026",
        )
        self.assertEqual(
            app.format_grocery_list_week_ending_heading(
                "2026-09-28T07:00:00Z"
            ),
            "Grocery List \u2014 Week Ending Sunday, October 4, 2026",
        )

    def test_html_needed_highlight_excludes_zero_and_blank_values(self):
        sections = [{
            "snapshot_section_id": 1,
            "name": "Needed Values",
            "display_order": 0,
            "items": [
                {
                    "snapshot_item_id": 1,
                    "item_name": "Meaningful Needed",
                    "stock_text": "Low",
                    "needed_text": "1",
                    "purchased": 0,
                    "display_order": 0,
                },
                {
                    "snapshot_item_id": 2,
                    "item_name": "Blank Needed",
                    "stock_text": "Available",
                    "needed_text": "",
                    "purchased": 0,
                    "display_order": 1,
                },
                {
                    "snapshot_item_id": 3,
                    "item_name": "Whitespace Needed",
                    "stock_text": "Available",
                    "needed_text": "   ",
                    "purchased": 0,
                    "display_order": 2,
                },
                {
                    "snapshot_item_id": 4,
                    "item_name": "Zero Needed",
                    "stock_text": "Available",
                    "needed_text": "0",
                    "purchased": 0,
                    "display_order": 3,
                },
                {
                    "snapshot_item_id": 5,
                    "item_name": "Padded Zero Needed",
                    "stock_text": "Available",
                    "needed_text": "  0  ",
                    "purchased": 0,
                    "display_order": 4,
                },
                {
                    "snapshot_item_id": 6,
                    "item_name": "Purchased Needed",
                    "stock_text": "Available",
                    "needed_text": "1",
                    "purchased": 1,
                    "display_order": 5,
                },
                {
                    "snapshot_item_id": 7,
                    "item_name": "Purchased Blank",
                    "stock_text": "Available",
                    "needed_text": "",
                    "purchased": 1,
                    "display_order": 6,
                },
            ],
        }]

        _presentation, _text, html = self.render(sections)

        self.assertEqual(html.count("background-color: #fff8d6;"), 1)
        self.assertEqual(html.count("background-color: #e8f5e9;"), 2)
        self.assertRegex(
            html,
            r'<tr style="background-color: #fff8d6;">\s*'
            r'<td[^>]*>Meaningful Needed</td>',
        )
        for item_name in (
            "Blank Needed",
            "Whitespace Needed",
            "Zero Needed",
            "Padded Zero Needed",
        ):
            self.assertNotRegex(
                html,
                rf'<tr style="background-color: #fff8d6;">\s*'
                rf'<td[^>]*>{item_name}</td>',
            )
        for item_name in ("Purchased Needed", "Purchased Blank"):
            self.assertRegex(
                html,
                rf'<tr style="background-color: #e8f5e9;">\s*'
                rf'<td[^>]*>{item_name}</td>',
            )
        self.assertIn(">0</td>", html)
        self.assertIn(">  0  </td>", html)

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
