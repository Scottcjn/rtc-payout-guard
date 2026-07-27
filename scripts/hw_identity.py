#!/usr/bin/env python3
"""Read a firmware-provided hardware identity, across the whole Elyan fleet.

Why this exists
---------------
RustChain pays vintage hardware more than modern silicon, and the pot is fixed,
so every extra identity dilutes every honest miner. The protocol currently uses
"is not a VM" as a proxy for "is one scarce physical machine". Those are not the
same claim, and the second is the one the economics actually rest on. A perfect
VM detector still would not stop one bare-metal host registering forty wallets.

The binding has to come from something the firmware knows and the operating
system cannot mint: a serial burned in at manufacture.

Where that lives, per platform
------------------------------
- **PowerPC / POWER, Linux**: Open Firmware exposes a flat device tree at
  ``/proc/device-tree``. ``system-id`` is the machine serial, ``model`` the
  machine type. Read-only to userspace; the kernel publishes what firmware
  handed it. Verified on an IBM S824: ``system-id = IBM,0221AAE9W``.
- **PowerPC, Darwin (OS X on G3/G4/G5)**: the same Open Firmware data surfaces
  through IOKit as ``IOPlatformSerialNumber`` and ``IOPlatformUUID``.
- **x86, Linux**: SMBIOS/DMI, which every BIOS since the Phoenix era populates.
  ``board_serial`` and ``product_uuid`` are the per-board values. Note these are
  mode 0400: **root only**. The world-readable ``sys_vendor`` and
  ``product_name`` are useful for spotting a hypervisor but are not identity.
- **x86, DOS / pre-SMBIOS**: no serial exists to read. Those machines cannot be
  bound this way and must be handled by a different assurance route rather than
  pretended into one.

What this does and does not prove
---------------------------------
It does not prove anything on its own. A miner speaks to the node over HTTP and
can send whatever bytes it likes, so a fabricated serial is always possible.
What a real serial buys is **scarcity with structure**: Apple and IBM serials
encode plant, year and model, so a claim can be checked for internal consistency
and against the model it claims to be, and one serial can be pinned to one
identity on first use. Inventing forty *plausible, mutually consistent, never
seen before* serials is a different problem from inventing forty wallet IDs.

That is the honest ceiling. This raises the cost of Sybil multiplication; it
does not make it impossible, and nothing reachable over plain HTTP would.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys

DT = "/proc/device-tree"
DMI = "/sys/class/dmi/id"


def _read_dt(name: str) -> str | None:
    """Open Firmware properties are NUL-terminated byte strings."""
    p = os.path.join(DT, name)
    try:
        with open(p, "rb") as fh:
            return fh.read().decode("utf-8", "replace").strip("\x00").strip() or None
    except OSError:
        return None


def _read_dmi(name: str) -> tuple[str | None, str]:
    """Returns (value, status). Distinguishes 'absent' from 'needs root'."""
    p = os.path.join(DMI, name)
    if not os.path.exists(p):
        return None, "absent"
    try:
        with open(p) as fh:
            return (fh.read().strip() or None), "ok"
    except PermissionError:
        return None, "permission_denied"
    except OSError as exc:
        return None, f"error:{exc.__class__.__name__}"


def _ioreg(key: str) -> str | None:
    """Darwin: pull a platform property out of IOKit."""
    try:
        out = subprocess.run(["ioreg", "-l"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(rf'"{re.escape(key)}"\s*=\s*"([^"]+)"', out)
    return m.group(1) if m else None


def collect() -> dict:
    sysname = platform.system()
    machine = platform.machine()
    out: dict = {
        "platform": sysname,
        "machine": machine,
        "source": None,
        "serial": None,
        "model": None,
        "uuid": None,
        "hypervisor_hint": None,
        "notes": [],
    }

    # --- Open Firmware, Linux on PowerPC/POWER -------------------------------
    if os.path.isdir(DT):
        serial = _read_dt("system-id") or _read_dt("serial-number")
        model = _read_dt("model")
        if serial or model:
            out.update(source="openfirmware-devicetree", serial=serial, model=model)
            part = _read_dt("ibm,partition-name")
            if part:
                out["notes"].append(f"LPAR partition-name={part}")
                out["hypervisor_hint"] = "lpar"   # PowerVM: real silicon, partitioned
            return out

    # --- Darwin: IOKit carries the same Open Firmware identity ---------------
    if sysname == "Darwin":
        serial = _ioreg("IOPlatformSerialNumber")
        uuid = _ioreg("IOPlatformUUID")
        model = None
        try:
            model = subprocess.run(["sysctl", "-n", "hw.model"],
                                   capture_output=True, text=True, timeout=10).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            pass
        if serial or model:
            out.update(source="iokit-platform", serial=serial, model=model, uuid=uuid)
            return out

    # --- SMBIOS / DMI, x86 back to the Phoenix BIOS era ----------------------
    if os.path.isdir(DMI):
        board_serial, s1 = _read_dmi("board_serial")
        product_serial, s2 = _read_dmi("product_serial")
        product_uuid, s3 = _read_dmi("product_uuid")
        vendor, _ = _read_dmi("sys_vendor")
        product, _ = _read_dmi("product_name")
        board, _ = _read_dmi("board_name")

        out.update(source="smbios-dmi",
                   serial=board_serial or product_serial,
                   model=" ".join(x for x in (vendor, product) if x) or board,
                   uuid=product_uuid)

        if s1 == "permission_denied" or s3 == "permission_denied":
            out["notes"].append(
                "board_serial/product_uuid are mode 0400 (root only). Without root "
                "the miner can see the vendor strings but not the identity.")
        # SMBIOS placeholders are extremely common on consumer boards.
        junk = {"", "none", "to be filled by o.e.m.", "default string", "system serial number",
                "not specified", "not applicable", "0", "123456789", "unknown"}
        if out["serial"] and out["serial"].strip().lower() in junk:
            out["notes"].append(f"serial is an OEM placeholder ({out['serial']!r}), not an identity")
            out["serial"] = None

        blob = " ".join(filter(None, (vendor, product, board))).lower()
        for tag, name in (("qemu", "qemu"), ("kvm", "kvm"), ("vmware", "vmware"),
                          ("virtualbox", "virtualbox"), ("innotek", "virtualbox"),
                          ("xen", "xen"), ("microsoft corporation virtual", "hyper-v"),
                          ("bochs", "bochs"), ("parallels", "parallels")):
            if tag in blob:
                out["hypervisor_hint"] = name
                break
        return out

    out["notes"].append("no firmware identity source on this platform")
    return out


def main() -> int:
    info = collect()
    if "--json" in sys.argv:
        print(json.dumps(info, indent=2))
        return 0
    print(f"platform      : {info['platform']} / {info['machine']}")
    print(f"source        : {info['source']}")
    print(f"model         : {info['model']}")
    print(f"serial        : {info['serial']}")
    print(f"uuid          : {info['uuid']}")
    print(f"hypervisor    : {info['hypervisor_hint']}")
    for n in info["notes"]:
        print(f"  note: {n}")
    if not info["serial"]:
        print("\nNo usable serial. This machine cannot be bound by firmware identity;")
        print("it needs a different assurance route rather than a pretended one.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
