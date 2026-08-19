# Scroll Fix, Live Touch Toggle, and Server UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make two-finger scroll actually scroll, let the touch toggle take effect without reconnecting, and simplify the server window.

**Context:** Touch input landed and works — pointer, tap-to-click and long-press right-click are all confirmed on hardware. Scroll is the one gesture that does nothing.

## Global Constraints

- Python 3.12; `./venv/bin/python -m pytest`, never bare `pytest`. 310 server tests pass.
- Android: `./gradlew testDebugUnitTest --tests '*Foo*'` (the wrapper is a milestone Gradle where `test` rejects `--tests`); `JAVA_HOME` must point at a real JDK. 35 Android tests pass.
- Do not change the video pipeline (`queue leaky=downstream`, `appsink drop=false`, the compositor), the geometry derivation, `ServerConfig.fps`, or the default codec.
- Do not weaken link confinement: Wi-Fi and docker peers stay refused.
- The hello is **exactly 94 bytes** with the capability extension at offset 94. Do not disturb that — it was the cause of a real bug.
- See `docs/superpowers/PROJECT-KNOWLEDGE.md` before touching capture, encoders or the tether link.

---

### Task 1: Make scroll actually scroll

**Root cause, already diagnosed.** The client emits scroll deltas in **normalised `[0,1]`** units:

```kotlin
actions.add(PointerAction.Scroll(x - lastX, y - lastY))   // x, y are in [0,1]
```

and the server passes them straight through:

```python
self._session.NotifyPointerAxis(dx, dy, 0)
```

Mutter expects **pixel-scale** deltas. A frame-to-frame two-finger drag produces roughly `0.005`, so the scroll happens but is about five thousandths of a pixel — invisible. Confirmed against Brave, which is Chromium-based and scrolls normally with real input.

**Files:**
- Modify: `server/core/remote_input.py`
- Modify: `server/core/server_core.py` (dispatch site, if scaling needs stream dimensions)
- Test: `tests/test_remote_input.py`

- [ ] **Step 1: Decide the scaling, and justify it in a comment**

Convert normalised deltas into scroll units the compositor and applications actually respond to. The natural mapping is to multiply by the stream's pixel dimensions, which `RemoteInput` already holds — a finger moving 10% down the video scrolls by 10% of the video's height in scroll units, so content tracks the finger roughly 1:1.

Add a sensitivity constant so this is tunable without re-deriving the maths, and comment *why* it exists rather than leaving a bare magic number.

Do the scaling **server-side**, not on the client. The server knows the stream dimensions authoritatively, and it keeps the wire format resolution-independent — the same reason coordinates travel normalised.

- [ ] **Step 2: Investigate whether discrete axis events work better**

Mutter also exposes `NotifyPointerAxisDiscrete`, which sends wheel-click style steps rather than smooth deltas. Chromium-based browsers historically handle discrete wheel events very reliably, while smooth-axis handling varies by toolkit.

Try both against a real Brave window on the virtual display and report which behaves better. If discrete wins, consider emitting discrete steps by accumulating normalised delta until it crosses a threshold — that also gives natural scroll "notches" instead of continuous drift.

Do not guess: this is a compatibility question that only a real application can answer. Report what you actually observed in Brave, and in one GTK application for contrast.

- [ ] **Step 3: Check the `flags` argument**

`NotifyPointerAxis(dx, dy, flags)` currently always passes `0`. Mutter's flags carry an axis-source/finish notion, and some clients keep kinetic-scroll state until a finish event arrives. Determine whether emitting a finish flag when the gesture ends improves behaviour, and wire it if so — `GestureInterpreter` already knows when a scroll gesture ends.

- [ ] **Step 4: Test**

Unit-test the scaling arithmetic with a fake session: a normalised delta at a known stream size produces the expected scroll magnitude, sign is preserved in both axes, and a zero delta produces no dispatch.

- [ ] **Step 5: Verify on hardware**

Two-finger scroll must visibly scroll a Brave page on the virtual display, in both directions, at a rate that feels proportional to the finger. Report the sensitivity value you settled on and why.

- [ ] **Step 6: Commit**

---

### Task 2: Make the touch toggle live

Today `touch_enabled` is read when a client connects, so flipping it needs a reconnect.

**There is an asymmetry that shapes the whole design, and it must be verified first.**

- **Disabling mid-session is genuinely easy:** stop dispatching, release any held buttons, stop the RemoteDesktop session. No effect on video.
- **Enabling mid-session may be impossible without restarting the capture**, because the RemoteDesktop session is bound to the ScreenCast session by passing `remote-desktop-session-id` to `ScreenCast.CreateSession` — i.e. **at creation time**. If there is no way to pair an already-running ScreenCast session, enabling requires tearing down and recreating it, which interrupts the stream.

**Files:**
- Modify: `server/core/server_core.py`, `server/core/remote_input.py`
- Modify: `server/ui/window.py`
- Test: `tests/test_input_reader.py` or a new test module

- [ ] **Step 1: Establish whether late pairing is possible**

Before designing, find out. Introspect `org.gnome.Mutter.ScreenCast` and its Session interface for any method that associates an existing session with a RemoteDesktop session after creation. Also check whether a RemoteDesktop session created *after* a ScreenCast session can still drive it.

Report the answer plainly. It decides the rest of this task, and guessing would produce a design that cannot work.

- [ ] **Step 2: Implement instant disable**

Regardless of the above, disabling must take effect immediately:
- stop dispatching input
- call `release_all()` so no button is left held on the user's desktop
- stop the RemoteDesktop session, so the capability genuinely ceases to exist rather than being ignored — that least-privilege property is deliberate and must survive

- [ ] **Step 3: Implement enable, per Step 1's finding**

If late pairing works, enable in place. If it does not, enabling must restart the capture session, and **the UI has to say so** — a control that silently drops the stream for a second is worse than one that warns. Prefer an explicit, honest label over a surprise.

Do not keep a RemoteDesktop session alive "just in case" while touch is off. That would make the toggle cheap at the cost of the security property the spec deliberately chose.

- [ ] **Step 4: Make the control live in the UI**

The toggle is currently locked while streaming. Unlock it, and have the status line reflect what is *actually* active — the same honesty rule the codec field follows, since a user on X11 gets video only regardless of the toggle.

- [ ] **Step 5: Test**

Cover: disabling mid-session releases held buttons and stops dispatch; a subsequent input message is ignored; re-enabling restores dispatch; and no path leaves a button held.

- [ ] **Step 6: Verify on hardware**

Toggle off mid-drag and confirm the pointer stops and no button stays down. Toggle back on and confirm input resumes. Confirm video is uninterrupted when disabling.

- [ ] **Step 7: Commit**

---

### Task 3: Simplify the server window

`server/ui/window.py` has grown by accretion: start/stop, a status bar, a log pane, a codec dropdown, and a touch switch, each added without revisiting the whole.

**This task needs a decision from the user before implementation** — "simplify and improve" is subjective, and building the wrong thing wastes the work. The plan records the analysis; the direction is theirs.

**What is actually there now:** one window containing connection status (client name, fps, resolution, codec, input state), a start/stop button, a codec selector, a touch toggle, and a scrolling log.

**Observations worth acting on:**

1. **The log dominates but is rarely useful to an end user.** It is a developer tool. Collapsing it behind a disclosure would reclaim most of the window.
2. **Two settings now sit inline with status.** As settings accumulate — audio and keyboard are planned for 2.1/2.2 — inline placement will not scale. A settings area, or a settings dialog, is the conventional answer.
3. **Status is a flat list of labels.** The three things a user actually wants at a glance are: is it connected, at what resolution and rate, and is input live. Those could be prominent, with the rest secondary.
4. **The README claimed a three-tab Libadwaita UI that never existed.** If a tabbed layout is wanted, that is a real (if larger) piece of work; if not, the docs are now honest and this is just a layout question.

**Open question for the user, before any code:** is the goal *fewer things on screen* (hide the log, move settings into a dialog, keep one window), or a *restructure* (tabs or a sidebar separating dashboard from settings)? The first is a contained change; the second is a rewrite of `window.py`.

- [ ] **Step 1: Get the user's answer to the question above.** Do not start implementing before this.
- [ ] **Step 2: Implement the agreed direction**, following the file's existing GTK4 idiom rather than introducing a new one, and keeping every control's "applies next connection" semantics accurate.
- [ ] **Step 3: Verify the window constructs and all controls work**, by walking the live widget tree under Xvfb as previous UI tasks did. State plainly that no visual inspection was performed if none was.
- [ ] **Step 4: Commit**

---

## Out of Scope

- Real multi-touch, keyboard (2.2.0), audio (2.1.0), clipboard.
- Kinetic/inertial scrolling. Get one-to-one scroll working first.
- Reworking the Android UI. This plan touches the Android client only if Task 1 needs a client-side change, which it should not.
