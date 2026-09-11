#!/usr/bin/env python3
"""
Raging Thunder 2 (iOS, 2010) compatibility patcher.

Patches a decrypted Raging Thunder 2 IPA so it launches under LiveContainer +
LiveExec32 on modern iOS. Optional flags add a money cheat and re-enable the
developer menu that Polarbit left in the binary.

The game IPA is not distributed with this tool. Supply your own copy.
"""

import argparse
import hashlib
import plistlib
import shutil
import struct
import sys
import zipfile
from pathlib import Path

# The binary is a thin armv6 Mach-O whose __TEXT segment is based at 0x1000,
# so every virtual address maps to (vaddr - 0x1000) in the file.
VM_BASE = 0x1000

EXPECTED_SHA256 = "6830fe4ac021d3ad7c1e49da04443ab72ea6a9645a623fda352fc5b5db9204e9"

# Free space: 2160 bytes of zero padding between the load commands and __text.
SEL_STRING_ADDR = 0x1B20
NIB_THUNK_ADDR = 0x1B10
CASH_THUNK_ADDR = 0x1B80

OBJC_MSGSEND_STUB = 0xEFB7C
GETCSTRING_SELREF = 0x105DE8
GETCSTRING_STRING = 0xF55E4
GETCSTRING_CALLS = (0xEEE9E, 0xEEED4)

GETCASH = 0x96FC4
CASH_AMOUNT = 1_000_000

DEV_PAGE_NAME_LITERAL = 0x7E450
CREDITS_PAGE_NAME_LITERAL = 0x81120
STR_DEVELOPER = 0xF2228
STR_CREDITS = 0xF21C4

REPLACEMENT_SELECTOR = b"getFileSystemRepresentation:maxLength:\x00"


class PatchError(Exception):
    pass


class Binary:
    def __init__(self, data: bytes):
        self.d = bytearray(data)

    def off(self, vaddr: int) -> int:
        return vaddr - VM_BASE

    def read(self, vaddr: int, n: int) -> bytes:
        o = self.off(vaddr)
        return bytes(self.d[o:o + n])

    def write(self, vaddr: int, payload: bytes, expect: bytes = None) -> None:
        o = self.off(vaddr)
        if expect is not None and bytes(self.d[o:o + len(expect)]) != expect:
            raise PatchError(
                f"unexpected bytes at 0x{vaddr:x}: "
                f"found {bytes(self.d[o:o + len(expect)]).hex()}, expected {expect.hex()}"
            )
        self.d[o:o + len(payload)] = payload

    def u32(self, vaddr: int) -> int:
        return struct.unpack_from("<I", self.d, self.off(vaddr))[0]

    def put_u32(self, vaddr: int, value: int, expect: int = None) -> None:
        if expect is not None and self.u32(vaddr) != expect:
            raise PatchError(
                f"unexpected word at 0x{vaddr:x}: "
                f"found 0x{self.u32(vaddr):x}, expected 0x{expect:x}"
            )
        struct.pack_into("<I", self.d, self.off(vaddr), value)


def thumb_branch(pc: int, target: int, exchange: bool) -> bytes:
    """Encode a Thumb BL (stays Thumb) or BLX (switches to ARM)."""
    # BLX(imm) is encoded relative to Align(PC + 4, 4); BL is not aligned.
    base = ((pc + 4) & ~3) if exchange else (pc + 4)
    offset = target - base
    if not -(1 << 22) <= offset < (1 << 22):
        raise PatchError(f"branch out of range: 0x{pc:x} -> 0x{target:x}")
    hi = 0xF000 | ((offset >> 12) & 0x7FF)
    lo = (0xE800 if exchange else 0xF800) | ((offset >> 1) & 0x7FF)
    return struct.pack("<2H", hi, lo)


def arm_branch(pc: int, target: int) -> int:
    """Encode an unconditional ARM B."""
    offset = target - (pc + 8)
    if offset % 4:
        raise PatchError("ARM branch target must be word aligned")
    return 0xEA000000 | ((offset >> 2) & 0xFFFFFF)


def fix_missing_nib(info: dict) -> bool:
    """
    The bundle ships no MainWindow.nib, but Info.plist still names one, so modern
    UIKit throws on launch. The delegate class is passed to UIApplicationMain in
    code, so the key is simply vestigial.
    """
    if "NSMainNibFile" not in info:
        return False
    del info["NSMainNibFile"]
    return True


def fix_getcstring(b: Binary) -> None:
    """
    -[NSString getCString:] was removed from Foundation. Redirect the selector to
    getFileSystemRepresentation:maxLength:, which survives and has the same
    meaning here (the call sites build a filesystem path). That selector needs a
    length argument in r3 and neither call site has room, so a small thunk
    supplies it.
    """
    b.write(SEL_STRING_ADDR, REPLACEMENT_SELECTOR,
            expect=b"\x00" * len(REPLACEMENT_SELECTOR))

    thunk = b"".join([
        struct.pack("<H", 0xB500),                                   # push {lr}
        struct.pack("<H", 0x23FF),                                   # movs r3, #255
        thumb_branch(NIB_THUNK_ADDR + 4, OBJC_MSGSEND_STUB, True),   # blx objc_msgSend
        struct.pack("<H", 0xBD00),                                   # pop  {pc}
    ])
    b.write(NIB_THUNK_ADDR, thunk, expect=b"\x00" * len(thunk))

    b.put_u32(GETCSTRING_SELREF, SEL_STRING_ADDR, expect=GETCSTRING_STRING)

    for site in GETCSTRING_CALLS:
        original = thumb_branch(site, OBJC_MSGSEND_STUB, True)
        b.write(site, thumb_branch(site, NIB_THUNK_ADDR, False), expect=original)


def cheat_money(b: Binary) -> None:
    """Make CProfile::GetCash() return a flat 1,000,000."""
    thunk = struct.pack(
        "<4I",
        0xE59F0004,   # ldr r0, [pc, #4]
        0xE3A01000,   # mov r1, #0        (high word of the long long)
        0xE12FFF1E,   # bx  lr
        CASH_AMOUNT,
    )
    b.write(CASH_THUNK_ADDR, thunk, expect=b"\x00" * len(thunk))
    b.put_u32(GETCASH, arm_branch(GETCASH, CASH_THUNK_ADDR), expect=0xE2800058)


def cheat_devmenu(b: Binary) -> None:
    """
    CreateDeveloperPages() runs on every launch, but nothing navigates to the
    page it builds. Pages are looked up by name, so swapping the developer and
    credits name pointers puts the cheat menu behind the Credits button.
    """
    b.put_u32(DEV_PAGE_NAME_LITERAL, STR_CREDITS, expect=STR_DEVELOPER)
    b.put_u32(CREDITS_PAGE_NAME_LITERAL, STR_DEVELOPER, expect=STR_CREDITS)


def locate_app(root: Path) -> Path:
    apps = list((root / "Payload").glob("*.app"))
    if not apps:
        raise PatchError("no .app bundle found under Payload/")
    return apps[0]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Patch Raging Thunder 2 for LiveContainer + LiveExec32.")
    ap.add_argument("input", type=Path, help="original .ipa")
    ap.add_argument("-o", "--output", type=Path, help="patched .ipa")
    ap.add_argument("--money", action="store_true",
                    help="report a permanent balance of 1,000,000")
    ap.add_argument("--devmenu", action="store_true",
                    help="put the developer cheat menu behind the Credits button")
    ap.add_argument("--cheats", action="store_true", help="same as --money --devmenu")
    ap.add_argument("--force", action="store_true",
                    help="continue even if the binary hash is unrecognised")
    args = ap.parse_args()

    if args.cheats:
        args.money = args.devmenu = True
    out = args.output or args.input.with_name(args.input.stem + "-patched.ipa")

    work = Path(str(out) + ".tmp")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    try:
        with zipfile.ZipFile(args.input) as z:
            names = z.namelist()
            z.extractall(work)

        app = locate_app(work)
        info_path = app / "Info.plist"
        info = plistlib.loads(info_path.read_bytes())
        binary_path = app / info["CFBundleExecutable"]
        raw = binary_path.read_bytes()

        digest = hashlib.sha256(raw).hexdigest()
        if digest != EXPECTED_SHA256:
            print(f"warning: unrecognised binary (sha256 {digest})")
            if not args.force:
                print("refusing to patch; pass --force to continue anyway")
                return 2
        else:
            print("input verified (Raging Thunder 2 v1.0.1, armv6)")

        if fix_missing_nib(info):
            info_path.write_bytes(plistlib.dumps(info, fmt=plistlib.FMT_BINARY))
            print("  removed NSMainNibFile (fixes the launch crash)")

        b = Binary(raw)
        fix_getcstring(b)
        print("  redirected getCString: (fixes the startup crash)")
        if args.money:
            cheat_money(b)
            print(f"  GetCash() now returns {CASH_AMOUNT:,}")
        if args.devmenu:
            cheat_devmenu(b)
            print("  developer menu moved onto the Credits button")
        binary_path.write_bytes(bytes(b.d))

        if out.exists():
            out.unlink()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for name in names:
                src = work / name
                if src.is_file():
                    z.write(src, name)
        print(f"wrote {out}")
        return 0

    except (PatchError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
