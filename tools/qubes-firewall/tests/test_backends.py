"""Backends: the demo data, the guarded save, and qubesadmin's own Rule/Firewall."""

import hashlib
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qubes_firewall import model  # noqa: E402
from qubes_firewall.backend import BackendError, StaleRulesError, create_backend  # noqa: E402
from qubes_firewall.backend.base import take_snapshot  # noqa: E402
from qubes_firewall.backend.mock import MockBackend  # noqa: E402
from qubes_firewall.rules import RuleList  # noqa: E402

UPSTREAM = ROOT / "tests" / "upstream" / "qubesadmin_firewall.py"
UPSTREAM_SHA256 = "9e67c1f4cdebad79307df53724e558566927dfdda42e9eba17d0e028bfae170d"


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

    def test_every_dashboard_demo_qube_has_a_firewall(self):
        names = ("dom0", "work", "office", "vault", "sys-net", "debian-13", "disp1234")
        for name in names:
            with self.subTest(name=name):
                self.backend.get_firewall(name)

    def test_dom0(self):
        fw = self.backend.get_firewall("dom0")
        self.assertTrue(fw.is_admin)
        with self.assertRaises(BackendError):
            self.backend.save_rules("dom0", RuleList(), RuleList())

    def test_unknown_qube(self):
        with self.assertRaises(BackendError):
            self.backend.get_firewall("nope")

    def test_netvm_addresses_only_when_networked(self):
        self.assertTrue(self.backend.get_firewall("work").netvm_addresses)
        self.assertEqual(self.backend.get_firewall("vault").netvm_addresses, ())

    def test_save_snapshots_then_writes(self):
        fw = self.backend.get_firewall("office")
        path = self.backend.save_rules("office", fw.rules, RuleList(["action=drop"]))
        self.assertEqual(Path(path).read_text(encoding="ascii"), fw.rules.to_api_text())
        self.assertEqual(self.backend.get_firewall("office").rules, RuleList(["action=drop"]))

    def test_save_refuses_when_changed_elsewhere(self):
        fw = self.backend.get_firewall("office")
        self.backend.change_elsewhere("office", ["action=accept proto=tcp"])
        with self.assertRaises(StaleRulesError):
            self.backend.save_rules("office", fw.rules, RuleList(["action=drop"]))
        self.assertEqual(self.backend.get_firewall("office").rules,
                         RuleList(["action=accept proto=tcp"]))
        self.assertEqual(os.listdir(self.backend.snapshot_directory), [])

    def test_demo_snapshots_stay_out_of_the_state_directory(self):
        self.assertTrue(self.backend.snapshot_directory.startswith(tempfile.gettempdir()))


class SnapshotTest(unittest.TestCase):
    def test_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            rules = RuleList(["action=accept"])
            first = take_snapshot("work", rules, directory, timestamp=0)
            second = take_snapshot("work", rules, directory, timestamp=0)
            self.assertNotEqual(first, second)
            self.assertEqual(Path(first).name, "work.19700101T000000Z.rules")
            self.assertEqual(oct(os.stat(first).st_mode & 0o777), "0o600")


# -- qubesadmin backend against upstream's Firewall ----------------------------
def _load_upstream():
    spec = importlib.util.spec_from_file_location("qubesadmin.firewall", UPSTREAM)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _QubesException(Exception):
    pass


class _FakeVM:
    """Answers admin.vm.firewall.Get/Set like qubesd, for upstream's Firewall."""

    def __init__(self, firewall_module, name, klass="AppVM", netvm="sys-firewall",
                 lines=("action=accept",)):
        self.name = name
        self.klass = klass
        self.netvm = netvm
        self.provides_network = False
        self.payload = "".join(f"{line}\n" for line in lines).encode("ascii")
        self.sets = []
        self.firewall = firewall_module.Firewall(self)

    def qubesd_call(self, _dest, method, payload=None):
        if method == "admin.vm.firewall.Get":
            return self.payload
        if method == "admin.vm.firewall.Set":
            self.sets.append(payload)
            self.payload = payload
            return b""
        raise AssertionError(method)


class QubesAdminBackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = _load_upstream()
        package = types.ModuleType("qubesadmin")
        package.exc = types.SimpleNamespace(QubesException=_QubesException)
        package.firewall = cls.upstream
        package.Qubes = lambda: None
        cls._saved = {k: sys.modules.get(k) for k in
                      ("qubesadmin", "qubesadmin.exc", "qubesadmin.firewall")}
        sys.modules.update({"qubesadmin": package, "qubesadmin.exc": package.exc,
                            "qubesadmin.firewall": cls.upstream})
        sys.modules.pop("qubes_firewall.backend.qubesadmin_backend", None)
        # pylint: disable=import-outside-toplevel
        from qubes_firewall.backend import qubesadmin_backend
        cls.module = qubesadmin_backend

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value
        sys.modules.pop("qubes_firewall.backend.qubesadmin_backend", None)

    def backend(self, *vms):
        app = types.SimpleNamespace(domains={vm.name: vm for vm in vms})
        backend = self.module.QubesAdminBackend(app)
        backend.snapshot_directory = tempfile.mkdtemp()
        return backend

    def test_upstream_copy_is_pinned(self):
        self.assertEqual(hashlib.sha256(UPSTREAM.read_bytes()).hexdigest(), UPSTREAM_SHA256)

    def test_reads_rules_and_netvm(self):
        vm = _FakeVM(self.upstream, "work",
                     lines=("action=accept specialtarget=dns", "action=drop"))
        fw = self.backend(vm).get_firewall("work")
        self.assertEqual([r.rule for r in fw.rules],
                         ["action=accept specialtarget=dns", "action=drop"])
        self.assertEqual(fw.netvm, "sys-firewall")
        self.assertEqual(fw.netvm_addresses, self.module.DNS_ADDRESSES)

    def test_no_netvm(self):
        vm = _FakeVM(self.upstream, "vault", netvm=None)
        self.assertEqual(self.backend(vm).get_firewall("vault").netvm_addresses, ())

    def test_dom0_is_not_read(self):
        vm = _FakeVM(self.upstream, "dom0", klass=model.ADMIN_VM)
        vm.qubesd_call = None  # would fail if called
        self.assertTrue(self.backend(vm).get_firewall("dom0").is_admin)

    def test_payload_is_what_qvm_firewall_would_send(self):
        lines = [
            "action=accept specialtarget=dns",
            "action=accept proto=icmp icmptype=8",
            "action=accept proto=tcp dsthost=mail.example.com dstports=993-993 comment=IMAP mail",
            "action=drop proto=udp dst4=10.0.0.0/8 dstports=1000-2000",
            "action=accept dst6=2001:db8::/32 expire=1900000000",
            "action=drop",
        ]
        vm = _FakeVM(self.upstream, "work")
        backend = self.backend(vm)
        fw = backend.get_firewall("work")
        backend.save_rules("work", fw.rules, RuleList(lines))
        self.assertEqual(vm.sets, ["".join(f"{line}\n" for line in lines).encode("ascii")])
        self.assertEqual(vm.sets[0], RuleList(lines).to_api_text().encode("ascii"))

    def test_stale_write_sends_nothing(self):
        vm = _FakeVM(self.upstream, "work")
        backend = self.backend(vm)
        fw = backend.get_firewall("work")
        vm.payload = b"action=drop\n"
        with self.assertRaises(StaleRulesError):
            backend.save_rules("work", fw.rules, RuleList(["action=accept proto=tcp"]))
        self.assertEqual(vm.sets, [])

    def test_refused_write_names_the_snapshot(self):
        vm = _FakeVM(self.upstream, "work")
        backend = self.backend(vm)
        fw = backend.get_firewall("work")

        def refuse(_dest, method, payload=None):
            if method == "admin.vm.firewall.Set":
                raise _QubesException("denied")
            return b"action=accept\n"
        vm.qubesd_call = refuse
        with self.assertRaises(BackendError) as caught:
            backend.save_rules("work", fw.rules, RuleList(["action=drop"]))
        self.assertIn(backend.snapshot_directory, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
