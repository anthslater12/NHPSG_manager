import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ShiftDashboardQuickEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "quick-entry.db"
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE isolated_test_marker (value TEXT)")
        conn.execute("INSERT INTO isolated_test_marker VALUES ('temporary')")
        conn.commit()
        conn.close()
        self.template = (ROOT / "templates" / "shift_dashboard.html").read_text(
            encoding="utf-8"
        )
        self.css = (ROOT / "static" / "css" / "style.css").read_text(
            encoding="utf-8"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_quick_entry_card_has_one_sticky_specific_class(self):
        self.assertEqual(self.template.count("shift-dashboard-quick-entry"), 1)
        card_start = self.template.index(
            '<div class="card shift-dashboard-quick-entry">'
        )
        card_end = self.template.index(
            "</div>\n{% endif %}", card_start
        ) + len("</div>")
        card = self.template[card_start:card_end]
        self.assertIn("<h3>Quick Entry</h3>", card)
        self.assertEqual(self.template.count("<h3>Quick Entry</h3>"), 1)

    def test_quick_entry_follows_closed_top_row_as_full_width_card(self):
        top_row_end = self.template.index(
            "</div>\n</div>\n\n{% if not shift_cancelled %}"
        )
        quick_entry_start = self.template.index(
            '<div class="card shift-dashboard-quick-entry">'
        )
        self.assertGreater(quick_entry_start, top_row_end)
        self.assertNotIn(
            "shift-dashboard-quick-entry",
            self.template[:top_row_end],
        )
        staff_notes_start = self.template.index(
            '<div class="card">\n    <h3>Staff Notes for this Shift</h3>'
        )
        staffing_start = self.template.index(
            "Historical Shift Staffing"
        )
        self.assertLess(quick_entry_start, staff_notes_start)
        self.assertLess(quick_entry_start, staffing_start)

    def test_only_top_row_cards_receive_compact_summary_class(self):
        self.assertEqual(
            self.template.count("shift-dashboard-summary-card"), 2
        )
        top_row = self.template[
            self.template.index('<div class="shift-dashboard-top-row">'):
            self.template.index("</div>\n\n<div class=\"card\">")
        ]
        self.assertEqual(top_row.count("shift-dashboard-summary-card"), 2)
        self.assertEqual(top_row.count("shift-dashboard-notices-card"), 1)
        self.assertNotIn(
            "shift-dashboard-summary-card",
            self.template[self.template.index("<h3>Quick Entry</h3>"):]
        )
        self.assertIn('<div class="card">\n    <h3>Staff Notes', self.template)
        self.assertIn('<div class="card">\n    <h3>Sleep', self.template)

    def test_existing_quick_entry_links_and_conditions_remain(self):
        start = self.template.index(
            '<div class="card shift-dashboard-quick-entry">'
        )
        end = self.template.index(
            "</div>\n{% endif %}", start
        )
        card = self.template[start:end]
        for text in (
            "Edit Staff Notes", "+ Toileting", "+ Food &amp; Fluid", "+ Sleep",
            "+ Activity", "+ Behaviour", "+ Incident", "Storyline",
            "shift.shift_id", "client_id=shift.client_id",
            "shift_notes_editable", "food_fluid_authorized", "sleep_authorized",
        ):
            self.assertIn(text, card)
        self.assertIn("{% if not shift_cancelled %}", self.template[:start])

    def test_sticky_css_is_local_and_mobile_safe(self):
        rule_start = self.css.index(".shift-dashboard-quick-entry {")
        rule_end = self.css.index("}", rule_start)
        rule = self.css[rule_start:rule_end]
        self.assertIn("position: sticky", rule)
        self.assertIn("top: 10px", rule)
        self.assertIn("z-index: 2", rule)
        mobile_start = self.css.index("@media (max-width: 600px)", rule_end)
        mobile_end = self.css.index("}", mobile_start)
        mobile = self.css[mobile_start:mobile_end]
        self.assertIn(".shift-dashboard-quick-entry", mobile)
        self.assertIn("position: static", mobile)
        self.assertNotIn("position: fixed", rule + mobile)
        self.assertIn("grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));", self.css)

    def test_summary_css_is_narrow_and_does_not_change_global_cards(self):
        self.assertIn(".shift-dashboard-top-row {", self.css)
        self.assertIn("align-items: stretch", self.css)
        summary_start = self.css.index(".shift-dashboard-summary-card {")
        summary_end = self.css.index(".shift-dashboard-top-row", summary_start)
        summary_css = self.css[summary_start:summary_end]
        self.assertIn("padding: 16px 18px", summary_css)
        self.assertIn(".shift-dashboard-summary-card h2", summary_css)
        self.assertIn(".shift-dashboard-summary-card p", summary_css)
        self.assertNotIn(".card {", summary_css)
        self.assertNotIn("padding: 16px 18px", self.css[:summary_start])

    def test_no_global_quick_entry_navigation_or_javascript_added(self):
        self.assertEqual(self.template.count("<h3>Quick Entry</h3>"), 1)
        self.assertNotIn("<script", self.template)


if __name__ == "__main__":
    unittest.main()
