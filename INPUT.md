# Keyboard and mouse from Home Assistant

Enable Modules > Windows > Keyboard and mouse from HA on the PC. It is off by default and saved immediately. Actions require a Home Assistant administrator when called by a user; automations can call them too.

## Keyboard shortcut

```yaml
action: what.send_keyboard
target:
  device_id: YOUR_PC_DEVICE_ID
data:
  keys: [CTRL, SHIFT, S]
```

Put modifiers before the main key. Supported keys: A-Z, 0-9, F1-F24, CTRL, ALT, SHIFT, WIN, ENTER, TAB, ESC, SPACE, BACKSPACE, DELETE, INSERT, HOME, END, PAGEUP, PAGEDOWN, LEFT, RIGHT, UP, DOWN, PRINTSCREEN. A chord has 1-6 unique keys. Keys are released in reverse order.

## Type text

```yaml
action: what.send_keyboard
target:
  device_id: YOUR_PC_DEVICE_ID
data:
  text: Hello from Home Assistant
```

Provide either keys or text, not both. Text supports Unicode, up to 1024 UTF-16 units (some emoji use two). Input goes to the currently focused window; it does not find or activate an application. Typed content is omitted from PC logs but may appear in HA automation traces.

## Mouse

```yaml
action: what.send_mouse
target:
  device_id: YOUR_PC_DEVICE_ID
data:
  x: 800
  y: 450
  button: left
  clicks: 1
```

Physical desktop pixels across all displays; (0,0) is the primary monitor's top-left. Displays to its left or above can use negative coordinates. Coordinates outside monitors, including gaps, are rejected. button is left, right, middle or none. For movement only, use none and omit clicks. clicks can be 1 or 2, defaulting to 1 with a button. Persistent button/key hold, dragging and wheel input are not supported.

Requires an unlocked interactive desktop. Secure desktops and UAC/elevated applications may reject input; the app does not elevate or bypass those restrictions. Locally held modifier keys or mouse buttons cause rejection. Commands use the existing Windows command worker, session cancellation and permission checks. Another long-running Windows command can make input return busy. Submission is not proof that the foreground application acted on it. Requests are never automatically retried.

Tests validate requests, permissions and routing without sending real input to the developer's desktop. Test mode performs no OS input.
