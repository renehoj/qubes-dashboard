"""The page: sections, formatting and the filter."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_information import model  # noqa: E402
from qubes_information.model import LiveStats, Prop, QubeInfo, Volume  # noqa: E402

GIB = 1024 ** 3


def info(**kwargs):
    base = dict(
        name="work", klass="AppVM", state="Running",
        props=(Prop("label", "blue"), Prop("template", "debian-13"),
               Prop("netvm", "sys-firewall", True), Prop("memory", "400", True),
               Prop("maxmem", "4000", True), Prop("include_in_backups", "True", True),
               Prop("backup_timestamp", ""), Prop("kernel", None)),
        features=(("gui", "1"), ("os-distribution", "debian"), ("os-version", "13"),
                  ("service.cups", "1"), ("service.crond", "")),
        tags=("created-by-dom0",),
        volumes=(Volume("private", "vm-pool", size=2 * GIB, usage=GIB, save_on_stop=True),
                 Volume("root", "vm-pool", size=20 * GIB, usage=5 * GIB, snap_on_start=True,
                        source="vm-debian-13-root")),
        firewall=("action=accept specialtarget=dns", "action=drop"),
        stats=LiveStats(memory_kb=1024 * 1024, max_memory_kb=4 * 1024 * 1024,
                        cpu_seconds=125, start_time=1000.0),
    )
    base.update(kwargs)
    return QubeInfo(**base)


def section(sections, title):
    return next(s for s in sections if s.title == title)


def rows(sections, title):
    return {row.key: row for row in section(sections, title).rows}


class FormatTest(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(model.format_bytes(None), "?")
        self.assertEqual(model.format_bytes(512), "512 B")
        self.assertEqual(model.format_bytes(1536 * 1024), "1.5 MiB")
        self.assertEqual(model.format_bytes(20 * GIB), "20.0 GiB")

    def test_duration(self):
        self.assertEqual(model.format_duration(45), "45s")
        self.assertEqual(model.format_duration(750), "12m 30s")
        self.assertEqual(model.format_duration(7500), "2h 05m")
        self.assertEqual(model.format_duration(3 * 86400 + 4 * 3600), "3d 4h")
        self.assertEqual(model.format_duration(-5), "0s")

    def test_props(self):
        self.assertEqual(model.format_prop(Prop("memory", "400")), "400 MiB")
        self.assertEqual(model.format_prop(Prop("qrexec_timeout", "60")), "60 s")
        self.assertEqual(model.format_prop(Prop("kernel", "")), model.EMPTY)
        self.assertEqual(model.format_prop(Prop("template", None)), model.UNSET)
        self.assertEqual(model.format_prop(Prop("backup_timestamp", "0")), "never")
        self.assertRegex(model.format_prop(Prop("start_time", "1761300000.5")),
                         r"^\d{4}-\d\d-\d\d \d\d:\d\d$")

    def test_is_true(self):
        for value in (None, "", "0", "False"):
            self.assertFalse(model.is_true(value))
        for value in ("1", "True", "yes"):
            self.assertTrue(model.is_true(value))


class SectionsTest(unittest.TestCase):
    def test_order_for_a_running_qube(self):
        titles = [s.title for s in model.sections(info(), now=4600)]
        self.assertEqual(titles, list(model.SECTION_ORDER))

    def test_no_live_section_when_halted(self):
        titles = [s.title for s in model.sections(info(state="Halted", stats=None))]
        self.assertNotIn(model.LIVE, titles)

    def test_dom0_has_no_storage_or_firewall(self):
        dom0 = info(name="dom0", klass="AdminVM", volumes=(), firewall=None)
        titles = [s.title for s in model.sections(dom0)]
        self.assertNotIn(model.STORAGE, titles)
        self.assertNotIn(model.FIREWALL, titles)
        self.assertNotIn("Net qube", rows(model.sections(dom0), model.OVERVIEW))

    def test_overview(self):
        overview = rows(model.sections(info()), model.OVERVIEW)
        self.assertEqual(overview["Template"].value, "debian-13")
        self.assertEqual(overview["Net qube"].value, "sys-firewall")
        self.assertEqual(overview["Operating system"].value, "debian 13")
        self.assertEqual(overview["Memory"].detail, "up to 4000 MiB")
        self.assertEqual(overview["Disk"].value, "6.0 GiB of 22.0 GiB")
        self.assertEqual(overview["Backups"].detail, "never backed up")

    def test_overview_without_network_or_balancing(self):
        overview = rows(model.sections(info(props=(
            Prop("netvm", ""), Prop("memory", "400"), Prop("maxmem", "0")))), model.OVERVIEW)
        self.assertEqual(overview["Net qube"].value, "none")
        self.assertTrue(overview["Net qube"].dim)
        self.assertIn("fixed", overview["Memory"].detail)

    def test_updates_available(self):
        features = (("updates-available", "1"), ("last-updates-check", "2026-10-24"))
        overview = rows(model.sections(info(features=features)), model.OVERVIEW)
        self.assertEqual(overview["Updates"].value, "available")
        self.assertEqual(overview["Updates"].detail, "last checked 2026-10-24")

    def test_live(self):
        live = rows(model.sections(info(), now=1000.0 + 7500), model.LIVE)
        self.assertEqual(live["Uptime"].value, "2h 05m")
        self.assertEqual(live["Memory now"].value, "1.0 GiB")
        self.assertEqual(live["Memory now"].detail, "of at most 4.0 GiB")
        self.assertEqual(live["CPU time"].value, "2m 05s")

    def test_properties_mark_defaults(self):
        props = rows(model.sections(info()), model.PROPERTIES)
        self.assertEqual(props["netvm"].detail, "default")
        self.assertTrue(props["netvm"].dim)
        self.assertFalse(props["label"].dim)
        self.assertEqual(props["kernel"].value, model.UNSET)

    def test_services_are_split_from_features(self):
        sections = model.sections(info())
        self.assertNotIn("service.cups", rows(sections, model.FEATURES))
        services = rows(sections, model.SERVICES)
        self.assertEqual(services["cups"].value, "enabled")
        self.assertEqual(services["crond"].value, "disabled")
        self.assertTrue(services["crond"].dim)

    def test_storage(self):
        storage = rows(model.sections(info()), model.STORAGE)
        self.assertEqual(storage["private"].value, "1.0 GiB of 2.0 GiB (50%)")
        self.assertIn("saved on stop", storage["private"].detail)
        self.assertIn("snapshot of vm-debian-13-root", storage["root"].detail)

    def test_firewall(self):
        firewall = section(model.sections(info()), model.FIREWALL)
        self.assertEqual(firewall.summary, "2 rules")
        self.assertEqual([r.key for r in firewall.rows], ["0", "1", ""])
        self.assertEqual(firewall.rows[-1].value, model.IMPLICIT_FIREWALL)

    def test_firewall_without_netvm_says_so(self):
        firewall = section(model.sections(info(props=(Prop("netvm", ""),))), model.FIREWALL)
        self.assertIn("no net qube", firewall.rows[-1].value)

    def test_error_row_leads_its_section(self):
        broken = info(firewall=None, errors=((model.FIREWALL, "refused"),))
        firewall = section(model.sections(broken), model.FIREWALL)
        self.assertTrue(firewall.rows[0].error)
        self.assertIn("refused", firewall.rows[0].value)


class FilterTest(unittest.TestCase):
    def setUp(self):
        self.sections = model.sections(info(), now=5000)

    def test_empty_query_keeps_everything(self):
        self.assertEqual(model.filter_sections(self.sections, "  "), self.sections)

    def test_words_must_all_match(self):
        shown = model.filter_sections(self.sections, "deb 13")
        keys = [(s.title, r.key) for s in shown for r in s.rows]
        self.assertIn(("Overview", "Operating system"), keys)
        self.assertNotIn(("Features", "os-distribution"), keys)  # "13" is elsewhere
        self.assertNotIn(("Features", "gui"), keys)

    def test_section_title_matches_all_its_rows(self):
        shown = model.filter_sections(self.sections, "TAGS")
        self.assertEqual([s.title for s in shown], [model.TAGS])

    def test_title_and_row_word_together(self):
        shown = model.filter_sections(self.sections, "services cups")
        self.assertEqual([(s.title, [r.key for r in s.rows]) for s in shown],
                         [(model.SERVICES, ["cups"])])

    def test_nothing(self):
        self.assertEqual(model.filter_sections(self.sections, "zzz"), [])


class TextTest(unittest.TestCase):
    def test_as_text(self):
        text = model.as_text(model.filter_sections(model.sections(info()), "tags"))
        self.assertEqual(text, "Tags  (1)\n  created-by-dom0\n")

    def test_empty_section_text(self):
        text = model.as_text(model.sections(info(tags=())))
        self.assertIn(model.EMPTY_TEXT[model.TAGS], text)


if __name__ == "__main__":
    unittest.main()
