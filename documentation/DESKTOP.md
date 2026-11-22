# Share Home Assistant with WHAT Desktop

Pair the Windows PC with this installation and enable **WHAT Desktop** in the Windows app's Modules page. The desktop widgets use the paired WHAT API or MQTT connection. Windows does not need a Home Assistant access token.

## Choose what a PC can access

A new pairing defaults to **Control shared entities**. Pick a home and an entity in WHAT Desktop and the widget is ready to use. For example, choosing `climate.air_con` gives you the supported temperature and mode controls. The default includes current and future entities, including locks and scripts. Viewing includes state, history and supported details such as camera snapshots and calendar events.

On Windows, open **WHAT Desktop > Edit > +** and browse entities from the installation you want. Filter by area or entity type, or search by name, entity ID, device, area or integration. For example, `kitchen ceiling` finds a device called *Ceiling* in the *Kitchen* area. You can also choose a widget first and use the same browser in its property editor.

Choose **View only** when pairing a display that should only show readings. To let it operate just a few devices, choose individual controls and select their entities. You can change these choices later under **Settings > Devices & services > WHAT > Configure > Shared entities** for that PC. To limit what appears in its picker, turn off **View all entities** and select what to share.

Existing pairings that have never had Desktop sharing configured use the new default. Existing sharing selections, including empty control lists, keep their previous restrictions. Windows permissions for commands that Home Assistant sends to the PC are configured separately in WHAT.

## Automatic entity widgets

Open **WHAT Desktop > Edit > +**, select the Home Assistant installation and pick an entity. WHAT uses its domain, device class, current attributes and supported actions to build the widget. Two homes can use the same entity ID. A widget stays bound to the installation where you selected it.

A thermostat can show the measured temperature, target or target range, HVAC mode, fan, preset and swing controls. A cover can offer position and tilt. A media player can offer playback, volume, source and a seek control. Each device supplies its own choices and limits. For a sensor or a custom domain, the widget can show the available state and unit even when the device offers no recognised controls.

Weather, calendar, camera, image and to-do widgets can fetch additional details from the selected entity. A legacy group shows up to 50 members that the PC can view. To operate a member, pick it for its own widget.

A view-only PC can see what a device supports. Grant that PC control to enable its buttons and inputs. For actions involving other targets, configure a saved action with those targets in Home Assistant.

### Lights

In WHAT Desktop, add a **Light** widget and choose a light from the installation you want. The tile uses the selected entity’s supported colour modes to show brightness and colour controls. A light group reports capabilities for the group as a whole. You can see supported controls on a view-only PC and operate them once that PC has control permission.

## Scripts, scenes and saved actions

Pick a shared script, automation or scene to use it from a button. Create an automation
or script from a blueprint first, then share the resulting entity with the PC.

For actions supplied by devices, integrations or Home Assistant OS, use **Configure >
Add a saved action**. Choose a device action or a registered service action in Home
Assistant's action editor, give it a recognisable name and set any targets and data.
Then enable **Allow saved actions** under
**Shared entities**. The PC can run the saved action using those fixed settings.
Check the scope before saving. Some actions apply to the whole installation when the
target is empty. **Edit a saved action** keeps existing widgets bound to it.
**Remove a saved action** revokes an individual shortcut.

A setting that has no registered action can still be reached through the Home Assistant
page widget. The browser uses the user's normal Home Assistant login and permissions.
[Windows widget and profile guide](https://github.com/Chuffnugget/what-windows/blob/main/documentation/DESKTOP.md#home-assistant-widgets)
