# tests/upstream — vendored upstream source, read-only

`qubesadmin_firewall.py` is a byte-identical copy of `qubesadmin/firewall.py`
from QubesOS/qubes-core-admin-client (`main`, fetched 2026-10-15), taken from
qvm-firewall-ui's `tests/upstream/`. LGPL-2.1+, see its header.

sha256 `9e67c1f4cdebad79307df53724e558566927dfdda42e9eba17d0e028bfae170d`,
asserted by `tests/test_backends.py`. Do not edit it; re-fetch and re-pin.

It loads standalone (it imports `qubesadmin.vm` only under `TYPE_CHECKING`), so
the tests run the qubesadmin backend against qubesadmin's real `Rule` and
`Firewall` without Qubes installed.
