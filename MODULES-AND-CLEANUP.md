# WHAT devices and entity cleanup

Each PC has a parent device, such as `WHAT · Office PC`, for connection diagnostics.
Readings and controls belong to module devices such as `WHAT · Office PC · HWiNFO`
and `WHAT · Office PC · BEACN`. Physical iCUE devices have their own entries.
HWiNFO readings share one module device.

Existing entity IDs, unique IDs and custom names are retained. Names for newly
created entities include WHAT, the PC and the module.

## Remove unshared entities

1. On the parent PC device, press **Preview Entity Cleanup**.
2. Open the **Entity Cleanup** diagnostic to review the count and first 20 candidates.
3. Press **Remove Previewed Entities** within two minutes to confirm.

**Nothing to remove** means the preview found no eligible entities. The removal
button is unavailable unless there is a valid preview containing candidates.

Cleanup removes unselected sensor and binary-sensor registrations. It also removes
the six unsupported BEACN Aux controls when WHAT explicitly identifies them as
obsolete. It keeps supported controls, diagnostics, currently received readings
and selected sensors, including temporarily unavailable readings and disabled
modules. Removed entities lose their custom names and settings; recorder history
is not erased.

The PC must be connected locally and provide a fresh, complete selection list.
Cloud connections omit this list. Cleanup is blocked if the PC is disconnected,
the list is stale, or the app does not supply it. Confirmation checks the selection
again and cannot remove more entities than the preview listed.

The integration reloads after removal. Selecting a removed sensor again lets it
be discovered again. Installation does not run cleanup automatically.

## PC diagnostics

The parent device also shows connection uptime, active transport, remote-access
permission and availability counts. Uptime resets on disconnection or integration
reload; changing transport alone does not reset it. Remote-access permission
shows what WHAT allows, not whether the PC is reachable over the Internet.
