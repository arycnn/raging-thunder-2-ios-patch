# Raging Thunder 2 iOS Patch

Patches the 2010 iOS release of **Raging Thunder 2** (Polarbit) so it launches and runs on modern iOS through [LiveContainer](https://github.com/LiveContainer/LiveContainer) and [LiveExec32](https://github.com/LiveContainer/LiveExec32).

The retail build crashes twice before it ever reaches the menu on current iOS. This patcher fixes both crashes, and can optionally re-enable the developer cheat menu that Polarbit left inside the shipping binary.

**The game is not included here.** This repository contains only the patcher. Supply your own copy of the IPA.

## Requirements

| What | Why |
| --- | --- |
| [LiveContainer](https://github.com/LiveContainer/LiveContainer) | Runs the app inside its own container, without installing it normally. |
| [LiveExec32](https://github.com/LiveContainer/LiveExec32) | Raging Thunder 2 is a 32 bit **armv6** binary. iOS dropped 32 bit support in iOS 11, so the code has to be emulated. LiveExec32 is the translation layer that does this. |
| JIT enabled | LiveExec32 uses a dynamic recompiler and will not run without JIT. Use whichever method suits your device and iOS version, for example [StikDebug](https://github.com/StikDebug/StikDebug) or [Jitterbug](https://github.com/osy/Jitterbug). |
| Python 3.8 or newer | Runs the patcher. No third party packages needed. |
| A decrypted Raging Thunder 2 IPA | Version 1.0.1. The patcher verifies the binary before touching it. |

Expected input binary (`Payload/Raging.app/Raging`):

```
sha256  6830fe4ac021d3ad7c1e49da04443ab72ea6a9645a623fda352fc5b5db9204e9
```

## Usage

Compatibility fixes only:

```bash
python3 patch_rt2.py RagingThunder2.ipa -o RagingThunder2-patched.ipa
```

Compatibility fixes plus cheats:

```bash
python3 patch_rt2.py RagingThunder2.ipa -o RagingThunder2-patched.ipa --cheats
```

| Flag | Effect |
| --- | --- |
| `--money` | `CProfile::GetCash()` always reports 1,000,000. |
| `--devmenu` | Puts the developer cheat menu behind the Credits button. |
| `--cheats` | Both of the above. |
| `--force` | Patch even if the binary hash is not recognised. |

Then import the patched IPA into LiveContainer, enable JIT, and launch it.

## What it fixes

### 1. Launch crash: a nib that does not exist

```
Could not load NIB in bundle ... with name 'MainWindow'
```

`Info.plist` sets `NSMainNibFile` to `MainWindow`, but the bundle contains **no nib file at all**. The app never needed one: `main()` passes the delegate class directly.

```
movs r2, #0x0          ; principalClassName = nil
ldr  r3, [pc, #0x2c]
add  r3, pc            ; delegateClassName  = @"iFuseAppDelegate"
blx  _UIApplicationMain
```

The key is a leftover from the Xcode template. It was harmless in 2010 because UIKit only logged a warning when the main nib was missing. Modern UIKit throws instead. The patcher deletes the key.

### 2. Startup crash: a Foundation method Apple deleted

```
-[__NSCFString getCString:]: unrecognized selector
  at -[iFuseAppDelegate applicationDidFinishLaunching:]
```

`-[NSString getCString:]` no longer exists. The game calls it while assembling the path it hands to its virtual filesystem, which holds every asset in `Data.vfs`:

```
[path getCString:buf];
strlen(buf);
buf[len] = '/';
...
setenv(name, buf, 1);
fuse_thread(0);
```

The replacement is `getFileSystemRepresentation:maxLength:`, which Apple kept and which means exactly the same thing for a path. It takes an extra length argument in `r3`, and neither call site has spare bytes, so the patcher writes a small thunk into the 2160 bytes of zero padding that sit between the load commands and `__text`:

```
push {lr}
movs r3, #255          ; maxLength
blx  objc_msgSend
pop  {pc}
```

One `__objc_selrefs` entry is repointed at the new selector name, which converts both call sites at once.

## Cheats

`--money` redirects `CProfile::GetCash()`, a three instruction function, to return a flat 1,000,000. Everything that reads your balance (the HUD, `CanAfford`, the shop) gets the same answer, so the total never drops. `SpendCash` already refuses to go negative, so nothing breaks.

`--devmenu` exposes the menu Polarbit shipped but hid:

```
Test keyboard
Delete Savegame     -> menu::InvokeDeleteSave
Unlock Everything   -> menu::InvokeUnlockEverything
Give $100000        -> menu::InvokeGiveMoney
Reset Profile       -> menu::InvokeResetProfile
Quit
```

`CRT2Frontend::CreateDeveloperPages()` still runs on every launch, so the page is built every time. Only the button that navigated to it was removed, which left `CProfile::UnlockEverything()` in the binary with zero callers. Pages are looked up by name, so the patcher swaps two pointer words:

| Address | Before | After |
| --- | --- | --- |
| `0x7e450` | `"developer"` | `"credits"` |
| `0x81120` | `"credits"` | `"developer"` |

The cheat menu now answers to `credits`, and the real credits screen is parked under `developer` where nothing navigates. Names stay unique, so load order does not matter. The only thing lost is the credits screen, which is a scrolling list of staff names.

Note that with `--money` active, "Give $100000" looks like it does nothing, because the balance is pinned regardless. That is expected.

## Known issues

**Track geometry disappears while driving.** Sections of road vanish and you see the skybox through the gap, while cars, barriers, props and the HUD keep drawing. The engine culls the static world with `CSGPortalCuller` and `CSGGrid2Culler`, and those areas are not being marked visible. Ruled out so far: shader compilation (the track and the car bodies use structurally identical shaders, and the cars are fine), screen aspect ratio, and render resolution. The remaining suspects are floating point behaviour inside the culler differing under emulation, and the compressed VFS streaming. Not fixed yet.

## How the patches were found

The shipping binary is **not stripped**. It carries full C++ symbols, which is how the money and menu patches were located:

```
00096fc4  CProfile::GetCash() const
00096fd0  CProfile::GiveCash(long long)
00097a80  CProfile::UnlockEverything(CApplication*)
0007de68  menu::CRT2Frontend::CreateDeveloperPages(...)
0005c7c0  menu::CManager::EnterPage(char const*, bool, bool, bool, bool)
```

Every patch verifies the bytes it is about to overwrite and aborts if they do not match, so the patcher cannot silently corrupt a different build.

## Legal

This repository contains no game code, assets, or binaries. It is a patcher that modifies a copy you already own, in the same spirit as a ROM patch. Raging Thunder 2 is the property of its copyright holders. The game was delisted from the App Store years ago and Polarbit is no longer trading, so there is no way to buy it, but that does not place it in the public domain.

The patcher itself is MIT licensed. See [LICENSE](LICENSE).
