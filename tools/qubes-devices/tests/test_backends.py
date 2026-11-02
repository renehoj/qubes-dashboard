"""Backends: the demo data, the guarded save, and the qubesadmin calls."""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_devices import model  # noqa: E402
from qubes_devices.backend import BackendError, StaleDevicesError, create_backend  # noqa: E402
from qubes_devices.backend.base import take_snapshot  # noqa: E402
from qubes_devices.backend.mock import MockBackend  # noqa: E402
from qubes_devices.model import Assignment  # noqa: E402

GPU = "00_01.0-00_00.0"
GPU_AUDIO = "00_01.0-00_00.1"
FRESCO = "00_1c.1-00_00.0"


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

    def test_every_dashboard_demo_qube_has_devices(self):
        for name in ("dom0", "work", "games", "sys-net", "debian-13", "disp1234"):
            with self.subTest(name=name):
                qd = self.backend.get_devices(name)
                self.assertEqual(len(qd.devices), 15)

    def test_games(self):
        qd = self.backend.get_devices("games")
        self.assertTrue(qd.editable)
        self.assertEqual({a.port_id for a in qd.assignments}, {GPU, GPU_AUDIO, FRESCO})
        self.assertEqual(qd.device("00_14.0").attached_to, "sys-usb")
        # The qube's own devices are not "used by" it.
        self.assertEqual(model.used_by_text(qd.device(GPU)), "")

    def test_running_qube_is_read_only(self):
        self.assertFalse(self.backend.get_devices("sys-net").editable)

    def test_set_states(self):
        self.backend.set_states({"games": model.RUNNING, "nope": model.RUNNING})
        qd = self.backend.get_devices("games")
        self.assertFalse(qd.editable)
        self.assertEqual(self.backend.get_devices("sys-net").device(GPU).attached_to, "games")

    def test_unknown_qube(self):
        with self.assertRaises(BackendError):
            self.backend.get_devices("nope")


class GuardedSaveTest(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend(delay=0)
        self.qd = self.backend.get_devices("games")

    def save(self, session):
        return self.backend.save_assignments("games", self.qd.assignments,
                                             session.assignments())

    def test_save(self):
        session = model.EditSession(self.qd)
        session.toggle(FRESCO)
        session.toggle("00_14.2")
        session.toggle_strict_reset(GPU)
        path = self.save(session)
        after = self.backend.get_devices("games")
        self.assertEqual({a.port_id for a in after.assignments}, {GPU, GPU_AUDIO, "00_14.2"})
        self.assertTrue(after.device(GPU) and
                        {a.port_id: a for a in after.assignments}[GPU].no_strict_reset)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), model.snapshot_lines("games", sorted(
                self.qd.assignments, key=lambda a: a.port_id)))

    def test_refuses_a_running_qube(self):
        self.backend.set_states({"games": model.RUNNING})
        session = model.EditSession(self.qd)
        session.toggle(FRESCO)
        with self.assertRaisesRegex(BackendError, "running"):
            self.save(session)
        self.assertEqual(len(self.backend._read_assignments("games")), 3)  # pylint: disable=protected-access

    def test_refuses_stale(self):
        self.backend.change_elsewhere("games", [Assignment(GPU)])
        session = model.EditSession(self.qd)
        session.toggle(FRESCO)
        with self.assertRaises(StaleDevicesError):
            self.save(session)
        self.assertEqual(self.backend._read_assignments("games"), (Assignment(GPU),))  # pylint: disable=protected-access

    def test_failed_step_names_the_snapshot(self):
        session = model.EditSession(self.qd)
        session.toggle(FRESCO)

        def refuse(_name, _assignment):
            raise BackendError("qubesd said no.")

        self.backend._unassign = refuse  # pylint: disable=protected-access
        with self.assertRaisesRegex(BackendError, "said no.*old assignments are in"):
            self.save(session)

    def test_snapshot_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            first = take_snapshot("games", (), directory, timestamp=0)
            second = take_snapshot("games", (), directory, timestamp=0)
            self.assertNotEqual(first, second)
            self.assertTrue(os.path.exists(first) and os.path.exists(second))


# -- qubesadmin, faked at the level of its device API ---------------------------
class QubesException(Exception):
    pass


class FakeDeviceAssignment:
    def __init__(self, port_id, device_id="*", options=None):
        self.port_id = port_id
        self.device_id = device_id
        self.options = options or {}

    @classmethod
    def new(cls, backend_domain, port_id, devclass, device_id=None, *,
            frontend_domain=None, options=None, mode="manual"):
        assert backend_domain.name == "dom0" and devclass == "pci" and mode == "required"
        del frontend_domain
        return cls(port_id, device_id or "*", options)


class FakeCategory:
    def __init__(self, name):
        self.name = name


class FakeDevice:
    def __init__(self, port_id, device_id, description, category, attachment=None):
        self.port_id = port_id
        self.device_id = device_id
        self.description = description
        self.interfaces = [types.SimpleNamespace(category=FakeCategory(category))]
        self.attachment = attachment


class FakeCollection:
    def __init__(self, exposed=(), assigned=()):
        self.exposed = list(exposed)
        self.assigned = list(assigned)
        self.calls = []

    def get_exposed_devices(self):
        return iter(self.exposed)

    def get_assigned_devices(self, required_only=False):
        assert required_only
        return iter(self.assigned)

    def clear_cache(self):
        self.calls.append("clear")

    def assign(self, assignment):
        self.calls.append(("assign", assignment.port_id, assignment.device_id,
                           dict(assignment.options)))
        self.assigned.append(assignment)

    def unassign(self, assignment):
        self.calls.append(("unassign", assignment.port_id))
        self.assigned.remove(assignment)


class FakeVM:
    def __init__(self, name, klass="AppVM", state="Halted", virt_mode="hvm", maxmem=0,
                 collection=None, denied=False):
        self.name = name
        self.klass = klass
        self.state = state
        self.virt_mode = virt_mode
        self.maxmem = maxmem
        self.collection = collection or FakeCollection()
        self.denied = denied

    @property
    def devices(self):
        if self.denied:
            raise QubesException("denied")
        return {"pci": self.collection}

    def get_power_state(self):
        return self.state


class FakeDomains(dict):
    def __iter__(self):
        return iter(self.values())


def install_fake_qubesadmin():
    qubesadmin = types.ModuleType("qubesadmin")
    exc = types.ModuleType("qubesadmin.exc")
    exc.QubesException = QubesException
    protocol = types.ModuleType("qubesadmin.device_protocol")
    protocol.DeviceAssignment = FakeDeviceAssignment
    qubesadmin.exc = exc
    qubesadmin.device_protocol = protocol
    qubesadmin.Qubes = lambda: None
    sys.modules.update({"qubesadmin": qubesadmin, "qubesadmin.exc": exc,
                        "qubesadmin.device_protocol": protocol})


class QubesAdminBackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = {k: v for k, v in sys.modules.items() if k.startswith("qubesadmin")}
        install_fake_qubesadmin()
        sys.modules.pop("qubes_devices.backend.qubesadmin_backend", None)
        from qubes_devices.backend import qubesadmin_backend  # pylint: disable=import-outside-toplevel
        cls.module = qubesadmin_backend

    @classmethod
    def tearDownClass(cls):
        for key in [k for k in sys.modules if k.startswith("qubesadmin")]:
            del sys.modules[key]
        sys.modules.update(cls.saved)
        sys.modules.pop("qubes_devices.backend.qubesadmin_backend", None)

    def setUp(self):
        sys_usb = FakeVM("sys-usb", state="Running",
                         collection=FakeCollection(assigned=[FakeDeviceAssignment("00_14.0")]))
        dom0 = FakeVM("dom0", klass="AdminVM", state="Running", collection=FakeCollection(exposed=[
            FakeDevice("00_01.0", "8086:a70d::p060400", "PCI_Bridge: Intel bridge", "PCI_Bridge"),
            FakeDevice(GPU, "10de:2803::p030000", "Display: NVIDIA AD106", "Display"),
            FakeDevice(GPU_AUDIO, "10de:22bd::p040300", "Audio: NVIDIA AD106M", "Audio"),
            FakeDevice("00_14.0", "8086:7a60::p0c0330", "PCI_USB: Intel XHCI", "PCI_USB",
                       attachment=sys_usb),
        ]))
        self.games = FakeVM("games", state="Halted", collection=FakeCollection(assigned=[
            FakeDeviceAssignment(GPU, "10de:2803::p030000", {"no-strict-reset": "True"}),
            FakeDeviceAssignment("00_03.0", "*"),
        ]))
        locked = FakeVM("vault", denied=True)
        app = types.SimpleNamespace(domains=FakeDomains(
            dom0=dom0, games=self.games, **{"sys-usb": sys_usb}, vault=locked))
        self.backend = self.module.QubesAdminBackend(app)

    def test_read(self):
        qd = self.backend.get_devices("games")
        self.assertEqual(qd.assignments, (
            Assignment(GPU, "10de:2803::p030000", no_strict_reset=True),
            Assignment("00_03.0", "*"),
        ))
        ports = [d.port_id for d in qd.devices]
        self.assertNotIn("00_01.0", ports)  # the bridge
        self.assertEqual(ports, [GPU, GPU_AUDIO, "00_03.0", "00_14.0"])
        self.assertFalse(qd.device("00_03.0").present)
        usb = qd.device("00_14.0")
        self.assertEqual((usb.attached_to, usb.assigned_to), ("sys-usb", ("sys-usb",)))
        self.assertTrue(qd.editable)
        self.assertIn("clear", self.games.collection.calls)

    def test_save(self):
        qd = self.backend.get_devices("games")
        session = model.EditSession(qd)
        session.toggle("00_03.0")
        session.toggle(GPU_AUDIO)
        session.toggle_strict_reset(GPU)
        old_dir = os.environ.get("XDG_STATE_HOME")
        with tempfile.TemporaryDirectory() as state:
            os.environ["XDG_STATE_HOME"] = state
            try:
                path = self.backend.save_assignments("games", qd.assignments,
                                                     session.assignments())
            finally:
                if old_dir is None:
                    del os.environ["XDG_STATE_HOME"]
                else:
                    os.environ["XDG_STATE_HOME"] = old_dir
            self.assertTrue(path.startswith(state))
        calls = [c for c in self.games.collection.calls if c != "clear"]
        self.assertEqual(calls, [
            ("unassign", GPU),
            ("unassign", "00_03.0"),
            ("assign", GPU, "10de:2803::p030000", {}),
            ("assign", GPU_AUDIO, "10de:22bd::p040300", {}),
        ])

    def test_running_qube_is_not_written(self):
        self.games.state = "Running"
        qd = self.backend.get_devices("games")
        self.assertFalse(qd.editable)
        with self.assertRaises(BackendError):
            self.backend.save_assignments("games", qd.assignments, ())
        self.assertEqual([c for c in self.games.collection.calls if c != "clear"], [])

    def test_unknown_qube(self):
        with self.assertRaises(BackendError):
            self.backend.get_devices("nope")


if __name__ == "__main__":
    unittest.main()
