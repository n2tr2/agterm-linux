#!/usr/bin/env python3
"""AT-SPI smoke coverage for the real GTK frontend, always under isolated state and HOME."""

import json
import os
import re
import shlex
import shutil
import socket as socket_module
import subprocess
import sys
import tempfile
import time

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ROOT)
BIN = os.environ.get("AGTERM_TEST_BIN", os.path.join(ROOT, ".build/debug/AgtermLinux"))
CTL = os.environ.get("AGTERM_TEST_CTL", os.path.join(ROOT, ".build/debug/agtermctl-linux"))
RESOURCE_ROOT = os.environ.get("AGTERM_RESOURCE_ROOT", os.path.join(REPO, "agterm/Resources"))


def collect(node, role=None, name=None, out=None):
    """Depth-first collection that tolerates transiently disappearing GTK nodes."""
    if out is None:
        out = []
    try:
        node_name = node.get_name() or ""
        node_role = node.get_role_name()
        role_matches = role is None or node_role == role or (
            role == "button" and node_role == "push button"
        )
        if role_matches and (name is None or node_name == name):
            out.append(node)
        for index in range(node.get_child_count()):
            collect(node.get_child_at_index(index), role, name, out)
    except Exception:
        pass
    return out


def find_app(process_id):
    desktop = Atspi.get_desktop(0)
    matches = []
    for index in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(index)
        if (
            app.get_process_id() == process_id
            and "agterm" in (app.get_name() or "").lower()
            and app.get_child_count() > 0
        ):
            matches.append(app)
    return matches[-1] if matches else None


def wait_for(predicate, message, timeout=12, required=True):
    """Poll `predicate` until it returns something truthy, then return that value.

    `required=False` returns None on timeout instead of asserting, for a leg that is ALLOWED not to
    happen — a Wayland compositor declining `window resize` is the one case, and it prints a SKIP
    rather than failing. The polling cadence lives here so the two modes cannot drift apart.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    if required:
        raise AssertionError(message)
    return None


def named(root, name, role=None):
    matches = collect(root, role=role, name=name)
    return matches[0] if matches else None


def preferences_windows(root):
    return collect(root, role="dialog", name="Preferences") + collect(
        root, role="panel", name="Preferences"
    )


def preferences_window(root):
    matches = preferences_windows(root)
    return matches[0] if matches else None


def actionable(root, name):
    for item in reversed(collect(root, name=name)):
        try:
            actions = item.get_action_iface()
            if actions and actions.get_n_actions() > 0:
                return item
        except Exception:
            pass
    return None


def activate(node):
    assert node is not None, "cannot activate a missing accessible"
    actions = node.get_action_iface()
    assert actions and actions.get_n_actions() > 0, f"{node.get_name()!r} has no accessible action"
    assert actions.do_action(0), f"accessible action failed for {node.get_name()!r}"


def descendants(node, role=None, name=None):
    result = collect(node, role=role, name=name)
    return [item for item in result if item != node]


def editable_descendant(node):
    for item in descendants(node):
        try:
            if item.get_editable_text_iface():
                return item
        except Exception:
            pass
    return None


def describe_tree(node, depth=0):
    """Print a compact tree on failure so toolkit accessibility changes are diagnosable."""
    try:
        name = node.get_name() or ""
        if name or depth < 2:
            print(f"A11Y {'  ' * depth}{node.get_role_name()}: {name!r}")
        for index in range(node.get_child_count()):
            describe_tree(node.get_child_at_index(index), depth + 1)
    except Exception:
        pass


def window_extents(node):
    """Window-relative extents for an accessible, or None while it is still unallocated.

    WINDOW and never SCREEN: Wayland withholds global coordinates from AT-SPI, so SCREEN reports a
    0,0 origin (the same reason `mouse_click` combines WINDOW extents with the compositor's client
    origin). GTK also publishes a node to AT-SPI before its first allocate, where it reports an
    empty box — returning None for such a sample lets `wait_for` keep polling instead of asserting
    against a placeholder. The SIZE is the whole test for that: a widget GTK has not allocated
    reports a zero-or-negative width and height.

    ⚠️ A NEGATIVE ORIGIN is NORMAL and must never be read as "not yet allocated". GTK reports WINDOW
    coordinates relative to the toplevel's CONTENT area, which sits INSIDE the client-side decoration
    border, so under CSD every leftmost widget has a negative x — measured under the Xvfb + openbox
    session CI runs, the frame itself reports x=-5 and the fully allocated 220px sidebar scroll pane
    reports `x=-5 y=-5 w=220 h=648`. An earlier `bounds.x < 0` term here rejected exactly that box, so
    `sidebar_column` never resolved and the whole `sidebar-narrow-clipping` scenario timed out on CI.
    Every caller is origin-relative — `sidebar_column` picks the minimum x, and the containment math
    in `fits` compares `column.x + column.width` against `box.x + box.width` — so negative origins
    need no handling beyond not discarding them. Do NOT "restore" the guard.
    """
    try:
        component = node.get_component_iface()
        if not component:
            return None
        bounds = component.get_extents(Atspi.CoordType.WINDOW)
    except Exception:
        return None
    if bounds.width <= 1 or bounds.height <= 1:
        return None
    return bounds


def press_x11_key(key, process_id, window_title=None):
    if window_title:
        app = wait_for(lambda: find_app(process_id), "agterm app disappeared before key input")
        window = wait_for(
            lambda: named(app, window_title, role="frame"),
            f"agterm window {window_title!r} disappeared before key input",
        )
        focus_accessible_window(window, process_id)
    else:
        focus_window(process_id)
    time.sleep(0.5)
    subprocess.run(
        ["xdotool", "key", "--clearmodifiers", key],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def type_x11_text(value, process_id, window_title=None):
    """Type through the real X11 keyboard path used by the isolated UI suite."""
    if window_title:
        app = wait_for(lambda: find_app(process_id), "agterm app disappeared before text input")
        window = wait_for(
            lambda: named(app, window_title, role="frame"),
            f"agterm window {window_title!r} disappeared before text input",
        )
        focus_accessible_window(window, process_id)
    else:
        focus_window(process_id)
    time.sleep(0.5)
    subprocess.run(
        ["xdotool", "type", "--clearmodifiers", "--delay", "1", "--", value],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def press_ctrl_comma(process_id, window_title=None):
    # AT-SPI's device-event controller cannot inject keys on non-Mutter Wayland.
    # Hyprland's compositor dispatcher sends the real shortcut to this test PID;
    # The isolated X11 path uses xdotool against its private Xvfb display.
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        target = f"title:^({window_title})$" if window_title else f"pid:{process_id}"
        subprocess.run(
            [
                "hyprctl", "dispatch", "sendshortcut",
                f"CTRL,comma,{target}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    press_x11_key("ctrl+comma", process_id, window_title)


def press_ctrl_shift_p(process_id, window_title=None):
    """Open the command palette in the focused isolated window."""
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        target = f"title:^({window_title})$" if window_title else f"pid:{process_id}"
        subprocess.run(
            [
                "hyprctl", "dispatch", "sendshortcut",
                f"CTRL SHIFT,P,{target}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    press_x11_key("ctrl+shift+p", process_id, window_title)


def press_escape(process_id, window_title=None):
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        target = f"title:^({window_title})$" if window_title else f"pid:{process_id}"
        subprocess.run(
            ["hyprctl", "dispatch", "sendshortcut", f",escape,{target}"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    press_x11_key("Escape", process_id, window_title)


def press_return(process_id, window_title=None):
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        target = f"title:^({window_title})$" if window_title else f"pid:{process_id}"
        subprocess.run(
            ["hyprctl", "dispatch", "sendshortcut", f",return,{target}"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    press_x11_key("Return", process_id, window_title)


def press_right(process_id, window_title=None):
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        target = f"title:^({window_title})$" if window_title else f"pid:{process_id}"
        subprocess.run(
            ["hyprctl", "dispatch", "focuswindow", target],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if shutil.which("dotool"):
            keyboard = subprocess.Popen(
                ["dotool"], stdin=subprocess.PIPE, text=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                time.sleep(0.5)
                keyboard.stdin.write("key right\n")
                keyboard.stdin.flush()
                time.sleep(0.2)
            finally:
                keyboard.stdin.close()
                keyboard.wait(timeout=3)
            return
        subprocess.run(
            ["hyprctl", "dispatch", "sendshortcut", f",right,{target}"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    press_x11_key("Right", process_id, window_title)


def focus_window(process_id):
    """Give the isolated app real keyboard focus before testing its shortcut."""
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        subprocess.run(
            ["hyprctl", "dispatch", "focuswindow", f"pid:{process_id}"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    app = wait_for(lambda: find_app(process_id), "agterm app disappeared before focus")
    windows = collect(app, role="frame")
    assert windows, "agterm has no accessible window to focus"
    focus_accessible_window(windows[-1], process_id)


def focus_accessible_window(window, process_id):
    """Focus one exact window when the isolated process owns more than one."""
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        title = window.get_name() or ""
        subprocess.run(
            ["hyprctl", "dispatch", "focuswindow", f"title:^({title})$"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    title = window.get_name() or ""
    subprocess.run(
        [
            "xdotool", "search", "--onlyvisible", "--name", f"^{re.escape(title)}$",
            "windowactivate", "--sync", "%@",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def mouse_click(node_provider, process_id, window_title=None, button="right"):
    """Send a real pointer click to an accessible in one exact GTK window."""
    if window_title and not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        app = wait_for(lambda: find_app(process_id), "agterm app disappeared before pointer input")
        window = wait_for(
            lambda: named(app, window_title, role="frame"),
            f"agterm window {window_title!r} disappeared before pointer input",
        )
        focus_accessible_window(window, process_id)
    else:
        focus_window(process_id)
    deadline = time.monotonic() + 8
    bounds = None
    while time.monotonic() < deadline:
        try:
            node = node_provider()
            component = node.get_component_iface() if node else None
            if component:
                bounds = component.get_extents(Atspi.CoordType.SCREEN)
                break
        except Exception:
            pass
        time.sleep(0.1)
    assert bounds, "session row did not expose stable screen bounds"
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        # Wayland intentionally hides global coordinates from AT-SPI (SCREEN reports 0,0), while
        # WINDOW coordinates remain valid. Combine those with Hyprland's own client origin.
        local = component.get_extents(Atspi.CoordType.WINDOW)
        clients = json.loads(subprocess.check_output(["hyprctl", "-j", "clients"], text=True))
        client = next(
            (
                item for item in clients
                if item.get("pid") == process_id
                and (window_title is None or item.get("title") == window_title)
            ),
            None,
        )
        assert client, "Hyprland did not expose the isolated agterm client"
        subprocess.run(
            ["hyprctl", "dispatch", "focuswindow", f"address:{client['address']}"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        x = client["at"][0] + local.x + max(1, local.width // 2)
        y = client["at"][1] + local.y + max(1, local.height // 2)
        if shutil.which("dotool"):
            pointer = subprocess.Popen(
                ["dotool"], stdin=subprocess.PIPE, text=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                time.sleep(0.5)  # Let Hyprland register the temporary uinput pointer.
                subprocess.run(
                    ["hyprctl", "dispatch", "movecursor", str(x), str(y)],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(0.2)
                pointer.stdin.write(f"click {button}\n")
                pointer.stdin.flush()
                time.sleep(0.2)
            finally:
                pointer.stdin.close()
                pointer.wait(timeout=3)
            return
        number = 3 if button == "right" else 1
        assert Atspi.generate_mouse_event(x, y, f"b{number}c"), "AT-SPI click failed"
        return
    local = component.get_extents(Atspi.CoordType.WINDOW)
    geometry = subprocess.check_output(
        ["xdotool", "getactivewindow", "getwindowgeometry", "--shell"], text=True
    )
    origin = dict(line.split("=", 1) for line in geometry.splitlines() if "=" in line)
    x = int(origin["X"]) + local.x + max(1, local.width // 2)
    y = int(origin["Y"]) + local.y + max(1, local.height // 2)
    number = 3 if button == "right" else 1
    time.sleep(0.2)
    subprocess.run(
        ["xdotool", "mousemove", "--sync", str(x), str(y), "click", str(number)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def right_click(node_provider, process_id, window_title=None):
    mouse_click(node_provider, process_id, window_title=window_title, button="right")


def launch(env):
    process = subprocess.Popen([BIN], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    app = wait_for(lambda: find_app(process.pid), "agterm app not present in the AT-SPI tree")
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        subprocess.run(
            ["hyprctl", "dispatch", "movetoworkspacesilent", f"3,pid:{process.pid}"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return process, app


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    wait_for(lambda: find_app(process.pid) is None, "agterm remained in the accessibility tree after exit")


def control_json(env, *arguments):
    output = subprocess.check_output(
        [CTL, *arguments, "--socket", env["AGTERM_CONTROL_SOCKET"]],
        env=env,
        text=True,
        timeout=10,
    )
    return json.loads(output)


def raw_control_json(env, request):
    """Send one wire request so protocol-only commands can be exercised without a CLI polling loop."""
    client = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    client.settimeout(10)
    try:
        client.connect(env["AGTERM_CONTROL_SOCKET"])
        client.sendall(json.dumps(request).encode("utf-8") + b"\n")
        response = b""
        while b"\n" not in response:
            chunk = client.recv(64 * 1024)
            assert chunk, "control socket closed before returning a response"
            response += chunk
        return json.loads(response.split(b"\n", 1)[0])
    finally:
        client.close()


def window_list(env):
    return control_json(env, "window", "list", "--json")["result"]["windows"]


def select_window(env, window_id):
    control_json(env, "window", "select", window_id, "--json")
    wait_for(
        lambda: next(
            (item for item in window_list(env) if item["id"] == window_id), {}
        ).get("active"),
        f"window {window_id} did not become active",
    )


def window_tree(env, window_id):
    return control_json(env, "tree", "--window", window_id, "--json")["result"]["tree"]


def session_count(tree):
    return sum(len(workspace["sessions"]) for workspace in tree["workspaces"])


def activate_reveal_action(env, identity):
    subprocess.run(
        ["gapplication", "action", env["AGTERM_APP_ID"], "reveal", f"'{identity}'"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


def run_palette_action(app, process_id, window_title, action_name):
    window = wait_for(
        lambda: named(app, window_title, role="frame"),
        f"custom-command window {window_title!r} is missing",
    )
    focus_accessible_window(window, process_id)
    press_ctrl_shift_p(process_id, window_title=window_title)
    palette = wait_for(
        lambda: named(app, "Command Palette", role="frame"),
        f"command palette did not open in {window_title!r}",
    )
    search = wait_for(
        lambda: editable_descendant(palette),
        "command palette search is missing",
    )
    assert search.get_editable_text_iface().set_text_contents(action_name)
    wait_for(
        lambda: named(palette, action_name) and not named(palette, "About agterm"),
        f"palette action {action_name!r} did not become the selected result",
    )
    press_return(process_id, window_title="Command Palette")
    wait_for(
        lambda: not named(app, "Command Palette", role="frame"),
        f"command palette did not close after {action_name!r}",
    )


def verify_normal_toolbar(env, state, home):
    process, app = launch(env)
    try:
        rows = wait_for(lambda: collect(app, role="list item"), "expected at least one session row")
        wait_for(lambda: named(app, "workspace 1", role="label"), "workspace label is missing")

        subprocess.run(
            [CTL, "session", "new", "--socket", env["AGTERM_CONTROL_SOCKET"]],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        wait_for(
            lambda: len(collect(app, role="list item")) == len(rows) + 1,
            "session.new did not update the accessibility tree",
        )

        # Closing a native window removes its AppController before GTK finishes unmapping it. Exercise
        # the late notify::is-active callback and prove it cannot dereference the retired controller.
        created = control_json(env, "window", "new", "teardown-check", "--json")["result"]["id"]
        control_json(env, "window", "close", created, "--json")
        assert process.poll() is None, "closing a secondary window terminated the application"
        control_json(env, "tree", "--json")

        assert not named(app, "Main Menu"), "toolbar still exposes the removed Main Menu button"

        assert not preferences_window(app), "Preferences was open before shortcut verification"
        focus_window(process.pid)
        press_ctrl_comma(process.pid)
        wait_for(
            lambda: preferences_window(app),
            "Ctrl+, did not open Preferences",
        )
        press_ctrl_comma(process.pid)
        wait_for(
            lambda: len(preferences_windows(app)) == 1,
            "Ctrl+, did not preserve the single Preferences dialog",
        )
        print("OK: menu-free toolbar and Ctrl+, Preferences shortcut")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        stop(process)


def verify_upstream_control_parity(env):
    """Round-trip the upstream v0.16 control additions through the real Linux socket and GTK host."""
    process, app = launch(env)
    try:
        window_id = next(item["id"] for item in window_list(env) if item["open"])
        initial_tree = window_tree(env, window_id)
        initial_session = initial_tree["workspaces"][0]["sessions"][0]["id"]

        bootstrap = raw_control_json(env, {"cmd": "events.read"})
        assert bootstrap["ok"], f"events.read bootstrap failed: {bootstrap}"
        anchor = bootstrap["result"]["events"]
        assert anchor["items"] == [], "events.read bootstrap replayed prior history"

        workspace_id = control_json(
            env, "workspace", "new", "parity-work", "--collapsed",
            "--window", window_id, "--json",
        )["result"]["id"]
        wait_for(
            lambda: named(app, "parity-work", role="label"),
            "collapsed workspace was not rendered",
        )

        def workspace_node():
            return next(
                workspace for workspace in window_tree(env, window_id)["workspaces"]
                if workspace["id"] == workspace_id
            )

        assert workspace_node().get("collapsed"), "workspace.new --collapsed did not persist model state"
        control_json(
            env, "workspace", "expand", "--target", workspace_id,
            "--window", window_id, "--json",
        )
        assert not workspace_node().get("collapsed"), "workspace.expand did not update tree read-back"
        control_json(
            env, "workspace", "collapse", "--target", workspace_id,
            "--window", window_id, "--json",
        )
        assert workspace_node().get("collapsed"), "workspace.collapse did not update tree read-back"

        restore_line = "printf restored-by-parity-hook"
        control_json(
            env, "session", "restore", restore_line, "--target", initial_session,
            "--window", window_id, "--json",
        )
        restored = window_tree(env, window_id)["workspaces"][0]["sessions"][0]
        assert restored.get("restoreCommand") == restore_line, "session.restore was not persisted"

        held_id = control_json(
            env, "session", "new", "--name", "held-command", "--command", "true", "--wait",
            "--no-select", "--window", window_id, "--json",
        )["result"]["id"]
        time.sleep(1.0)
        held = next((
            session for workspace in window_tree(env, window_id)["workspaces"]
            for session in workspace["sessions"] if session["id"] == held_id
        ), None)
        assert held and held.get("commandWait"), "session.new --wait did not keep the exited command session"

        page = raw_control_json(env, {
            "cmd": "events.read",
            "args": {
                "run": anchor["run"],
                "after": str(anchor["next"]),
                "kinds": ["session.created"],
                "limit": 100,
            },
        })
        assert page["ok"], f"events.read cursor failed: {page}"
        assert any(
            item.get("kind") == "session.created" and item.get("session") == held_id
            for item in page["result"]["events"]["items"]
        ), "events.read did not return the Linux-created session event"
        stop(process)
        process = None

        process, app = launch(env)
        persisted_tree = window_tree(env, window_id)
        persisted_workspace = next(
            workspace for workspace in persisted_tree["workspaces"] if workspace["id"] == workspace_id
        )
        persisted_initial = next(
            session for workspace in persisted_tree["workspaces"]
            for session in workspace["sessions"] if session["id"] == initial_session
        )
        persisted_held = next(
            session for workspace in persisted_tree["workspaces"]
            for session in workspace["sessions"] if session["id"] == held_id
        )
        assert persisted_workspace.get("collapsed"), "workspace collapse state did not survive relaunch"
        assert persisted_initial.get("restoreCommand") == restore_line, (
            "restore override did not survive relaunch"
        )
        assert persisted_held.get("commandWait"), "command wait state did not survive relaunch"
        print("OK: events, restore overrides, held commands, and workspace collapse round-trip")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        if process is not None:
            stop(process)


def verify_dashboard_modal(env):
    process, app = launch(env)
    try:
        window_id = wait_for(
            lambda: next((item["id"] for item in window_list(env) if item["open"]), None),
            "initial window was not registered",
        )
        tree = window_tree(env, window_id)
        session_id = tree["workspaces"][0]["sessions"][0]["id"]
        control_json(env, "session", "rename", "modal-session", "--window", window_id, "--json")
        control_json(env, "window", "rename", window_id, "release", "--json")
        control_json(
            env, "session", "split", "on", "--target", session_id,
            "--window", window_id, "--json",
        )
        wait_for(lambda: named(app, "modal-session", role="frame"), "renamed modal window is missing")

        dashboard_button = wait_for(
            lambda: actionable(app, "Dashboard (Ctrl+Shift+M)"),
            "Dashboard header button is not actionable",
        )
        activate(dashboard_button)
        wait_for(
            lambda: named(app, "Dashboard — release", role="label"),
            "dashboard did not expose its custom-window title",
        )
        exit_dashboard = wait_for(
            lambda: named(app, "Exit Dashboard", role="button"),
            "dashboard close button is not actionable",
        )
        dashboard_tree = window_tree(env, window_id)
        assert dashboard_tree.get("dashboardMembers") == [
            f"{session_id}:left", f"{session_id}:right",
        ], "Dashboard header button did not open the pane-exact MRU grid"
        activate(exit_dashboard)
        wait_for(
            lambda: not window_tree(env, window_id).get("dashboardMembers"),
            "Exit Dashboard did not close the dashboard",
        )

        # Keyboard navigation changes the already-visible highlight and Enter selects immediately.
        activate(wait_for(
            lambda: actionable(app, "Dashboard (Ctrl+Shift+M)"),
            "Dashboard header button did not return after close",
        ))
        press_right(process.pid, window_title="modal-session")
        wait_for(
            lambda: window_tree(env, window_id).get("dashboardHighlighted") == f"{session_id}:right",
            "Right Arrow did not move the dashboard highlight to the split pane",
        )
        press_return(process.pid, window_title="modal-session")
        wait_for(
            lambda: not window_tree(env, window_id).get("dashboardMembers"),
            "Enter did not close the dashboard immediately",
        )
        assert window_tree(env, window_id)["workspaces"][0]["sessions"][0].get("splitFocused"), (
            "keyboard dashboard entry did not focus the exact split pane"
        )
        input_marker = os.path.join(env["AGTERM_STATE_DIR"], "dashboard-input-restored")
        type_x11_text(
            f"printf dashboard-restored > {shlex.quote(input_marker)}",
            process.pid,
            window_title="modal-session",
        )
        press_return(process.pid, window_title="modal-session")
        wait_for(
            lambda: os.path.exists(input_marker),
            "terminal did not accept keyboard input after Dashboard closed",
        )
        with open(input_marker, encoding="utf-8") as marker:
            assert marker.read() == "dashboard-restored", (
                "post-Dashboard terminal command produced unexpected output"
            )

        # A real single pointer click flashes the split cell, then enters it after the 180 ms delay.
        control_json(
            env, "session", "focus", "left", "--target", session_id,
            "--window", window_id, "--json",
        )
        activate(wait_for(
            lambda: actionable(app, "Dashboard (Ctrl+Shift+M)"),
            "Dashboard header button did not reopen for pointer coverage",
        ))
        wait_for(
            lambda: named(app, "modal-session · Right", role="label"),
            "dashboard split cell caption is missing",
        )
        mouse_click(
            lambda: named(app, "modal-session · Right", role="label"),
            process.pid,
            window_title="modal-session",
            button="left",
        )
        wait_for(
            lambda: not window_tree(env, window_id).get("dashboardMembers"),
            "single-click dashboard entry did not close after its highlight flash",
        )
        assert window_tree(env, window_id)["workspaces"][0]["sessions"][0].get("splitFocused"), (
            "single-click dashboard entry did not focus the exact split pane"
        )

        # The zoom chrome carries the normal composite title. Opening either modal closes the other.
        control_json(
            env, "surface", "zoom", "show", "--target", "active",
            "--window", window_id, "--json",
        )
        wait_for(
            lambda: named(app, "modal-session — release", role="label"),
            "terminal zoom did not expose its session/window title",
        )
        wait_for(
            lambda: actionable(app, "Exit Terminal Zoom"),
            "terminal zoom close button is not actionable",
        )
        control_json(env, "dashboard", "--mru", "--window", window_id, "--json")
        wait_for(
            lambda: window_tree(env, window_id).get("dashboardMembers")
            and not window_tree(env, window_id).get("zoomedSurface"),
            "opening Dashboard did not close Terminal Zoom",
        )
        control_json(
            env, "surface", "zoom", "show", "--target", "active",
            "--window", window_id, "--json",
        )
        wait_for(
            lambda: window_tree(env, window_id).get("zoomedSurface")
            and not window_tree(env, window_id).get("dashboardMembers"),
            "opening Terminal Zoom did not close Dashboard",
        )
        wait_for(
            lambda: named(app, "Exit Terminal Zoom", role="button"),
            "Terminal Zoom exit button disappeared",
        )
        activate(named(app, "Exit Terminal Zoom", role="button"))
        wait_for(
            lambda: not window_tree(env, window_id).get("zoomedSurface"),
            "Exit Terminal Zoom did not restore the normal window",
        )
        print("OK: dashboard single-click, modal titles, exact panes, and zoom exclusion")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        stop(process)


def verify_context_menu(env):
    process, app = launch(env)
    try:
        rows = wait_for(lambda: collect(app, role="list item"), "expected at least one session row")
        flag = None
        for _ in range(3):
            right_click(lambda: next(iter(collect(app, role="list item")), None), process.pid)
            try:
                flag = wait_for(lambda: actionable(app, "Flag"), "session context menu did not open", timeout=1)
                break
            except AssertionError:
                pass
        assert flag, "session context menu did not open"
        assert process.poll() is None, "session context menu terminated the app"
        created = control_json(env, "window", "new", "context-background", "--json")["result"]["id"]
        assert process.poll() is None, "backgrounding a window with a context menu terminated the app"
        control_json(env, "window", "close", created, "--json")
        activate(wait_for(lambda: actionable(app, "New Session"), "New Session button is not actionable"))
        wait_for(
            lambda: len(collect(app, role="list item")) == len(rows) + 1,
            "creating a session with a context menu open blocked the app",
        )
        control_json(env, "tree", "--json")
        print("OK: session context menu survives a sidebar rebuild")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        stop(process)


def verify_window_callback_ownership(env):
    process, app = launch(env)
    try:
        primary_id = wait_for(
            lambda: next((item["id"] for item in window_list(env) if item["open"]), None),
            "primary window was not registered",
        )
        control_json(env, "session", "rename", "primary-session", "--window", primary_id, "--json")
        secondary_id = control_json(env, "window", "new", "secondary", "--json")["result"]["id"]
        control_json(env, "session", "rename", "secondary-session", "--window", secondary_id, "--json")

        primary = wait_for(
            lambda: named(app, "primary-session", role="frame"),
            "primary window did not expose its unique session title",
        )
        wait_for(
            lambda: named(app, "secondary-session", role="frame"),
            "secondary window did not expose its unique session title",
        )
        select_window(env, secondary_id)
        before_primary = session_count(window_tree(env, primary_id))
        before_secondary = session_count(window_tree(env, secondary_id))
        activate(wait_for(
            lambda: actionable(primary, "New Session"),
            "background primary window's New Session button is not actionable",
        ))
        wait_for(
            lambda: session_count(window_tree(env, primary_id)) == before_primary + 1,
            "background-window action did not mutate its owning window",
        )
        assert session_count(window_tree(env, secondary_id)) == before_secondary, (
            "background-window action mutated the frontmost window"
        )
        control_json(env, "session", "rename", "primary-session", "--window", primary_id, "--json")
        primary = wait_for(
            lambda: named(app, "primary-session", role="frame"),
            "primary frame title did not follow its new active session",
        )

        # Open an auxiliary palette from the primary window, then make the secondary window frontmost
        # before editing its search. The callback must filter the originating palette, not look for a
        # nonexistent palette on the newly frontmost controller.
        select_window(env, primary_id)
        focus_accessible_window(primary, process.pid)
        wait_for(
            lambda: next(
                (item for item in window_list(env) if item["id"] == primary_id), {}
            ).get("active"),
            "primary window did not receive keyboard focus",
        )
        press_ctrl_shift_p(process.pid, window_title="primary-session")
        palette = wait_for(
            lambda: named(app, "Command Palette", role="frame"),
            "primary command palette did not open",
        )
        select_window(env, secondary_id)
        palette_search = wait_for(
            lambda: editable_descendant(palette),
            "background command palette did not expose an editable search",
        )
        assert palette_search.get_editable_text_iface().set_text_contents("New Session")
        wait_for(
            lambda: named(palette, "New Session   ctrl+shift+t") and not named(palette, "About agterm"),
            "background command palette search routed to the frontmost window",
        )
        press_escape(process.pid, window_title="Command Palette")
        wait_for(
            lambda: not named(app, "Command Palette", role="frame"),
            "background command palette did not close through its owner-bound key callback",
        )

        # Exercise a pending split restore while another window becomes active, then prove the original
        # session still accepts and persists a divider resize through its explicit window address.
        primary_session = window_tree(env, primary_id)["workspaces"][0]["sessions"][0]["id"]
        select_window(env, primary_id)
        control_json(
            env, "session", "split", "on", "--target", primary_session,
            "--window", primary_id, "--json",
        )
        select_window(env, secondary_id)
        control_json(
            env, "session", "resize", "--split-ratio", "0.31", "--target", primary_session,
            "--window", primary_id, "--json",
        )
        wait_for(
            lambda: abs(
                window_tree(env, primary_id)["workspaces"][0]["sessions"][0].get("splitRatio", 0) - 0.31
            ) < 0.001,
            "background split ratio was not persisted after its restore timer",
        )

        # Keep Preferences open on the primary, move focus away, and toggle a setting through the
        # background dialog. This covers both the GAction root context and settings widget ancestry.
        select_window(env, primary_id)
        primary = wait_for(
            lambda: named(app, "primary-session", role="frame"),
            "primary frame disappeared before Preferences coverage",
        )
        focus_accessible_window(primary, process.pid)
        press_ctrl_comma(process.pid, window_title="primary-session")
        preferences = wait_for(
            lambda: preferences_window(app),
            "primary Preferences dialog did not open",
        )
        select_window(env, secondary_id)
        right_click_switch = wait_for(
            lambda: actionable(preferences, "Right-click pastes"),
            "background Preferences switch is not actionable",
        )
        activate(right_click_switch)
        assert process.poll() is None, "background Preferences activity terminated the application"
        select_window(env, primary_id)
        press_escape(process.pid, window_title="primary-session")
        wait_for(
            lambda: not preferences_window(app),
            "background Preferences dialog did not close through its owning window",
        )

        # Repeatedly close secondary windows with a fresh split restore and palette/window callbacks in
        # flight. The application and the surviving primary controller must remain usable.
        for index in range(4):
            transient_id = control_json(
                env, "window", "new", f"teardown-{index}", "--json"
            )["result"]["id"]
            transient_session = window_tree(env, transient_id)["workspaces"][0]["sessions"][0]["id"]
            control_json(
                env, "session", "split", "on", "--target", transient_session,
                "--window", transient_id, "--json",
            )
            control_json(env, "window", "close", transient_id, "--json")
            assert process.poll() is None, "closing a secondary window terminated the application"
        control_json(env, "tree", "--window", primary_id, "--json")

        print("OK: background callbacks and pending secondary-window teardown keep their owners")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        stop(process)


def verify_notification_reveal(env):
    process, app = launch(env)
    try:
        primary_id = wait_for(
            lambda: next((item["id"] for item in window_list(env) if item["open"]), None),
            "primary notification window was not registered",
        )
        primary_tree = window_tree(env, primary_id)
        session_id = primary_tree["workspaces"][0]["sessions"][0]["id"]
        control_json(
            env, "session", "split", "on", "--target", session_id,
            "--window", primary_id, "--json",
        )
        control_json(
            env, "session", "focus", "right", "--target", session_id,
            "--window", primary_id, "--json",
        )
        secondary_id = control_json(env, "window", "new", "reveal-survivor", "--json")["result"]["id"]
        control_json(env, "window", "close", primary_id, "--json")
        wait_for(
            lambda: not next(
                (item for item in window_list(env) if item["id"] == primary_id), {"open": True}
            )["open"],
            "source notification window did not close",
        )

        identity = f"{primary_id}:{session_id}:split"
        activate_reveal_action(env, identity)
        wait_for(
            lambda: next(
                (item for item in window_list(env) if item["id"] == primary_id), {}
            ).get("open"),
            "notification reveal did not reopen its encoded window",
        )

        def revealed_split():
            tree = window_tree(env, primary_id)
            sessions = [session for workspace in tree["workspaces"] for session in workspace["sessions"]]
            target = next((session for session in sessions if session["id"] == session_id), None)
            return target and target.get("active") and target.get("splitFocused")

        wait_for(revealed_split, "notification reveal did not select the encoded split pane")
        assert next(
            item for item in window_list(env) if item["id"] == secondary_id
        )["open"], "notification reveal disturbed the surviving window"
        assert process.poll() is None, "notification reveal terminated the application"
        print("OK: notification action reopens its encoded window and split pane")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        stop(process)


def verify_notification_focus_policy(env):
    with open(os.path.join(env["AGTERM_STATE_DIR"], "settings.json"), "w", encoding="utf-8") as target:
        json.dump({"notificationsEnabled": False}, target)
    process, app = launch(env)
    try:
        focus_window(process.pid)
        tree = control_json(env, "tree", "--json")["result"]["tree"]
        initial = tree["workspaces"][0]["sessions"][0]
        window_id = next(item["id"] for item in window_list(env) if item["open"])
        time.sleep(1.0)  # let the initial login shell reach its prompt before injecting printf

        def unseen(session_id):
            current = window_tree(env, window_id)
            sessions = [session for workspace in current["workspaces"] for session in workspace["sessions"]]
            return next(session for session in sessions if session["id"] == session_id).get("unseen", 0)

        def emit_osc(session_id, title):
            command = f"printf '\\033]9;{title} Body\\007'\n"
            control_json(
                env, "session", "type", command, "--target", session_id,
                "--window", window_id, "--json",
            )

        emit_osc(initial["id"], "Focused")
        time.sleep(0.6)
        assert unseen(initial["id"]) == 0, "focused pane OSC notification created an unseen badge"

        foreground_id = control_json(
            env, "session", "new", "--name", "foreground", "--window", window_id, "--json"
        )["result"]["id"]
        wait_for(
            lambda: window_tree(env, window_id)["workspaces"][0]["sessions"][-1].get("active"),
            "new foreground session did not become active",
        )
        wait_for(
            lambda: named(app, "foreground", role="frame"),
            "new foreground session did not become the visible GTK surface",
        )
        time.sleep(0.5)
        emit_osc(initial["id"], "Hidden")
        wait_for(
            lambda: unseen(initial["id"]) == 1,
            "hidden pane OSC notification did not create an unseen badge",
        )

        control_json(
            env, "notify", "--title", "Explicit", "--target", foreground_id,
            "control bypass", "--window", window_id, "--json",
        )
        wait_for(
            lambda: unseen(foreground_id) == 1,
            "explicit control notification did not bypass focused-pane suppression",
        )
        assert process.poll() is None, "notification focus policy terminated the application"
        print("OK: focused OSC suppresses badge while hidden and explicit notifications deliver")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        stop(process)


def verify_notification_banner_round_trip(env):
    assert shutil.which("makoctl"), "makoctl is required for the desktop-banner round trip"
    notification_id = None
    process, app = launch(env)
    try:
        focus_window(process.pid)
        tree = control_json(env, "tree", "--json")["result"]["tree"]
        initial = tree["workspaces"][0]["sessions"][0]
        window_id = next(item["id"] for item in window_list(env) if item["open"])
        time.sleep(1.0)

        def unseen(session_id):
            current = window_tree(env, window_id)
            sessions = [session for workspace in current["workspaces"] for session in workspace["sessions"]]
            return next(session for session in sessions if session["id"] == session_id).get("unseen", 0)

        def emit_osc(session_id, body):
            control_json(
                env, "session", "type", f"printf '\\033]9;{body}\\007'\n",
                "--target", session_id, "--window", window_id, "--json",
            )

        test_suffix = os.path.basename(env["AGTERM_STATE_DIR"])
        suppressed_body = f"Focused banner must suppress {test_suffix}"
        delivered_body = f"Hidden banner must deliver {test_suffix}"
        emit_osc(initial["id"], suppressed_body)
        time.sleep(0.8)
        assert unseen(initial["id"]) == 0
        assert not any(
            item.get("body") == suppressed_body
            for item in json.loads(subprocess.check_output(["makoctl", "list", "-j"], text=True))
        ), "focused pane posted a desktop banner"

        control_json(env, "session", "new", "--name", "banner-foreground", "--window", window_id, "--json")
        wait_for(lambda: named(app, "banner-foreground", role="frame"), "foreground banner session not visible")
        time.sleep(0.5)
        emit_osc(initial["id"], delivered_body)
        wait_for(lambda: unseen(initial["id"]) == 1, "hidden pane did not raise its badge")
        notification = wait_for(
            lambda: next((
                item for item in json.loads(subprocess.check_output(["makoctl", "list", "-j"], text=True))
                if item.get("body") == delivered_body
            ), None),
            "hidden pane did not post a desktop banner",
        )
        notification_id = notification["id"]

        survivor = control_json(env, "window", "new", "banner-survivor", "--json")["result"]["id"]
        control_json(env, "window", "close", window_id, "--json")
        wait_for(
            lambda: not next(item for item in window_list(env) if item["id"] == window_id)["open"],
            "banner source window did not close",
        )
        subprocess.run(["makoctl", "invoke", "-n", str(notification_id)], check=True)
        wait_for(
            lambda: next(item for item in window_list(env) if item["id"] == window_id)["open"],
            "desktop banner action did not reopen the source window",
        )
        wait_for(
            lambda: next(
                session for workspace in window_tree(env, window_id)["workspaces"]
                for session in workspace["sessions"] if session["id"] == initial["id"]
            ).get("active"),
            "desktop banner action did not select its source session",
        )
        assert next(item for item in window_list(env) if item["id"] == survivor)["open"]
        print("OK: real desktop banner suppresses, delivers, and reopens its source window")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        if notification_id is not None:
            subprocess.run(
                ["makoctl", "dismiss", "-n", str(notification_id), "-h"], check=False
            )
        stop(process)


def verify_custom_command_failures(env):
    config = os.path.join(env["AGTERM_STATE_DIR"], "config")
    os.makedirs(config)
    with open(os.path.join(config, "keymap.conf"), "w", encoding="utf-8") as target:
        target.write(
            'command "Launch Failure" true\n'
            'command "Exit Failure" exit 23\n'
            'command "Slow Failure" sleep 1; exit 29\n'
        )
    process, app = launch(env)
    try:
        first_window = next(item["id"] for item in window_list(env) if item["open"])
        first_cwd = os.path.join(env["AGTERM_STATE_DIR"], "command-cwd-a")
        second_cwd = os.path.join(env["AGTERM_STATE_DIR"], "command-cwd-b")
        os.makedirs(first_cwd)
        os.makedirs(second_cwd)
        first_session = control_json(
            env, "session", "new", "--name", "command-origin-a", "--cwd", first_cwd,
            "--window", first_window, "--json",
        )["result"]["id"]
        second_window = control_json(env, "window", "new", "command-window-b", "--json")["result"]["id"]
        second_session = control_json(
            env, "session", "new", "--name", "command-origin-b", "--cwd", second_cwd,
            "--window", second_window, "--json",
        )["result"]["id"]

        def frame(title):
            return named(app, title, role="frame")

        def failure_named(window, prefix):
            return next((item for item in collect(window) if (item.get_name() or "").startswith(prefix)), None)

        wait_for(lambda: frame("command-origin-a"), "first command window did not become accessible")
        wait_for(lambda: frame("command-origin-b"), "second command window did not become accessible")
        time.sleep(0.5)
        shutil.rmtree(first_cwd)
        shutil.rmtree(second_cwd)
        exit_titles = {}
        for window_id, session_id, title, other_title in (
            (first_window, first_session, "command-origin-a", "command-origin-b"),
            (second_window, second_session, "command-origin-b", "command-exit-a"),
        ):
            run_palette_action(app, process.pid, title, "Launch Failure  (custom)")
            launch_prefix = "command failed to launch: Launch Failure —"
            wait_for(
                lambda: failure_named(frame(title), launch_prefix),
                f"launch failure toast did not appear in {title}",
            )
            assert not failure_named(frame(other_title), launch_prefix), (
                f"launch failure from {title} leaked into {other_title}"
            )

            suffix = "a" if window_id == first_window else "b"
            exit_title = f"command-exit-{suffix}"
            control_json(
                env, "session", "new", "--name", exit_title, "--cwd", "/tmp",
                "--window", window_id, "--json",
            )
            wait_for(lambda: frame(exit_title), f"{exit_title} did not become accessible")
            exit_titles[window_id] = exit_title
            run_palette_action(app, process.pid, exit_title, "Exit Failure  (custom)")
            exit_message = "command failed (exit 23): Exit Failure"
            wait_for(
                lambda: named(frame(exit_title), exit_message),
                f"non-zero failure toast did not appear in {exit_title}",
            )
            assert not named(frame(other_title), exit_message), (
                f"non-zero failure from {exit_title} leaked into {other_title}"
            )

        run_palette_action(app, process.pid, exit_titles[first_window], "Slow Failure  (custom)")
        control_json(env, "window", "close", first_window, "--json")
        wait_for(
            lambda: not next(item for item in window_list(env) if item["id"] == first_window)["open"],
            "slow-command source window did not close",
        )
        control_json(env, "window", "select", first_window, "--json")
        wait_for(
            lambda: next(item for item in window_list(env) if item["id"] == first_window)["open"],
            "slow-command source window did not reopen",
        )
        time.sleep(1.4)
        slow_message = "command failed (exit 29): Slow Failure"
        assert not named(frame(exit_titles[first_window]), slow_message), (
            "old command completion reached the reopened controller incarnation"
        )
        assert not named(frame(exit_titles[second_window]), slow_message), (
            "old command completion leaked into the other window"
        )
        print("OK: custom-command failures stay with their originating controller incarnation")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        stop(process)


def verify_surface_configuration_lifetimes(env):
    """Exercise libghostty-owned command, cwd, overlay, and restored initial-input buffers."""
    state = env["AGTERM_STATE_DIR"]
    runner = os.path.join(state, "surface-lifetime-probe.sh")
    with open(runner, "w", encoding="utf-8") as target:
        target.write(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$PWD\" > \"$1\"\n"
            "sleep 0.4\n"
        )
    os.chmod(runner, 0o755)
    command_cwd = os.path.join(state, "command-cwd")
    overlay_cwd = os.path.join(state, "overlay-cwd")
    os.makedirs(command_cwd)
    os.makedirs(overlay_cwd)
    command_marker = os.path.join(state, "command.marker")
    overlay_marker = os.path.join(state, "overlay.marker")
    restore_marker = os.path.join(state, "restore.marker")
    url_marker = os.path.join(state, "url.marker")
    url_callback_marker = os.path.join(state, "url-callback.marker")
    url = "https://example.test/agterm/" + ("length-delimited-" * 10) + "end?q=one%20two#fragment"
    xdg_data = os.path.join(state, "xdg-data")
    xdg_config = os.path.join(state, "xdg-config")
    applications = os.path.join(xdg_data, "applications")
    os.makedirs(applications)
    os.makedirs(xdg_config)
    url_capture = os.path.join(state, "capture-url.sh")
    with open(url_capture, "w", encoding="utf-8") as target:
        target.write(f"#!/bin/sh\nprintf '%s' \"$1\" > {url_marker}\n")
    os.chmod(url_capture, 0o755)
    with open(os.path.join(applications, "agterm-url-test.desktop"), "w", encoding="utf-8") as target:
        target.write(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=agterm URL test\n"
            f"Exec={url_capture} %u\n"
            "MimeType=x-scheme-handler/https;\n"
            "NoDisplay=true\n"
        )
    with open(os.path.join(xdg_config, "mimeapps.list"), "w", encoding="utf-8") as target:
        target.write("[Default Applications]\nx-scheme-handler/https=agterm-url-test.desktop;\n")
    env["XDG_DATA_HOME"] = xdg_data
    env["XDG_CONFIG_HOME"] = xdg_config
    env["GTK_A11Y"] = "atspi"
    subprocess.run(["gio", "open", url], env=env, check=True)
    wait_for(lambda: os.path.exists(url_marker), "test URL handler did not register")
    os.remove(url_marker)

    process, _ = launch(env)
    try:
        window_id = next(item["id"] for item in window_list(env) if item["open"])
        initial = window_tree(env, window_id)["workspaces"][0]["sessions"][0]
        control_json(
            env, "session", "new", "--name", "surface-command", "--cwd", command_cwd,
            "--command", f"{runner} {command_marker}", "--window", window_id, "--json",
        )
        wait_for(lambda: os.path.exists(command_marker), "session --command did not run")
        with open(command_marker, encoding="utf-8") as source:
            assert source.read().strip() == command_cwd

        control_json(
            env, "session", "overlay", "open", f"{runner} {overlay_marker}",
            "--cwd", overlay_cwd, "--target", initial["id"], "--window", window_id, "--json",
        )
        wait_for(lambda: os.path.exists(overlay_marker), "overlay command did not run")
        with open(overlay_marker, encoding="utf-8") as source:
            assert source.read().strip() == overlay_cwd
    finally:
        stop(process)

    snapshot_path = os.path.join(state, "windows", f"{window_id}.json")
    with open(snapshot_path, encoding="utf-8") as source:
        snapshot = json.load(source)
    restored = next(
        session for workspace in snapshot["workspaces"] for session in workspace["sessions"]
        if session["id"] == initial["id"]
    )
    restored["foregroundCommand"] = [runner, restore_marker]
    with open(snapshot_path, "w", encoding="utf-8") as target:
        json.dump(snapshot, target)
    with open(os.path.join(state, "settings.json"), "w", encoding="utf-8") as target:
        json.dump({"restoreRunningCommand": True}, target)
    env["AGTERM_ATSPI_OPEN_URL"] = url
    env["AGTERM_ATSPI_URL_CAPTURE"] = url_callback_marker

    process, _ = launch(env)
    try:
        wait_for(
            lambda: os.path.exists(restore_marker),
            "restored foreground command was not delivered through initial input",
        )
        with open(restore_marker, encoding="utf-8") as source:
            assert source.read().strip() == initial["cwd"]
        wait_for(
            lambda: os.path.exists(url_callback_marker),
            "runtime URL action did not reach the Linux launch boundary",
        )
        with open(url_callback_marker, encoding="utf-8") as source:
            assert source.read() == url, "libghostty URL callback was truncated or changed"
        wait_for(lambda: os.path.exists(url_marker), "runtime URL action did not reach the URL launcher")
        with open(url_marker, encoding="utf-8") as source:
            assert source.read() == url, "terminal hyperlink was truncated or changed"
        print("OK: libghostty buffers survive command/cwd/restore paths and exact URL launch")
    finally:
        stop(process)


def verify_sidebar_row_height_follows_font_size(env):
    """Sidebar rows must follow the sidebar font size instead of Adwaita's 36px navigation-sidebar pin."""
    settings_path = os.path.join(env["AGTERM_STATE_DIR"], "settings.json")

    def sample_row_height(app):
        # Preferences AdwActionRows carry the same `list item` role, so this scenario's single-session
        # state is what pins the measurement to the sidebar row; an extra row means something else
        # opened and the reading would be meaningless, so that case ASSERTS (naming the count) instead
        # of polling, which would otherwise surface 12s later as a generic timeout. Extents are read in
        # WINDOW coordinates because SCREEN reports a 0,0 origin under Wayland. A row is published to
        # the accessibility tree before its first allocate and reports height 0, so an implausible
        # sample returns None and lets the caller poll; the 20px gate sits deliberately BELOW the
        # smallest CSS floor this scenario can reach (24px at 9pt) so an override that never applied
        # fails loudly in the band assertion rather than as "never reported a settled height".
        rows = collect(app, role="list item")
        if not rows:
            return None
        assert len(rows) == 1, f"expected exactly one sidebar row, found {len(rows)} list items"
        try:
            component = rows[0].get_component_iface()
            if component is None:
                return None
            height = component.get_extents(Atspi.CoordType.WINDOW).height
        except Exception:
            return None
        return height if height >= 20 else None

    def settled_row_height(app):
        first = sample_row_height(app)
        if first is None:
            return None
        time.sleep(0.1)
        return first if sample_row_height(app) == first else None

    def measure(font_size, label, check):
        if font_size is not None:
            # Merge rather than clobber, so a future first-run settings write is not silently reset and
            # this keeps measuring a font-size change against otherwise unchanged state.
            settings = {}
            if os.path.exists(settings_path):
                with open(settings_path, encoding="utf-8") as source:
                    settings = json.load(source)
            settings["sidebarFontSize"] = font_size
            with open(settings_path, "w", encoding="utf-8") as destination:
                json.dump(settings, destination)
        process, app = launch(env)
        try:
            height = wait_for(
                lambda: settled_row_height(app),
                f"the sidebar row never reported a settled height {label}",
            )
            check(height)
            return height
        except AssertionError:
            describe_tree(app)
            raise
        finally:
            stop(process)

    def default_band(height):
        # Triage: a height at or near 36 means the emitted CSS override never applied at all - the theme
        # pin makes anything under 36 unreachable without it.
        assert 28 <= height < 36, (
            f"default sidebar row height {height}px left the 28px floor .. 36px Adwaita pin band"
        )

    def dense_band(height):
        # Only the CSS floor is asserted upward: the exact height is host font metrics (Cantarell here,
        # whatever fontconfig picks in the CI container), and the densification claim is carried by the
        # comparison against the default row rather than by a hand-tuned pixel cap.
        assert height >= 24, f"9pt sidebar row height {height}px sank below the 24px floor"
        assert height < default_height, (
            f"9pt rows ({height}px) did not densify below the default rows ({default_height}px)"
        )

    def large_band(height):
        # min-height is a FLOOR, not a cap: 20pt text must grow the row past both its own floor and the
        # default row instead of being clipped to a fixed height.
        assert height >= 35, f"20pt sidebar row height {height}px sank below the 35px floor"
        assert height > default_height, (
            f"20pt rows ({height}px) did not grow past the default rows ({default_height}px)"
        )

    # The two comparison bands read `default_height`, so the passes must stay in this order.
    default_height = measure(None, "at the default sidebar font size", default_band)
    small_height = measure(9, "at the 9pt sidebar font size", dense_band)
    large_height = measure(20, "at the 20pt sidebar font size", large_band)
    print(
        "OK: sidebar row height follows the sidebar font size "
        f"({default_height}px default -> {small_height}px at 9pt -> {large_height}px at 20pt)"
    )


def sidebar_column(app):
    """The sidebar column's own window-relative box, or None while it is still unallocated.

    The `GtkScrolledWindow` wrapping the sidebar IS the column: it is the clipping boundary, so its
    allocation tracks the paned position instead of the overflowing content — unlike the viewport,
    content box, list box, and row parent box BELOW it, which all inherit the overflow under the bug and
    would make the containment assertion vacuously true. Both edges come from it on purpose: AT-SPI
    WINDOW coordinates are relative to the toplevel SURFACE, which under client-side decorations
    includes the shadow inset, so the sidebar's left edge is not reliably 0 and a right edge compared
    against a bare WIDTH would fail wherever the compositor floats the window with a shadow. The
    sidebar is the leftmost scrolled window in the tree.
    """
    boxes = [box for node in collect(app, role="scroll pane") if (box := window_extents(node))]
    return min(boxes, key=lambda box: box.x) if boxes else None


# AppStore.sidebarWidthDefault: the width the Linux floor PINS to whenever the measured sidebar content
# fits inside it. Launches 1 and 3 are the two halves of that pin/follow gate and launch 4 needs the pin
# to be unambiguous, so the number is named ONCE here instead of being spelled into each assertion and
# each failure message — where a moved default would otherwise report itself under the old number.
SIDEBAR_DEFAULT_WIDTH = 220
# Launch 4's seeded request: comfortably above the pin, so a column at this width can only be the
# restored request and never the floor.
SIDEBAR_REQUESTED_WIDTH = 400
# How far past the column's right edge a part may sit before it counts as clipped.
#
# ⚠️ This is theme-inset tolerance, NOT a fudge factor, and shrinking it back to 1 will make this
# scenario fail on Ubuntu noble for a clip nobody can see. A widget's AT-SPI extents include its own
# CSS margin and padding, and how far a TRAILING widget's box sits from the scroller's content edge
# varies by libadwaita version. Measured at the same 220px column, with the same tree:
#
#   host                     add-session button          decorated row box
#   libadwaita 1.9.2 (Arch)  ends exactly ON the edge     ends 5px clear
#   Ubuntu noble (CI)        ends 5px past                ends 2px past
#
# So the trailing button lands on the boundary with ZERO margin on one host — any theme that insets
# the header row differently tips it over. The bytes past the edge are padding: the `+` icon is
# centred in a ~35px button, so 5px off its right edge clips no glyph.
#
# It costs nothing in discriminating power, because the regression this gate exists for overflows by
# two orders of magnitude more: dropping the breadcrumb's ellipsize reports a part 1065px wide against
# a 560px column, and dropping the pill's put it 190px past. Nothing real lands in the 1..8 band.
SIDEBAR_EDGE_SLACK = 8
# A row narrower than this fraction of the column was caught mid-layout rather than laid out inside it:
# it separates "the sidebar truncates properly" from "nothing has been allocated yet".
SIDEBAR_MIN_ROW_FRACTION = 0.5

# Trap 5's remedy applies ONLY on a live Wayland session, where a parked window really can stall the
# frame clock, so it is appended only there. Under Xvfb — which is how CI runs this suite, with no
# compositor to blame — it sent every failure chasing a Hyprland workaround that cannot apply, and it
# said nothing the message it was appended to did not already say, so that branch appends nothing.
SIDEBAR_SETTLE_HINT = (
    " — on Hyprland run this scenario with `env -u HYPRLAND_INSTANCE_SIGNATURE` so the window keeps "
    "rendering and rebuilt rows allocate"
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    else "")


def sidebar_column_now(app):
    """The sidebar column AS IT IS RIGHT NOW, for the one containment check about to run.

    Never hoist this out of a check. Every step of the sweep below — decorating the row, focusing the
    workspace, switching to flagged mode, dropping the last flag — goes through `rebuildSidebar` →
    `refreshSidebarWidthFloor`, which re-measures the content and re-lays the divider. A limit captured
    once is stale for all of them: a step that NARROWS the column (the flagged row drops the flag star,
    the empty hint drops the rows entirely) passes vacuously against the wider old limit, and a step
    that widens it false-fails with a clipping message that is not a clipping.
    """
    return wait_for(lambda: sidebar_column(app),
                    f"the sidebar column never allocated{SIDEBAR_SETTLE_HINT}")


def sidebar_settled(app, role, match=None):
    """The first node of `role` (optionally filtered by accessible name) reporting a real extent."""
    for candidate in collect(app, role=role):
        if match is not None and not match(candidate.get_name() or ""):
            continue
        bounds = window_extents(candidate)
        if bounds:
            return candidate, bounds
    return None


def sidebar_fits(column, box, description):
    """Triage: a part PAST the limit means a sidebar label lost its PANGO_ELLIPSIZE_END (or a new
    row builder never set one); an equal-and-tiny box means it was measured before GTK allocated
    it, so widen the settle poll instead.

    ⚠️ This CANNOT be the only check on a sidebar site. The floor FOLLOWS the measured content
    (`refreshSidebarWidthFloor` re-measures `sidebarBox` after every rebuild), so a label that lost
    its ellipsize WIDENS the column instead of overflowing it, and containment only starts failing
    once the width the content demands clears `AppStore.sidebarWidthMax` (560). It does clear it for
    this scenario's 40-character session name and its ~68-character flagged breadcrumb, which is why
    those two are checked this way; for the narrower sites (the focus pill, the wrapped hint) the
    discriminating check is `sidebar_does_not_widen` below.
    """
    limit = column.x + column.width
    edge = box.x + box.width
    assert edge <= limit + SIDEBAR_EDGE_SLACK, (
        f"{description} is pushed past the {column.width}px sidebar column (right edge "
        f"{limit}px): x={box.x} width={box.width} right={edge}")


def sidebar_does_not_widen(app, baseline, description):
    """The column did NOT have to GROW to fit `description` — the discriminating half of the sweep.

    Measured against the shipped binary with a user `gtk-4.0/gtk.css` raising ONE site's minimum to
    400px (the same lever launch 3 uses): `.agterm-focus-pill label` took the column 220 → 450 with
    the pill's right edge at 408, and `.agterm-sidebar label.dim-label` took it 220 → 400 with the
    hint's right edge at 400 — both parts comfortably INSIDE their own widened column, i.e. both
    `sidebar_fits` calls passing while the regression was live. Growth is what actually distinguishes
    them.

    It needs no width of its own, which is what makes it independent of the host font family and the
    desktop's text scaling (and so of Decision A, where the floor may legitimately exceed the old
    240): a sidebar part that truncates correctly reports a minimum FAR narrower than a decorated
    row — the pill collapses to `✕ …` plus its button padding, the wrapped hint to its longest WORD —
    so it cannot move a column the decorated rows have already sized, at any size. Losing the
    ellipsize/wrap makes it report its whole string instead, which is wider than the row chrome by
    the same construction.

    `baseline` is deliberately an EARLIER column width, which is the ONE place in this sweep where
    hoisting the column read is the point rather than the trap `sidebar_column_now` warns about: it
    is the yardstick, and re-reading it after the part appeared would measure the regression against
    itself.
    """
    column = sidebar_column_now(app)
    assert column.width <= baseline + SIDEBAR_EDGE_SLACK, (
        f"the sidebar column GREW from {baseline}px to {column.width}px when {description} appeared "
        "— correctly truncated it is narrower than a decorated row and cannot move the column, so "
        "this is that part reporting its whole text as its minimum width (a lost ellipsize on the "
        "pill's label, or a lost wrap on the hint). The floor follows the measured content, so it "
        "widens the column instead of overflowing it — which is exactly why the containment check "
        "beside this one still passes")


def sidebar_row_fits(app, row, bounds, expected_images, expected_labels):
    """Every VISIBLE PART of one sidebar row, against the column as it is right now.

    ⚠️ The row's OWN box is deliberately NOT contained here, and restoring that check will make this
    scenario fail on some libadwaita versions for a clip nobody can see. A `GtkListBoxRow`'s AT-SPI
    extents include the Adwaita `.navigation-sidebar > row` margin, which is empty space, and the
    row's inset inside the column is theme-dependent: measured at the same 220px column, the row
    starts 19px in on libadwaita 1.9.2 (Arch) but 28px in on Ubuntu noble, so the same 194px row box
    ends 5px clear of the column on one and 2px past it on the other. Those 2px are margin — the
    badge, the rightmost thing actually drawn, still ends ~6px inside the row's own right edge.

    The parts below are the real gate, and the reported symptom: the bug pushed the status glyph, the
    star and the badge OUT of the viewport, and each of those is checked individually. A row whose
    label stopped ellipsizing overflows by hundreds of pixels, not two, so the parts catch it — the
    fail-first run reported the 40-character name at 658px against a 560px column.
    """
    column = sidebar_column_now(app)
    assert bounds.width >= column.width * SIDEBAR_MIN_ROW_FRACTION, (
        f"sidebar row is implausibly narrow for a {column.width}px column: width={bounds.width}")
    parts = [(item, box) for item in descendants(row) if (box := window_extents(item))]
    roles = [item.get_role_name() for item, _ in parts]
    # The counts keep this from silently checking nothing if GTK ever stops exposing GtkImage.
    assert roles.count("image") >= expected_images and roles.count("label") >= expected_labels, (
        f"decorated row exposed {roles}, expected at least {expected_images} images and "
        f"{expected_labels} labels")
    for item, box in parts:
        sidebar_fits(column, box, f"{item.get_role_name()} {(item.get_name() or '')[:32]!r}")
    return len(parts)


def sidebar_pin_gate(env, settings_path):
    """LAUNCH 1 — the PIN gate: the floor stays at `AppStore.sidebarWidthDefault` for every sidebar the
    measured content fits inside.

    It is the one assertion a regression cannot satisfy by moving the yardstick (everything in the
    containment sweep measures against the column the app chose). Two seeds make it exact on any host:
    `toolbarMode: hidden` drops the sidebar AdwHeaderBar, whose own minimum wherever the compositor does
    NOT draw the window buttons (the layout CI picks) would otherwise bind instead of the floor — see
    the sidebar rule's effective-floor bullet, which owns that measurement — and the SMALLEST sidebar
    font keeps the measured row minimum (165px here, ~176px in DejaVu Sans) under the pin for host text
    scaling up to ~1.75.
    It does NOT gate the measurement — a constant pin satisfies it by construction. Launch 3 does.
    """
    with open(settings_path, "w", encoding="utf-8") as target:
        json.dump({"toolbarMode": "hidden", "sidebarFontSize": 9}, target)
    process, app = launch(dict(env, AGTERM_APP_ID=env["AGTERM_APP_ID"] + ".floor"))
    try:
        column = sidebar_column_now(app)
        assert column.width == SIDEBAR_DEFAULT_WIDTH, (
            f"the sidebar floor is {column.width}px, not the {SIDEBAR_DEFAULT_WIDTH}px "
            "AppStore.sidebarWidthDefault it pins to whenever the measured content fits inside it — a "
            "second size_request on the sidebar tree lands here, and so does a pin that stopped being "
            "the default")
    finally:
        stop(process)


def sidebar_containment_sweep(env, settings_path, workspace_name):
    """LAUNCH 2 — the containment sweep at the LARGEST sidebar font, the thinnest-margin configuration:
    the row chrome grows with the font while the floor only follows it once the chrome stops fitting.

    It asserts no width of its own. Each site is checked in one of two ways, and which one is not a
    style choice — see `sidebar_fits` and `sidebar_does_not_widen` for the mechanism:

    - CONTAINMENT (`sidebar_fits`, against the column re-read immediately before it) for the two sites
      whose un-truncated text needs more than `AppStore.sidebarWidthMax` (560), where the floor is
      capped and the part really does overflow: the 40-character session name and the ~68-character
      flagged breadcrumb;
    - NO GROWTH (`sidebar_does_not_widen`, against the tree-mode column captured once) for the two
      narrower sites, where a lost ellipsize/wrap only WIDENS the column: the focus pill and the
      wrapped flagged-empty hint. Both keep their containment check as well, which costs nothing and
      still bites wherever the site's own text clears the cap — a workspace name far longer than this
      scenario's, for the pill.
    """
    with open(settings_path, "w", encoding="utf-8") as target:
        json.dump({"sidebarFontSize": 20}, target)
    process, app = launch(env)
    try:
        window_id = next(item["id"] for item in window_list(env) if item["open"])
        session_id = window_tree(env, window_id)["workspaces"][0]["sessions"][0]["id"]
        # Far longer than any sidebar column, so an un-ellipsized label reports a minimum some hundreds
        # of pixels past it and the row overflows rather than truncating.
        session_name = "sidebar-clipping-regression-session-name"
        control_json(env, "session", "rename", session_name, "--target", session_id, "--json")
        control_json(env, "session", "flag", "on", "--target", session_id, "--json")
        control_json(env, "session", "status", "blocked", "--target", session_id, "--json")
        # `unseenCount` and `agentIndicator` are EPHEMERAL — SessionSnapshot carries neither — so badge
        # and status glyph can only be driven at runtime, and the session must NOT be re-selected
        # afterwards, because AppStore.selectSession zeroes unseenCount.
        control_json(env, "notify", "narrow sidebar", "--title", "clipping",
                     "--target", session_id, "--json")

        def decorated():
            session = window_tree(env, window_id)["workspaces"][0]["sessions"][0]
            return (session.get("status") == "blocked" and session.get("flagged")
                    and session.get("unseen", 0) > 0)

        wait_for(decorated, "session never took the status, flag, and unseen-badge decorations")

        # Poll a row FIRST: a settled row means the paned already allocated, so the column read after
        # it is final rather than a value caught mid-transition from the seeded width.
        row, bounds = wait_for(lambda: sidebar_settled(app, "list item"),
                               f"no sidebar row reported a settled extent{SIDEBAR_SETTLE_HINT}")
        # No ceiling here: the floor is measured, so at 20pt it legitimately lands anywhere from the pin
        # to well past 240px on a wider font family or a text-scaled desktop. Launch 1 pins the floor
        # where the content fits; launch 3 pins that it follows the content where it does not.
        column = sidebar_column_now(app)
        assert column.width >= SIDEBAR_DEFAULT_WIDTH, (
            f"the sidebar narrowed below its {SIDEBAR_DEFAULT_WIDTH}px pin: {column.width}px")

        # Tree mode: terminal icon, agent status glyph and flag star are the three images; the name and
        # the unseen badge the two labels.
        tree_parts = sidebar_row_fits(app, row, bounds, expected_images=3, expected_labels=2)
        # The YARDSTICK for the two `sidebar_does_not_widen` checks below: the column as the fully
        # decorated tree row sized it, captured ONCE and on purpose. See that helper for why growth,
        # not overflow, is the regression signal for the sites it guards.
        tree_column = sidebar_column_now(app).width
        # The workspace header is a plain GtkBox, not a list item, and the reported symptom named its
        # `+` as what gets pushed out — so measure it separately, by its tooltip-derived name. This one
        # is a BACKSTOP, not a discriminator: the header's name goes through the same `makeNameWidget`
        # as the session row's, so the 40-character name above is what actually gates its ellipsize,
        # and the `+` itself hugs. It is here to catch a NEW widget appended to the header that does
        # not, in the one configuration where the margin is thinnest.
        add, add_box = wait_for(
            lambda: sidebar_settled(app, "button", lambda name: name == f"New Session in {workspace_name}"),
            "the workspace header never exposed its add-session button")
        sidebar_fits(sidebar_column_now(app), add_box, "the workspace-row add-session button")

        # The focus pill is the one site whose widget STRUCTURE changed (gtk_button_set_label became an
        # explicit gtk_label_new + gtk_button_set_child, so the ellipsize lands on a label the button
        # does not build privately), and only a focused workspace renders it. Matching it BY NAME also
        # pins the claim that a button with a GtkLabel child still exposes the whole string as its
        # accessible name — which `gtk_button_get_label` no longer does for it.
        workspace_id = window_tree(env, window_id)["workspaces"][0]["id"]
        control_json(env, "workspace", "focus", "on", "--target", workspace_id, "--json")
        pill, pill_box = wait_for(
            lambda: sidebar_settled(app, "button",
                                    lambda name: name.endswith(workspace_name) and "✕" in name),
            f"the focus pill never rendered for the focused workspace{SIDEBAR_SETTLE_HINT}")
        sidebar_fits(sidebar_column_now(app), pill_box, "the focus pill")
        sidebar_does_not_widen(app, tree_column, "the focus pill")
        control_json(env, "workspace", "focus", "off", "--target", workspace_id, "--json")

        # Flagged mode renders the LONGEST string the sidebar has, the "<session>  —  <workspace>"
        # breadcrumb, through a different label site than makeNameWidget. The flag star is suppressed
        # there (every row is flagged), so only two images remain.
        control_json(env, "sidebar", "mode", "flagged", "--json")
        breadcrumb = f"{session_name}  —  {workspace_name}"
        wait_for(lambda: sidebar_settled(app, "label", lambda name: name.startswith(breadcrumb)),
                 f"the flagged view never rebuilt the breadcrumb row{SIDEBAR_SETTLE_HINT}")
        flagged_row, flagged_bounds = wait_for(
            lambda: sidebar_settled(app, "list item"),
            f"the flagged row never reported a settled extent{SIDEBAR_SETTLE_HINT}")
        flagged_parts = sidebar_row_fits(app, flagged_row, flagged_bounds,
                                         expected_images=2, expected_labels=2)

        # The empty flagged view swaps the rows for the wrapped instructional hint, which is the one
        # sidebar label that must NOT ellipsize — its minimum comes from its longest WORD instead.
        control_json(env, "session", "flag", "off", "--target", session_id, "--json")
        empty, empty_box = wait_for(
            lambda: sidebar_settled(app, "label", lambda name: name.startswith("No flagged sessions")),
            f"the empty flagged view never rebuilt its hint{SIDEBAR_SETTLE_HINT}")
        sidebar_fits(sidebar_column_now(app), empty_box, "the flagged-empty hint")
        # The empty view drops the rows entirely, so the column can only be NARROWER than the tree
        # yardstick — unless the hint stopped wrapping and started reporting its longest LINE.
        sidebar_does_not_widen(app, tree_column, "the flagged-empty hint")
        print(f"OK: decorated rows truncate inside the sidebar column the app chose "
              f"({tree_parts} tree parts, {flagged_parts} flagged parts, the add button, the focus "
              f"pill and the wrapped empty-state hint all inside the column measured at each step), "
              f"and neither the pill nor the hint widened the {tree_column}px tree-mode column")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        stop(process)


def sidebar_measurement_gate(env, state, settings_path):
    """LAUNCH 3 — the MEASUREMENT gate: the floor FOLLOWS the measured content minimum once the content
    no longer fits the pin.

    It reuses launch 1's seeds — `toolbarMode: hidden` and the smallest sidebar font — so the sidebar
    AdwHeaderBar cannot be what widens the column and the app's own sidebar CSS asks for the narrowest
    rows it has; the ONLY thing left that can push the column past the pin is the measurement.

    The lever is a user `gtk-4.0/gtk.css` under an isolated XDG_CONFIG_HOME. GTK honours it (unlike
    `settings.ini` — see trap 4) at GTK_STYLE_PROVIDER_PRIORITY_USER (800), above the 651 the app's own
    sidebar provider uses, so it wins on any host and at any text scale. `min-width` rather than a font
    size on purpose: it is in px, so the raised minimum is the same number on every font family and text
    scale, and it stays comfortably under AppStore.sidebarWidthMax (560) — a floor capped by the max
    would overflow its column and report as a clipping instead.
    """
    gtk_config = os.path.join(state, "xdg-config", "gtk-4.0")
    os.makedirs(gtk_config, exist_ok=True)
    with open(os.path.join(gtk_config, "gtk.css"), "w", encoding="utf-8") as target:
        target.write(".agterm-sidebar label { min-width: 300px; }\n")
    with open(settings_path, "w", encoding="utf-8") as target:
        json.dump({"toolbarMode": "hidden", "sidebarFontSize": 9}, target)
    process, app = launch(dict(env,
                               XDG_CONFIG_HOME=os.path.join(state, "xdg-config"),
                               AGTERM_APP_ID=env["AGTERM_APP_ID"] + ".measured"))
    try:
        # Launch 2 left the sidebar in flagged mode with nothing flagged, which renders only the hint.
        control_json(env, "sidebar", "mode", "tree", "--json")
        row, bounds = wait_for(lambda: sidebar_settled(app, "list item"),
                               f"no sidebar row reported a settled extent{SIDEBAR_SETTLE_HINT}")
        column = sidebar_column_now(app)
        assert column.width > SIDEBAR_DEFAULT_WIDTH, (
            f"the sidebar column stayed at {column.width}px while its content needs more than the "
            f"{SIDEBAR_DEFAULT_WIDTH}px pin — the floor is no longer following gtk_widget_measure (a "
            "constant, or a measure pointed at the wrong widget: the scroller measures ~46px, the "
            "sidebar box is the widest sidebar site by construction)")
        # …and the raised floor is a floor the content really fits inside, not just a bigger number.
        sidebar_row_fits(app, row, bounds, expected_images=1, expected_labels=1)
        print(f"OK: the sidebar floor followed its measured content up to {column.width}px, and the "
              "row fits inside it")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        stop(process)


def sidebar_window_width_gate(env, state, settings_path):
    """LAUNCH 4 — the WINDOW-WIDTH gate: a divider GTK capped because the window got narrow comes back
    when the window gets wide again.

    `store.sidebarWidth` holds the user's REQUEST, so narrowing the window past it correctly does NOT
    rewrite it (that is the `max-position` leg of `persistedSidebarWidth`, unit-covered) — but nothing
    then pulls the divider back up, because GTK does not emit `notify::position` for the widening at
    all. Only `notify::max-position` fires, which is why `buildSidebarSplit` connects it. Without that
    handler the sidebar stays at the narrow window's cap while the store still holds the request, until
    some unrelated sidebar rebuild happens to re-lay it out — never, in an idle window.

    It reuses launch 1's seeds (`toolbarMode: hidden`, the smallest sidebar font) so the floor is the
    plain pin and the request is unambiguously above it, and seeds the request by patching the
    per-window record the earlier launches already wrote.
    """
    windows_dir = os.path.join(state, "windows")
    record_path = next(os.path.join(windows_dir, name) for name in sorted(os.listdir(windows_dir))
                       if name.endswith(".json"))
    with open(record_path, encoding="utf-8") as source:
        record = json.load(source)
    record["sidebarWidth"] = SIDEBAR_REQUESTED_WIDTH
    with open(record_path, "w", encoding="utf-8") as target:
        json.dump(record, target)
    with open(settings_path, "w", encoding="utf-8") as target:
        json.dump({"toolbarMode": "hidden", "sidebarFontSize": 9}, target)
    process, app = launch(dict(env, AGTERM_APP_ID=env["AGTERM_APP_ID"] + ".rewiden"))
    try:
        window_id = next(item["id"] for item in window_list(env) if item["open"])

        def column_width_is(predicate):
            box = sidebar_column_now(app)
            return box.width if predicate(box.width) else None

        wait_for(lambda: column_width_is(lambda width: width == SIDEBAR_REQUESTED_WIDTH),
                 f"the sidebar never restored its saved {SIDEBAR_REQUESTED_WIDTH}px "
                 f"request{SIDEBAR_SETTLE_HINT}")

        def resize(width, height):
            control_json(env, "window", "resize", window_id,
                         "--width", str(width), "--height", str(height), "--json")

        # Narrow the window until GTK has to cap the divider below the request. The width asked for is
        # what the window CAN be, not what it will be: the terminal deck and the sidebar floor both
        # carry real minimums and the compositor may hand back more — the cycle only needs the column
        # to end up under the request, at whatever number that takes.
        #
        # `window.resize` is `gtk_window_set_default_size`, and a WAYLAND compositor is free to ignore
        # it for a window it manages (a tiled Hyprland window keeps its tile, verified). So the narrow
        # leg is CONDITIONAL — `required=False` — and when the resize does not take there is nothing
        # for the widening to restore, so the cycle is skipped out loud rather than failing for the
        # compositor's choice. It does take under the Xvfb + openbox session `scripts/test-linux-ui.sh`
        # builds, which is how CI runs this suite, and that is where this gate is authoritative —
        # EXECUTED there, not assumed: the leg ran under a reconstructed X11 session and reported the
        # divider capped to 349px and restored to 400px. It could not have run before the CSD
        # negative-origin fix in `window_extents`, which aborted this scenario at launch 1 on every X11
        # host.
        resize(360, 700)
        capped = wait_for(lambda: column_width_is(lambda width: width < SIDEBAR_REQUESTED_WIDTH),
                          "the window never narrowed", timeout=8, required=False)
        if capped is None:
            print("SKIP: the compositor kept the window at its own size, so the sidebar was never "
                  "capped and the widen-restores-the-request cycle cannot run here (it runs under "
                  "the Xvfb + openbox session CI uses)")
        else:
            # Widening it back must restore the request. This is the handler under test.
            resize(1100, 700)
            wait_for(lambda: column_width_is(lambda width: width == SIDEBAR_REQUESTED_WIDTH),
                     f"the sidebar stayed at the narrow window's {capped}px cap after the window "
                     f"widened again instead of returning to its {SIDEBAR_REQUESTED_WIDTH}px request "
                     "— `notify::position` does not fire for a widening, so `notify::max-position` is "
                     f"the only signal that can pull the divider back up{SIDEBAR_SETTLE_HINT}")
            print(f"OK: the sidebar was capped to {capped}px by the narrow window and returned to its "
                  f"{SIDEBAR_REQUESTED_WIDTH}px request when the window widened again")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        stop(process)
    with open(record_path, encoding="utf-8") as source:
        assert json.load(source).get("sidebarWidth") == SIDEBAR_REQUESTED_WIDTH, (
            "the narrow window's cap was persisted over the saved sidebar width — `captureSidebarWidth` "
            "must drop a position the LAYOUT produced, or the wider request is destroyed for good")


def verify_sidebar_narrow_clipping(env):
    """A narrow sidebar truncates fully decorated rows instead of overflowing its column.

    Regression cover for the shrink-clipping bug: a GtkLabel carrying neither ellipsize nor wrap
    reports its WHOLE text as its minimum width, so one long session name forced every sidebar row
    wider than the scroller's viewport — hard-cutting the name mid-glyph and carrying the agent status
    glyph, the flag star, and the unseen badge off the right edge. It runs FOUR launches, one helper
    each: the PIN gate, the containment sweep at the largest sidebar font, the MEASUREMENT gate, then
    the WINDOW-WIDTH gate. Only the first, third and fourth can assert a number, and only because each
    seeds the thing it measures; the second cannot, because the floor is MEASURED and the width it
    lands at depends on the resolved font family and the desktop's text scaling — so it compares the
    column against ITSELF, both for containment and for the no-growth gates trap (6) explains.

    Launches 1 and 3 are two halves of one gate and neither covers the other. `sidebarWidthFloor` pins
    the measured content minimum to `AppStore.sidebarWidthDefault` and follows it above that, so launch
    1 (nothing added to the sidebar) asserts the pin EXACTLY and launch 3 (a user stylesheet that
    raises the content minimum well past it) asserts the floor followed. Replacing the whole
    `gtk_widget_measure` with a constant passes launch 1 by construction and passes launch 2 too — at
    20pt with this scenario's one-digit badge the content minimum still sits under the pin on every
    font family, so the measured branch is never reached there — and only launch 3 catches it.

    Six traps it encodes.
    (1) Measure against the scrolled window itself and never a node INSIDE it (see `sidebar_column`).
    (2) Re-read that column immediately before EVERY containment check (see `sidebar_column_now`),
    never once up front: each step re-measures the floor and can move the divider either way.
    (3) The badge it drives is a one-digit `1`, materially narrower at 20pt than the `99+` worst case
    (the sidebar rule carries the measured badge widths) — which would need 100 `notify` calls and 100
    real desktop banners, so that case is covered by widget measurement instead.
    (4) The host's text scaling CANNOT be pinned: GTK 4.22.4 ignores `gtk-4.0/settings.ini` whenever a
    settings portal or an XSETTINGS manager answers first (verified — an isolated XDG_CONFIG_HOME
    setting `gtk-xft-dpi` AND `gtk-font-name` changed neither, on both backends, with and without
    DBUS_SESSION_BUS_ADDRESS), so the gates below are built to hold at any plausible scale instead. A
    user `gtk-4.0/gtk.css` in that same isolated config dir IS honoured, which is the lever launch 3
    uses.
    (5) ⚠️ Driving the decorations REBUILDS the row, and GTK allocates a rebuilt widget only while the
    window is actually being rendered — always true under Xvfb, which is how CI runs this suite. On a
    live Wayland session `launch()` parks the window on a silent workspace, and while the compositor
    is not displaying it the frame clock stalls, so every rebuilt row stays at a zero extent and the
    settle polls below time out. If that happens, run this one as `env -u HYPRLAND_INSTANCE_SIGNATURE
    AGTERM_ATSPI_SCENARIO=sidebar-narrow-clipping python3 …`, which skips the parking and leaves the
    window on the current workspace; the app still picks the Hyprland decoration layout, because
    `LinuxDesktopEnvironment` falls back to XDG_CURRENT_DESKTOP.
    (6) ⚠️ Containment is a VACUOUS check for most sidebar sites, and for the same reason (2) exists:
    the floor follows the content, so a label that lost its ellipsize widens the column instead of
    overflowing it. Only text needing more than the 560px `AppStore.sidebarWidthMax` can be caught
    that way. Launch 2 therefore gates the two narrow sites on the column NOT GROWING — see
    `sidebar_does_not_widen`, which carries the measurement showing the containment-only version
    passed with the regression live.
    """
    state = env["AGTERM_STATE_DIR"]
    settings_path = os.path.join(state, "settings.json")
    # `sidebarWidth` is per-window state in windows/<uuid>.json and that uuid does not exist before the
    # first launch, so seed the legacy `workspaces.json` instead — `WindowLibrary` migrates it into a
    # window record when windows.json is absent. AppStore.sidebarWidthMin is the narrowest width the
    # shared model accepts; GTK then widens it to whatever the Linux floor really is, which is exactly
    # the width the launches below want to measure at.
    workspace_name = "narrow sidebar workspace"
    with open(os.path.join(state, "workspaces.json"), "w", encoding="utf-8") as target:
        json.dump({
            "version": 1,
            "sidebarWidth": 160,
            "workspaces": [{
                "id": "4C2A1E80-6C1E-4C6B-9B2E-1B0A5F3D77A1",
                "name": workspace_name,
                "sessions": [{"id": "9E6D3F14-2B77-4A55-8C31-0D5E9A2B6C48", "cwd": env["HOME"]}],
            }],
        }, target)

    sidebar_pin_gate(env, settings_path)
    sidebar_containment_sweep(env, settings_path, workspace_name)
    sidebar_measurement_gate(env, state, settings_path)
    sidebar_window_width_gate(env, state, settings_path)


def verify_preferences_pages(env, home):
    process, app = launch(env)
    try:
        focus_window(process.pid)
        assert not named(app, "Main Menu"), "Preferences test found the removed Main Menu button"
        wait_for(
            lambda: named(app, "Right-click pastes"),
            "Preferences did not expose the corrected Right-click pastes row",
        )
        for page in ["General", "Appearance", "Notifications", "Agent Status", "Key Mapping", "Integrations"]:
            assert named(app, page), f"Preferences page {page!r} is missing"

        wait_for(lambda: actionable(app, "Right-click pastes"), "Right-click switch is not actionable")
        stop(process)
        process = None
        os.makedirs(os.path.join(home, ".pi/agent"))
        env = dict(
            env,
            AGTERM_APP_ID=env["AGTERM_APP_ID"] + ".integrations",
            AGTERM_ATSPI_OPEN_PREFERENCES="integrations",
        )
        process, app = launch(env)
        window_title = wait_for(
            lambda: next((item.get_name() for item in collect(app, role="frame") if item.get_name()), None),
            "integration test window title is missing",
        )
        pi_row = wait_for(lambda: named(app, "Pi Extension"), "Pi integration row is missing")
        pi_install = wait_for(
            lambda: next((item for item in descendants(pi_row) if item.get_name() == "Install"), None),
            "Pi extension did not become installable",
        )
        activate(pi_install)
        wait_for(lambda: named(app, "Apply Integration Changes?"), "Pi hooks preflight was not shown")
        pi_extension = os.path.join(home, ".pi/agent/extensions/agterm-status.ts")
        assert not os.path.exists(pi_extension), "Pi preflight mutated HOME"
        wait_for(lambda: actionable(app, "Apply"), "Pi hooks preflight has no Apply action")
        press_return(process.pid, window_title=window_title)
        wait_for(lambda: os.path.exists(pi_extension), "Pi extension was not installed")
        with open(pi_extension, encoding="utf-8") as source:
            assert "// agterm-pi-status-extension" in source.read(), "Pi ownership marker is missing"
        wait_for(lambda: named(app, "Integration Updated"), "Pi hooks result was not shown")
        wait_for(lambda: actionable(app, "OK"), "Pi hooks result has no OK action")
        press_escape(process.pid, window_title=window_title)
        wait_for(
            lambda: next((item for item in descendants(pi_row) if item.get_name() == "Current"), None),
            "Pi row did not refresh to Current",
        )

        skill_row = wait_for(
            lambda: named(app, "Agent Skill", role="list item"),
            "Agent Skill integration row is missing",
        )
        install = wait_for(
            lambda: next((item for item in descendants(skill_row) if item.get_name() == "Install"), None),
            "Agent Skill did not become installable",
        )
        activate(install)
        wait_for(lambda: named(app, "Apply Integration Changes?"), "integration preflight was not shown")
        assert not os.path.exists(os.path.join(home, ".claude/skills/agterm")), "preflight mutated HOME"
        stop(process)
        process = None
        assert not os.path.exists(os.path.join(home, ".claude/skills/agterm")), "closing preflight mutated HOME"

        subprocess.run(
            [CTL, "integration", "install", "skill"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        wait_for(
            lambda: os.path.exists(os.path.join(home, ".claude/skills/agterm/SKILL.md"))
            and os.path.exists(os.path.join(home, ".codex/skills/agterm/SKILL.md")),
            "safe skill installation did not write both isolated destinations",
        )
        assert os.path.realpath(home) not in os.path.realpath(os.path.expanduser("~/.claude")), "test HOME is not isolated"
        print("OK: Preferences pages, Pi hooks preflight/apply, and safe skill install")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        if process is not None:
            stop(process)


def verify_hidden_toolbar(env, state):
    settings_path = os.path.join(state, "settings.json")
    settings = {}
    if os.path.exists(settings_path):
        with open(settings_path, encoding="utf-8") as source:
            settings = json.load(source)
    settings["toolbarMode"] = "hidden"
    with open(settings_path, "w", encoding="utf-8") as destination:
        json.dump(settings, destination)

    process, app = launch(env)
    try:
        assert not named(app, "Main Menu"), "hidden toolbar still exposes the header menu"
        window_id = next(item["id"] for item in window_list(env) if item["open"])
        control_json(env, "dashboard", "--mru", "--window", window_id, "--json")
        wait_for(
            lambda: window_tree(env, window_id).get("dashboardMembers"),
            "hidden-toolbar dashboard did not open over control",
        )
        assert not named(app, "Exit Dashboard", role="button"), (
            "hidden toolbar exposed the dashboard modal header"
        )
        control_json(env, "dashboard", "--close", "--window", window_id, "--json")
        control_json(
            env, "surface", "zoom", "show", "--target", "active",
            "--window", window_id, "--json",
        )
        wait_for(
            lambda: window_tree(env, window_id).get("zoomedSurface"),
            "hidden-toolbar terminal zoom did not open",
        )
        assert not named(app, "Exit Terminal Zoom", role="button"), (
            "hidden toolbar exposed the terminal-zoom modal header"
        )
        control_json(
            env, "surface", "zoom", "hide", "--target", "active",
            "--window", window_id, "--json",
        )
        assert not preferences_window(app), "Preferences was open before hidden-toolbar shortcut"
        focus_window(process.pid)
        press_ctrl_comma(process.pid)
        wait_for(
            lambda: preferences_window(app),
            "Ctrl+, did not open Preferences with toolbar hidden",
        )
        print("OK: hidden modal chrome stays hidden and Preferences remains keyboard-accessible")
    finally:
        stop(process)


def verify_session_pickers(env, state):
    settings_path = os.path.join(state, "settings.json")
    with open(settings_path, "w", encoding="utf-8") as destination:
        json.dump({"attentionButtonEnabled": True}, destination)

    process, app = launch(env)
    try:
        tree = control_json(env, "tree", "--json")["result"]["tree"]
        original_id = tree["workspaces"][0]["sessions"][0]["id"]
        subprocess.run(
            [CTL, "session", "new", "--socket", env["AGTERM_CONTROL_SOCKET"]],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        subprocess.run(
            [
                CTL, "session", "status", "blocked", "--target", original_id,
                "--socket", env["AGTERM_CONTROL_SOCKET"],
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )

        recent = wait_for(
            lambda: actionable(app, "Recent Sessions (Ctrl+Tab)"),
            "Recent Sessions button is missing or not actionable",
        )
        activate(recent)
        recent_row = wait_for(
            lambda: next(
                (
                    item for item in collect(app, role="button")
                    if "workspace 1 ·" in (item.get_name() or "")
                ),
                None,
            ),
            "Recent Sessions popover did not expose a session row",
        )
        activate(recent_row)
        wait_for(
            lambda: actionable(app, "Show sessions that need attention (Ctrl+Shift+I)"),
            "Attention button is missing or not actionable",
        )
        activate(actionable(app, "Show sessions that need attention (Ctrl+Shift+I)"))
        wait_for(
            lambda: next(
                (
                    item for item in collect(app, role="button")
                    if "workspace 1 ·" in (item.get_name() or "")
                ),
                None,
            ),
            "Attention popover did not expose a session row",
        )
        print("OK: recent-session and attention popovers expose actionable rows")
    except AssertionError:
        describe_tree(app)
        raise
    finally:
        stop(process)


def verify_auto_follow(env, state):
    auto_state = state + "-auto-follow"
    os.makedirs(auto_state)
    auto_env = dict(
        env,
        AGTERM_STATE_DIR=auto_state,
        AGTERM_CONTROL_SOCKET=os.path.join(auto_state, "agterm.sock"),
        AGTERM_APP_ID="io.github.melonamin.agterm.atspi.autofollow",
    )
    with open(os.path.join(auto_state, "settings.json"), "w", encoding="utf-8") as destination:
        json.dump({"autoFollowAttention": "s5"}, destination)

    process, app = launch(auto_env)
    try:
        tree = control_json(auto_env, "tree", "--json")["result"]["tree"]
        blocked_id = tree["workspaces"][0]["sessions"][0]["id"]
        subprocess.run(
            [CTL, "session", "new", "--socket", auto_env["AGTERM_CONTROL_SOCKET"]],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=auto_env,
        )
        def set_status(status):
            subprocess.run(
                [
                    CTL, "session", "status", status, "--target", blocked_id,
                    "--socket", auto_env["AGTERM_CONTROL_SOCKET"],
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=auto_env,
            )

        def auto_followed():
            sessions = control_json(auto_env, "tree", "--json")["result"]["tree"]["workspaces"][0]["sessions"]
            return next((session for session in sessions if session["id"] == blocked_id), {}).get("active")

        wait_for(
            lambda: preferences_window(app),
            "startup Preferences dialog did not open for auto-follow test",
        )
        set_status("blocked")
        time.sleep(6)
        assert not auto_followed(), "auto-follow changed sessions while Preferences was open"
        print("OK: GTK/GLib auto-follow pauses for Preferences")
    finally:
        stop(process)


def main():
    for path in (BIN, CTL):
        if not os.path.exists(path):
            print(f"FAIL: required build product is missing: {path}")
            return 2

    scenario = os.environ.get("AGTERM_ATSPI_SCENARIO")
    if scenario is None:
        for child_scenario in (
            "normal", "upstream-controls", "dashboard-modal", "context-menu",
            "window-ownership", "preferences-pages",
            "notification-reveal", "notification-focus", "session-pickers",
            "custom-command-failures", "surface-lifetimes",
            "sidebar-row-height", "sidebar-narrow-clipping",
            "auto-follow", "hidden-toolbar",
        ):
            child_env = dict(os.environ, AGTERM_ATSPI_SCENARIO=child_scenario)
            result = subprocess.run([sys.executable, __file__], env=child_env)
            if result.returncode != 0:
                return result.returncode
        print("PASS")
        return 0

    root = tempfile.mkdtemp(prefix="agterm-atspi-")
    home = os.path.join(root, "home")
    state = os.path.join(root, "state")
    os.makedirs(os.path.join(home, ".claude"))
    os.makedirs(os.path.join(home, ".codex"))
    os.makedirs(state)
    socket = os.path.join(state, "agterm.sock")
    env = dict(
        os.environ,
        HOME=home,
        AGTERM_STATE_DIR=state,
        AGTERM_CONTROL_SOCKET=socket,
        AGTERM_RESOURCE_ROOT=RESOURCE_ROOT,
        AGTERM_APP_ID=f"io.github.melonamin.agterm.atspi.{scenario.replace('-', '_')}",
        PATH="/usr/bin:/bin",
    )
    if scenario in ("preferences-pages", "auto-follow"):
        # Page inspection and auto-follow need an already-mapped modal while another process owns focus.
        env["AGTERM_ATSPI_OPEN_PREFERENCES"] = "general"
    try:
        Atspi.init()
        if scenario == "normal":
            verify_normal_toolbar(env, state, home)
        elif scenario == "upstream-controls":
            verify_upstream_control_parity(env)
        elif scenario == "dashboard-modal":
            verify_dashboard_modal(env)
        elif scenario == "context-menu":
            verify_context_menu(env)
        elif scenario == "window-ownership":
            verify_window_callback_ownership(env)
        elif scenario == "notification-reveal":
            verify_notification_reveal(env)
        elif scenario == "notification-focus":
            verify_notification_focus_policy(env)
        elif scenario == "notification-banner":
            verify_notification_banner_round_trip(env)
        elif scenario == "custom-command-failures":
            verify_custom_command_failures(env)
        elif scenario == "surface-lifetimes":
            verify_surface_configuration_lifetimes(env)
        elif scenario == "sidebar-row-height":
            verify_sidebar_row_height_follows_font_size(env)
        elif scenario == "sidebar-narrow-clipping":
            verify_sidebar_narrow_clipping(env)
        elif scenario == "preferences-pages":
            verify_preferences_pages(env, home)
        elif scenario == "auto-follow":
            verify_auto_follow(env, state)
        elif scenario == "session-pickers":
            verify_session_pickers(env, state)
        elif scenario == "hidden-toolbar":
            verify_hidden_toolbar(env, state)
        else:
            raise ValueError(f"unknown AT-SPI scenario: {scenario}")
        print(f"PASS: {scenario}")
        return 0
    except (AssertionError, subprocess.CalledProcessError, OSError, ValueError) as error:
        print(f"FAIL: {error}")
        return 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
