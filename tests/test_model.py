"""Filtering, ordering and the per-state action rules."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_dashboard import model  # noqa: E402
from qubes_dashboard.model import Qube  # noqa: E402


def q(name, state=model.RUNNING, klass="AppVM", template="debian-13"):
    return Qube(name, klass, "red", state, template)


QUBES = [
    q("work"),
    q("personal", model.HALTED),
    q("sys-net"),
    q("sys-usb", model.TRANSIENT),
    q("vault", model.HALTED),
    q("browser-sys", model.PAUSED),
    q("dom0", klass="AdminVM", template=None),
]


def names(qubes):
    return [qube.name for qube in qubes]


class VisibleQubesTest(unittest.TestCase):
    def test_default_view_is_qubes_that_are_on(self):
        self.assertEqual(
            names(model.visible_qubes(QUBES)),
            ["browser-sys", "dom0", "sys-net", "sys-usb", "work"],
        )

    def test_show_all_includes_halted(self):
        self.assertEqual(len(model.visible_qubes(QUBES, show_all=True)), len(QUBES))

    def test_search_matches_name_case_insensitively(self):
        self.assertEqual(names(model.visible_qubes(QUBES, "WORK")), ["work"])

    def test_search_does_not_match_template(self):
        self.assertEqual(model.visible_qubes(QUBES, "debian"), [])

    def test_search_respects_on_filter(self):
        self.assertEqual(model.visible_qubes(QUBES, "vault"), [])
        self.assertEqual(names(model.visible_qubes(QUBES, "vault", show_all=True)), ["vault"])

    def test_words_are_anded(self):
        self.assertEqual(names(model.visible_qubes(QUBES, "sys net")), ["sys-net"])

    def test_prefix_matches_come_first(self):
        self.assertEqual(
            names(model.visible_qubes(QUBES, "sys")),
            ["sys-net", "sys-usb", "browser-sys"],
        )

    def test_keep_shows_a_halted_qube_with_an_action_in_flight(self):
        self.assertIn("personal", names(model.visible_qubes(QUBES, keep={"personal"})))


class ActionsTest(unittest.TestCase):
    def test_halted(self):
        qube = q("x", model.HALTED)
        self.assertEqual(model.available_actions(qube),
                         {model.START, model.APPLICATIONS, model.SETTINGS, model.FIREWALL,
                          model.DEVICES, model.INFORMATION, model.LOGS, model.BACKUP})
        self.assertEqual(model.default_action(qube), model.START)

    def test_running_never_defaults_to_stop(self):
        qube = q("x")
        self.assertEqual(
            model.available_actions(qube),
            {model.STOP, model.RESTART, model.APPLICATIONS, model.SETTINGS, model.FIREWALL,
             model.DEVICES, model.INFORMATION, model.LOGS, model.BACKUP, model.CONSOLE},
        )
        self.assertEqual(model.default_action(qube), model.SETTINGS)

    def test_paused_is_resumed(self):
        qube = q("x", model.PAUSED)
        self.assertIn(model.START, model.available_actions(qube))
        self.assertEqual(model.action_label(model.START, qube), "Resume")

    def test_transient_only_the_windows(self):
        self.assertEqual(model.available_actions(q("x", model.TRANSIENT)),
                         {model.SETTINGS, model.FIREWALL, model.DEVICES, model.INFORMATION,
                          model.LOGS, model.BACKUP})

    def test_busy_only_the_windows(self):
        self.assertEqual(model.available_actions(q("x"), busy=True),
                         {model.SETTINGS, model.FIREWALL, model.DEVICES, model.INFORMATION,
                          model.LOGS, model.BACKUP})

    def test_dom0_only_settings_information_and_logs(self):
        # dom0 has no firewall, and no PCI devices of its own to assign.
        self.assertEqual(
            model.available_actions(q("dom0", klass="AdminVM", template=None)),
            {model.SETTINGS, model.INFORMATION, model.LOGS},
        )

    def test_information_logs_and_backup_in_every_state(self):
        for state in (model.RUNNING, model.HALTED, model.PAUSED, model.TRANSIENT,
                      model.HALTING, model.CRASHED, model.NA):
            with self.subTest(state=state):
                actions = model.available_actions(q("x", state))
                self.assertIn(model.INFORMATION, actions)
                self.assertIn(model.LOGS, actions)
                self.assertIn(model.BACKUP, actions)

    def test_backup_is_b(self):
        # dom0 has no Backup: test_dom0_only_settings_information_and_logs.
        self.assertEqual(model.ACTION_KEYS[model.BACKUP], "b")
        self.assertEqual(len(set(model.ACTION_KEYS.values())), len(model.ACTIONS))
        self.assertEqual(model.action_label(model.BACKUP, q("x")), "Backup")

    def test_console_only_for_a_running_qube(self):
        for state in (model.HALTED, model.PAUSED, model.TRANSIENT, model.HALTING,
                      model.CRASHED, model.NA):
            with self.subTest(state=state):
                self.assertNotIn(model.CONSOLE, model.available_actions(q("x", state)))
        self.assertNotIn(model.CONSOLE, model.available_actions(q("x"), busy=True))
        self.assertNotIn(model.CONSOLE, model.available_actions(
            q("dom0", klass="AdminVM", template=None)))

    def test_applications_when_running_or_off(self):
        for state in (model.RUNNING, model.HALTED, model.CRASHED, model.NA):
            with self.subTest(state=state):
                self.assertIn(model.APPLICATIONS, model.available_actions(q("x", state)))
        for state in (model.PAUSED, model.TRANSIENT, model.HALTING, model.DYING):
            with self.subTest(state=state):
                self.assertNotIn(model.APPLICATIONS, model.available_actions(q("x", state)))
        self.assertNotIn(model.APPLICATIONS, model.available_actions(q("x"), busy=True))
        self.assertNotIn(model.APPLICATIONS, model.available_actions(
            q("dom0", klass="AdminVM", template=None)))
        self.assertEqual(model.ACTION_KEYS[model.APPLICATIONS], "a")
        self.assertEqual(model.action_label(model.APPLICATIONS, q("x")), "Applications")

    def test_matching_apps_by_every_word(self):
        apps = [model.App("a", "LibreOffice Writer"), model.App("b", "LibreOffice Calc"),
                model.App("c", "Terminal")]
        self.assertEqual(model.matching_apps(apps, ""), apps)
        self.assertEqual(model.matching_apps(apps, "libre"), apps[:2])
        self.assertEqual(model.matching_apps(apps, "office WRI"), apps[:1])
        self.assertEqual(model.matching_apps(apps, "xyz"), [])

    def test_stop_and_restart_are_confirmed(self):
        self.assertEqual(model.CONFIRMED_ACTIONS, {model.STOP, model.RESTART})
        for action in model.CONFIRMED_ACTIONS:
            self.assertIn(action, model.CONFIRM_DETAIL)
        self.assertEqual(model.confirm_question(model.STOP, q("work")), "Stop work?")
        self.assertEqual(model.confirm_question(model.RESTART, q("work")), "Restart work?")

    def test_shortcut_letters_are_unique_and_in_their_labels(self):
        keys = list(model.ACTION_KEYS.values())
        self.assertEqual(len(keys), len(set(keys)))
        for action, key in model.ACTION_KEYS.items():
            self.assertIn(key, model.action_label(action, q("x")).lower())


class TemplateTextTest(unittest.TestCase):
    def test_template(self):
        self.assertEqual(model.template_text(q("x")), ("debian-13", False))

    def test_placeholders(self):
        self.assertEqual(
            model.template_text(q("t", klass="TemplateVM", template=None)),
            ("template", True),
        )
        self.assertEqual(
            model.template_text(q("s", klass="StandaloneVM", template=None)),
            ("standalone", True),
        )


if __name__ == "__main__":
    unittest.main()
