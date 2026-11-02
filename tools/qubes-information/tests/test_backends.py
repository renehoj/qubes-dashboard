"""Backends: the demo data, and the qubesadmin backend against a stand-in qubesadmin."""

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_information import model  # noqa: E402
from qubes_information.backend import BackendError, create_backend  # noqa: E402
from qubes_information.backend.mock import MockBackend  # noqa: E402


class MockBackendTest(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend(delay=0)

    def test_auto_is_demo_without_qubesadmin(self):
        try:
            import qubesadmin  # noqa: F401  pylint: disable=unused-import,import-outside-toplevel
        except ImportError:
            self.assertTrue(create_backend("auto").is_demo)
        else:
            self.skipTest("qubesadmin is installed")

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            create_backend("bogus")

    def test_every_dashboard_demo_qube_has_information(self):
        for name in ("dom0", "work", "vault", "sys-net", "debian-13", "disp1234", "games"):
            with self.subTest(name=name):
                info = self.backend.get_info(name)
                self.assertEqual(info.name, name)
                self.assertTrue(model.sections(info))

    def test_unknown_qube(self):
        with self.assertRaises(BackendError):
            self.backend.get_info("nope")
        with self.assertRaises(BackendError):
            self.backend.get_stats("nope")

    def test_stats_only_while_running(self):
        self.assertIsNotNone(self.backend.get_stats("work"))
        self.assertIsNone(self.backend.get_stats("vault"))
        self.assertIsNone(self.backend.get_info("vault").stats)

    def test_dom0(self):
        info = self.backend.get_info("dom0")
        self.assertTrue(info.is_admin)
        self.assertIsNone(info.firewall)
        self.assertEqual(info.volumes, ())

    def test_a_section_can_fail_alone(self):
        info = self.backend.get_info("disp1234")
        self.assertIsNotNone(info.error(model.FIREWALL))
        self.assertTrue(info.props)


# -- a stand-in for qubesadmin ---------------------------------------------------
class QubesException(Exception):
    pass


class FakeVolume:
    def __init__(self, **attrs):
        self.__dict__.update(attrs)

    def is_outdated(self):
        return self.__dict__.get("outdated", False)


class FakeFeatures(dict):
    pass


class FakeRule:
    def __init__(self, rule):
        self.rule = rule


class FakeVM:
    def __init__(self, name, klass, state="Halted", props=None, features=None, tags=(),
                 volumes=None, rules=(), refuse=()):
        self.name = name
        self.klass = klass
        self._state = state
        self._props = props or {}
        self._defaults = {"netvm"}
        self.features = FakeFeatures(features or {})
        self._tags = tags
        self._volumes = volumes or {}
        self._rules = rules
        self._refuse = set(refuse)

    def __getattr__(self, prop):
        props = self.__dict__.get("_props", {})
        if prop in props:
            return props[prop]
        raise AttributeError(prop)

    def __str__(self):
        return self.name

    def get_power_state(self):
        return self._state

    def property_list(self):
        return list(self._props) + ["template_unset"]

    def property_is_default(self, prop):
        return prop in self._defaults

    @property
    def tags(self):
        if "tags" in self._refuse:
            raise QubesException("Request refused")
        return iter(self._tags)

    @property
    def volumes(self):
        return self._volumes

    @property
    def firewall(self):
        if "firewall" in self._refuse:
            raise QubesException("Request refused")
        return types.SimpleNamespace(rules=[FakeRule(r) for r in self._rules])


class FakeApp:
    domains = {}

    def __init__(self):
        self.cache_enabled = False

    def qubesd_call(self, dest, method):
        assert method == "admin.vm.CurrentState"
        return f"mem=2097152 mem_static_max=4096000 cputime=90000000000 " \
               f"power_state={self.domains[dest].get_power_state()}".encode()


def install_fake_qubesadmin():
    module = types.ModuleType("qubesadmin")
    module.Qubes = FakeApp
    module.exc = types.ModuleType("qubesadmin.exc")
    module.exc.QubesException = QubesException
    sys.modules["qubesadmin"] = module
    sys.modules["qubesadmin.exc"] = module.exc


class Label:
    def __str__(self):
        return "blue"


class QubesAdminBackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = {k: sys.modules.get(k) for k in ("qubesadmin", "qubesadmin.exc")}
        install_fake_qubesadmin()
        # pylint: disable=import-outside-toplevel
        from qubes_information.backend import qubesadmin_backend
        cls.module = qubesadmin_backend

    @classmethod
    def tearDownClass(cls):
        for key, value in cls.saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value
        sys.modules.pop("qubes_information.backend.qubesadmin_backend", None)

    def setUp(self):
        sys_net = FakeVM("sys-firewall", "AppVM")
        FakeApp.domains = {
            "work": FakeVM(
                "work", "AppVM", "Running",
                props={"label": Label(), "netvm": sys_net, "memory": 400,
                       "include_in_backups": True, "ip6": None, "start_time": "1000.5"},
                features={"service.cups": "1", "gui": "1"},
                tags=("b", "a"),
                volumes={"private": FakeVolume(pool="vm-pool", vid="vm-work-private",
                                               size="2147483648", usage=1073741824, rw=True,
                                               snap_on_start=False, save_on_stop=True,
                                               source=None, revisions_to_keep=2,
                                               revisions=["111-back"], ephemeral=False)},
                rules=("action=accept",)),
            "dom0": FakeVM("dom0", "AdminVM", "Running", props={"label": "black"}),
            "locked": FakeVM("locked", "AppVM", props={"label": "red"},
                             refuse=("tags", "firewall")),
        }
        self.backend = self.module.QubesAdminBackend()

    def test_reads_every_part(self):
        info = self.backend.get_info("work")
        props = {p.name: p for p in info.props}
        self.assertEqual(props["label"].value, "blue")
        self.assertEqual(props["netvm"].value, "sys-firewall")
        self.assertTrue(props["netvm"].is_default)
        self.assertEqual(props["memory"].value, "400")
        self.assertEqual(props["ip6"].value, "")
        self.assertIsNone(props["template_unset"].value)
        self.assertEqual(info.features, (("gui", "1"), ("service.cups", "1")))
        self.assertEqual(info.tags, ("a", "b"))
        volume = info.volumes[0]
        self.assertEqual((volume.name, volume.size, volume.usage), ("private", 2 * 1024 ** 3,
                                                                     1024 ** 3))
        self.assertEqual(volume.revisions, ("111-back",))
        self.assertEqual(info.firewall, ("action=accept",))
        self.assertEqual(info.stats.memory_kb, 2097152)
        self.assertEqual(info.stats.cpu_seconds, 90.0)
        self.assertEqual(info.stats.start_time, 1000.5)
        self.assertEqual(info.errors, ())

    def test_dom0_skips_storage_and_firewall(self):
        info = self.backend.get_info("dom0")
        self.assertIsNone(info.firewall)
        self.assertEqual(info.volumes, ())

    def test_a_refused_part_is_named_not_fatal(self):
        info = self.backend.get_info("locked")
        self.assertEqual(dict(info.errors).keys(), {model.TAGS, model.FIREWALL})
        self.assertEqual(info.tags, ())
        self.assertIsNone(info.firewall)
        self.assertIsNone(info.stats)

    def test_unknown_qube(self):
        with self.assertRaises(BackendError):
            self.backend.get_info("nope")

    def test_stats(self):
        self.assertEqual(self.backend.get_stats("work").max_memory_kb, 4096000)
        self.assertIsNone(self.backend.get_stats("locked"))


if __name__ == "__main__":
    unittest.main()
